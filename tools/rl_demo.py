# -*- coding: utf-8 -*-
"""单次行程的决策演示：把 Q-learning 的建议打印成一张"前端能看"的表。

用法
----
    python tools/rl_demo.py                       # 用 outputs/rl/policy.json（没有就现场训 200 单）
    python tools/rl_demo.py --departure 480 --load 30 --strategy carbon
    python tools/rl_demo.py --train 400           # 强制重训

输出三段：① 出发前的策略选择与 ETA ② 逐段明细（时间/碳/油/功率/拥堵）
③ 行程汇总与基线对比。这份打印稿就是前端 payload 的字段顺序来源。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rl import settings  # noqa: E402
from src.rl.env import TripEnv, TripRequest  # noqa: E402
from src.rl.network import build_demo_network  # noqa: E402
from src.rl.policy import RecommendationService, plan_route  # noqa: E402
from src.rl.train import (build_timeline, demo_requests, load_policy, save_policy,  # noqa: E402
                          train)


def _hhmm(minute: float) -> str:
    total = int(round(minute)) % 1440
    return "%02d:%02d" % (total // 60, total % 60)


def main() -> int:
    parser = argparse.ArgumentParser(description="Q-learning 动态路径决策演示")
    parser.add_argument("--config", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--departure", type=float, default=480.0)
    parser.add_argument("--load", type=float, default=18.0)
    parser.add_argument("--origin", default="G00")
    parser.add_argument("--destination", default="G33")
    parser.add_argument("--strategy", default=None,
                        help="强制指定策略：time/carbon/balanced/smooth")
    parser.add_argument("--train", type=int, default=0, help="重训回合数（0=直接加载）")
    args = parser.parse_args()

    config = settings.load_config(args.config)
    out_dir = Path(args.out) if args.out else settings.ROOT / "outputs" / "rl"
    net = build_demo_network()
    timeline = build_timeline(net, config)
    policy_path = out_dir / "policy.json"

    if args.train or not policy_path.exists():
        episodes = args.train or 200
        print("训练 %d 单…" % episodes)
        policy, _ = train(net, timeline, config, episodes=episodes, verbose=False)
        save_policy(policy, policy_path, meta={"demo": True})
    else:
        policy = load_policy(policy_path, config)

    request = TripRequest(args.origin, args.destination, load_t=args.load,
                          departure_minute=args.departure)
    bias_table = settings.model_bias_table(config)
    env = TripEnv(net, timeline, request, bias_table=bias_table)
    policy.start_episode()

    balanced = next((s for s in policy.strategies if s.sid == "balanced"),
                    policy.strategies[0])
    normalizer = settings.reference_normalizer(env, balanced, config)
    service = RecommendationService(policy, normalizer)

    print("\n=== 出发：%s → %s ｜ %s ｜ 载重 %.1f t ===" % (
        args.origin, args.destination, _hhmm(args.departure), args.load))
    first = service.recommend(env, strategy=args.strategy)
    print("策略：%s（Q 值 %s，置信度 %.2f）"
          % (first["strategy"]["label"], first["q_values"], first["strategy_confidence"]))
    print("ETA：%.2f min（保守 %.2f min，模型 %.2f，学习值 %.2f，TD 权重 %.2f）" % (
        first["eta"]["eta_min"], first["eta"]["eta_p90_min"],
        first["eta"]["model_time_min"], first["eta"]["learned_time_min"],
        first["eta"]["confidence"]))
    print("预计碳排：%.3f kg ｜ 预计费用：%.2f 元"
          % (first["plan"]["carbon_kg"], first["plan"]["cost_yuan"]))
    for reason in first["reasons"]:
        print("  · " + reason)

    rows = []
    while not env.done:
        decision = service.recommend(env, strategy=args.strategy)
        outcome = env.step(decision["action"])
        rows.append((outcome, decision["strategy"]["label"]))

    print("\n=== 逐段明细（%d 段）===" % len(rows))
    header = "%-16s %-7s %6s %6s %6s %7s %8s %8s"
    print(header % ("路段", "策略", "km", "km/h", "min", "功率kW", "g/km", "kg CO2"))
    for outcome, label in rows:
        print(header % (outcome.sid, label, "%.2f" % outcome.length_km,
                        "%.0f" % outcome.speed_kmh, "%.2f" % outcome.time_min,
                        "%.0f" % outcome.power_kw, "%.0f" % outcome.ef_g_per_km,
                        "%.3f" % outcome.carbon_kg))

    # 与"时间优先"基线对比
    env_time = TripEnv(net, timeline, request, bias_table=bias_table)
    base = plan_route(net, env_time.snapshot(), request.origin, request.destination,
                      policy.strategy_by_id("time"), normalizer, env_time.cost_model)
    print("\n=== 汇总 ===")
    print("实际：%.2f min ｜ 碳 %.3f kg ｜ 费用 %.2f 元 ｜ 油耗 %.3f L ｜ 重规划 %d 次" % (
        env.total_time_min, env.total_carbon_kg, env.total_cost_yuan,
        sum(o.fuel_l for o in env.outcomes), env.replans))
    print("未修正模型口径（对照，不含学习到的偏差修正）：%.2f min ｜ 碳 %.3f kg ｜ 费用 %.2f 元" % (
        base.time_min, base.carbon_kg, base.cost_yuan))
    print("\n学习器状态：TD 状态 %d 个/访问 %d 次 ｜ Q 表 %d×%d/访问 %d 次 ｜ ε=%.3f" % (
        policy.td.n_states, int(policy.td.visits.sum()),
        policy.q.n_states, policy.q.n_actions, int(policy.q.visits.sum()),
        policy.q.epsilon))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
