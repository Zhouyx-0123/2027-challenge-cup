# -*- coding: utf-8 -*-
"""地点名称解析：地址 / POI / 车站 → GCJ-02 坐标。"""
from __future__ import annotations

from typing import Any, Dict

import requests

from .amap_client import load_amap_key

GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
TIMEOUT_SECONDS = 15


def _coordinate_pair(value: str) -> tuple[float, float] | None:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 2:
        return None
    try:
        lng, lat = float(parts[0]), float(parts[1])
    except (TypeError, ValueError):
        return None
    if -180 <= lng <= 180 and -90 <= lat <= 90:
        return lng, lat
    return None


def geocode_place(query: str, city: str = "", api_key: str | None = None) -> Dict[str, Any]:
    """把一个地点名解析为地图可用坐标。

    直接输入“经度,纬度”时跳过网络请求，便于调试和兼容旧用法。
    """
    text = str(query or "").strip()
    if not text:
        raise ValueError("请输入起点或终点地点")
    if len(text) > 160:
        raise ValueError("地点名称过长，请压缩到 160 个字符以内")

    direct = _coordinate_pair(text)
    if direct:
        lng, lat = direct
        return {
            "query": text, "label": "自定义坐标", "location": f"{lng:.6f},{lat:.6f}",
            "lng": lng, "lat": lat, "city": "", "district": "",
            "formatted_address": text, "level": "坐标", "source": "coordinate",
        }

    key = (api_key or load_amap_key()).strip()
    if not key:
        raise ValueError("未配置高德 Web 服务 Key，无法解析地点")
    params = {"key": key, "address": text, "output": "JSON"}
    if city.strip():
        params["city"] = city.strip()
    try:
        response = requests.get(GEOCODE_URL, params=params, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise RuntimeError("地点解析请求失败：%s" % type(exc).__name__) from exc
    except ValueError as exc:
        raise RuntimeError("地点解析响应不是合法 JSON") from exc

    if str(payload.get("status")) != "1":
        raise RuntimeError("地点解析失败：%s（%s）" % (
            payload.get("info") or "未知错误", payload.get("infocode") or "—"))
    rows = payload.get("geocodes") or []
    if not rows:
        raise ValueError("没有找到地点“%s”，请补充城市或输入更完整的地址" % text)
    match = rows[0]
    pair = _coordinate_pair(str(match.get("location") or ""))
    if not pair:
        raise RuntimeError("地点“%s”缺少有效坐标" % text)
    lng, lat = pair
    return {
        "query": text,
        "label": text,
        "location": f"{lng:.6f},{lat:.6f}",
        "lng": lng,
        "lat": lat,
        "city": match.get("city") or match.get("province") or "",
        "district": match.get("district") or "",
        "formatted_address": match.get("formatted_address") or text,
        "level": match.get("level") or "",
        "source": "geocode",
    }


__all__ = ["GEOCODE_URL", "geocode_place"]
