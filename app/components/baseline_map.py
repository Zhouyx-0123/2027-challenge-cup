# -*- coding: utf-8 -*-
"""D6 · 基线地图的 payload 组装：steps.parquet → OpenLayers 组件入参。

纪律（docs/35 §9）
------------------
* **只搬运，不计算**：距离/时长/碳排/占比全部来自结果表，
  这里只做"表行 → 前端 feature"的搬运与着色；
* 几何是 step 自带的 GeoJSON（WGS-84），**前端不再做任何坐标转换**；
* 底图选天地图 WMTS（CGCS2000，与 WGS-84 同基准，叠加零偏移）——
  **绝不能配高德底图**，那是 GCJ-02，会偏 500–800 m。

参考：docs/35_上海北京基线_任务书.md §5.1 / D6。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pandas as pd

from . import olmapview

#: 各级道路在地图上的线宽（像素）
_WIDTHS = {
    "motorway": 6.0,
    "trunk": 4.6,
    "secondary": 3.4,
    "urban": 2.8,
    "local": 2.2,
    "ramp": 2.4,
    "unknown": 2.2,
}


def _parse_geometry(value: Any) -> Optional[Dict[str, Any]]:
    """step 的 geometry_geojson 字符串 → dict。失败返回 None（跳过该 step）。"""
    if not value:
        return None
    if isinstance(value, dict):
        return value
    try:
        geom = json.loads(str(value))
    except (TypeError, ValueError):
        return None
    if not isinstance(geom, dict) or geom.get("type") != "LineString":
        return None
    coords = geom.get("coordinates") or []
    if len(coords) < 2:
        return None
    return geom


def steps_to_features(steps: pd.DataFrame) -> List[Dict[str, Any]]:
    """逐 step 表 → OpenLayers feature 列表（按道路等级着色）。"""
    features: List[Dict[str, Any]] = []
    if steps is None or steps.empty:
        return features
    for _, row in steps.iterrows():
        geom = _parse_geometry(row.get("geometry_geojson"))
        if geom is None:
            continue
        klass = str(row.get("road_class") or "unknown")
        features.append({
            "id": int(row.get("step_index", len(features))),
            "geometry": geom,
            "road_class": klass,
            "color": olmapview.ROAD_COLORS.get(klass, "#5D6A78"),
            "width": _WIDTHS.get(klass, 2.0),
            "opacity": 0.20,
        })
    return features


def steps_to_list(steps: pd.DataFrame) -> List[Dict[str, Any]]:
    """逐 step 表 → 右侧列表条目（只搬运已算好的数字）。"""
    out: List[Dict[str, Any]] = []
    if steps is None or steps.empty:
        return out
    for _, row in steps.iterrows():
        out.append({
            "id": int(row.get("step_index", len(out))),
            "road_name": str(row.get("road_name") or ""),
            "road_ref": str(row.get("road_ref") or ""),
            "road_class": str(row.get("road_class") or "unknown"),
            "road_class_confidence": str(row.get("road_class_confidence") or ""),
            "distance_km": float(row.get("distance_km") or 0.0),
            "duration_min": float(row.get("duration_min") or 0.0),
            "speed_kmh": float(row.get("speed_kmh") or 0.0),
            "carbon_kg": float(row.get("carbon_kg") or 0.0),
        })
    return out


def route_center(steps: pd.DataFrame) -> List[float]:
    """路线中心点（WGS-84 经纬）。取所有 step 几何点的中位数，避免被长段拉偏。"""
    xs: List[float] = []
    ys: List[float] = []
    if steps is not None and not steps.empty:
        for _, row in steps.iterrows():
            geom = _parse_geometry(row.get("geometry_geojson"))
            if not geom:
                continue
            for lng, lat in geom.get("coordinates", [])[::10]:
                xs.append(float(lng))
                ys.append(float(lat))
    if not xs:
        return [116.66, 35.46]
    xs.sort()
    ys.sort()
    return [xs[len(xs) // 2], ys[len(ys) // 2]]


def build_baseline_payload(steps: pd.DataFrame, summary: Dict[str, Any],
                           layers: Optional[List[Dict[str, Any]]] = None,
                           zoom: float = 6.0) -> Dict[str, Any]:
    """组装基线地图 payload。

    layers 传入 WMS/WMTS 底图配置；不传则由调用方决定是否用天地图。
    """
    features = steps_to_features(steps)
    payload = olmapview.build_payload(
        features=features,
        layers=layers or [],
        center=route_center(steps),
        zoom=zoom,
        attribution=str(summary.get("attribution") or ""),
    )
    payload["steps"] = steps_to_list(steps)
    payload["summary"] = summary
    payload["road_class_share"] = summary.get("road_class_share") or {}
    return payload
