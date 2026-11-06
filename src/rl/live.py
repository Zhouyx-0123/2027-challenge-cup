# -*- coding: utf-8 -*-
"""把前端/设备数据接进学习器的最小胶水层（实时预测与在线更新的入口）。

为什么需要这一层
----------------
``TripEnv`` 的时钟和路况来自 ``TrafficTimeline``。仿真时它由 BPR 一次算完；
真实运行时它应该由**前端推送**逐帧长出来。本模块只做三件事，别的都不碰：

1. ``states_from_payload()``：前端 JSON → ``SegmentState``（不懂的字段不收、
   缺的字段不编，返回 accepted/ignored/rejected 三档计数）；
2. ``apply_payload()``：把这一帧推给时间线（稀疏帧合法，只覆盖出现的路段）；
3. ``LiveTrip``：一张运单的会话状态——环境 + 策略 + 归一化器，
   给 Web 层三个方法：``traffic()`` / ``decide()`` / ``report()``。

数据契约（与 docs/59 §9.4 一致）
--------------------------------
    {
      "minute": 480.5,                       # 可选；缺省用会话当前分钟
      "segments": [                          # 也接受 {"sid": {...}} 的字典形式
        {"sid": "E_G00_G01", "speed_kmh": 18.2,
         "vc_ratio": 1.1, "incident": false} # vc_ratio / incident 可缺
      ]
    }

**不编数据**：``speed_kmh`` 缺失或非正的记录直接计入 rejected，不用自由流兜底；
只有整帧缺失的路段才沿用上一帧（增量推送的常态）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from . import settings
from .env import SegmentCostModel, TripEnv, TripRequest
from .network import RoadNetwork, SegmentState, TrafficSnapshot, TrafficTimeline


def _as_records(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        rows = payload.get("segments")
        if isinstance(rows, dict):
            out = []
            for sid, fields in rows.items():
                if not isinstance(fields, dict):
                    raise ValueError("segments[%r] 必须是对象" % (sid,))
                record = dict(fields)
                record.setdefault("sid", sid)
                out.append(record)
            return out
        if isinstance(rows, list):
            return [dict(row) for row in rows if isinstance(row, dict)]
        raise ValueError("payload 需要 segments 字段（列表或对象）")
    if isinstance(payload, list):
        return [dict(row) for row in payload if isinstance(row, dict)]
    raise ValueError("payload 必须是对象或列表")


def states_from_payload(network: RoadNetwork, payload: Any,
                        minute: float = 0.0) -> Tuple[Dict[str, SegmentState], Dict[str, Any]]:
    """前端快照 → 路段状态。返回 ``(states, 报告)``。

    报告里的三个计数是给前端排错用的，不静默吞：
    ``accepted`` 收下几条、``ignored`` 路网里没有的 sid、``rejected`` 字段非法。
    """
    states: Dict[str, SegmentState] = {}
    ignored: List[str] = []
    rejected: List[Dict[str, str]] = []
    for record in _as_records(payload):
        sid = str(record.get("sid") or record.get("segment_id") or "").strip()
        if not sid:
            rejected.append({"sid": "", "reason": "missing_sid"})
            continue
        if sid not in network.segments:
            ignored.append(sid)
            continue
        try:
            speed = float(record.get("speed_kmh"))
        except (TypeError, ValueError):
            rejected.append({"sid": sid, "reason": "missing_speed"})
            continue
        if not np.isfinite(speed) or speed <= 0:
            rejected.append({"sid": sid, "reason": "invalid_speed"})
            continue
        segment = network.segments[sid]
        try:
            vc = float(record.get("vc_ratio", 0.0) or 0.0)
        except (TypeError, ValueError):
            vc = 0.0
        states[sid] = SegmentState(
            sid=sid, minute=float(minute), speed_kmh=speed,
            vc_ratio=max(vc, 0.0), free_speed_kmh=segment.free_speed_kmh,
            incident=bool(record.get("incident", False)))
    return states, {"accepted": len(states), "ignored": ignored,
                    "rejected": rejected, "minute": float(minute)}


def apply_payload(timeline: TrafficTimeline, network: RoadNetwork, payload: Any,
                  minute: Optional[float] = None) -> Dict[str, Any]:
    """把前端快照推给时间线；返回 ``states_from_payload`` 的报告。"""
    target = float(timeline.start_minute if minute is None else minute)
    states, report = states_from_payload(network, payload, target)
    if states:
        timeline.push(target, states, merge=True)
    report["pushed"] = len(states)
    return report


class LiveTrip:
    """一张运单的实时会话：喂路况 → 出建议 → 收实际结果并更新模型。

    典型用法（Flask 视图里）::

        trip = LiveTrip(network, policy, TripRequest("G00", "G33", load_t=18))
        trip.traffic(amap_payload)            # 前端每分钟推一次
        advice = trip.decide()                # 建议 + ETA + 逐段明细
        trip.report(advice["action"], power_kw=obd_power_kw)   # 司机走完这一段
    """

    def __init__(self, network: RoadNetwork, policy: Any, request: TripRequest,
                 config: Optional[Dict[str, Any]] = None, *,
                 fuel_price: Optional[float] = None,
                 carbon_price: Optional[float] = None,
                 blocked: Optional[Iterable[str]] = None):
        self.config = config if config is not None else settings.load_config()
        self.network = network
        self.policy = policy
        self.request = request
        table, bins = settings.k_table(self.config)
        econ = settings.economics(self.config)
        self.cost_model = SegmentCostModel(
            request, k_table=table, load_bins=bins,
            fuel_price=econ["fuel_price"] if fuel_price is None else fuel_price,
            carbon_price=econ["carbon_price"] if carbon_price is None else carbon_price,
            coefficients=settings.emission_table(self.config))
        self.timeline = TrafficTimeline(
            network, {0: self._free_flow_states(request.departure_minute)},
            start_minute=float(request.departure_minute))
        self.env = TripEnv(network, self.timeline, request,
                           cost_model=self.cost_model, blocked=blocked)
        balanced = next((s for s in policy.strategies if s.sid == "balanced"),
                        policy.strategies[0])
        self.normalizer = settings.reference_normalizer(self.env, balanced, self.config)
        self.policy.start_episode()
        self._decision: Optional[Dict[str, Any]] = None
        self.history: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ 路况
    def _free_flow_states(self, minute: float) -> Dict[str, SegmentState]:
        return {sid: SegmentState(sid=sid, minute=float(minute),
                                  speed_kmh=segment.free_speed_kmh, vc_ratio=0.0,
                                  free_speed_kmh=segment.free_speed_kmh)
                for sid, segment in self.network.segments.items()}

    def traffic(self, payload: Any, minute: Optional[float] = None) -> Dict[str, Any]:
        """推一帧前端路况；``minute`` 缺省用会话当前分钟。"""
        target = self.env.minute if minute is None else float(minute)
        report = apply_payload(self.timeline, self.network, payload, target)
        report["session_minute"] = round(self.env.minute, 2)
        return report

    def snapshot(self) -> TrafficSnapshot:
        return self.env.snapshot()

    # ------------------------------------------------------------------ 决策
    def decide(self, *, strategy: Optional[str] = None,
               explore: bool = False) -> Dict[str, Any]:
        """出一次建议（策略 + 路径 + ETA + 逐段明细 + 理由）。"""
        self._decision = self.policy.act(self.env, self.normalizer,
                                         explore=explore,
                                         forced_strategy=strategy)
        plan = self._decision["plan"]
        eta = self.policy.predict_eta(self.env, plan)
        return {
            "action": self._decision["next_segment"],
            "strategy": self._decision["strategy"],
            "plan": plan.to_dict(),
            "eta": eta,
            "segment_detail": [dict(row) for row in plan.rows],
            "kept_plan": bool(self._decision.get("kept_plan")),
            "state": self.env.observation(),
            "history_len": len(self.history),
        }

    # ------------------------------------------------------------------ 反馈
    def report(self, sid: str, power_kw: Optional[float] = None) -> Dict[str, Any]:
        """司机走完一段：用实测功率记账，并把这一leg喂给 TD / Q / 修正层。"""
        if self._decision is None:
            self.decide()
        assert self._decision is not None
        outcome = self.env.step(sid, power_kw=power_kw)
        info = self.policy.observe(self.env, self._decision, outcome, self.normalizer)
        record = {"sid": sid, "time_min": round(outcome.time_min, 3),
                  "carbon_kg": round(outcome.carbon_kg, 5),
                  "power_kw": round(outcome.power_kw, 2),
                  "done": bool(self.env.done), "update": info}
        self.history.append(record)
        if not self.env.done:
            self._decision = None
        return record

    # ------------------------------------------------------------------ 汇总
    def summary(self) -> Dict[str, Any]:
        return {
            "request": self.request.to_dict(),
            "done": bool(self.env.done),
            "time_min": round(self.env.total_time_min, 3),
            "carbon_kg": round(self.env.total_carbon_kg, 5),
            "cost_yuan": round(self.env.total_cost_yuan, 3),
            "segments": len(self.history),
            "node": self.env.node,
            "minute": round(self.env.minute, 2),
        }


__all__ = ["states_from_payload", "apply_payload", "LiveTrip"]
