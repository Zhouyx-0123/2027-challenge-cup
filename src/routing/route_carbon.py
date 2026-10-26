# -*- coding: utf-8 -*-
"""路线沙盘碳核算：距离 + 估算拥堵系数 -> 既有 calc_carbon 模型。"""
from __future__ import annotations

from typing import Any, Dict, List

from src.invoice import calc_carbon

FREE_FLOW_SPEED_KMH = 60.0
MAX_CONGESTION_FACTOR = 1.5


def calc_carbon_for_route(route: Dict[str, Any], truck_type: str,
                          load: float) -> Dict[str, Any]:
    """计算单条路线的碳排及中间指标。

    route 使用高德 API 单位：distance(米)、duration(秒)、tolls(元)。
    拥堵系数 = clip(60 / 实际均速, 1.0, 1.5)，是沙盘估算假设，不是官方因子。
    """
    distance_m = float(route.get("distance", 0.0))
    duration_s = float(route.get("duration", 0.0))
    load_t = float(load)
    if distance_m <= 0 or duration_s <= 0:
        raise ValueError("路线 distance 和 duration 必须大于 0")
    if not 0 <= load_t <= 60:
        raise ValueError("载重必须在 0–60 吨之间")

    distance_km = distance_m / 1000.0
    duration_min = duration_s / 60.0
    speed_kmh = distance_m / duration_s * 3.6
    congestion_factor = min(
        max(FREE_FLOW_SPEED_KMH / speed_kmh, 1.0), MAX_CONGESTION_FACTOR)
    base_carbon_kg = calc_carbon(load_t, distance_km, truck_type=truck_type)
    carbon_kg = base_carbon_kg * congestion_factor
    return {
        "route_id": int(route.get("route_id", 1)),
        "distance_km": round(distance_km, 3),
        "duration_min": round(duration_min, 2),
        "tolls": round(float(route.get("tolls") or 0.0), 2),
        "traffic_lights": int(float(route.get("traffic_lights") or 0)),
        "avg_speed_kmh": round(speed_kmh, 2),
        "congestion_factor": round(congestion_factor, 4),
        "base_carbon_kg": round(base_carbon_kg, 3),
        "carbon_kg": round(carbon_kg, 3),
        "polyline": route.get("polyline") or [],
    }


def evaluate_routes(routes: List[Dict[str, Any]], truck_type: str, load: float,
                    carbon_price: float, energy_cost_per_km: float = 0.0) -> List[Dict[str, Any]]:
    """批量计算路线碳账；综合成本 = 过路费 + 能耗成本 + 碳成本。"""
    price = float(carbon_price)
    energy_rate = float(energy_cost_per_km)
    if price < 0 or energy_rate < 0:
        raise ValueError("碳价和能耗成本不能为负数")
    evaluated: List[Dict[str, Any]] = []
    for route in routes:
        item = calc_carbon_for_route(route, truck_type, load)
        item["energy_cost_yuan"] = round(item["distance_km"] * energy_rate, 2)
        item["carbon_cost_yuan"] = round(item["carbon_kg"] / 1000.0 * price, 2)
        item["total_cost_yuan"] = round(
            item["tolls"] + item["energy_cost_yuan"] + item["carbon_cost_yuan"], 2)
        item["route"] = "路线 %d" % item["route_id"]
        evaluated.append(item)
    return evaluated


def as_route_choices(routes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """未开启进化时的利用基线：统一输出字段，仅推荐最低成本路线。"""
    if not routes:
        return []
    ordered = sorted(routes, key=lambda item: item["total_cost_yuan"])
    choices = []
    for index, route in enumerate(ordered):
        choices.append({
            "route_id": str(route["route_id"]),
            "distance": float(route["distance_km"]),
            "duration": float(route["duration_min"]),
            "tolls": float(route["tolls"]),
            "carbon": float(route["carbon_kg"]),
            "cost": float(route["total_cost_yuan"]),
            "is_exploration": False,
            "is_recommended": index == 0,
            "exploration_strategy": "exploitation" if index == 0 else "initial",
        })
    return choices
