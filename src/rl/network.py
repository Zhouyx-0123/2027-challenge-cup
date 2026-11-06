# -*- coding: utf-8 -*-
"""动态路网与交通演化（本项目的"数字孪生"最小区块）。

为什么自己造一个仿真器
----------------------
Q-learning 的训练需要「同一套路段、千百次不同拥堵状况」的样本。高德 API 只能
给当下这一秒的快照，给不了"如果 18:05 从 G00 出发会怎样"，更给不了反事实对比。
所以这里做一个**外生交通演化器**：车流随时间变化，且**与"我们这辆车怎么走"无关**
（货运单车的路径选择不影响整条路的拥堵），这一点很关键——它让"事后最优解"
（hindsight optimal）可以被精确算出来，从而给学习策略一个可解释的差距指标。

模型
----
1. 时段需求：早晚高峰用高斯峰叠在基线上（``demand_scale``），城市/城际两套形状。
2. 速度-流量：BPR 阻抗函数 ``v = v_free / (1 + 0.15·(q/c)^4)``，这是交通工程
   标准形式，比"线性掉速"更接近真实（拥堵到临界点才断崖）。
3. 事故：按泊松到达生成，持续 10–40 min，等效于通行能力下降（乘在 v/c 上）。
4. 溢出：下游 v/c 高时反向压低上游速度（排队长龙），做一轮前向传播。
5. 天气：雨天/雾天整体降速并提高事故率（乘子表）。

一切随机都走 ``numpy.random.Generator(seed)``，同 seed 结果完全可复现——
测试与答辩演示都靠这一点。
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

#: 拥堵等级标签（按拥堵指数的上界切分，与前端配色一一对应）
CONGESTION_LABELS: Tuple[Tuple[float, str], ...] = (
    (0.20, "畅通"),
    (0.45, "缓行"),
    (0.70, "拥堵"),
    (1.01, "严重拥堵"),
)
#: 天气 → (速度乘子, 事故率乘子)
WEATHER_FACTORS: Dict[str, Tuple[float, float]] = {
    "clear": (1.00, 1.0),
    "rain": (0.85, 1.9),
    "fog": (0.70, 2.4),
    "snow": (0.60, 2.8),
}
#: 道路等级 → 默认需求权重（主干道最挤，高速次之，支路最少）
ROAD_CLASS_DEMAND: Dict[int, float] = {1: 0.85, 2: 1.05, 3: 0.70, 4: 0.55}
#: 道路等级中文名
ROAD_CLASS_NAMES: Dict[int, str] = {1: "高速/快速路", 2: "主干道", 3: "次干道/支路", 4: "匝道/园区"}
#: BPR 参数（标准值：α=0.15, β=4）
BPR_ALPHA = 0.15
BPR_BETA = 4.0
#: 最低计算速度（km/h），避免 BPR 在极端 v/c 下把速度压到 0
MIN_SPEED_KMH = 4.0


def congestion_label(index: float) -> str:
    for upper, label in CONGESTION_LABELS:
        if index < upper:
            return label
    return CONGESTION_LABELS[-1][1]


def demand_scale(minute_of_day: float, profile: str = "urban") -> float:
    """时段需求系数（0.2–1.45）。早晚高峰用高斯峰，避免阶梯函数导致速度跳变。"""
    hour = (float(minute_of_day) % 1440.0) / 60.0

    def bump(center: float, width: float, amp: float) -> float:
        return amp * math.exp(-0.5 * ((hour - center) / width) ** 2)

    if profile == "night":
        return 0.25 + bump(3.0, 2.0, 0.10)
    if profile == "intercity":
        return 0.50 + bump(10.0, 2.0, 0.35) + bump(16.0, 2.2, 0.40)
    if profile == "holiday":
        return 0.45 + bump(11.0, 2.5, 0.35) + bump(19.0, 2.5, 0.30)
    return 0.55 + bump(8.0, 1.0, 1.15) + bump(12.5, 0.9, 0.35) + bump(18.0, 1.1, 1.25)


@dataclass(frozen=True)
class RoadSegment:
    """一条有向路段。``sid`` 唯一；``u``/``v`` 是起点/终点节点名。"""

    sid: str
    u: str
    v: str
    length_m: float
    road_class: int
    free_speed_kmh: float
    capacity_vph: float
    grade_pct: float = 0.0
    toll_yuan: float = 0.0
    demand_weight: float = 0.0

    @property
    def length_km(self) -> float:
        return self.length_m / 1000.0

    @property
    def free_travel_s(self) -> float:
        return self.length_m / 1000.0 / self.free_speed_kmh * 3600.0

    @property
    def demand(self) -> float:
        return self.demand_weight or ROAD_CLASS_DEMAND.get(int(self.road_class), 0.8)


@dataclass(frozen=True)
class SegmentState:
    """某一分钟某条路的"实测"状态——前端要显示的中间量都在这里。"""

    sid: str
    minute: float
    speed_kmh: float
    vc_ratio: float
    free_speed_kmh: float
    incident: bool = False
    incident_min_left: float = 0.0

    @property
    def congestion_index(self) -> float:
        return float(min(max(1.0 - self.speed_kmh / max(self.free_speed_kmh, 1e-6), 0.0), 1.0))

    @property
    def incident_label(self) -> str:
        return "事故" if self.incident else "正常"

    def to_dict(self, segment: Optional[RoadSegment] = None) -> Dict[str, Any]:
        payload = {
            "sid": self.sid,
            "minute": round(self.minute, 2),
            "speed_kmh": round(self.speed_kmh, 2),
            "vc_ratio": round(self.vc_ratio, 4),
            "congestion_index": round(self.congestion_index, 4),
            "congestion_label": congestion_label(self.congestion_index),
            "incident": bool(self.incident),
            "incident_label": self.incident_label,
        }
        if segment is not None:
            payload.update({
                "length_km": round(segment.length_km, 4),
                "road_class": segment.road_class,
                "road_class_name": ROAD_CLASS_NAMES.get(segment.road_class, "?"),
                "travel_time_min": round(segment.length_km / max(self.speed_kmh, 1e-6) * 60.0, 3),
            })
        return payload


@dataclass
class TrafficSnapshot:
    """某一分钟的全网快照。所有"前端数据"都从这里取，保证同源。"""

    minute: float
    states: Dict[str, SegmentState]

    def state(self, sid: str) -> SegmentState:
        try:
            return self.states[sid]
        except KeyError as exc:
            raise KeyError("快照中没有路段 %r" % (sid,)) from exc

    def speed(self, sid: str) -> float:
        return self.state(sid).speed_kmh

    def travel_time_min(self, segment: RoadSegment) -> float:
        return segment.length_km / max(self.speed(segment.sid), 1e-6) * 60.0

    def mean_congestion(self, network: "RoadNetwork") -> float:
        if not self.states:
            return 0.0
        return float(np.mean([self.state(sid).congestion_index for sid in self.states]))

    def to_dict(self, network: Optional["RoadNetwork"] = None) -> Dict[str, Any]:
        rows = {}
        for sid, state in self.states.items():
            segment = network.segments.get(sid) if network else None
            rows[sid] = state.to_dict(segment)
        return {"minute": round(self.minute, 2), "segments": rows}


class RoadNetwork:
    """有向路网：只存路段，邻接表按需构建。"""

    def __init__(self, segments: Iterable[RoadSegment]):
        self.segments: Dict[str, RoadSegment] = {}
        for segment in segments:
            if segment.sid in self.segments:
                raise ValueError("重复路段 id：%s" % segment.sid)
            if segment.length_m <= 0:
                raise ValueError("路段 %s 长度必须为正" % segment.sid)
            if segment.free_speed_kmh <= 0:
                raise ValueError("路段 %s 自由流速度必须为正" % segment.sid)
            self.segments[segment.sid] = segment
        if not self.segments:
            raise ValueError("路网为空")
        self.outgoing: Dict[str, List[str]] = {}
        self.incoming: Dict[str, List[str]] = {}
        for sid, segment in self.segments.items():
            self.outgoing.setdefault(segment.u, []).append(sid)
            self.incoming.setdefault(segment.v, []).append(sid)
        for bucket in self.outgoing.values():
            bucket.sort()
        for bucket in self.incoming.values():
            bucket.sort()

    @property
    def nodes(self) -> List[str]:
        return sorted(set(self.outgoing) | set(self.incoming))

    def out_edges(self, node: str) -> List[str]:
        return list(self.outgoing.get(node, []))

    def in_edges(self, node: str) -> List[str]:
        return list(self.incoming.get(node, []))

    def segment(self, sid: str) -> RoadSegment:
        try:
            return self.segments[sid]
        except KeyError as exc:
            raise KeyError("未知路段 %r" % (sid,)) from exc

    def path_length_km(self, path: Sequence[str]) -> float:
        return float(sum(self.segment(sid).length_km for sid in path))

    def path_nodes(self, path: Sequence[str]) -> List[str]:
        if not path:
            return []
        return [self.segment(path[0]).u] + [self.segment(sid).v for sid in path]

    @classmethod
    def from_spec(cls, spec: Dict[str, Any]) -> "RoadNetwork":
        """从 JSON spec 建网：``{"segments":[{...}, ...]}``。"""
        rows = spec.get("segments") if isinstance(spec, dict) else spec
        if not rows:
            raise ValueError("路网 spec 缺少 segments")
        segments = [RoadSegment(
            sid=str(row["sid"]), u=str(row["u"]), v=str(row["v"]),
            length_m=float(row["length_m"]), road_class=int(row.get("road_class", 2)),
            free_speed_kmh=float(row.get("free_speed_kmh", 50.0)),
            capacity_vph=float(row.get("capacity_vph", 1200.0)),
            grade_pct=float(row.get("grade_pct", 0.0)),
            toll_yuan=float(row.get("toll_yuan", 0.0)),
            demand_weight=float(row.get("demand_weight", 0.0)),
        ) for row in rows]
        return cls(segments)

    def to_spec(self) -> Dict[str, Any]:
        return {"version": "rl-network-v1", "segments": [
            {"sid": s.sid, "u": s.u, "v": s.v, "length_m": s.length_m,
             "road_class": s.road_class, "free_speed_kmh": s.free_speed_kmh,
             "capacity_vph": s.capacity_vph, "grade_pct": s.grade_pct,
             "toll_yuan": s.toll_yuan, "demand_weight": s.demand_weight}
            for s in self.segments.values()]}


def build_demo_network(rows: int = 4, cols: int = 4, seed: int = 7) -> RoadNetwork:
    """4×4 方格 + 一条对角高架的演示路网（G00 → G33）。

    为什么是这个形状
    ----------------
    要让「时间 vs 碳」真的打架，必须两条路的距离和速度都不同：

    - **地面**：6 段 × 0.75 km ≈ 4.5 km，50 km/h，路口多、拥堵时掉到 20–30 km/h；
    - **高架**：匝道 0.35 km + 高架 4.8 km + 匝道 0.35 km ≈ 5.5 km（1.22 倍距离），
      85 km/h 且不受地面拥堵影响。

    于是：平峰走高架只快 1 分钟、碳多 30%（亏）；高峰地面掉到 25 km/h 时，
    高架快 3–4 分钟（赚）。**优劣随时段反转**——这正是 Q-learning 要学的东西。
    如果像早期版本那样放对角支路当捷径，所有策略都会选同一条路，学不到任何策略。

    坡度取 ±0.6%（城市道路常见量级）：早期用 ±2% 时，1% 下坡在 30 km/h 就能抵消
    滚动阻力，卡车变成「滑行工况」，碳排被算成 80 g/km，明显违背实测。
    """
    rng = np.random.default_rng(seed)
    segments: List[RoadSegment] = []

    def add(sid: str, u: str, v: str, length: float, road_class: int,
            free_speed: float, capacity: float, grade: float = 0.0,
            toll: float = 0.0) -> None:
        segments.append(RoadSegment(sid=sid, u=u, v=v, length_m=round(length, 1),
                                    road_class=road_class, free_speed_kmh=free_speed,
                                    capacity_vph=capacity, grade_pct=grade,
                                    toll_yuan=toll))

    def jitter(low: float, high: float) -> float:
        return float(rng.uniform(low, high))

    for r in range(rows):
        for c in range(cols):
            node = "G%d%d" % (r, c)
            if c + 1 < cols:
                east = "G%d%d" % (r, c + 1)
                length = jitter(650, 900)
                grade = jitter(-0.6, 0.6)
                add("E_%s_%s" % (node, east), node, east, length, 2,
                    jitter(45, 55), 1200, grade=grade)
                add("W_%s_%s" % (east, node), east, node, length, 2,
                    jitter(45, 55), 1200, grade=-grade)
            if r + 1 < rows:
                south = "G%d%d" % (r + 1, c)
                length = jitter(650, 900)
                grade = jitter(-0.6, 0.6)
                add("S_%s_%s" % (node, south), node, south, length, 2,
                    jitter(45, 55), 1200, grade=grade)
                add("N_%s_%s" % (south, node), south, node, length, 2,
                    jitter(45, 55), 1200, grade=-grade)

    # 对角高架：G00 → 匝道 X1 → X2 → X3 → X4 → 匝道 → G33
    ramp_len = 350.0
    add("RU_G00_X1", "G00", "X1", ramp_len, 4, 45.0, 900, grade=2.0)
    add("RD_X1_G00", "X1", "G00", ramp_len, 4, 45.0, 900, grade=-2.0)
    add("RU_G33_X4", "G33", "X4", ramp_len, 4, 45.0, 900, grade=2.0)
    add("RD_X4_G33", "X4", "G33", ramp_len, 4, 45.0, 900, grade=-2.0)
    for index in range(3):
        u, v = "X%d" % (index + 1), "X%d" % (index + 2)
        length = jitter(1500, 1700)
        grade = jitter(-0.4, 0.4)
        add("H_%s_%s" % (u, v), u, v, length, 1, 85.0, 2400, grade=grade)
        add("H_%s_%s" % (v, u), v, u, length, 1, 85.0, 2400, grade=-grade)
    return RoadNetwork(segments)


@dataclass
class TrafficConfig:
    """交通仿真参数。``seed`` 决定事故过程，同 seed 完全可复现。"""

    seed: int = 20261103
    profile: str = "urban"
    weather: str = "clear"
    volume: float = 1.0
    incident_rate_per_hour: float = 1.5  # 全网每小时新增事故数（不是每段）
    min_incident_min: float = 10.0
    max_incident_min: float = 40.0

    def weather_factors(self) -> Tuple[float, float]:
        return WEATHER_FACTORS.get(self.weather, WEATHER_FACTORS["clear"])


class TrafficSimulator:
    """外生交通演化器：给定起点时刻，向前推演一条分钟级时间线。"""

    def __init__(self, network: RoadNetwork, config: Optional[TrafficConfig] = None):
        self.network = network
        self.config = config or TrafficConfig()

    def _incident_probability(self) -> float:
        _, factor = self.config.weather_factors()
        return min(self.config.incident_rate_per_hour * factor / 60.0, 0.9)

    def simulate(self, start_minute: float = 0.0, horizon_min: int = 240,
                 step_min: float = 1.0) -> "TrafficTimeline":
        rng = np.random.default_rng(self.config.seed)
        speed_factor, _ = self.config.weather_factors()
        steps = int(math.ceil(horizon_min / step_min)) + 1
        incidents: Dict[str, Tuple[float, float]] = {}  # sid -> (end_minute, severity)
        frames: Dict[int, Dict[str, SegmentState]] = {}

        def vc_for(segment: RoadSegment, minute: float) -> Tuple[float, bool, float]:
            base = (segment.demand * demand_scale(minute, self.config.profile)
                    * self.config.volume)
            incident = incidents.get(segment.sid)
            if incident and minute <= incident[0]:
                severity = incident[1]
                return base * (1.0 + severity), True, incident[0] - minute
            return base, False, 0.0

        for index in range(steps):
            minute = start_minute + index * step_min
            # 1) 事故到期与新发
            expired = [sid for sid, (end, _) in incidents.items() if minute > end]
            for sid in expired:
                incidents.pop(sid, None)
            if rng.random() < self._incident_probability():
                sid = list(self.network.segments)[int(rng.integers(0, len(self.network.segments)))]
                if sid not in incidents:
                    duration = float(rng.uniform(self.config.min_incident_min,
                                                 self.config.max_incident_min))
                    incidents[sid] = (minute + duration, float(rng.uniform(0.5, 1.4)))

            # 2) 逐段算 v/c 与 BPR 速度
            vc_map: Dict[str, float] = {}
            flag_map: Dict[str, Tuple[bool, float]] = {}
            for sid, segment in self.network.segments.items():
                vc, incident, left = vc_for(segment, minute)
                vc_map[sid] = max(vc, 0.0)
                flag_map[sid] = (incident, left)
            # 3) 溢出：下游挤 → 上游更挤。只做一轮、且先算后写（若边算边写，
            #    沿路网会一路自乘，峰值直接锁死在 4 km/h，那是数值爆炸不是交通）。
            spill: Dict[str, float] = {}
            for sid, segment in self.network.segments.items():
                downstream = self.network.out_edges(segment.v)
                if not downstream:
                    continue
                worst = max(vc_map.get(x, 0.0) for x in downstream)
                if worst > 0.90:
                    spill[sid] = vc_map[sid] * (1.0 + 0.15 * min(1.0, (worst - 0.90) / 0.10))
            for sid, value in spill.items():
                vc_map[sid] = max(vc_map[sid], value)
            # 4) 速度
            frame: Dict[str, SegmentState] = {}
            for sid, segment in self.network.segments.items():
                vc = vc_map[sid]
                speed = segment.free_speed_kmh / (1.0 + BPR_ALPHA * vc ** BPR_BETA)
                speed = min(max(speed * speed_factor, MIN_SPEED_KMH), segment.free_speed_kmh)
                incident, left = flag_map[sid]
                frame[sid] = SegmentState(
                    sid=sid, minute=minute, speed_kmh=speed, vc_ratio=vc,
                    free_speed_kmh=segment.free_speed_kmh,
                    incident=incident, incident_min_left=left)
            frames[int(round(minute - start_minute))] = frame
        return TrafficTimeline(self.network, frames, start_minute=start_minute,
                               step_min=step_min)

    def snapshot(self, timeline: "TrafficTimeline", minute: float) -> TrafficSnapshot:
        return timeline.at(minute)


class TrafficTimeline:
    """分钟级快照序列；``at(minute)`` 取最近一帧（外墙透明）。"""
    def __init__(self, network: RoadNetwork, frames: Dict[int, Dict[str, SegmentState]],
                 start_minute: float = 0.0, step_min: float = 1.0):
        if not frames:
            raise ValueError("时间线为空")
        self.network = network
        self.frames = frames
        self.start_minute = float(start_minute)
        self.step_min = float(step_min)
        self.end_minute = self.start_minute + max(frames) * self.step_min

    def _index(self, minute: float) -> int:
        offset = (float(minute) - self.start_minute) / self.step_min
        index = int(round(offset))
        return min(max(index, 0), max(self.frames))

    def _nearest_key(self, index: int) -> Optional[int]:
        """不超过 index 的最近一帧；稀疏时间线（实时推送）靠它兜底。"""
        if not self.frames:
            return None
        keys = sorted(self.frames)
        position = bisect.bisect_right(keys, index)
        return keys[position - 1] if position else None

    def at(self, minute: float) -> TrafficSnapshot:
        index = self._index(minute)
        if index not in self.frames:
            nearest = self._nearest_key(index)
            if nearest is None:
                raise KeyError("时间线里没有不晚于 %.2f 分钟的帧" % float(minute))
            index = nearest
        return TrafficSnapshot(minute=self.start_minute + index * self.step_min,
                               states=self.frames[index])

    def push(self, minute: float, states: Dict[str, SegmentState],
             merge: bool = True) -> int:
        """实时推送一帧（前端拥堵快照 / 设备回报）。

        稀疏帧也合法：`at()` 会退到"不晚于该分钟"的最近一帧。`merge=True`
        时只覆盖 payload 里出现的路段，其余沿用上一帧——这符合前端只推变化的
        增量数据的实际行为。
        """
        index = int(round((float(minute) - self.start_minute) / self.step_min))
        if index < 0:
            raise ValueError("推送时刻早于时间线起点")
        if not states:
            return 0
        if merge:
            if index in self.frames:
                merged = dict(self.frames[index])
            else:
                # 新的一帧：先继承"最近一帧"的全网状态，再叠本次推送的增量。
                # 前端只推变化的路段，其余路段不能凭空消失（否则快照会缺段）。
                nearest = self._nearest_key(index)
                merged = dict(self.frames[nearest]) if nearest is not None else {}
            merged.update(states)
        else:
            merged = dict(states)
        self.frames[index] = merged
        self.end_minute = max(self.end_minute, self.start_minute + index * self.step_min)
        return len(merged)

    def future_minutes(self, minute: float, horizon_min: float) -> List[int]:
        first = self._index(minute)
        last = self._index(minute + horizon_min)
        return list(range(first, last + 1))


def dijkstra(network: RoadNetwork, origin: str, destination: str,
             weight_fn, blocked: Optional[Iterable[str]] = None
             ) -> Tuple[List[str], float]:
    """通用最短路（非负权）。返回 ``(路段序列, 总代价)``；不可达返回 ``([], inf)``。

    ``weight_fn(segment) -> float`` 由调用方决定口径：自由流时间、碳排放、
    标量化综合成本都行。路由与"进度分档"共用这一个实现，保证口径一致。
    """
    import heapq

    blocked_set = set(blocked or ())
    if origin == destination:
        return [], 0.0
    best: Dict[str, float] = {origin: 0.0}
    prev: Dict[str, Tuple[str, str]] = {}
    heap: List[Tuple[float, str]] = [(0.0, origin)]
    while heap:
        cost, node = heapq.heappop(heap)
        if cost > best.get(node, float("inf")) + 1e-12:
            continue
        if node == destination:
            break
        for sid in network.out_edges(node):
            if sid in blocked_set:
                continue
            segment = network.segment(sid)
            step = float(weight_fn(segment))
            if step < 0:
                raise ValueError("dijkstra 权重不能为负：%s" % sid)
            candidate = cost + step
            if candidate < best.get(segment.v, float("inf")) - 1e-12:
                best[segment.v] = candidate
                prev[segment.v] = (node, sid)
                heapq.heappush(heap, (candidate, segment.v))
    if destination not in best:
        return [], float("inf")
    path: List[str] = []
    node = destination
    while node != origin:
        previous, sid = prev[node]
        path.append(sid)
        node = previous
    path.reverse()
    return path, float(best[destination])


def free_flow_shortest_km(network: RoadNetwork, origin: str, destination: str,
                          blocked: Optional[Iterable[str]] = None) -> float:
    """自由流最短路里程（km）：只用于进度分档，不参与任何碳/时口径。"""
    path, _ = dijkstra(network, origin, destination,
                       lambda seg: seg.length_km, blocked=blocked)
    if not path:
        return 0.0
    return float(sum(network.segment(sid).length_km for sid in path))


__all__ = [
    "CONGESTION_LABELS", "WEATHER_FACTORS", "ROAD_CLASS_DEMAND", "ROAD_CLASS_NAMES",
    "BPR_ALPHA", "BPR_BETA", "MIN_SPEED_KMH", "congestion_label", "demand_scale",
    "RoadSegment", "SegmentState", "TrafficSnapshot", "RoadNetwork",
    "build_demo_network", "TrafficConfig", "TrafficSimulator", "TrafficTimeline",
    "dijkstra", "free_flow_shortest_km",
]
