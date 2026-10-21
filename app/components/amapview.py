# -*- coding: utf-8 -*-
"""在线地图组件：高德 JS API v2.0 实时路线（路线实验室 / 决策面板共用）。

定位（docs/23 / docs/24 / docs/28）：T7 动态路线的前端渲染层。五条纪律：

  1. 只在 config/amap_js_key.txt 配置了真实 key 时启用，否则本模块返回 False，
     由调用方回退 mapview（Plotly 离线底图）——**key 缺失不是错误，是演示模式**；
  2. 浏览器端零业务计算——碳排/成本/推荐车型全部来自 data/processed/route_*.parquet；
     高德实时规划只提供几何、距离、时长、过路费；
  3. key 只在服务端拼 HTML 时注入，不出现在任何前端源码；
  4. 断网不留灰块——SDK 未加载 / Driving 超时都会在 iframe 内显示可读提示；
  5. 几何使用 AMap.Driving 实时返回的逐点路径（约 0.1 km 一点），
     不再拿 route_od.parquet 里 RDP 1500 上限的缓存折线做主视觉。

坐标一律 GCJ-02（高德原生）。本地 CITY 表是城市级近似坐标，仅用于 Plotly 离线回退。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

from . import route_lab_html

ROOT = Path(__file__).resolve().parents[2]
KEY_PATH = ROOT / "config" / "amap_js_key.txt"
CODE_PATH = ROOT / "config" / "amap_js_code.txt"
PLACEHOLDER = "请至高德开放平台申请自有 key 并替换此占位符"


def _read_secret(path: Path) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    return "" if text.startswith(PLACEHOLDER) else text


def load_js_key() -> str:
    return _read_secret(KEY_PATH)


def load_js_code() -> str:
    return _read_secret(CODE_PATH)


def online_available() -> bool:
    """在线模式开关：key 存在且非占位。"""
    return bool(load_js_key())


def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def _coord(row: Dict[str, Any], prefix: str) -> Optional[List[float]]:
    """从 o_lng/o_lat 或 o=[lng,lat] 里取坐标。"""
    packed = row.get(prefix)
    if isinstance(packed, (list, tuple)) and len(packed) >= 2:
        lng, lat = _num(packed[0]), _num(packed[1])
        if lng is not None and lat is not None and not (lng == 0 and lat == 0):
            return [round(lng, 6), round(lat, 6)]
    lng = _num(row.get(prefix + "_lng"))
    lat = _num(row.get(prefix + "_lat"))
    if lng is None or lat is None or (lng == 0 and lat == 0):
        return None
    return [round(lng, 6), round(lat, 6)]


def _parse_polyline(value: Any) -> List[List[float]]:
    """把 'lng,lat;...' 或 [[lng,lat],...] 统一成点列。仅 compact 模式使用。"""
    if isinstance(value, (list, tuple)):
        points = []
        for item in value:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                lng, lat = _num(item[0]), _num(item[1])
                if lng is not None and lat is not None:
                    points.append([round(lng, 6), round(lat, 6)])
        return points
    if not isinstance(value, str) or not value:
        return []
    points: List[List[float]] = []
    for chunk in value.split(";"):
        if "," not in chunk:
            continue
        lng_text, lat_text = chunk.split(",", 1)
        lng, lat = _num(lng_text), _num(lat_text)
        if lng is not None and lat is not None:
            points.append([round(lng, 6), round(lat, 6)])
    return points


def _pick(row: Dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and row.get(name) is not None:
            return row.get(name)
    return None


def _normalize(row: Any, with_poly: bool = False) -> Optional[Dict[str, Any]]:
    """结果表行 → 浏览器契约。只搬运，不计算业务口径。"""
    try:
        data = dict(row)
    except (TypeError, ValueError):
        return None

    o = _coord(data, "o")
    d = _coord(data, "d")
    if not o or not d:
        return None

    origin = str(_pick(data, "origin", "on", "dep_site") or "")
    dest = str(_pick(data, "dest", "dn", "arr_site") or "")
    od_key = str(_pick(data, "od_key", "od_site", "id") or (origin + "|" + dest))
    if not origin and not dest:
        return None

    km = _num(_pick(data, "km", "distance_km", "route_km"), 0.0) or 0.0
    minutes = _num(_pick(data, "min", "duration_min", "route_min"), 0.0) or 0.0
    toll = _num(_pick(data, "toll", "toll_yuan"), 0.0) or 0.0

    co2 = _num(_pick(data, "co2", "co2_t"))
    if co2 is None:
        kg = _num(_pick(data, "kg_co2_route", "kg_co2"))
        co2 = (kg / 1000.0) if kg is not None else None

    opex = _num(_pick(data, "opex", "opex_yuan"))
    cc = _num(_pick(data, "cc", "carbon_cost_yuan"))
    score = _num(_pick(data, "score", "score_yuan"))
    veh = str(_pick(data, "veh", "vehicle_type") or "")
    load = _num(_pick(data, "load", "load_t"))
    source = str(_pick(data, "src", "source") or "")
    est = bool(int(_num(_pick(data, "est", "flag_route_est"), 0) or 0))

    item: Dict[str, Any] = {
        "id": od_key,
        "on": origin,
        "dn": dest,
        "od": str(_pick(data, "od_pair") or (origin + " → " + dest)),
        "o": o,
        "d": d,
        "km": round(km, 3),
        "min": round(minutes, 1),
        "toll": round(toll, 1),
        "co2": (round(co2, 4) if co2 is not None else None),
        "opex": (round(opex, 1) if opex is not None else None),
        "cc": (round(cc, 1) if cc is not None else None),
        "score": (round(score, 1) if score is not None else None),
        "veh": veh,
        "load": (round(load, 2) if load is not None else None),
        "src": source,
        "est": est,
    }
    if with_poly:
        item["poly"] = _parse_polyline(_pick(data, "polyline", "poly"))
    return item


def route_lab(routes: List[Dict[str, Any]], height: int = 820,
              default_key: Optional[str] = None, lam: float = 100.0,
              candidates: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None) -> bool:
    """第 6 页：路线实验室（左侧线路列表 + 右侧高德实时路线 + 平滑动画）。

    candidates：od_key → {strategy: 同策略碳账}；有值时前端切换策略会同步
    更新碳排/成本/推荐车型，不再使用“综合策略缓存”兜底。
    未配置 key / 没有路线时返回 False，调用方回退离线示意图。
    """
    key = load_js_key()
    if not key or not routes:
        return False
    items: List[Dict[str, Any]] = []
    seen = set()
    for row in routes:
        item = _normalize(row, with_poly=False)
        if not item or item["id"] in seen:
            continue
        seen.add(item["id"])
        if candidates:
            cand = candidates.get(item["id"])
            if cand:
                item["cand"] = {str(k): v for k, v in cand.items()}
        items.append(item)
    if not items:
        return False
    default_id = str(default_key) if default_key else items[0]["id"]
    if default_id not in seen:
        default_id = items[0]["id"]
    payload = {"routes": items, "default": default_id, "lam": float(lam or 100.0)}
    doc = route_lab_html.build_doc(
        payload, key=key, jscode=load_js_code(), mode="lab",
        height=max(480, int(height)), title="路线实验室 · 高德实时路线",
    )
    st.iframe(doc, height=max(480, int(height)))
    return True


def route_map(routes: List[Dict[str, Any]], height: int = 560) -> bool:
    """紧凑模式：单条/少量路线的高德实时图（第 3 页决策面板用）。

    未配置 key 返回 False（调用方回退离线图）。
    """
    key = load_js_key()
    if not key or not routes:
        return False
    items: List[Dict[str, Any]] = []
    for row in routes[:4]:
        item = _normalize(row, with_poly=True)
        if item:
            items.append(item)
    if not items:
        return False
    payload = {"routes": items, "default": items[0]["id"], "lam": 100.0}
    doc = route_lab_html.build_doc(
        payload, key=key, jscode=load_js_code(), mode="compact",
        height=max(360, int(height)), title="路线图 · 高德实时路线",
    )
    st.iframe(doc, height=max(360, int(height)))
    return True


def route_map_single(route: Dict[str, Any], height: int = 520) -> bool:
    """单条路线的地图（评委点一条线就出一张图）。"""
    return route_map([route], height=height)
