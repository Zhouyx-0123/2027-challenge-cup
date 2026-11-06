# -*- coding: utf-8 -*-
"""把高德缓存 polyline + 四策略指标搭成 Q-learning 可走的真实路网。

重要边界
--------
- 几何来自 route_od.parquet 的高德实算 polyline，不是合成 4x4 网。
- 四条策略各自是一条平行链，共享同一份高德主路线几何；策略指标
  （里程/时长/收费）来自 route_candidates.parquet 的高德实算。
- 链路分段按里程等分，段速用整条路线的 AMap 均速；没有逐段实测速度，
  所以在 UI 和文档里必须写明“段速=整条路线均速”。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import asin, cos, radians, sin, sqrt
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from .network import (ROAD_CLASS_NAMES, RoadNetwork, RoadSegment, SegmentState,
                      TrafficTimeline)

ORIGIN_NODE = "AMAP_ORIGIN"
DEST_NODE = "AMAP_DEST"
MAX_SEGMENTS = 40
MIN_SEGMENTS = 6

ROAD_FREE_SPEED = {1: 90.0, 2: 70.0, 3: 50.0, 4: 35.0}
ROAD_CAPACITY = {1: 2200.0, 2: 1800.0, 3: 1400.0, 4: 900.0}


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def parse_polyline(payload: Any) -> List[List[float]]:
    """解析高德 polyline：支持 'lng,lat;lng,lat' 与 [[lng,lat], ...]。"""
    points: List[List[float]] = []
    if isinstance(payload, str):
        for chunk in payload.split(";"):
            chunk = chunk.strip()
            if not chunk:
                continue
            parts = chunk.split(",")
            if len(parts) < 2:
                continue
            lng, lat = _as_float(parts[0]), _as_float(parts[1])
            if -180 <= lng <= 180 and -90 <= lat <= 90:
                points.append([round(lng, 6), round(lat, 6)])
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                lng, lat = _as_float(item[0]), _as_float(item[1])
                if -180 <= lng <= 180 and -90 <= lat <= 90:
                    points.append([round(lng, 6), round(lat, 6)])
    clean: List[List[float]] = []
    for point in points:
        if not clean or point != clean[-1]:
            clean.append(point)
    return clean


def haversine_km(a: Sequence[float], b: Sequence[float]) -> float:
    lng1, lat1 = radians(float(a[0])), radians(float(a[1]))
    lng2, lat2 = radians(float(b[0])), radians(float(b[1]))
    dlat, dlng = lat2 - lat1, lng2 - lng1
    h = sin(dlat / 2.0) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2.0) ** 2
    return 2.0 * 6371.0088 * asin(min(1.0, sqrt(h)))


def _split_bounds(points: Sequence[Sequence[float]], n_segments: int) -> List[int]:
    """按累计球面距离把折线切成 n 段，返回 n+1 个点下标。"""
    if len(points) < 2:
        raise ValueError("polyline 至少需要两个点")
    n_segments = max(1, min(int(n_segments), len(points) - 1))
    cumulative = [0.0]
    for prev, cur in zip(points[:-1], points[1:]):
        cumulative.append(cumulative[-1] + haversine_km(prev, cur))
    total = cumulative[-1]
    if total <= 0:
        step = max(1, (len(points) - 1) // n_segments)
        bounds = [min(len(points) - 1, i * step) for i in range(n_segments)]
        bounds.append(len(points) - 1)
        return bounds
    bounds = [0]
    cursor = 0
    for k in range(1, n_segments):
        target = total * k / n_segments
        while cursor + 1 < len(cumulative) and cumulative[cursor + 1] < target:
            cursor += 1
        idx = min(len(points) - 1, cursor + (0 if cumulative[cursor] >= target else 1))
        if idx <= bounds[-1]:
            idx = min(len(points) - 1, bounds[-1] + 1)
        bounds.append(idx)
    bounds.append(len(points) - 1)
    # 若点太少导致重复，退化为等差下标
    if len(set(bounds)) < len(bounds):
        return [round(i * (len(points) - 1) / n_segments) for i in range(n_segments + 1)]
    return bounds


def road_class_for_speed(avg_speed_kmh: float) -> int:
    if avg_speed_kmh >= 65:
        return 1
    if avg_speed_kmh >= 45:
        return 2
    if avg_speed_kmh >= 25:
        return 3
    return 4


@dataclass(frozen=True)
class RealRoute:
    route_id: str
    strategy: int
    strategy_name: str
    distance_km: float
    duration_min: float
    toll_yuan: float
    traffic_lights: int
    avg_speed_kmh: float
    road_class: int
    n_segments: int
    source: str = "amap_rest"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "route_id": self.route_id, "strategy": self.strategy,
            "strategy_name": self.strategy_name,
            "distance_km": round(self.distance_km, 3),
            "duration_min": round(self.duration_min, 3),
            "toll_yuan": round(self.toll_yuan, 2),
            "traffic_lights": int(self.traffic_lights),
            "avg_speed_kmh": round(self.avg_speed_kmh, 3),
            "road_class": int(self.road_class),
            "road_class_name": ROAD_CLASS_NAMES.get(int(self.road_class), "?"),
            "n_segments": int(self.n_segments),
            "source": self.source,
        }


@dataclass
class RealRouteNetwork:
    network: RoadNetwork
    timeline: TrafficTimeline
    routes: Tuple[RealRoute, ...]
    path_segments: Dict[str, List[str]]
    segment_points: Dict[str, List[List[float]]]
    segment_meta: Dict[str, Dict[str, Any]]
    points: List[List[float]]
    origin_xy: Tuple[float, float]
    dest_xy: Tuple[float, float]
    departure_minute: float
    n_segments: int
    meta: Dict[str, Any] = field(default_factory=dict)

    def route(self, route_id: str) -> RealRoute:
        for item in self.routes:
            if item.route_id == route_id:
                return item
        raise KeyError("未知路线：%s" % route_id)

    def route_of_path(self, path: Sequence[str]) -> str:
        for sid in path:
            for route_id, sids in self.path_segments.items():
                if sid in sids:
                    return route_id
        raise KeyError("路径不属于任何候选路线")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "departure_minute": float(self.departure_minute),
            "origin": [round(self.origin_xy[0], 6), round(self.origin_xy[1], 6)],
            "destination": [round(self.dest_xy[0], 6), round(self.dest_xy[1], 6)],
            "n_segments": int(self.n_segments),
            "routes": [r.to_dict() for r in self.routes],
            "meta": dict(self.meta),
        }


def build_real_network(points: Any,
                       origin_xy: Sequence[float],
                       dest_xy: Sequence[float],
                       candidates: Iterable[Any],
                       departure_minute: float = 480.0,
                       max_segments: int = MAX_SEGMENTS) -> RealRouteNetwork:
    """按四条高德策略指标建平行链式路网。"""
    clean = parse_polyline(points)
    if len(clean) < 2:
        raise ValueError("高德 polyline 不足两个点，无法建网")
    origin = (float(origin_xy[0]), float(origin_xy[1]))
    dest = (float(dest_xy[0]), float(dest_xy[1]))
    if not (-180 <= origin[0] <= 180 and -90 <= origin[1] <= 90):
        raise ValueError("起点坐标非法")
    if not (-180 <= dest[0] <= 180 and -90 <= dest[1] <= 90):
        raise ValueError("终点坐标非法")

    options: List[Dict[str, Any]] = []
    for item in candidates:
        row = item.to_dict() if hasattr(item, "to_dict") else dict(item)
        distance = float(row.get("distance_km") or 0.0)
        duration = float(row.get("duration_min") or 0.0)
        if not (distance > 0 and duration > 0):
            continue
        options.append(row)
    if not options:
        raise ValueError("没有可用的高德策略指标")

    shortest_km = min(float(row["distance_km"]) for row in options)
    n_segments = max(MIN_SEGMENTS, min(int(max_segments), int(round(shortest_km / 4.0))))
    n_segments = max(1, min(n_segments, len(clean) - 1))
    bounds = _split_bounds(clean, n_segments)

    segments: List[RoadSegment] = []
    routes: List[RealRoute] = []
    path_segments: Dict[str, List[str]] = {}
    segment_points: Dict[str, List[List[float]]] = {}
    segment_meta: Dict[str, Dict[str, Any]] = {}
    states: Dict[str, SegmentState] = {}

    for order, row in enumerate(options):
        strategy = int(row.get("strategy", order))
        route_id = "AMAP-S%d" % strategy
        distance = float(row["distance_km"])
        duration = float(row["duration_min"])
        toll = max(0.0, float(row.get("toll_yuan") or 0.0))
        traffic_lights = int(row.get("traffic_lights") or 0)
        avg_speed = distance / (duration / 60.0)
        road_class = road_class_for_speed(avg_speed)
        free_speed = max(float(ROAD_FREE_SPEED[road_class]), avg_speed * 1.05)
        capacity = float(ROAD_CAPACITY[road_class])
        vc_ratio = max(0.0, min(1.0, 1.0 - avg_speed / free_speed))
        path: List[str] = []
        for index in range(n_segments):
            sid = "%s-N%02d" % (route_id, index)
            start, end = bounds[index], bounds[index + 1]
            piece = [list(p) for p in clean[start:end + 1]]
            if len(piece) < 2:
                piece = [list(clean[start]), list(clean[min(end + 1, len(clean) - 1)])]
            u = ORIGIN_NODE if index == 0 else "%s-NODE%02d" % (route_id, index - 1)
            v = DEST_NODE if index == n_segments - 1 else "%s-NODE%02d" % (route_id, index)
            segments.append(RoadSegment(
                sid=sid, u=u, v=v, length_m=distance * 1000.0 / n_segments,
                road_class=road_class, free_speed_kmh=free_speed,
                capacity_vph=capacity, grade_pct=0.0, toll_yuan=toll / n_segments))
            path.append(sid)
            segment_points[sid] = piece
            segment_meta[sid] = {
                "route_id": route_id, "strategy": strategy,
                "index": index, "n_segments": n_segments,
                "from_km": round(distance * index / n_segments, 3),
                "to_km": round(distance * (index + 1) / n_segments, 3),
                "road_class": road_class,
            }
            states[sid] = SegmentState(
                sid=sid, minute=float(departure_minute), speed_kmh=avg_speed,
                vc_ratio=vc_ratio, free_speed_kmh=free_speed)
        path_segments[route_id] = path
        routes.append(RealRoute(
            route_id=route_id, strategy=strategy,
            strategy_name=str(row.get("strategy_name") or ("策略%d" % strategy)),
            distance_km=distance, duration_min=duration, toll_yuan=toll,
            traffic_lights=traffic_lights, avg_speed_kmh=avg_speed,
            road_class=road_class, n_segments=n_segments,
            source=str(row.get("source") or "amap_rest")))

    if not segments:
        raise ValueError("路网没有路段")
    network = RoadNetwork(segments)
    timeline = TrafficTimeline(network, {0: states}, start_minute=float(departure_minute))
    meta = {
        "geometry_source": "amap_cached_polyline",
        "geometry_shared": True,
        "segment_speed": "整条路线 AMap 均速，非逐段实测",
        "toll_allocation": "按段数等分",
        "grade_pct": 0.0,
        "n_candidates": len(routes),
        "n_segments_per_route": n_segments,
        "note": "四条策略是平行链；几何共享高德主路线，指标来自高德四策略实算。",
    }
    return RealRouteNetwork(
        network=network, timeline=timeline, routes=tuple(routes),
        path_segments=path_segments, segment_points=segment_points,
        segment_meta=segment_meta, points=clean, origin_xy=origin, dest_xy=dest,
        departure_minute=float(departure_minute), n_segments=n_segments, meta=meta)


__all__ = ["parse_polyline", "haversine_km", "road_class_for_speed", "RealRoute",
           "RealRouteNetwork", "build_real_network", "ORIGIN_NODE", "DEST_NODE"]
