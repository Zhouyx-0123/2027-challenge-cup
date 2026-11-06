# -*- coding: utf-8 -*-
"""单次行程的 MDP 环境 + 逐段成本模型。

为什么要有 ``SegmentCostModel``
-------------------------------
路由（Dijkstra 估代价）和环境（真正走一段记账）必须用**同一个**成本函数，
否则"规划说 12 分钟、走完变 15 分钟"的差额会被错误归因给"学习效果"。
把逐段成本收进一个类，路由、环境、决策服务三处共用。

MDP 建模
--------
一次决策 = 在节点上从若干条出边里选一条。这样建模的三个理由：

1. **动作空间天然有限**：每个节点出度 2–4 条，永远是几十个候选，
   不需要把整条路径当动作（那是组合爆炸）。
2. **可以逐段记账**：每走一段就产出一条"实测"记录（时间/碳/油/费用/功率），
   这正是前端要展示的"中间具体数据"，也是 TD 学习的一步转移样本。
3. **可以中途重规划**：到节点就能换路，符合实际导航行为；切换成本由策略层
   单独惩罚，避免"每 100 米换一次路"的抖动。

约束
----
- ``blocked``：禁行路段（施工/限行/低排放区），动作直接屏蔽；
- ``max_steps``：防止在环路上无限绕行；
- 不可达剪枝：走完某段后若无法到达终点，则该动作不可选（避免进死胡同）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np

from . import emission
from .network import (RoadNetwork, RoadSegment, SegmentState, TrafficSnapshot,
                      TrafficTimeline, congestion_label, free_flow_shortest_km)

#: 柴油单价（元/L），与 docs/20 的 7.5 元/L 展示口径一致；可被 config 覆盖
DEFAULT_FUEL_PRICE = 7.5
#: 碳价（元/tCO₂），与 route_carbon 的默认 100 一致
DEFAULT_CARBON_PRICE = 100.0


@dataclass
class TripRequest:
    """一张运单：从哪到哪、什么车、装多少、几点走。"""

    origin: str
    destination: str
    vclass: int = 3
    load_t: float = 18.0
    departure_minute: float = 480.0     # 分钟（0=00:00）
    strategy: str = "balanced"
    order_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id, "origin": self.origin,
            "destination": self.destination, "vclass": int(self.vclass),
            "load_t": float(self.load_t),
            "departure_minute": float(self.departure_minute),
            "strategy": self.strategy,
        }


class SegmentCostModel:
    """逐段成本：时间 / 碳 / 油 / 通行费 / 能量费 / 碳成本。

    碳排走 ``emission.segment_carbon``（用户给的功率-速度公式），
    ``k_{i,j}`` 来自 k-means 载重分箱后的标定表。
    """

    def __init__(self, request: TripRequest, *,
                 k_table: Optional[Dict[str, Any]] = None,
                 load_bins: Optional[Sequence[float]] = None,
                 fuel_price: float = DEFAULT_FUEL_PRICE,
                 carbon_price: float = DEFAULT_CARBON_PRICE,
                 coefficients: Optional[Dict[int, emission.EmissionCoefficients]] = None,
                 corrector: Any = None):
        self.request = request
        self.k_table = k_table
        self.load_bins = list(load_bins) if load_bins else None
        self.fuel_price = float(fuel_price)
        self.carbon_price = float(carbon_price)
        self.coefficients = coefficients or emission.DEFAULT_COEFFICIENTS
        # 模型残差修正器（agent.ResidualCorrector）。None = 纯模型口径；
        # 路由用它把"实际/模型"的偏差补回来（见 docs/59 §14）。
        self.corrector = corrector

    def k_factor(self, segment: RoadSegment) -> float:
        return emission.lookup_k_factor(
            self.k_table, segment.road_class, self.request.load_t, self.load_bins)

    def correction_factors(self, road_class: int, minute: float) -> Tuple[float, float]:
        if self.corrector is None:
            return 1.0, 1.0
        return (self.corrector.factor_time(road_class, minute),
                self.corrector.factor_carbon(road_class, minute))

    def evaluate(self, segment: RoadSegment, snapshot: TrafficSnapshot,
                 power_kw: Optional[float] = None,
                 apply_correction: bool = True) -> Dict[str, Any]:
        state = snapshot.state(segment.sid)
        speed = max(state.speed_kmh, 1e-3)
        time_min = segment.length_km / speed * 60.0
        k_factor = self.k_factor(segment)
        carbon = emission.segment_carbon(
            segment.length_km, speed, vclass=self.request.vclass,
            load_t=self.request.load_t, k_factor=k_factor, power_kw=power_kw,
            grade_pct=segment.grade_pct,
            coefficients=self.coefficients.get(int(self.request.vclass)))
        factor_t, factor_c = (1.0, 1.0)
        if apply_correction and self.corrector is not None:
            factor_t, factor_c = self.correction_factors(segment.road_class,
                                                         snapshot.minute)
            time_min *= factor_t
            carbon = dict(carbon)
            carbon["co2_kg"] = carbon["co2_kg"] * factor_c
            carbon["fuel_l"] = carbon["fuel_l"] * factor_c
            carbon["ef_g_per_km"] = carbon["ef_g_per_km"] * factor_c
        energy_cost = carbon["fuel_l"] * self.fuel_price
        carbon_cost = carbon["co2_kg"] / 1000.0 * self.carbon_price
        return {
            "sid": segment.sid, "time_min": time_min, "speed_kmh": speed,
            "congestion_index": state.congestion_index,
            "congestion_label": congestion_label(state.congestion_index),
            "incident": bool(state.incident), "road_class": segment.road_class,
            "length_km": segment.length_km, "grade_pct": segment.grade_pct,
            "power_kw": carbon["power_kw"], "power_source": carbon["power_source"],
            "ef_g_per_km": carbon["ef_g_per_km"], "k_factor": k_factor,
            "carbon_kg": carbon["co2_kg"], "fuel_l": carbon["fuel_l"],
            "toll_yuan": segment.toll_yuan, "energy_cost_yuan": energy_cost,
            "carbon_cost_yuan": carbon_cost,
            "cost_yuan": segment.toll_yuan + energy_cost + carbon_cost,
            "correction_time": factor_t, "correction_carbon": factor_c,
        }


@dataclass
class StepOutcome:
    """走完一段路的实测结果 —— 前端表格的一行，也是 RL 的一个 transition。"""

    sid: str
    start: str
    end: str
    minute: float
    time_min: float
    speed_kmh: float
    congestion_index: float
    congestion_label: str
    road_class: int
    length_km: float
    grade_pct: float
    power_kw: float
    power_source: str
    ef_g_per_km: float
    k_factor: float
    carbon_kg: float
    fuel_l: float
    toll_yuan: float
    energy_cost_yuan: float
    carbon_cost_yuan: float
    incident: bool
    done: bool
    model_time_min: float = 0.0
    model_carbon_kg: float = 0.0

    @property
    def cost_yuan(self) -> float:
        return self.toll_yuan + self.energy_cost_yuan + self.carbon_cost_yuan

    def cost_vector(self) -> np.ndarray:
        return np.array([self.time_min, self.carbon_kg, self.cost_yuan], dtype=float)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sid": self.sid, "start": self.start, "end": self.end,
            "minute": round(self.minute, 2),
            "time_min": round(self.time_min, 3),
            "speed_kmh": round(self.speed_kmh, 2),
            "congestion_index": round(self.congestion_index, 4),
            "congestion_label": self.congestion_label,
            "road_class": self.road_class,
            "length_km": round(self.length_km, 4),
            "grade_pct": round(self.grade_pct, 2),
            "power_kw": round(self.power_kw, 2),
            "power_source": self.power_source,
            "ef_g_per_km": round(self.ef_g_per_km, 2),
            "k_factor": round(self.k_factor, 4),
            "carbon_kg": round(self.carbon_kg, 5),
            "fuel_l": round(self.fuel_l, 5),
            "toll_yuan": round(self.toll_yuan, 2),
            "energy_cost_yuan": round(self.energy_cost_yuan, 2),
            "carbon_cost_yuan": round(self.carbon_cost_yuan, 2),
            "cost_yuan": round(self.cost_yuan, 2),
            "incident": bool(self.incident),
            "done": bool(self.done),
            "model_time_min": round(self.model_time_min, 3),
            "model_carbon_kg": round(self.model_carbon_kg, 5),
        }


class TripEnv:
    """行程环境：``reset()`` → 反复 ``step(sid)`` → 到终点结束。"""

    def __init__(self, network: RoadNetwork, timeline: TrafficTimeline,
                 request: TripRequest, *,
                 cost_model: Optional[SegmentCostModel] = None,
                 max_steps: int = 120,
                 blocked: Optional[Iterable[str]] = None,
                 time_bias: float = 1.0,
                 carbon_bias: float = 1.0,
                 bias_table: Optional[Dict[str, Sequence[float]]] = None,
                 bias_buckets: int = 8,
                 **cost_kwargs: Any):
        self.network = network
        self.timeline = timeline
        self.request = request
        self.cost_model = cost_model or SegmentCostModel(request, **cost_kwargs)
        self.max_steps = int(max_steps)
        self.blocked: Set[str] = set(blocked or ())
        # sim-to-real 偏差注入：路由模型不知道这两个乘子，实际行程按它们记账。
        # 真实世界模型必然有偏差（驾驶行为/车况/路况），TD 的作用就是把它学回来。
        self.time_bias = float(time_bias)
        self.carbon_bias = float(carbon_bias)
        # 分道路等级 × 时段的仿真偏差（"实际 = 模型 × 系数"），用来模拟
        # 模型对匝道/高架排队的系统性低估；学习器不知道这张表，只能从实测反推。
        self.bias_table: Dict[str, Sequence[float]] = dict(bias_table or {})
        self.bias_buckets = max(int(bias_buckets), 1)
        if request.origin not in network.nodes:
            raise ValueError("起点不在路网：%s" % request.origin)
        if request.destination not in network.nodes:
            raise ValueError("终点不在路网：%s" % request.destination)
        if request.origin == request.destination:
            raise ValueError("起点与终点相同")
        self.reset()

    # ------------------------------------------------------------------ 状态
    def reset(self) -> Dict[str, Any]:
        self.node = self.request.origin
        self.minute = float(self.request.departure_minute)
        self.path: List[str] = []
        self.outcomes: List[StepOutcome] = []
        self.total_time_min = 0.0
        self.total_carbon_kg = 0.0
        self.total_cost_yuan = 0.0
        self.replans = 0
        self._done = False
        self._prev_congestion = 0.0
        self.initial_remaining_km = max(
            free_flow_shortest_km(self.network, self.request.origin,
                                  self.request.destination, self.blocked), 1e-3)
        return self.observation()

    @property
    def done(self) -> bool:
        return self._done

    @property
    def finished(self) -> bool:
        return self._done

    def snapshot(self) -> TrafficSnapshot:
        return self.timeline.at(self.minute)

    def observation(self) -> Dict[str, Any]:
        snap = self.snapshot()
        candidates = self.candidates()
        congestion = [snap.state(sid).congestion_index for sid in candidates]
        return {
            "node": self.node,
            "minute": round(self.minute, 2),
            "remaining_edges": self.max_steps - len(self.path),
            "candidates": candidates,
            "candidate_congestion": [round(c, 4) for c in congestion],
            "mean_congestion": round(float(np.mean(congestion)) if congestion else 0.0, 4),
            "prev_congestion": round(self._prev_congestion, 4),
            "elapsed_min": round(self.total_time_min, 3),
            "carbon_kg": round(self.total_carbon_kg, 5),
            "cost_yuan": round(self.total_cost_yuan, 2),
            "progress": round(self.progress(), 4),
            "done": self._done,
        }

    # ------------------------------------------------------------ 可达性/约束
    def _reachable_nodes(self) -> Set[str]:
        """从终点反向 BFS，得到"能从该节点走到终点"的集合（每步现算，路网很小）。"""
        seen = {self.request.destination}
        queue = [self.request.destination]
        while queue:
            node = queue.pop()
            for sid in self.network.in_edges(node):
                if sid in self.blocked:
                    continue
                segment = self.network.segment(sid)
                if segment.u not in seen:
                    seen.add(segment.u)
                    queue.append(segment.u)
        return seen

    def candidates(self) -> List[str]:
        if self._done:
            return []
        reachable = self._reachable_nodes()
        out: List[str] = []
        for sid in self.network.out_edges(self.node):
            if sid in self.blocked:
                continue
            if len(self.path) >= self.max_steps:
                continue
            if self.network.segment(sid).v not in reachable:
                continue
            out.append(sid)
        return out

    # ------------------------------------------------------------------ 转移
    def step(self, sid: str, power_kw: Optional[float] = None) -> StepOutcome:
        if self._done:
            raise RuntimeError("行程已结束，先 reset()")
        if sid not in self.candidates():
            raise ValueError("路段 %r 不在当前可选集合内" % (sid,))
        segment = self.network.segment(sid)
        snapshot = self.snapshot()
        # 模型口径（未修正）：留给残差学习器做"实际/模型"比值
        raw = self.cost_model.evaluate(segment, snapshot, power_kw=power_kw,
                                       apply_correction=False)
        factor_t, factor_c = self._bias_factors(segment.road_class, self.minute)
        time_min = raw["time_min"] * factor_t * self.time_bias
        carbon_kg = raw["carbon_kg"] * factor_c * self.carbon_bias
        fuel_l = raw["fuel_l"] * factor_c * self.carbon_bias
        energy_cost = fuel_l * self.cost_model.fuel_price
        carbon_cost = carbon_kg / 1000.0 * self.cost_model.carbon_price
        speed = segment.length_km / max(time_min, 1e-9) * 60.0
        outcome = StepOutcome(
            sid=sid, start=segment.u, end=segment.v, minute=self.minute,
            time_min=time_min, speed_kmh=speed,
            congestion_index=raw["congestion_index"],
            congestion_label=("事故·" + raw["congestion_label"]) if raw["incident"]
            else raw["congestion_label"],
            road_class=segment.road_class, length_km=segment.length_km,
            grade_pct=segment.grade_pct, power_kw=raw["power_kw"],
            power_source=raw["power_source"], ef_g_per_km=raw["ef_g_per_km"],
            k_factor=raw["k_factor"], carbon_kg=carbon_kg,
            fuel_l=fuel_l, toll_yuan=segment.toll_yuan,
            energy_cost_yuan=energy_cost,
            carbon_cost_yuan=carbon_cost, incident=raw["incident"],
            done=(segment.v == self.request.destination),
            model_time_min=raw["time_min"], model_carbon_kg=raw["carbon_kg"],
        )
        self.path.append(sid)
        self.outcomes.append(outcome)
        self.total_time_min += outcome.time_min
        self.total_carbon_kg += outcome.carbon_kg
        self.total_cost_yuan += outcome.cost_yuan
        self.minute += outcome.time_min
        self._prev_congestion = outcome.congestion_index
        self.node = segment.v
        self._done = bool(outcome.done) or len(self.path) >= self.max_steps
        outcome.done = self._done
        return outcome

    def _bias_factors(self, road_class: int, minute: float) -> Tuple[float, float]:
        """取 (时间, 碳) 的仿真偏差系数；无表则为 1.0。"""
        if not self.bias_table:
            return 1.0, 1.0
        index = int((float(minute) % 1440.0) // (1440.0 / self.bias_buckets))
        index = min(index, self.bias_buckets - 1)
        time_row = self.bias_table.get("time") or {}
        carbon_row = self.bias_table.get("carbon") or {}
        factor_t = float((time_row.get(str(int(road_class))) or [1.0] * self.bias_buckets)[index]) \
            if time_row.get(str(int(road_class))) else 1.0
        factor_c = float((carbon_row.get(str(int(road_class))) or [1.0] * self.bias_buckets)[index]) \
            if carbon_row.get(str(int(road_class))) else 1.0
        return factor_t, factor_c

    # -------------------------------------------------------------- 状态特征
    def state_features(self, snapshot: Optional[TrafficSnapshot] = None) -> np.ndarray:
        """给 k-means / 值函数用的连续特征（顺序固定，落 config）。

        ``[上游拥堵, 下游平均拥堵, 载重(t), 下游平均坡度(%), 时段(0–24h)]``
        """
        snap = snapshot or self.snapshot()
        candidates = self.candidates()
        congestion = [snap.state(sid).congestion_index for sid in candidates] or [0.0]
        grades = [self.network.segment(sid).grade_pct for sid in candidates] or [0.0]
        return np.array([
            self._prev_congestion,
            float(np.mean(congestion)),
            float(self.request.load_t),
            float(np.mean(grades)),
            (self.minute % 1440.0) / 60.0,
        ], dtype=float)

    def remaining_km(self) -> float:
        """剩余自由流里程（km）——只用于进度分档与前端展示，不进碳/时口径。"""
        return free_flow_shortest_km(self.network, self.node,
                                     self.request.destination, self.blocked)

    def progress(self) -> float:
        """行程完成度 0–1：``1 - 剩余自由流里程 / 初始自由流里程``。"""
        if not self.path:
            return 0.0
        if self._done:
            return 1.0
        return float(min(max(1.0 - self.remaining_km() / self.initial_remaining_km,
                             0.0), 0.999))

    def remaining_path_hint(self) -> Dict[str, Any]:
        """当前进度快照（前端/日志用）。"""
        return {"elapsed_min": self.total_time_min,
                "carbon_kg": self.total_carbon_kg,
                "cost_yuan": self.total_cost_yuan,
                "segments_done": len(self.path),
                "remaining_km": self.remaining_km(),
                "progress": self.progress(),
                "node": self.node}


__all__ = ["TripRequest", "SegmentCostModel", "StepOutcome", "TripEnv",
           "DEFAULT_FUEL_PRICE", "DEFAULT_CARBON_PRICE"]
