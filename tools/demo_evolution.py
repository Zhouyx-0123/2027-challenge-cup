# -*- coding: utf-8 -*-
"""命令行演示：跑一遍真实的「高德路线 → 碳核算 → NSGA-II + Q-learning 进化」。

用法：
    python tools/demo_evolution.py                       # 上海虹桥站 → 北京南站
    python tools/demo_evolution.py "杭州东站" "南京南站" --carbon-price 150

输出：
    · 每代的多样性 / Q-learning 动作 / 变异率 / 奖励（页面上的决策日志同源）
    · Q 值表随代次的演化
    · 最终帕累托候选与推荐策略

不写任何数据文件：记忆库只读不写，虚拟进化路线不落盘。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in (str(ROOT), str(ROOT / "app")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

# Windows 控制台默认 GBK，装不下「¥」这类符号，强制 UTF-8 输出。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - 老解释器
        pass

from src.routing import evolve_routes as evo  # noqa: E402


def _trace_q_table() -> list:
    """挂一层钩子，把每代更新后的 Q 表抓出来（不改动算法本身）。"""
    original = evo.QLearningAgent.update
    trace: list = []

    def traced(self, state, action, reward, next_state):  # noqa: ANN001
        original(self, state, action, reward, next_state)
        trace.append({
            "state": state, "action": action, "reward": reward,
            "next_state": next_state, "q": self.q.copy(),
            "mutation_rate": self.mutation_rate,
        })

    evo.QLearningAgent.update = traced
    return trace


def _print_q_table(trace: list) -> None:
    print("\n── Q 值表演化（行=状态，列=动作）────────────────────────────")
    print("状态 0=多样性极低(<0.005)  1=偏低(<0.03)  2=良好")
    print("动作 0=降变异率(利用)  1=保持(平衡)  2=升变异率(探索)")
    for index, step in enumerate(trace, start=1):
        q = step["q"]
        print("第 %d 次更新  s=%d a=%d r=%+.2f → s'=%d  变异率 %.2f   Q=[[%.3f %.3f %.3f]"
              " [%.3f %.3f %.3f] [%.3f %.3f %.3f]]"
              % (index, step["state"], step["action"], step["reward"],
                 step["next_state"], step["mutation_rate"],
                 q[0][0], q[0][1], q[0][2], q[1][0], q[1][1], q[1][2],
                 q[2][0], q[2][1], q[2][2]))


def main() -> int:
    parser = argparse.ArgumentParser(description="四维路线进化演示")
    parser.add_argument("origin", nargs="?", default="上海虹桥站")
    parser.add_argument("destination", nargs="?", default="北京南站")
    parser.add_argument("--carbon-price", type=float, default=100.0)
    parser.add_argument("--energy-cost", type=float, default=0.8)
    parser.add_argument("--truck-type", default="重型")
    parser.add_argument("--load", type=float, default=18.0)
    parser.add_argument("--explore-prob", type=float, default=0.2)
    parser.add_argument("--generations", type=int, default=8)
    parser.add_argument("--population", type=int, default=16)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    import live_services

    if not live_services.amap_key_available():
        print("未配置高德 Web 服务 Key，无法拉取真实路线。")
        return 1

    print("解析地点：%s → %s" % (args.origin, args.destination))
    start = live_services.resolve_route_place(args.origin)
    end = live_services.resolve_route_place(args.destination)
    print("  起点 %s (%s) → 终点 %s (%s)"
          % (start["label"], start["location"], end["label"], end["location"]))

    raw = live_services.amap_driving_routes(start["location"], end["location"])
    print("高德返回 %d 条备选路线" % len(raw))

    evaluated = live_services.evaluate_route_records(
        raw, args.truck_type, args.load, args.carbon_price, args.energy_cost)
    print("\n── 碳核算与综合成本（碳价 ¥%g/t，能耗 ¥%g/km）──────────────"
          % (args.carbon_price, args.energy_cost))
    print("  路线   距离km   时间min   过路费   碳排kg   拥堵系数   综合成本")
    for item in evaluated:
        print("  %-4s  %7.1f  %7.1f  %7.1f  %8.2f  %7.3f  %10.2f"
              % (item["route_id"], item["distance_km"], item["duration_min"],
                 item["tolls"], item["carbon_kg"], item["congestion_factor"],
                 item["total_cost_yuan"]))

    trace = _trace_q_table()
    logs: list = []
    records = evo.evolve_routes(
        evaluated, explore_prob=args.explore_prob, carbon_price=args.carbon_price,
        energy_cost_per_km=args.energy_cost, n_gen=args.generations,
        pop_size=args.population, seed=args.seed, decision_log=logs)

    print("\n── 智能体决策日志（与页面「查看智能体决策日志」同源）──────────")
    for message in logs:
        print("  " + message)

    if trace:
        _print_q_table(trace)

    print("\n── 最终候选（%d 条）──────────────────────────────────────"
          % len(records))
    print("  路线            距离km   时间min   碳排kg   综合成本  探索  推荐  策略")
    for item in records:
        print("  %-14s %7.1f  %7.1f  %8.2f  %9.2f  %s    %s    %s"
              % (item["route_id"], item["distance"], item["duration"],
                 item["carbon"], item["cost"],
                 "是" if item["is_exploration"] else "否",
                 "是" if item["is_recommended"] else "否",
                 item["exploration_strategy"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
