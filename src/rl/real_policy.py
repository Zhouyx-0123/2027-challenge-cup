# -*- coding: utf-8 -*-
"""真实 OD 上的 Q 表拟合：把历史 EF 与高德四策略变成 (state, action, reward)。

Q 的动作是四套策略，状态是「工况簇 × 时段」，奖励是归一化运营成本。
每个 OD 的四个候选方案都可离线枚举，所以这里做的是**全动作 Q 评估**
（contextual bandit 形式），不是逐段探索；这正好避免真实路网上的
随机探索把车开到绕路上，同时保留 Q 表“按工况选策略”的决策形态。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .agent import DEFAULT_OPERATION_WEIGHTS, ObjectiveNormalizer
from .amap_network import build_real_network
from .env import TripEnv, TripRequest
from .historical import (HistoricalProfile, ODOption, build_od_catalog,
                         build_profile)
from .policy import plan_route
from .real_world import (DEFAULT_REAL_POLICY_PATH, HistoricalCostModel,
                         _default_strategies, _manual_operation_cost, _reference)
from .settings import build_policy, load_config


def fit_real_policy(options: Sequence[ODOption], profile: HistoricalProfile,
                    *, config: Optional[Dict[str, Any]] = None,
                    epochs: int = 30, seed: int = 20261106,
                    fuel_price: float = 7.5,
                    carbon_price: float = 100.0) -> Tuple[Any, Dict[str, Any]]:
    """在真实 OD 目录上拟合 Q 表；返回 (policy, audit)。"""
    config = config if config is not None else load_config()
    strategy_list = _default_strategies(config)
    n_actions = len(strategy_list)
    records: List[Dict[str, Any]] = []
    feature_rows: List[np.ndarray] = []

    for option in options:
        departure = float(option.departure_minute)
        try:
            rn = build_real_network(option.polyline, (option.o_lng, option.o_lat),
                                    (option.d_lng, option.d_lat), option.candidates,
                                    departure_minute=departure)
        except ValueError:
            continue
        request = TripRequest("AMAP_ORIGIN", "AMAP_DEST", vclass=3,
                              load_t=float(option.load_t),
                              departure_minute=departure, order_id=option.od_key)
        model = HistoricalCostModel(request, profile, fuel_price=fuel_price,
                                    carbon_price=carbon_price)
        ref, normalizer = _reference(option, profile, float(option.load_t),
                                     fuel_price, carbon_price)
        env = TripEnv(rn.network, rn.timeline, request, cost_model=model,
                      max_steps=max(20, rn.n_segments * 2 + 5))
        features = env.state_features().astype(float)
        plans: List[Optional[Dict[str, Any]]] = []
        for strategy in strategy_list:
            plan = plan_route(rn.network, rn.timeline.at(departure),
                              "AMAP_ORIGIN", "AMAP_DEST", strategy, normalizer, model)
            if not plan.path:
                plans.append(None)
                continue
            vector = [float(plan.time_min), float(plan.carbon_kg), float(plan.cost_yuan)]
            op = _manual_operation_cost(vector, normalizer, DEFAULT_OPERATION_WEIGHTS)
            plans.append({
                "strategy": strategy.sid,
                "time_min": round(vector[0], 4),
                "carbon_kg": round(vector[1], 4),
                "cost_yuan": round(vector[2], 3),
                "operation_cost": round(float(op), 6),
            })
        records.append({"od_key": option.od_key, "features": features,
                        "departure": departure, "load_t": float(option.load_t),
                        "plans": plans})
        feature_rows.append(features)

    if not feature_rows:
        raise ValueError("没有可用于训练的真实 OD（全部建网失败）")

    policy = build_policy(network=None, config=config,
                          state_samples=np.vstack(feature_rows), seed=seed)
    if policy.q.n_actions != n_actions:
        raise RuntimeError("Q 动作数与策略数不一致：%d vs %d" %
                           (policy.q.n_actions, n_actions))

    for _ in range(max(1, int(epochs))):
        for record in records:
            _, sidx, _ = policy.encoder.encode(record["features"], 0.0,
                                               record["departure"])
            for action, item in enumerate(record["plans"]):
                if item is not None:
                    policy.q.update(sidx, action, -float(item["operation_cost"]),
                                    terminal=True)

    q_values = policy.q.Q
    visits = policy.q.visits
    state_visits = visits.sum(axis=1)
    active = np.flatnonzero(state_visits > 0)
    best_actions = np.argmax(q_values, axis=1)
    action_counts: Dict[str, int] = {s.sid: 0 for s in strategy_list}
    mean_q = np.zeros(n_actions, dtype=float)
    if len(active):
        for state in active:
            action_counts[strategy_list[int(best_actions[state])].sid] += 1
        mean_q = q_values[active].mean(axis=0)

    audit = {
        "grain": "real_od_strategy_bandit",
        "n_od": len(records),
        "n_od_input": len(options),
        "n_actions": n_actions,
        "n_epochs": int(epochs),
        "n_updates": int(len(records) * max(1, int(epochs)) * n_actions),
        "n_q_states": int(policy.q.n_states),
        "n_states_visited": int(len(active)),
        "n_clusters": int(policy.encoder.quantizer.k),
        "strategies": [s.to_dict() for s in strategy_list],
        "action_counts_best": action_counts,
        "mean_q_by_action": {s.sid: round(float(mean_q[i]), 6)
                             for i, s in enumerate(strategy_list)},
        "state_samples": int(len(records)),
        "max_visits": int(state_visits.max()) if len(state_visits) else 0,
        "note": "每个 OD 枚举四策略后按运营成本给 Q 奖励；非逐段随机探索。",
    }
    return policy, audit


def save_real_policy(policy: Any, audit: Dict[str, Any],
                     path: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    target = Path(path) if path else DEFAULT_REAL_POLICY_PATH
    payload = policy.to_dict()
    payload["real_data"] = audit
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    return str(target), payload


def main(argv: Optional[Sequence[str]] = None) -> int:
    """真实 OD 策略拟合 CLI：读四张缓存表，写出 config/rl_policy_real.json。"""
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="在真实高德 OD 上拟合 Q 策略")
    parser.add_argument("--master", default=str(root / "data/processed/jd_segment_master.parquet"))
    parser.add_argument("--baseline", default=str(root / "data/processed/baseline_carbon.parquet"))
    parser.add_argument("--route-od", default=str(root / "data/processed/route_od.parquet"))
    parser.add_argument("--route-candidates", default=str(root / "data/processed/route_candidates.parquet"))
    parser.add_argument("--out", default=str(DEFAULT_REAL_POLICY_PATH))
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20261106)
    parser.add_argument("--carbon-price", type=float, default=100.0)
    parser.add_argument("--fuel-price", type=float, default=7.5)
    args = parser.parse_args(argv)

    master = pd.read_parquet(args.master)
    baseline = pd.read_parquet(args.baseline)
    route_od = pd.read_parquet(args.route_od)
    candidates = pd.read_parquet(args.route_candidates)
    profile = build_profile(master)
    options, catalog_meta = build_od_catalog(master, baseline, route_od, candidates)
    policy, audit = fit_real_policy(
        options, profile, epochs=args.epochs, seed=args.seed,
        carbon_price=args.carbon_price, fuel_price=args.fuel_price)
    path, _payload = save_real_policy(policy, audit, args.out)
    print(json.dumps({
        "out": path, "catalog": catalog_meta, "audit": audit,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["fit_real_policy", "save_real_policy", "main"]
