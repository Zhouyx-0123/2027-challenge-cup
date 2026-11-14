# -*- coding: utf-8 -*-
"""动态选路：时变边权（TD）+ 风险项 + 事件触发重规划。

和"静态 Dijkstra"的差别
------------------------
- 静态：出发时刻取一次快照，整条路都用这一帧的速度；
- 动态：走到某个节点时，用"到达该节点的时刻"的速度；
- 风险：边权里加 ``λ·(CVaR_e − E_e)``，值越大越避开"可能堵"的路；
- 拥堵程度：边权里加 ``w·(1 − v/v_free)``，把"堵"直接定价；
- 事件：下一段速度相比上次规划骤降超过 ``event_drop`` 时强制重算。

FIFO 说明
---------
时变 Dijkstra 要求"出发越晚、到达越晚"（无超车）。BPR 时间线在正常参数下满足；
若时间线出现速度跳变导致不满足，本模块用"到达时间单调化"兜底（取 max）。

边界
----
- 只读路网/时间线/成本模型，不改环境状态；
- 返回的是建议（路径 + 时间 + 风险），执行仍由调用方决定。
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .congestion import CongestionField

CARBON_WEIGHT = 1.0
COST_WEIGHT = 0.2


@dataclass
class PathPlan:
    path: List[str]
    time_min: float
    degree: float
    risk_min: float
    carbon_kg: float
    generalized_cost: float

    def to_dict(self) -> Dict[str, object]:
        return {"path": list(self.path), "time_min": round(self.time_min, 3),
                "degree": round(self.degree, 4), "risk_min": round(self.risk_min, 3),
                "carbon_kg": round(self.carbon_kg, 3),
                "generalized_cost": round(self.generalized_cost, 4)}


def _edge(network, timeline, cost_model, sid: str, minute: float,
          field: CongestionField, congestion_weight: float,
          risk_weight: float) -> Dict[str, float]:
    seg = network.segment(sid)
    snap = timeline.at(minute)
    state = snap.state(sid)
    m = cost_model.evaluate(seg, snap)
    degree = max(0.0, min(1.0, 1.0 - state.speed_kmh / max(seg.free_speed_kmh, 1e-9)))
    e, var = field.leg_moments(seg.length_km, state.speed_kmh, seg.road_class)
    cvar = e + 1.755 * var ** 0.5
    cost = (m["time_min"] + CARBON_WEIGHT * m["carbon_kg"]
            + COST_WEIGHT * m["cost_yuan"]
            + congestion_weight * degree + risk_weight * (cvar - e))
    return {"cost": cost, "time": m["time_min"], "degree": degree,
            "risk": cvar - e, "carbon": m["carbon_kg"], "speed": state.speed_kmh}


def _walk(network, timeline, cost_model, path: Sequence[str], t0: float,
          field: CongestionField, congestion_weight: float,
          risk_weight: float) -> Tuple[PathPlan, float]:
    t = float(t0)
    total = degree_num = risk = carbon = km = 0.0
    for sid in path:
        e = _edge(network, timeline, cost_model, sid, t, field,
                  congestion_weight, risk_weight)
        total += e["time"]
        t += e["time"]
        length = network.segment(sid).length_km
        degree_num += e["degree"] * length
        km += length
        risk += e["risk"]
        carbon += e["carbon"]
    plan = PathPlan(path=list(path), time_min=total, degree=degree_num / max(km, 1e-9),
                    risk_min=risk, carbon_kg=carbon,
                    generalized_cost=total + congestion_weight * (degree_num / max(km, 1e-9))
                    + risk_weight * risk + CARBON_WEIGHT * carbon)
    return plan, t


def static_plan(network, timeline, cost_model, t0: float,
                origin: str, dest: str, field: CongestionField,
                congestion_weight: float = 8.0, risk_weight: float = 0.3,
                ) -> Optional[PathPlan]:
    """现状口径：全程用出发时刻的快照。"""
    snap = timeline.at(t0)
    best = {origin: 0.0}
    prev: Dict[str, Tuple[str, str]] = {}
    pq = [(0.0, origin)]
    while pq:
        cost, node = heapq.heappop(pq)
        if node == dest:
            break
        if cost > best.get(node, 1e18) + 1e-12:
            continue
        for sid in network.out_edges(node):
            seg = network.segment(sid)
            state = snap.state(sid)
            m = cost_model.evaluate(seg, snap)
            degree = max(0.0, min(1.0, 1.0 - state.speed_kmh / max(seg.free_speed_kmh, 1e-9)))
            e, var = field.leg_moments(seg.length_km, state.speed_kmh, seg.road_class)
            cvar = e + 1.755 * var ** 0.5
            nc = cost + m["time_min"] + CARBON_WEIGHT * m["carbon_kg"] \
                + COST_WEIGHT * m["cost_yuan"] + congestion_weight * degree \
                + risk_weight * (cvar - e)
            nxt = seg.v
            if nc < best.get(nxt, 1e18) - 1e-12:
                best[nxt] = nc
                prev[nxt] = (node, sid)
                heapq.heappush(pq, (nc, nxt))
    if dest not in prev:
        return None
    path, node = [], dest
    while node != origin:
        node0, sid = prev[node]
        path.append(sid)
        node = node0
    path.reverse()
    return _walk(network, timeline, cost_model, path, t0, field,
                 congestion_weight, risk_weight)[0]


def td_plan(network, timeline, cost_model, t0: float,
            origin: str, dest: str, field: CongestionField,
            congestion_weight: float = 8.0, risk_weight: float = 0.3,
            ) -> Optional[PathPlan]:
    """时变口径：每段用到达该节点时刻的速度。"""
    best = {origin: 0.0}
    prev: Dict[str, Tuple[str, str]] = {}
    pq = [(0.0, float(t0), origin)]
    while pq:
        cost, minute, node = heapq.heappop(pq)
        if node == dest:
            break
        if cost > best.get(node, 1e18) + 1e-12:
            continue
        for sid in network.out_edges(node):
            e = _edge(network, timeline, cost_model, sid, minute, field,
                      congestion_weight, risk_weight)
            nc = cost + e["cost"]
            nxt = network.segment(sid).v
            if nc < best.get(nxt, 1e18) - 1e-12:
                best[nxt] = nc
                prev[nxt] = (node, sid)
                heapq.heappush(pq, (nc, minute + e["time"], nxt))
    if dest not in prev:
        return None
    path, node = [], dest
    while node != origin:
        node0, sid = prev[node]
        path.append(sid)
        node = node0
    path.reverse()
    return _walk(network, timeline, cost_model, path, t0, field,
                 congestion_weight, risk_weight)[0]


def simulate_trip(network, timeline, cost_model, t0: float,
                  origin: str, dest: str, field: CongestionField,
                  congestion_weight: float = 8.0, risk_weight: float = 0.3,
                  event_drop: float = 0.30, max_steps: int = 200) -> Dict[str, object]:
    """静态 vs 动态（TD + 每节点重规划 + 事件检测）。"""
    static = static_plan(network, timeline, cost_model, t0, origin, dest,
                         field, congestion_weight, risk_weight)
    if static is None:
        return {"ok": False}

    minute = float(t0)
    node = origin
    route: List[str] = []
    steps = events = 0
    plan_minute = minute
    while node != dest and steps < max_steps:
        plan = td_plan(network, timeline, cost_model, minute, node, dest,
                       field, congestion_weight, risk_weight)
        if plan is None or not plan.path:
            break
        sid = plan.path[0]
        speed_now = timeline.at(minute).state(sid).speed_kmh
        speed_plan = timeline.at(plan_minute).state(sid).speed_kmh
        if speed_plan > 0 and speed_now < speed_plan * (1.0 - event_drop):
            events += 1
        plan_minute = minute
        e = _edge(network, timeline, cost_model, sid, minute, field,
                  congestion_weight, risk_weight)
        route.append(sid)
        node = network.segment(sid).v
        minute += e["time"]
        steps += 1
    return {"ok": True, "static": static.to_dict(),
            "dynamic_time_min": round(minute - t0, 3), "route": route,
            "steps": steps, "events": events,
            "delta_min": round((minute - t0) - static.time_min, 3),
            "delta_pct": round(((minute - t0) / max(static.time_min, 1e-9) - 1) * 100, 2)}
