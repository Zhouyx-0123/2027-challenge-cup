# -*- coding: utf-8 -*-
"""D6 · 基线地图的 payload 组装：steps.parquet → OpenLayers 组件入参。

纪律（docs/35 §9）
------------------
* **只搬运，不计算**：距离/时长/碳排/占比全部来自结果表，
  这里只做"表行 → 前端 feature"的搬运与着色；
* **数值一个字节都不动**：坐标基准转换只改几何坐标，
  distance/duration/carbon 原样透传；
* 底图选谁由 :mod:`app.components.basemap_align` 决定：
  天地图（CGCS2000）→ 原样；高德（GCJ-02）→ 几何转 GCJ-02。

参考：docs/35_上海北京基线_任务书.md §5.1 / D6。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from . import basemap_align, olmapview, route_visual


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


def steps_to_features(steps: pd.DataFrame,
                      geom_crs: str = basemap_align.CRS_WGS84) -> List[Dict[str, Any]]:
    """逐 step 表 → OpenLayers feature 列表（按道路等级着色 + 视觉层次）。

    :param geom_crs: 目标基准。与源基准不同时**在这里**完成坐标转换
        （服务端转换，前端零计算），且只改几何、不改任何数值。
    """
    features: List[Dict[str, Any]] = []
    if steps is None or steps.empty:
        return features
    for _, row in steps.iterrows():
        geom = _parse_geometry(row.get("geometry_geojson"))
        if geom is None:
            continue
        klass = str(row.get("road_class") or "unknown")
        distance = float(row.get("distance_km") or 0.0)
        color = olmapview.ROAD_COLORS.get(klass, "#5D6A78")
        style = route_visual.feature_style(klass, distance, color)
        features.append({
            "id": int(row.get("step_index", len(features))),
            "geometry": geom,
            "road_class": klass,
            "road_name": str(row.get("road_name") or ""),
            "road_ref": str(row.get("road_ref") or ""),
            "color": color,
            "width": style["width"],
            "halo": style["halo"],
            "haloColor": style["haloColor"],
            "opacity": style["opacity"],
            "distance_km": distance,
            "speed_kmh": float(row.get("speed_kmh") or 0.0),
            "duration_min": float(row.get("duration_min") or 0.0),
            "carbon_kg": float(row.get("carbon_kg") or 0.0),
        })
    return basemap_align.convert_features(
        features, src=basemap_align.CRS_WGS84, dst=geom_crs)


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


def route_markers(steps: pd.DataFrame,
                  geom_crs: str = basemap_align.CRS_WGS84) -> List[Dict[str, Any]]:
    """起终点 marker（高德那张图有，这张原来没有 —— 缺了就像"一条线"）。

    起终点取**整条路线几何的首尾点**，不是首尾 step 的首尾点，
    这样和路线端点严格一致。
    """
    if steps is None or steps.empty:
        return []
    coords: List[List[float]] = []
    for _, row in steps.iterrows():
        geom = _parse_geometry(row.get("geometry_geojson"))
        if not geom:
            continue
        pts = geom.get("coordinates") or []
        if not coords:
            coords.append(list(pts[0]))
        coords.append(list(pts[-1]))
    if len(coords) < 2:
        return []
    start, end = coords[0], coords[-1]
    if geom_crs == basemap_align.CRS_GCJ02:
        start = list(basemap_align.wgs84_to_gcj02(start[0], start[1]))
        end = list(basemap_align.wgs84_to_gcj02(end[0], end[1]))
    return [
        dict(route_visual.marker_style("start"),
             lng=start[0], lat=start[1], label="起", kind="start"),
        dict(route_visual.marker_style("end"),
             lng=end[0], lat=end[1], label="终", kind="end"),
    ]


def _segments_for_annotations(steps: pd.DataFrame) -> List[Dict[str, Any]]:
    """抽出做标注用的路段序列（几何 + 名称 + 里程 + 等级）。

    只搬运 parquet 里已有的字段，不算任何新指标。
    """
    out: List[Dict[str, Any]] = []
    if steps is None or steps.empty:
        return out
    for _, row in steps.iterrows():
        geom = _parse_geometry(row.get("geometry_geojson"))
        if geom is None:
            continue
        out.append({
            "step_index": int(row.get("step_index", len(out))),
            "geometry": geom.get("coordinates") or [],
            "distance_km": float(row.get("distance_km") or 0.0),
            "road_name": str(row.get("road_name") or ""),
            "road_ref": str(row.get("road_ref") or ""),
            "road_class": str(row.get("road_class") or "unknown"),
        })
    return out


def _convert_points(points: Sequence[Dict[str, Any]],
                    geom_crs: str) -> List[Dict[str, Any]]:
    """把 {lng, lat} 点集按目标基准转换（只改坐标）。"""
    if geom_crs == basemap_align.CRS_GCJ02:
        out: List[Dict[str, Any]] = []
        for p in points:
            lng, lat = basemap_align.wgs84_to_gcj02(
                float(p["lng"]), float(p["lat"]))
            item = dict(p)
            item["lng"] = round(lng, 6)
            item["lat"] = round(lat, 6)
            out.append(item)
        return out
    return [dict(p) for p in points]


def build_baseline_payload(steps: pd.DataFrame, summary: Dict[str, Any],
                           layers: Optional[List[Dict[str, Any]]] = None,
                           zoom: float = 6.0) -> Dict[str, Any]:
    """组装基线地图 payload。

    ``layers`` 传入 WMS/WMTS/XYZ 底图配置；坐标基准由图层自动推断。
    """
    layers = list(layers or [])
    geom_crs = basemap_align.geometry_crs_for(layers)
    # 给每层带上基准标记，前端切换器会显示它
    for layer in layers:
        layer.setdefault("crs", basemap_align.layer_crs(layer))

    features = steps_to_features(steps, geom_crs=geom_crs)
    payload = olmapview.build_payload(
        features=features,
        layers=layers,
        center=route_center(steps),
        zoom=zoom,
        attribution=str(summary.get("attribution") or ""),
        geom_crs=geom_crs,
        markers=route_markers(steps, geom_crs=geom_crs),
    )
    payload["steps"] = steps_to_list(steps)
    payload["summary"] = summary
    payload["road_class_share"] = summary.get("road_class_share") or {}

    # 里程刻度 + 道路名标注（服务端算，前端只画）
    # 注意 key 命名：**不能叫 "labels"** —— 前端 payload 里 "labels" 已经
    # 用来存"道路等级 → 中文名"的映射（图例要用）。撞名会让图例退化成英文。
    # 这里用 "roadNames" 存路名标注，"mileage" 存里程刻度。
    segments = _segments_for_annotations(steps)
    payload["mileage"] = _convert_points(
        route_visual.mileage_ticks(segments), geom_crs)
    payload["roadNames"] = _convert_points(
        route_visual.road_labels(segments), geom_crs)
    return payload
