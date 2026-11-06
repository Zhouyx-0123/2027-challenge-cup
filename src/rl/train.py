# -*- coding: utf-8 -*-
"""离线训练 / 评估 CLI：``python -m src.rl.train``。

产出（全部落在 ``outputs/rl/``，已被 .gitignore 挡住）
--------------------------------------------------
- ``policy.json``：训练好的策略（量化器 + TD 值函数 + Q 表），可直接被前端/服务加载
- ``report.json``：与各基线的对比、ETA 误差、碳误差、学习曲线
- ``trips.csv``：每条行程的逐段明细（答辩时"中间数据"的原始出处）

基线为什么是这几个
------------------
- **时间最短**（静态）：高德默认策略，最容易被质疑"你凭什么更好"；
- **低碳最短**（静态）：把权重全压到碳上的贪心；
- **专家规则**：高峰用抗堵、平峰用均衡（行业常见的人工规则）；
- **随机**：验证"不是路网本身好，是策略在选"；
- **事后最优（hindsight）**：知道未来 24 小时交通的时刻表，算真正的下界。
  它与学习策略的差距才是"还能改进多少"的诚实答案。
"""
from __future__ import annotations

import argparse
import csv
import heapq
import json
import math
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from . import settings
from .agent import (DEFAULT_STRATEGIES, ObjectiveNormalizer, ResidualCorrector, Strategy)
from .env import SegmentCostModel, TripEnv, TripRequest
from .network import (RoadNetwork, TrafficSnapshot, TrafficTimeline, build_demo_network,
                      dijkstra)
from .policy import RoutePlan, RoutePolicy, plan_route
from .quota import FleetQuota, weights_for_pressure
from .settings import (build_policy, economics, k_table, load_config, policy_from_dict,
                       reference_normalizer, strategies, traffic_config)
from .network import TrafficSimulator


# ---------------------------------------------------------------- 场景构造
def demo_requests(config: Optional[Dict[str, Any]] = None) -> List[TripRequest]:
    """按配置生成演示/训练用的一批运单（出发时刻 × 载重）。"""
    config = config or load_config()
    block = config.get("scenarios") or {}
    origins = block.get("origins") or ["G00"]
    destinations = block.get("destinations") or ["G33"]
    departures = block.get("departure_minutes") or [480]
    loads = block.get("loads_t") or [18.0]
    vclass = int(block.get("vclass", 3))
    requests: List[TripRequest] = []
    for index, minute in enumerate(departures):
        origin = origins[index % len(origins)]
        destination = destinations[index % len(destinations)]
        for load in loads:
            requests.append(TripRequest(origin=origin, destination=destination,
                                        vclass=vclass, load_t=float(load),
                                        departure_minute=float(minute),
                                        order_id="RL-%03d" % (len(requests) + 1)))
    return requests


def build_timeline(network: RoadNetwork, config: Optional[Dict[str, Any]] = None,
                   overrides: Optional[Dict[str, Any]] = None,
                   horizon_min: int = 1440) -> TrafficTimeline:
    cfg = traffic_config(config, overrides)
    return TrafficSimulator(network, cfg).simulate(0.0, horizon_min)


# ---------------------------------------------------------------- 状态采样
def collect_state_samples(network: RoadNetwork, timeline: TrafficTimeline,
                          config: Optional[Dict[str, Any]] = None,
                          episodes: int = 30, seed: int = 20261103) -> np.ndarray:
    """随机游走采样真实状态特征，给 k-means 拟合用（比均匀覆盖更贴分布）。"""
    rng = np.random.default_rng(int(seed))
    requests = demo_requests(config)
    rows: List[np.ndarray] = []
    for index in range(int(episodes)):
        request = requests[index % len(requests)]
        env = TripEnv(network, timeline, request)
        while not env.done:
            rows.append(env.state_features())
            candidates = env.candidates()
            if not candidates:
                break
            env.step(candidates[int(rng.integers(0, len(candidates)))])
    if not rows:
        raise RuntimeError("采样失败：没有任何状态")
    return np.vstack(rows)


# ---------------------------------------------------------------- 单集执行
def run_episode(network: RoadNetwork, timeline: TrafficTimeline,
                request: TripRequest, *,
                policy: Optional[RoutePolicy] = None,
                config: Optional[Dict[str, Any]] = None,
                learn: bool = False, explore: bool = False,
                forced_strategy: Optional[str] = None,
                decide_fn: Optional[Callable[[TripEnv], str]] = None,
                rng: Optional[np.random.Generator] = None,
                time_bias: float = 1.0,
                carbon_bias: float = 1.0,
                bias_table: Optional[Dict[str, Any]] = None,
                pressure: float = 0.0,
                weights: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """跑完一次行程；``decide_fn`` 用于基线，``policy`` 用于学习/推荐。"""
    config = config or {}
    table, bins = k_table(config)
    econ = economics(config)
    env = TripEnv(network, timeline, request, k_table=table, load_bins=bins,
                  fuel_price=econ["fuel_price"], carbon_price=econ["carbon_price"],
                  time_bias=time_bias, carbon_bias=carbon_bias,
                  bias_table=bias_table)
    ref_strategy = next((s for s in strategies(config) if s.sid == "balanced"),
                        DEFAULT_STRATEGIES[2])
    normalizer = reference_normalizer(env, ref_strategy, config)
    if policy is not None:
        policy.start_episode()
        if weights:
            # 当前这一单的"裁判口径"：配额紧时碳权重会被抬高
            policy.operation_weights = dict(weights)

    steps: List[Dict[str, Any]] = []
    eta_pairs: List[Tuple[float, float, float, float]] = []   # (eta, p90, model, elapsed)
    replans = 0
    previous_path: Optional[List[str]] = None
    strategy_counter: Counter = Counter()
    td_errors: List[float] = []

    while not env.done:
        if decide_fn is not None:
            sid = decide_fn(env)
            outcome = env.step(sid)
            steps.append(outcome.to_dict())
            continue
        if policy is None:
            raise ValueError("policy 与 decide_fn 必须给一个")
        decision = policy.act(env, normalizer, explore=explore,
                              forced_strategy=forced_strategy, pressure=pressure)
        plan: RoutePlan = decision["plan"]
        eta = policy.predict_eta(env, plan)
        eta_pairs.append((eta["eta_min"], eta["eta_p90_min"],
                          eta["model_time_min"], env.total_time_min))
        if not decision.get("kept_plan", False):
            replans += 1
        strategy_counter[decision["strategy"]["sid"]] += 1
        outcome = env.step(decision["next_segment"])
        if learn:
            info = policy.observe(env, decision, outcome, normalizer)
            td_errors.append(abs(info["td_error"][0]))
        steps.append(outcome.to_dict())

    total_time = env.total_time_min
    total_carbon = env.total_carbon_kg
    total_cost = env.total_cost_yuan
    eta_abs = [abs(eta - (total_time - elapsed)) for eta, _, _, elapsed in eta_pairs]
    model_abs = [abs(model - (total_time - elapsed))
                 for _, _, model, elapsed in eta_pairs]
    p90_hits = [1.0 if (total_time - elapsed) <= p90 else 0.0
                for _, p90, _, elapsed in eta_pairs]
    op_weights = dict(weights) if weights else settings.operation_weights(config)
    operation = float(sum(op_weights.get(k, 0.0) * v
                          for k, v in zip(("time", "carbon", "cost"),
                                          normalizer.normalize(
                                              [total_time, total_carbon, total_cost]))))
    return {
        "order_id": request.order_id,
        "request": request.to_dict(),
        "time_min": round(total_time, 3),
        "carbon_kg": round(total_carbon, 5),
        "cost_yuan": round(total_cost, 3),
        "fuel_l": round(sum(s["fuel_l"] for s in steps), 4),
        "distance_km": round(sum(s["length_km"] for s in steps), 4),
        "segments": len(steps),
        "operation_cost": round(operation, 6),
        "replans": replans,
        "strategy_counts": dict(strategy_counter),
        "eta_mae_min": round(float(np.mean(eta_abs)), 3) if eta_abs else None,
        "model_eta_mae_min": round(float(np.mean(model_abs)), 3) if model_abs else None,
        "eta_p90_coverage": round(float(np.mean(p90_hits)), 3) if p90_hits else None,
        "mean_td_error": round(float(np.mean(td_errors)), 4) if td_errors else None,
        "path": list(env.path),
        "steps": steps,
    }


# ---------------------------------------------------------------- 基线
def static_decide(network: RoadNetwork, strategy: Strategy,
                  normalizer: ObjectiveNormalizer, cost_model: SegmentCostModel,
                  blocked: Optional[Iterable[str]] = None
                  ) -> Callable[[TripEnv], str]:
    def decide(env: TripEnv) -> str:
        plan = plan_route(env.network, env.snapshot(), env.node,
                          env.request.destination, strategy, normalizer,
                          env.cost_model, env.blocked)
        if not plan.path:
            raise RuntimeError("基线不可达")
        return plan.path[0]
    return decide


def random_decide(rng: np.random.Generator) -> Callable[[TripEnv], str]:
    def decide(env: TripEnv) -> str:
        candidates = env.candidates()
        if not candidates:
            raise RuntimeError("无路可走")
        return candidates[int(rng.integers(0, len(candidates)))]
    return decide


def expert_decide(strategy_list: Sequence[Strategy], normalizer: ObjectiveNormalizer
                  ) -> Callable[[TripEnv], str]:
    """人工规则：拥堵指数 > 0.45 用抗堵，否则均衡。"""
    by_id = {s.sid: s for s in strategy_list}
    anti = by_id.get("smooth", strategy_list[-1])
    normal = by_id.get("balanced", strategy_list[0])

    def decide(env: TripEnv) -> str:
        observation = env.observation()
        strategy = anti if observation["mean_congestion"] > 0.45 else normal
        plan = plan_route(env.network, env.snapshot(), env.node,
                          env.request.destination, strategy, normalizer,
                          env.cost_model, env.blocked)
        if not plan.path:
            raise RuntimeError("专家规则不可达")
        return plan.path[0]
    return decide


def hindsight_path(network: RoadNetwork, timeline: TrafficTimeline,
                   request: TripRequest, strategy: Strategy,
                   normalizer: ObjectiveNormalizer,
                   cost_model: SegmentCostModel) -> List[str]:
    """事后最优：已知未来交通时刻表的时间依赖最短路（= 该场景下的下界）。"""
    start = float(request.departure_minute)
    best: Dict[str, float] = {request.origin: 0.0}
    arrival: Dict[str, float] = {request.origin: start}
    previous: Dict[str, Tuple[str, str]] = {}
    heap: List[Tuple[float, str]] = [(0.0, request.origin)]
    while heap:
        cost, node = heapq.heappop(heap)
        if cost > best.get(node, float("inf")) + 1e-12:
            continue
        if node == request.destination:
            break
        for sid in network.out_edges(node):
            segment = network.segment(sid)
            snapshot = timeline.at(arrival.get(node, start))
            row = cost_model.evaluate(segment, snapshot)
            step = strategy.scalarize(normalizer.normalize(
                [row["time_min"], row["carbon_kg"], row["cost_yuan"]]))
            candidate = cost + step
            if candidate < best.get(segment.v, float("inf")) - 1e-12:
                best[segment.v] = candidate
                arrival[segment.v] = arrival.get(node, start) + row["time_min"]
                previous[segment.v] = (node, sid)
                heapq.heappush(heap, (candidate, segment.v))
    if request.destination not in best:
        return []
    path: List[str] = []
    node = request.destination
    while node != request.origin:
        prior, sid = previous[node]
        path.append(sid)
        node = prior
    path.reverse()
    return path


def hindsight_decide(path: Sequence[str]) -> Callable[[TripEnv], str]:
    state = {"index": 0}

    def decide(env: TripEnv) -> str:
        if state["index"] >= len(path):
            candidates = env.candidates()
            if not candidates:
                raise RuntimeError("事后最优路径已走完但未达终点")
            return candidates[0]
        sid = path[state["index"]]
        state["index"] += 1
        return sid
    return decide


# ---------------------------------------------------------------- 评估
def _aggregate(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {}
    def mean(key: str) -> Optional[float]:
        values = [r[key] for r in rows if r.get(key) is not None]
        return round(float(np.mean(values)), 4) if values else None
    return {
        "episodes": len(rows),
        "time_min": mean("time_min"), "carbon_kg": mean("carbon_kg"),
        "cost_yuan": mean("cost_yuan"), "fuel_l": mean("fuel_l"),
        "distance_km": mean("distance_km"), "operation_cost": mean("operation_cost"),
        "replans": mean("replans"), "eta_mae_min": mean("eta_mae_min"),
        "model_eta_mae_min": mean("model_eta_mae_min"),
        "eta_p90_coverage": mean("eta_p90_coverage"),
    }


def evaluate(network: RoadNetwork, timeline: TrafficTimeline,
             requests: Sequence[TripRequest], policy: RoutePolicy,
             config: Optional[Dict[str, Any]] = None,
             include_hindsight: bool = True,
             time_bias: float = 1.0,
             carbon_bias: float = 1.0) -> Dict[str, Any]:
    config = config or load_config()
    strategy_list = strategies(config)
    by_id = {s.sid: s for s in strategy_list}
    balanced = by_id.get("balanced", strategy_list[0])
    econ = economics(config)
    table, bins = k_table(config)
    bias_table = settings.model_bias_table(config)

    def episode_results(decide_factory: Callable[[TripRequest], Callable[[TripEnv], str]]
                        ) -> List[Dict[str, Any]]:
        rows = []
        for request in requests:
            rows.append(run_episode(network, timeline, request, config=config,
                                    decide_fn=decide_factory(request),
                                    bias_table=bias_table))
        return rows

    # 学习策略（利用模式，不探索）
    learned: List[Dict[str, Any]] = []
    for request in requests:
        learned.append(run_episode(network, timeline, request, policy=policy,
                                   config=config, learn=False, explore=False,
                                   time_bias=time_bias, carbon_bias=carbon_bias,
                                   bias_table=bias_table))

    def ref_normalizer(request: TripRequest) -> ObjectiveNormalizer:
        env = TripEnv(network, timeline, request, k_table=table, load_bins=bins,
                      fuel_price=econ["fuel_price"], carbon_price=econ["carbon_price"])
        return reference_normalizer(env, balanced, config)

    def static_factory(strategy_id: str):
        def factory(request: TripRequest):
            env = TripEnv(network, timeline, request, k_table=table, load_bins=bins,
                          fuel_price=econ["fuel_price"],
                          carbon_price=econ["carbon_price"])
            normalizer = reference_normalizer(env, by_id[strategy_id], config)
            return static_decide(network, by_id[strategy_id], normalizer, env.cost_model)
        return factory

    baselines = {
        "static_time": episode_results(static_factory("time")),
        "static_carbon": episode_results(static_factory("carbon")),
        "expert_rule": episode_results(
            lambda request: expert_decide(strategy_list, ref_normalizer(request))),
        "random": episode_results(
            lambda request: random_decide(np.random.default_rng(
                abs(hash(request.order_id)) % (2 ** 32)))),
    }

    # 事后最优必须最小化**运营口径**（Q 的裁判标准），不是均衡权重，
    # 否则它只是"另一条均衡路线"，不能当下界用。
    operation_strategy = Strategy("operation", "运营口径",
                                  settings.operation_weights(config))
    hindsight: List[Dict[str, Any]] = []
    if include_hindsight:
        for request in requests:
            env = TripEnv(network, timeline, request, k_table=table, load_bins=bins,
                          fuel_price=econ["fuel_price"],
                          carbon_price=econ["carbon_price"])
            normalizer = reference_normalizer(env, balanced, config)
            # 事后最优必须知道"真实偏差"，否则它也只是另一条模型路线
            env.cost_model.corrector = ResidualCorrector.from_bias_table(bias_table)
            path = hindsight_path(network, timeline, request, operation_strategy,
                                  normalizer, env.cost_model)
            if not path:
                continue
            hindsight.append(run_episode(network, timeline, request, config=config,
                                         decide_fn=hindsight_decide(path),
                                         bias_table=bias_table))

    summary = {"learned": _aggregate(learned)}
    for name, rows in baselines.items():
        summary[name] = _aggregate(rows)
    if hindsight:
        summary["hindsight"] = _aggregate(hindsight)

    base_time = summary["static_time"].get("time_min")
    base_carbon = summary["static_time"].get("carbon_kg")
    base_cost = summary["static_time"].get("cost_yuan")
    delta = {}
    if base_time:
        delta["time_pct_vs_fastest"] = round(
            (summary["learned"]["time_min"] - base_time) / base_time * 100.0, 2)
    if base_carbon:
        delta["carbon_pct_vs_fastest"] = round(
            (summary["learned"]["carbon_kg"] - base_carbon) / base_carbon * 100.0, 2)
    if base_cost:
        delta["cost_pct_vs_fastest"] = round(
            (summary["learned"]["cost_yuan"] - base_cost) / base_cost * 100.0, 2)
    if hindsight:
        gap_time = (summary["learned"]["time_min"]
                    - summary["hindsight"]["time_min"])
        gap_carbon = (summary["learned"]["carbon_kg"]
                      - summary["hindsight"]["carbon_kg"])
        delta["time_gap_to_hindsight_min"] = round(float(gap_time), 3)
        delta["carbon_gap_to_hindsight_kg"] = round(float(gap_carbon), 4)
    return {"summary": summary, "delta": delta, "episodes": learned,
            "baseline_episodes": baselines}


# ---------------------------------------------------------------- 日级评估（配额）
def run_day(network: RoadNetwork, timeline: TrafficTimeline,
            requests: Sequence[TripRequest], config: Optional[Dict[str, Any]] = None,
            *, budget_kg: float, policy: Optional[RoutePolicy] = None,
            decide_factory: Optional[Callable[[TripRequest], Callable[[TripEnv], str]]] = None
            ) -> Tuple[List[Dict[str, Any]], FleetQuota]:
    """按配额递减跑完一天：每单开始时的压力决定这一单的"裁判口径"。"""
    config = config or {}
    base = settings.operation_weights(config)
    quota = FleetQuota(budget_kg=budget_kg, trips_total=len(requests),
                       base_weights=base)
    rows: List[Dict[str, Any]] = []
    for request in requests:
        weights = quota.weights()
        pressure = quota.pressure()
        before = quota.state()
        result = run_episode(
            network, timeline, request, config=config, policy=policy,
            decide_fn=decide_factory(request) if decide_factory else None,
            bias_table=settings.model_bias_table(config),
            pressure=pressure, weights=weights)
        result["quota_before"] = before
        result["quota_weights"] = {k: round(v, 4) for k, v in weights.items()}
        quota.consume(result["carbon_kg"])
        result["quota_after"] = quota.state()
        rows.append(result)
    return rows, quota


def evaluate_day(network: RoadNetwork, timeline: TrafficTimeline,
                 requests: Sequence[TripRequest], policy: RoutePolicy,
                 config: Optional[Dict[str, Any]] = None,
                 budget_kg: float = 60.0) -> Dict[str, Any]:
    """学习策略 vs 固定权重基线：同一天、同一预算、各自消耗。

    注意：U 用**该策略自己那一刻的压力权重**算。这不是偷换口径——配额是共享资源，
    排得少的那条策略本来就不该被同一把尺子追着罚。
    """
    config = config or load_config()
    strategy_list = strategies(config)
    by_id = {s.sid: s for s in strategy_list}
    balanced = by_id.get("balanced", strategy_list[0])
    econ = economics(config)
    table, bins = k_table(config)

    learned, quota = run_day(network, timeline, requests, config, budget_kg=budget_kg,
                             policy=policy)

    def static_factory(strategy_id: str):
        def factory(request: TripRequest):
            env = TripEnv(network, timeline, request, k_table=table, load_bins=bins,
                          fuel_price=econ["fuel_price"],
                          carbon_price=econ["carbon_price"])
            normalizer = reference_normalizer(env, by_id[strategy_id], config)
            return static_decide(network, by_id[strategy_id], normalizer, env.cost_model)
        return factory

    static_rows, static_quota = run_day(network, timeline, requests, config,
                                        budget_kg=budget_kg,
                                        decide_factory=static_factory("time"))
    carbon_rows, carbon_quota = run_day(network, timeline, requests, config,
                                        budget_kg=budget_kg,
                                        decide_factory=static_factory("carbon"))

    def summary(rows: List[Dict[str, Any]], quota_state: FleetQuota) -> Dict[str, Any]:
        return {"episodes": len(rows),
                "time_min": round(float(np.mean([r["time_min"] for r in rows])), 4),
                "carbon_kg": round(float(np.sum([r["carbon_kg"] for r in rows])), 4),
                "cost_yuan": round(float(np.sum([r["cost_yuan"] for r in rows])), 4),
                "operation_cost": round(float(np.sum([r["operation_cost"] for r in rows])), 4),
                "max_pressure": round(max(r["quota_before"]["pressure"] for r in rows), 4),
                "quota": quota_state.state()}

    result = {"budget_kg": budget_kg, "trips": len(requests)}
    result["learned"] = summary(learned, quota)
    result["static_time"] = summary(static_rows, static_quota)
    result["static_carbon"] = summary(carbon_rows, carbon_quota)
    best_static = min(result["static_time"]["operation_cost"],
                      result["static_carbon"]["operation_cost"])
    result["delta"] = {
        "u_vs_best_static_pct": round(
            (result["learned"]["operation_cost"] - best_static) / max(best_static, 1e-9) * 100.0, 2),
        "carbon_saved_vs_time_kg": round(
            result["static_time"]["carbon_kg"] - result["learned"]["carbon_kg"], 4)}
    result["episodes"] = learned
    return result


# ---------------------------------------------------------------- 训练
def train(network: RoadNetwork, timeline: TrafficTimeline,
          config: Optional[Dict[str, Any]] = None, episodes: int = 300,
          seed: int = 20261103, verbose: bool = True,
          time_bias: float = 1.0, carbon_bias: float = 1.0
          ) -> Tuple[RoutePolicy, List[Dict[str, Any]]]:
    config = config or load_config()
    samples = collect_state_samples(network, timeline, config, episodes=24, seed=seed)
    policy = build_policy(network, config, state_samples=samples, seed=seed)
    requests = demo_requests(config)
    rng = np.random.default_rng(seed)
    curve: List[Dict[str, Any]] = []
    quota_cfg = config.get("quota") or {}
    curriculum = None
    if bool(quota_cfg.get("enabled", False)):
        # 域随机化：把"压力"当成训练环境的一部分，四档轮转，
        # 否则模型只见过"配额紧"这一种世界，到了宽松日就会过度省碳。
        curriculum = [float(x) for x in quota_cfg.get(
            "pressure_curriculum", [0.90, 1.05, 1.25, 1.60])]
    base_weights = settings.operation_weights(config)
    started = time.time()
    for episode in range(int(episodes)):
        request = requests[episode % len(requests)]
        result = run_episode(network, timeline, request, policy=policy, config=config,
                             learn=True, explore=True, rng=rng,
                             time_bias=time_bias, carbon_bias=carbon_bias,
                             bias_table=settings.model_bias_table(config),
                             pressure=curriculum[episode % len(curriculum)] if curriculum else 0.0,
                             weights=weights_for_pressure(
                                 curriculum[episode % len(curriculum)], base_weights)
                             if curriculum else None)
        policy.q.decay_epsilon()
        if (episode + 1) % max(1, episodes // 20) == 0 or episode == 0:
            curve.append({"episode": episode + 1,
                          "operation_cost": result["operation_cost"],
                          "time_min": result["time_min"],
                          "carbon_kg": result["carbon_kg"],
                          "epsilon": round(policy.q.epsilon, 4)})
            if verbose:
                print("[%4d/%d] U=%.4f  time=%.2f min  carbon=%.3f kg  eps=%.3f"
                      % (episode + 1, episodes, result["operation_cost"],
                         result["time_min"], result["carbon_kg"], policy.q.epsilon))
    if verbose:
        print("训练完成，用时 %.1f s" % (time.time() - started))
    return policy, curve


def save_policy(policy: RoutePolicy, path: Path, meta: Optional[Dict[str, Any]] = None) -> Path:
    payload = policy.to_dict()
    payload["version"] = "rl-policy-state-v1"
    payload["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    if meta:
        payload["meta"] = meta
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def load_policy(path: Path, config: Optional[Dict[str, Any]] = None,
                network: Optional[RoadNetwork] = None) -> RoutePolicy:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return policy_from_dict(payload, config, network)


def write_trips_csv(rows: Sequence[Dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["order_id", "sid", "start", "end", "minute", "time_min", "speed_kmh",
               "congestion_label", "road_class", "length_km", "power_kw", "ef_g_per_km",
               "k_factor", "carbon_kg", "fuel_l", "cost_yuan"]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            for step in row["steps"]:
                writer.writerow({key: step.get(key) for key in columns})
    return path


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Q-learning 动态路径策略：训练 + 评估")
    parser.add_argument("--episodes", type=int, default=300, help="训练回合数")
    parser.add_argument("--seed", type=int, default=20261103)
    parser.add_argument("--config", default=None, help="rl_policy.json 路径")
    parser.add_argument("--out", default=None, help="输出目录（默认 outputs/rl）")
    parser.add_argument("--eval-only", default=None, help="只评估已落盘的 policy.json")
    parser.add_argument("--weather", default=None, help="覆盖天气：clear/rain/fog/snow")
    parser.add_argument("--bias-time", type=float, default=1.0,
                        help="注入时间偏差（sim-to-real 验证用，如 1.12 表示实际慢 12%%）")
    parser.add_argument("--bias-carbon", type=float, default=1.0,
                        help="注入碳排偏差（如 1.15 表示实际高 15%%）")
    parser.add_argument("--no-correction", action="store_true",
                        help="关掉模型残差修正层（A/B 对照：纯模型 ETA）")
    parser.add_argument("--no-forecast", action="store_true",
                        help="关掉 15 分钟预报（A/B 对照：只靠模型+TD）")
    parser.add_argument("--quota-budget", type=float, default=0.0,
                        help="启用配额模式：给出周期碳预算(kg)，跑日级评估")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if args.quota_budget:
        config = json.loads(json.dumps(config))
        config.setdefault("quota", {})["enabled"] = True
        config["quota"]["budget_kg"] = float(args.quota_budget)
        config["quota"]["pressure_buckets"] = 4
    if args.no_forecast:
        config = json.loads(json.dumps(config))
        config.setdefault("forecast", {})["enabled"] = False
    if args.no_correction:
        config = json.loads(json.dumps(config))
        config.setdefault("correction", {})["enabled"] = False
    out_dir = Path(args.out) if args.out else settings.ROOT / "outputs" / "rl"
    network = build_demo_network()
    timeline = build_timeline(network, config, overrides=(
        {"weather": args.weather} if args.weather else None))
    requests = demo_requests(config)

    if args.eval_only:
        policy = load_policy(Path(args.eval_only), config)
        curve: List[Dict[str, Any]] = []
    else:
        policy, curve = train(network, timeline, config, episodes=args.episodes,
                              seed=args.seed, verbose=True,
                              time_bias=args.bias_time,
                              carbon_bias=args.bias_carbon)

    report = evaluate(network, timeline, requests, policy, config,
                      time_bias=args.bias_time, carbon_bias=args.bias_carbon)
    report["config"] = {"version": config.get("version"), "episodes": args.episodes,
                        "seed": args.seed}
    report["learning_curve"] = curve
    if args.quota_budget:
        out_dir.mkdir(parents=True, exist_ok=True)
        day = evaluate_day(network, timeline, requests, policy, config,
                           budget_kg=float(args.quota_budget))
        (out_dir / "day_report.json").write_text(
            json.dumps(day, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n=== 日级配额评估（预算 %.1f kg / %d 单）===" % (
            day["budget_kg"], day["trips"]))
        for name in ("learned", "static_time", "static_carbon"):
            row = day[name]
            print("%-13s 碳 %7.2f kg ｜ 费用 %8.2f ｜ U %7.3f ｜ 峰值压力 %.2f" % (
                name, row["carbon_kg"], row["cost_yuan"], row["operation_cost"],
                row["max_pressure"]))
        print("相对最强静态基线：U %+.2f%% ｜ 较'时间优先'省碳 %.2f kg" % (
            day["delta"]["u_vs_best_static_pct"],
            day["delta"]["carbon_saved_vs_time_kg"]))

    out_dir.mkdir(parents=True, exist_ok=True)
    save_policy(policy, out_dir / "policy.json",
                meta={"config_version": config.get("version"),
                      "episodes": args.episodes, "seed": args.seed})
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_trips_csv(report["episodes"], out_dir / "trips.csv")

    summary = report["summary"]
    print("\n=== 评估（%d 条运单）===" % len(requests))
    header = "%-14s %8s %8s %8s %8s %8s"
    print(header % ("策略", "时间min", "碳kg", "费用元", "油耗L", "ETA_MAE"))
    for name, row in summary.items():
        print(header % (name, row.get("time_min"), row.get("carbon_kg"),
                        row.get("cost_yuan"), row.get("fuel_l"), row.get("eta_mae_min")))
    print("\n相对最快基线的偏差：", json.dumps(report["delta"], ensure_ascii=False))
    print("已写入：%s" % out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
