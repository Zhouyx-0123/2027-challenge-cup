# -*- coding: utf-8 -*-
"""真实高德路网上的 Q-learning 求解：历史 EF + 四策略 + 策略兜底。

策略来自 config/rl_policy_real.json（真实 OD 离线拟合）或
outputs/rl/policy.json（合成网 900 单）。真实路网上的状态分布与合成网
不同，因此必须同时给出四策略枚举最优，并在 Q 置信度低或明显劣于枚举时回退。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .agent import DEFAULT_OPERATION_WEIGHTS, ObjectiveNormalizer, Strategy
from .amap_network import (DEST_NODE, ORIGIN_NODE, RealRouteNetwork,
                           build_real_network)
from .env import TripEnv, TripRequest
from .historical import DIESEL_KG_CO2_PER_L, HistoricalProfile, ODOption
from .network import SegmentState, TrafficSnapshot, congestion_label
from .policy import RoutePlan, plan_route
from .settings import (build_policy, load_config, operation_weights,
                       policy_from_dict, strategies as config_strategies)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = ROOT / "outputs" / "rl" / "policy.json"
DEFAULT_REAL_POLICY_PATH = ROOT / "config" / "rl_policy_real.json"


class HistoricalCostModel:
    """与 SegmentCostModel 同接口的历史 EF 成本模型（TTW 口径）。"""

    def __init__(self, request: TripRequest, profile: HistoricalProfile,
                 *, fuel_price: float = 7.5, carbon_price: float = 100.0,
                 corrector: Any = None):
        self.request = request
        self.profile = profile
        self.fuel_price = float(fuel_price)
        self.carbon_price = float(carbon_price)
        self.corrector = corrector

    def evaluate(self, segment: Any, snapshot: TrafficSnapshot,
                 power_kw: Optional[float] = None,
                 apply_correction: bool = True) -> Dict[str, Any]:
        state = snapshot.state(segment.sid)
        speed = max(float(state.speed_kmh), 1e-3)
        time_min = float(segment.length_km) / speed * 60.0
        cell = self.profile.cell(speed, self.request.load_t)
        ef = float(cell["ef_g_km"])
        carbon_kg = ef * float(segment.length_km) / 1000.0
        fuel_l = carbon_kg / DIESEL_KG_CO2_PER_L
        energy_cost = fuel_l * self.fuel_price
        carbon_cost = carbon_kg / 1000.0 * self.carbon_price
        toll = float(segment.toll_yuan)
        return {
            "sid": segment.sid, "time_min": time_min, "speed_kmh": speed,
            "congestion_index": state.congestion_index,
            "congestion_label": congestion_label(state.congestion_index),
            "incident": bool(state.incident), "road_class": segment.road_class,
            "length_km": float(segment.length_km), "grade_pct": float(segment.grade_pct),
            "power_kw": 0.0, "power_source": "historical_ef",
            "ef_g_per_km": ef, "k_factor": 1.0,
            "carbon_kg": carbon_kg, "fuel_l": fuel_l, "toll_yuan": toll,
            "energy_cost_yuan": energy_cost, "carbon_cost_yuan": carbon_cost,
            "cost_yuan": toll + energy_cost + carbon_cost,
            "correction_time": 1.0, "correction_carbon": 1.0,
            "ef_cell": cell["speed_bin"] + "/" + cell["load_bin"],
            "ef_source": cell["source"], "ef_low_sample": bool(cell["low_sample"]),
        }


def load_policy_payload(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """读取 Q 策略。

    默认优先本轮用真实 OD 训练出的 config/rl_policy_real.json；
    该文件不存在时才回退到合成网 900 单的 outputs/rl/policy.json。
    显式传入 path 时只读该路径，不做回退。
    """
    if path is not None:
        target = Path(path)
        if not target.exists():
            return None
        return json.loads(target.read_text(encoding="utf-8"))
    for target in (DEFAULT_REAL_POLICY_PATH, DEFAULT_POLICY_PATH):
        if target.exists():
            return json.loads(target.read_text(encoding="utf-8"))
    return None


def _default_strategies(config: Dict[str, Any]) -> List[Strategy]:
    rows = config_strategies(config)
    return rows or [
        Strategy("time", "时间优先", {"time": 1.0, "carbon": 0.2, "cost": 0.3}),
        Strategy("carbon", "低碳优先", {"time": 0.3, "carbon": 1.0, "cost": 0.25}),
        Strategy("balanced", "均衡", {"time": 0.55, "carbon": 0.55, "cost": 0.45}),
        Strategy("smooth", "抗堵稳健", {"time": 0.75, "carbon": 0.35, "cost": 0.55}),
    ]


def _manual_operation_cost(vector: Sequence[float], normalizer: ObjectiveNormalizer,
                           weights: Dict[str, float]) -> float:
    norm = normalizer.normalize([float(x) for x in vector])
    return float(sum(float(weights.get(k, DEFAULT_OPERATION_WEIGHTS[k])) * norm[i]
                     for i, k in enumerate(("time", "carbon", "cost"))))


def _reference(option: ODOption, profile: HistoricalProfile, load_t: float,
               fuel_price: float, carbon_price: float) -> Tuple[Dict[str, Any], ObjectiveNormalizer]:
    primary = next((c for c in option.candidates if c.strategy == 0), option.candidates[0])
    speed = primary.distance_km / (primary.duration_min / 60.0)
    ef = profile.ef_g_km(speed, load_t)
    carbon = ef * primary.distance_km / 1000.0
    fuel = carbon / DIESEL_KG_CO2_PER_L
    cost = fuel * fuel_price + carbon / 1000.0 * carbon_price + primary.toll_yuan
    ref = {
        "route_id": "AMAP-S%d" % primary.strategy,
        "strategy": primary.strategy,
        "strategy_name": primary.strategy_name,
        "time_min": float(primary.duration_min),
        "carbon_kg": float(carbon),
        "cost_yuan": float(cost),
        "distance_km": float(primary.distance_km),
        "toll_yuan": float(primary.toll_yuan),
        "ef_g_km": float(ef),
    }
    return ref, ObjectiveNormalizer(ref["time_min"], ref["carbon_kg"], ref["cost_yuan"])


def _segments(plan: RoutePlan, model: HistoricalCostModel, rn: RealRouteNetwork) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    from_km = 0.0
    for index, (sid, row) in enumerate(zip(plan.path, plan.rows)):
        length = float(row["length_km"])
        meta = rn.segment_meta.get(sid, {})
        out.append({
            "index": index,
            "sid": sid,
            "route_id": meta.get("route_id"),
            "from_km": round(from_km, 3),
            "to_km": round(from_km + length, 3),
            "length_km": round(length, 3),
            "speed_kmh": round(float(row["speed_kmh"]), 2),
            "time_min": round(float(row["time_min"]), 3),
            "carbon_kg": round(float(row["carbon_kg"]), 4),
            "carbon_g_per_km": round(float(row["carbon_kg"]) * 1000.0 / max(length, 1e-9), 2),
            "cost_yuan": round(float(row["cost_yuan"]), 3),
            "toll_yuan": round(float(row["toll_yuan"]), 3),
            "ef_g_per_km": round(float(row["ef_g_per_km"]), 2),
            "ef_cell": row.get("ef_cell"),
            "ef_source": row.get("ef_source"),
            "road_class": row.get("road_class"),
            "congestion_index": round(float(row.get("congestion_index", 0.0)), 4),
            "points": rn.segment_points.get(sid, []),
        })
        from_km += length
    return out


def _solution(strategy: Strategy, plan: RoutePlan, normalizer: ObjectiveNormalizer,
              model: HistoricalCostModel, rn: RealRouteNetwork,
              policy: Any = None) -> Optional[Dict[str, Any]]:
    if not plan.path:
        return None
    route_id = rn.route_of_path(plan.path)
    segments = _segments(plan, model, rn)
    time_min = float(plan.time_min)
    carbon_kg = float(plan.carbon_kg)
    cost_yuan = float(plan.cost_yuan)
    if policy is not None:
        op = float(policy.operation_cost([time_min, carbon_kg, cost_yuan], normalizer))
    else:
        op = _manual_operation_cost([time_min, carbon_kg, cost_yuan], normalizer,
                                    DEFAULT_OPERATION_WEIGHTS)
    return {
        "strategy": strategy.sid,
        "label": strategy.label,
        "description": strategy.description,
        "weights": {k: float(v) for k, v in strategy.weights.items()},
        "route_id": route_id,
        "route": rn.route(route_id).to_dict(),
        "time_min": round(time_min, 4),
        "carbon_kg": round(carbon_kg, 4),
        "cost_yuan": round(cost_yuan, 3),
        "operation_cost": round(op, 6),
        "distance_km": round(sum(s["length_km"] for s in segments), 3),
        "toll_yuan": round(sum(s["toll_yuan"] for s in segments), 3),
        "fuel_l": round(carbon_kg / DIESEL_KG_CO2_PER_L, 4),
        "avg_speed_kmh": round(sum(s["length_km"] for s in segments) /
                               max(time_min / 60.0, 1e-9), 3),
        "n_segments": len(segments),
        "segments": segments,
        "points": rn.points,
    }


def solve_real_route(option: ODOption, profile: HistoricalProfile,
                     *, load_t: Optional[float] = None,
                     departure_minute: Optional[float] = None,
                     carbon_price: float = 100.0, fuel_price: float = 7.5,
                     policy_payload: Optional[Dict[str, Any]] = None,
                     config: Optional[Dict[str, Any]] = None,
                     use_q: bool = True,
                     q_confidence_floor: float = 0.05,
                     q_gap_limit: float = 0.10) -> Dict[str, Any]:
    """对单个真实 OD 做四策略求解与 Q 策略推理。"""
    config = config if config is not None else load_config()
    departure = float(option.departure_minute if departure_minute is None else departure_minute)
    load = float(option.load_t if load_t is None else load_t)
    if not (0 < load <= 60):
        raise ValueError("载重必须在 (0, 60] t 内")
    load = round(load, 3)

    rn = build_real_network(option.polyline, (option.o_lng, option.o_lat),
                            (option.d_lng, option.d_lat), option.candidates,
                            departure_minute=departure)
    request = TripRequest(ORIGIN_NODE, DEST_NODE, vclass=3, load_t=load,
                          departure_minute=departure,
                          strategy="balanced", order_id=option.od_key)
    model = HistoricalCostModel(request, profile, fuel_price=fuel_price,
                                carbon_price=carbon_price)
    ref, normalizer = _reference(option, profile, load, fuel_price, carbon_price)
    env = TripEnv(rn.network, rn.timeline, request, cost_model=model,
                  max_steps=max(20, rn.n_segments * 2 + 5))

    policy = None
    q_payload = policy_payload if use_q else None
    if q_payload:
        policy = policy_from_dict(q_payload, config=config, network=rn.network)
    strategy_list = policy.strategies if policy is not None else _default_strategies(config)

    solutions: List[Dict[str, Any]] = []
    for strategy in strategy_list:
        plan = plan_route(rn.network, rn.timeline.at(departure), 
                          ORIGIN_NODE, DEST_NODE, strategy, normalizer, model)
        item = _solution(strategy, plan, normalizer, model, rn, policy)
        if item is not None:
            item["is_q"] = False
            item["is_best"] = False
            item["is_selected"] = False
            solutions.append(item)
    if not solutions:
        raise RuntimeError("四策略均无可行路径：%s" % option.od_key)
    best = min(solutions, key=lambda x: x["operation_cost"])

    q_info: Dict[str, Any] = {
        "available": bool(policy is not None),
        "used": False,
        "strategy": None, "label": None, "confidence": 0.0,
        "q_values": [], "state_index": None, "strategy_state": None,
        "features": [], "feature_names": ["上游拥堵", "下游平均拥堵", "载重t", "平均坡度%", "小时"],
        "q_labels": [s.label for s in strategy_list],
        "trained_on_real": bool(q_payload and q_payload.get("real_data")),
        "selected_route": None, "gap_vs_best_pct": None,
        "fallback": False, "reason": "未启用 Q 策略" if not policy else "",
        "note": ("Q 表在真实 OD 上离线拟合：动作=四策略，奖励=归一化运营成本；"
                 "每个 OD 四策略全枚举，不是逐段随机探索。")
                if q_payload and q_payload.get("real_data") else
                ("Q 表训练于合成 4x4 网；真实路网推理属分布外，必须与枚举最优并排看。"),
    }
    q_solution: Optional[Dict[str, Any]] = None
    if policy is not None:
        policy.start_episode()
        decision = policy.act(env, normalizer)
        q_strategy = decision["strategy"]
        q_plan = decision["plan"]
        q_item = _solution(Strategy(q_strategy["sid"], q_strategy["label"],
                                    dict(q_strategy.get("weights") or {}),
                                    q_strategy.get("description", "")),
                           q_plan, normalizer, model, rn, policy)
        if q_item is not None:
            q_item["is_q"] = True
            q_solution = q_item
            q_info.update({
                "used": True,
                "strategy": q_strategy["sid"],
                "label": q_strategy["label"],
                "confidence": float(decision.get("confidence") or 0.0),
                "q_values": [round(float(x), 6) for x in decision.get("q_values") or []],
                "state_index": int(decision.get("state_index") or 0),
                "strategy_state": int(decision.get("strategy_state") or 0),
                "features": [round(float(x), 4) for x in env.state_features().tolist()],
                "selected_route": q_item["route_id"],
                "gap_vs_best_pct": round((q_item["operation_cost"] /
                                          max(best["operation_cost"], 1e-9) - 1.0) * 100.0, 3),
            })

    selected = q_solution or best
    reason = "" if q_solution else "Q 未给出可行路径，采用枚举最优"
    if q_solution is not None:
        if q_info["confidence"] < q_confidence_floor:
            selected, reason = best, "Q 状态置信度 %.3f < %.2f，回退枚举最优" % (
                q_info["confidence"], q_confidence_floor)
        elif q_solution["operation_cost"] > best["operation_cost"] * (1.0 + q_gap_limit):
            selected, reason = best, "Q 选择比枚举最优贵 %.1f%% > %.0f%%，回退枚举最优" % (
                q_info["gap_vs_best_pct"], q_gap_limit * 100.0)
    q_info["fallback"] = bool(selected is best)
    q_info["reason"] = reason or ("Q 选择即枚举最优" if q_solution and selected is q_solution
                                  and best["route_id"] == q_solution["route_id"] else "Q 选择在容忍带内采用")
    q_info["selected_route"] = selected["route_id"]

    for item in solutions:
        item["is_selected"] = item["route_id"] == selected["route_id"] and item["strategy"] == selected["strategy"]
    if q_solution is not None and all(item["strategy"] != q_solution["strategy"] for item in solutions):
        solutions.append(q_solution)
    for item in solutions:
        item["is_best"] = item["strategy"] == best["strategy"]
        item["is_selected"] = item["strategy"] == selected["strategy"]

    map_route = {
        "route_id": selected["route_id"],
        "route_label": selected["label"],
        "polyline": selected["points"],
        "total_cost_yuan": selected["cost_yuan"],
        "carbon_kg": selected["carbon_kg"],
        "duration_min": selected["time_min"],
        "distance_km": selected["distance_km"],
        "tolls": selected["toll_yuan"],
        "avg_speed_kmh": selected["avg_speed_kmh"],
        "congestion_factor": 1.0,
    }
    origin_str = "%.6f,%.6f" % rn.origin_xy
    dest_str = "%.6f,%.6f" % rn.dest_xy
    return {
        "od": option.to_dict(),
        "request": {"load_t": load, "departure_minute": departure,
                    "hour": round((departure % 1440.0) / 60.0, 2),
                    "carbon_price": float(carbon_price), "fuel_price": float(fuel_price)},
        "network": rn.to_dict(),
        "profile": profile.to_dict(),
        "profile_cell": profile.cell(selected["avg_speed_kmh"], load),
        "reference": ref,
        "solutions": solutions,
        "q": q_info,
        "selected": selected,
        "best": best,
        "selected_is_q": bool(q_solution is not None and not q_info["fallback"]
                             and selected["strategy"] == q_solution["strategy"]),
        "map_route": map_route,
        "origin": origin_str, "destination": dest_str,
        "origin_label": option.origin, "destination_label": option.dest,
        "notes": [
            "几何 = 高德缓存主路线 polyline；四策略共享同一几何。",
            "四策略里程/时长/收费 = 高德 route_candidates 实算。",
            "碳 = 历史 5 档×4 载重带 EF × 里程，TTW 2.63 kgCO2/L，与主账 E0 同口径。",
            "演示通道，不写 data/processed，不进 E0/28 闸门。",
        ],
    }


__all__ = ["HistoricalCostModel", "load_policy_payload", "solve_real_route",
           "DEFAULT_POLICY_PATH", "DEFAULT_REAL_POLICY_PATH"]
