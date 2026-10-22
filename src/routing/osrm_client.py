# -*- coding: utf-8 -*-
"""路线 Provider：接口 + OSRM 公共服务实现（spike 级）。

接口（docs/35 §4.3）
-------------------
    class RouteProvider:
        def route(self, origin_wgs, dest_wgs, alternatives=2) -> RouteResult: ...

第一版只实现 :class:`OsrmProvider`；正式版换本地 GraphHopper 时，
新增 ``GraphHopperProvider`` 复用同一个 :class:`RouteResult`，**上层不用改**。

RouteResult 字段（docs/35 §4.3 最低要求 + 本实现补充）
------------------------------------------------------
==================== ==========================================================
字段                  说明
==================== ==========================================================
provider             ``osrm_public`` / ``graphhopper_local`` …
traffic_mode         ``freeflow``（无实时路况）——**恒定值，不是可选项**
distance_km          路线总距离（km）
duration_min         路线总时长（min）
geometry_geojson     整条路线的 GeoJSON LineString（WGS-84）
steps                逐 step 列表，每项含 name/ref/距离/时长/速度/道路等级
points               路线几何点数（summary 里的 points）
==================== ==========================================================

端点吸附校验
------------
:class:`OsrmProvider` 请求前会调用 OSRM 的 ``/nearest``，把请求点吸附到
真实路网节点上，并记录**吸附距离**。这是"端点误差"的**唯一有效判据**：

* 端点必须在 OSM 真实路网上（吸附距离 < 50 m，docs/35 §2）；
* 判据不是"与某个写死的坐标一致"——写死的坐标来自实现细节，
  一旦转换实现升级就会漂移，而"是否落在真实路网上"是客观事实。

实测（上海车墩 → 北京通州）：两端吸附距离均在 30 m 以内。

红线（docs/35 §9）
------------------
1. 公共 OSRM 只用于 spike，不写进生产循环；
2. 不读 ``config/amap_*``，不调 AMap，不爬高德瓦片；
3. 原始响应落 ``outputs/route_baseline/raw_osrm.json`` 便于复核，但**不进 Git**。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from . import coord
from .road_class import classify_road

#: 公共服务端点。仅 spike 用；正式版换本地实例（改这一个常量即可）。
OSRM_PUBLIC_URL = "https://router.project-osrm.org"

#: 请求超时（秒）。OSRM 公共实例算 1,200 km 长距离路线实测 20–25 s。
DEFAULT_TIMEOUT = 120

#: 端点吸附距离上限（米）。docs/35 §2：端点误差 > 50 m 视为失败。
SNAP_TOLERANCE_M = 50.0

#: 公共实例限流很敏感，串行 + 退避。
_RETRY_BACKOFF = (5, 15, 40)


@dataclass
class RouteStep:
    """一个 OSRM step（docs/35 §5.1 的行级来源）。"""

    step_index: int
    distance_km: float
    duration_min: float
    speed_kmh: float
    road_name: str
    road_ref: str
    road_class: str
    road_class_source: str
    road_class_confidence: str
    geometry_geojson: str
    maneuver_type: str = ""
    toll_flag: Optional[int] = None
    toll_yuan: Optional[float] = None
    traffic_lights: Optional[int] = None


@dataclass
class RouteResult:
    """一条路线的完整结果。"""

    provider: str
    traffic_mode: str
    distance_km: float
    duration_min: float
    geometry_geojson: str
    steps: List[RouteStep] = field(default_factory=list)
    points: int = 0
    origin_wgs: Tuple[float, float] = (0.0, 0.0)
    dest_wgs: Tuple[float, float] = (0.0, 0.0)
    snap_distance_m: Dict[str, float] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    def step_distance_sum(self) -> float:
        return sum(s.distance_km for s in self.steps)

    def step_duration_sum(self) -> float:
        return sum(s.duration_min for s in self.steps)


class RouteProvider:
    """路线 Provider 抽象基类。

    抽象成类是给正式版留位：本地 GraphHopper 只需实现同名方法，
    上层（tools/route_baseline.py、页面）一行不用改。
    """

    name = "abstract"

    def route(self, origin_wgs: Tuple[float, float],
              dest_wgs: Tuple[float, float],
              alternatives: int = 2) -> RouteResult:
        raise NotImplementedError

    @staticmethod
    def _parse_coord(text: str) -> Tuple[float, float]:
        """解析 "lng,lat"。"""
        parts = str(text).replace("，", ",").split(",")
        if len(parts) < 2:
            raise ValueError("坐标格式应为 'lng,lat'，收到: %r" % text)
        return float(parts[0]), float(parts[1])


class OsrmProvider(RouteProvider):
    """OSRM 公共服务（spike 级）。

    参数
    ----
    base_url:
        OSRM 实例地址。默认公共实例；正式版指向本地 ``localhost:5000``。
    timeout:
        单次请求超时（秒）。
    user_agent:
        公共实例要求带 UA，避免被当成爬虫拒。
    """

    name = "osrm_public"

    def __init__(self, base_url: str = OSRM_PUBLIC_URL,
                 timeout: int = DEFAULT_TIMEOUT,
                 user_agent: str = "carbon-tide-baseline/1.0") -> None:
        self.base_url = str(base_url).rstrip("/")
        self.timeout = int(timeout)
        self.user_agent = user_agent

    # ---- HTTP ----

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = self.base_url + path
        last_error = ""
        for attempt, wait in enumerate((0,) + _RETRY_BACKOFF):
            if wait:
                time.sleep(wait)
            try:
                resp = requests.get(url, params=params, timeout=self.timeout,
                                    headers={"User-Agent": self.user_agent})
            except requests.RequestException as exc:
                last_error = "网络异常: %s" % exc
                continue
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError:
                    last_error = "响应不是合法 JSON"
                    continue
            if resp.status_code == 429:  # 公共实例限流
                last_error = "被限流 (429)"
                continue
            last_error = "HTTP %d: %s" % (resp.status_code, resp.text[:200])
            break
        raise RuntimeError("OSRM 请求失败 (%s): %s" % (url, last_error))

    # ---- 端点吸附 ----

    def snap_to_road(self, point_wgs: Tuple[float, float]) -> Tuple[float, float, float]:
        """把 WGS-84 点吸附到 OSM 最近路网节点。

        返回 ``(吸附后 lng, 吸附后 lat, 吸附距离米)``。
        OSRM 会自动把 route 请求的端点吸附到最近道路，但那个吸附是**隐式**的，
        我们显式做一遍，才能把"端点误差"变成一个可验收的数字。
        """
        lng, lat = point_wgs
        data = self._get("/nearest/v1/driving/%s" % ("%f,%f" % (lng, lat)))
        waypoints = data.get("waypoints") or []
        if not waypoints:
            raise RuntimeError("OSRM nearest 无结果: %s" % data.get("code"))
        wp = waypoints[0]
        return float(wp["location"][0]), float(wp["location"][1]), float(wp.get("distance", 0.0))

    # ---- 取路 ----

    def route(self, origin_wgs: Tuple[float, float],
              dest_wgs: Tuple[float, float],
              alternatives: int = 2) -> RouteResult:
        """取一条路线（freeflow，无实时路况）。"""
        o_lng, o_lat, o_snap = self.snap_to_road(origin_wgs)
        d_lng, d_lat, d_snap = self.snap_to_road(dest_wgs)
        if o_snap > SNAP_TOLERANCE_M or d_snap > SNAP_TOLERANCE_M:
            raise RuntimeError(
                "端点未落在 OSM 路网上（容差 %.0f m）：起点吸附 %.1f m，终点吸附 %.1f m。"
                "请检查坐标基准是否搞反——GCJ-02 的点直接喂 OSRM 就会这样。"
                % (SNAP_TOLERANCE_M, o_snap, d_snap))

        params = {
            "overview": "full",
            "geometries": "geojson",
            "steps": "true",
            "alternatives": "true" if int(alternatives) > 0 else "false",
        }
        url = "/route/v1/driving/%s" % ("%f,%f;%f,%f" % (o_lng, o_lat, d_lng, d_lat))
        data = self._get(url, params=params)
        if data.get("code") != "Ok":
            raise RuntimeError("OSRM 返回异常: %s (%s)" % (data.get("code"), data.get("message")))
        routes = data.get("routes") or []
        if not routes:
            raise RuntimeError("OSRM 没有返回路线")

        best = routes[0]
        steps: List[RouteStep] = []
        index = 0
        for leg in best.get("legs", []):
            for raw in leg.get("steps", []):
                steps.append(self._build_step(raw, index))
                index += 1

        geometry = best.get("geometry") or {"type": "LineString", "coordinates": []}
        return RouteResult(
            provider=self.name,
            traffic_mode="freeflow",
            distance_km=float(best.get("distance", 0.0)) / 1000.0,
            duration_min=float(best.get("duration", 0.0)) / 60.0,
            geometry_geojson=json.dumps(geometry, ensure_ascii=False, separators=(",", ":")),
            steps=steps,
            points=len(geometry.get("coordinates", []) or []),
            origin_wgs=(o_lng, o_lat),
            dest_wgs=(d_lng, d_lat),
            snap_distance_m={"origin": o_snap, "dest": d_snap},
            raw=data,
        )

    @staticmethod
    def _build_step(raw: Dict[str, Any], index: int) -> RouteStep:
        """OSRM step → RouteStep（只搬运 + 分类，不算碳）。"""
        distance_km = float(raw.get("distance", 0.0)) / 1000.0
        duration_min = float(raw.get("duration", 0.0)) / 60.0
        # speed = 距离/时长，防 0（docs/35 §5.1）
        speed = (distance_km / duration_min * 60.0) if duration_min > 0 else 0.0
        name = str(raw.get("name") or "").strip()
        ref = str(raw.get("ref") or "").strip()
        maneuver = str((raw.get("maneuver") or {}).get("type") or "").strip()
        geometry = raw.get("geometry") or {"type": "LineString", "coordinates": []}
        klass, source, confidence = classify_road(
            name=name, ref=ref, speed_kmh=speed, distance_km=distance_km,
            maneuver_type=maneuver)
        return RouteStep(
            step_index=index,
            distance_km=distance_km,
            duration_min=duration_min,
            speed_kmh=speed,
            road_name=name,
            road_ref=ref,
            road_class=klass,
            road_class_source=source,
            road_class_confidence=confidence,
            geometry_geojson=json.dumps(geometry, ensure_ascii=False, separators=(",", ":")),
            maneuver_type=maneuver,
            # OSRM 不提供过路费金额与信号灯数量 —— 留空，不编（docs/35 §9 第 4/5 条）
            toll_flag=None,
            toll_yuan=None,
            traffic_lights=None,
        )


def save_raw(payload: Dict[str, Any], path: Path) -> Path:
    """原始响应落盘（复核用）。调用方负责确保路径在 .gitignore 内。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path
