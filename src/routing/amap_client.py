# -*- coding: utf-8 -*-
"""高德驾车路径规划 2.0：服务端 REST 调用，密钥不进入浏览器。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Sequence

import requests

ROOT = Path(__file__).resolve().parents[2]
KEY_PATH = ROOT / "config" / "amap_key.txt"
DRIVING_V5_URL = "https://restapi.amap.com/v5/direction/driving"
PLACEHOLDER = "请至高德开放平台申请自有 key 并替换此占位符"
TIMEOUT_SECONDS = 15


def load_amap_key() -> str:
    """读取 Web 服务 Key：环境变量优先，其次本地 config/amap_key.txt。"""
    key = (os.environ.get("AMAP_WEB_SERVICE_KEY") or "").strip()
    if not key and KEY_PATH.exists():
        key = KEY_PATH.read_text(encoding="utf-8").strip()
    if not key or key.startswith(PLACEHOLDER):
        return ""
    return key


def _coordinate_text(value: str | Sequence[float], name: str) -> str:
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
    else:
        parts = list(value)
    if len(parts) != 2:
        raise ValueError("%s必须是‘经度,纬度’" % name)
    try:
        lng, lat = float(parts[0]), float(parts[1])
    except (TypeError, ValueError) as exc:
        raise ValueError("%s经纬度必须是数值" % name) from exc
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        raise ValueError("%s经纬度超出有效范围" % name)
    return f"{lng:.6f},{lat:.6f}"


def _number(value: Any, field: str, required: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        if required:
            raise RuntimeError("高德返回缺少有效字段：%s" % field) from exc
        return 0.0
    if required and number <= 0:
        raise RuntimeError("高德返回字段必须大于 0：%s" % field)
    return number


def _parse_polyline(steps: Any) -> List[List[float]]:
    """合并分段 polyline，输出 [[lng, lat], ...] 并去除连续重复点。"""
    points: List[List[float]] = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        for pair in str(step.get("polyline") or "").split(";"):
            parts = pair.split(",")
            if len(parts) != 2:
                continue
            try:
                lng, lat = float(parts[0]), float(parts[1])
            except (TypeError, ValueError):
                continue
            if not (-180 <= lng <= 180 and -90 <= lat <= 90):
                continue
            point = [round(lng, 6), round(lat, 6)]
            if not points or point != points[-1]:
                points.append(point)
    return points


def fetch_driving_routes(origin: str | Sequence[float],
                         destination: str | Sequence[float],
                         api_key: str | None = None,
                         strategy: int = 32) -> List[Dict[str, Any]]:
    """返回高德给出的全部备选路线。

    输入坐标为 GCJ-02 的“经度,纬度”。返回字段保持 API 单位：
    distance=米、duration=秒、tolls=元、traffic_lights=个。
    """
    key = (api_key or load_amap_key()).strip()
    if not key:
        raise ValueError("未配置高德 Web 服务 Key")
    params = {
        "key": key,
        "origin": _coordinate_text(origin, "起点"),
        "destination": _coordinate_text(destination, "终点"),
        "strategy": int(strategy),
        "show_fields": "cost,polyline",
        "output": "json",
    }
    try:
        response = requests.get(DRIVING_V5_URL, params=params, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise RuntimeError("高德路线请求失败：%s" % type(exc).__name__) from exc
    except ValueError as exc:
        raise RuntimeError("高德路线响应不是合法 JSON") from exc

    if str(payload.get("status")) != "1":
        raise RuntimeError("高德路线规划失败：%s（%s）" % (
            payload.get("info") or "未知错误", payload.get("infocode") or "—"))
    paths = (payload.get("route") or {}).get("paths") or []
    routes: List[Dict[str, Any]] = []
    for index, path in enumerate(paths, start=1):
        if not isinstance(path, dict):
            continue
        cost = path.get("cost") or {}
        routes.append({
            "route_id": index,
            "distance": _number(path.get("distance"), "distance", required=True),
            "duration": _number(path.get("duration", cost.get("duration")), "duration", required=True),
            "tolls": _number(path.get("tolls", cost.get("tolls")), "tolls"),
            "traffic_lights": int(_number(
                path.get("traffic_lights", cost.get("traffic_lights")), "traffic_lights")),
            "polyline": _parse_polyline(path.get("steps")),
        })
    if not routes:
        raise RuntimeError("高德未返回可用驾车路线")
    return routes
