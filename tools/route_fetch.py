# -*- coding: utf-8 -*-
"""路线预取层：网点级 OD → 高德驾车路线（T7 前置件）。

定位（docs/23 §5.1）：**网络 I/O 是工具行为，不是业务口径**，所以放 tools/，不进 src/。
它只做一件事——把"这条 OD 走哪条路、多远、多久、过路费多少、折线怎么画"预取成
``data/processed/route_od.parquet``，供引擎层（O8）和前端（只读）消费。

四条纪律（与 docs/23 §1.2 一致）：
    1. 失败不抛异常——单 OD 失败 → offline_route() 兜底并打 flag_route_est；
    2. key 只在服务端读，永不进 git、永不进前端；
    3. 幂等——已缓存的 od_key 直接跳过，第二次运行零 API 调用；
    4. 坐标系单一——高德返回即 GCJ-02，直上高德底图；本地坐标仅离线兜底并标注近似。

用法：
    python -m tools.route_fetch                     # 全量（310 条热点 OD）
    python -m tools.route_fetch --limit 20          # 先小样本试跑
    python -m tools.route_fetch --refresh           # 忽略缓存重抓
    python -m tools.route_fetch --offline           # 强制离线兜底（无 key 演练用）

离线兜底口径：haversine 直线距离 × 1.28 绕行系数，与 docs/20 §2「距离缺失→绕行系数 1.28」
完全同口径，保证"有网/无网"两套数字可比。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
OD_BAND_PATH = PROCESSED / "od_band.parquet"
ROUTE_PATH = PROCESSED / "route_od.parquet"
GEO_CACHE_PATH = PROCESSED / "route_geo.parquet"

KEY_PATH = ROOT / "config" / "amap_key.txt"
PLACEHOLDER = "请至高德开放平台申请自有 key 并替换此占位符"

# 高德 REST 端点
GEO_URL = "https://rest.amap.com/v3/geocode/geo"
DRIVING_URL = "https://rest.amap.com/v3/direction/driving"

# 离线兜底参数（与 docs/20 §2 同口径）
DETOUR_FACTOR = 1.28
OFFLINE_SPEED_KMH = 50.0

# 路线表契约（前端与引擎层共用，docs/24 §4）
ROUTE_COLUMNS: List[str] = [
    "od_key", "od_site", "od_pair", "origin", "dest",
    "o_lng", "o_lat", "d_lng", "d_lat",
    "o_precise", "d_precise",
    "distance_km", "duration_min", "toll_yuan", "traffic_lights",
    "strategy", "polyline",
    "source", "flag_route_est", "fetched_at",
]

_HTTP_TIMEOUT = 10.0
_QPS = 3.0
_MAX_RETRY = 3

# 高德对**并发**请求限流：实测同一批 24 个地址，
#   串行 24/24 成功（13.5s）｜并发 6 无节流 10/24｜并发 6 + QPS4 15/20
#   **并发 2 + QPS 2.0 → 20/20 全成功**（10.4s）
# 所以并发必须配一个**全局令牌桶**——不是每线程一个，是所有线程共用一个。
# QPS 取 2.0：官方个人 key 基础服务 QPS 通常 3~5，但并发会额外触发限流，
# 留足余量比抢那几秒划算——地理编码失败会导致该网点退化成城市级近似坐标。
_GLOBAL_QPS = 2.0
_RATE_WORKERS = 2
_rate_lock = threading.Lock()
_last_call = [0.0]


def _throttle() -> None:
    """全局令牌桶：保证所有线程合起来不超过 _GLOBAL_QPS 次/秒。"""
    interval = 1.0 / _GLOBAL_QPS
    with _rate_lock:
        now = time.monotonic()
        wait = interval - (now - _last_call[0])
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _last_call[0] = now


# ---------------------------------------------------------------------------
# key 与地理编码
# ---------------------------------------------------------------------------

def load_key() -> str:
    """env → config/amap_key.txt → 占位符。占位符 = 演示回退模式，不报错。"""
    env = os.environ.get("AMAP_WEB_SERVICE_KEY", "").strip()
    if env and not env.startswith(PLACEHOLDER):
        return env
    if KEY_PATH.exists():
        text = KEY_PATH.read_text(encoding="utf-8").strip()
        if text and not text.startswith(PLACEHOLDER):
            return text
    return PLACEHOLDER


def key_available() -> bool:
    return load_key() != PLACEHOLDER


def _request(url: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """一次带重试的 GET。高德限流/网络抖动不抛异常，失败返回 None。

    **只有"临时性"错误才重试**（网络异常、5xx、10003 配额超限、10004 访问过频）。
    "确定性"错误（key 无效、参数错、引擎解析不了）**立刻放弃**——
    实测 246 个网点如果每个都重试 3 次 + sleep，流程会卡死十几分钟。
    """
    key = load_key()
    if key == PLACEHOLDER:
        return None
    query = dict(params)
    query["key"] = key
    query["output"] = "json"

    # 这些错误重试也没用：key 不对/没权限/参数错/引擎解析不了
    _FATAL_CODES = {"10001", "10002", "10005", "10006", "10007", "10009",
                    "10020", "10021", "30001", "30002", "30003"}
    # 这些是临时性的，值得重试
    _RETRY_CODES = {"10003", "10004"}

    delay = 0.6
    for attempt in range(_MAX_RETRY):
        try:
            import requests
            _throttle()
            resp = requests.get(url, params=query, timeout=_HTTP_TIMEOUT)
            if resp.status_code != 200:
                if resp.status_code >= 500:
                    time.sleep(delay)
                    delay *= 2
                    continue
                return None
            payload = resp.json()
            if payload.get("status") == "1":
                return payload
            code = str(payload.get("infocode", ""))
            if code in _FATAL_CODES:
                return None
            if code not in _RETRY_CODES:
                return None
            time.sleep(delay)
            delay *= 2
        except Exception:
            time.sleep(delay)
            delay *= 2
    return None


def _haversine_km(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """大圆距离（km）。"""
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def offline_route(origin: Tuple[float, float], dest: Tuple[float, float]) -> Dict[str, Any]:
    """无网兜底：haversine × 1.28 绕行系数，时长按 50 km/h 反推。

    与 docs/20 §2「距离缺失→绕行系数 1.28」完全同口径，保证有网/无网数字可比。

    **坐标缺失时返回 None**——调用方必须回退到宽表实测里程。
    不能拿 (0,0) 当坐标算 haversine：那会算出上万公里的荒谬距离，
    比"没有路线"糟糕得多。宁可标为缺失，也不给错数字。
    """
    o_lng, o_lat = float(origin[0]), float(origin[1])
    d_lng, d_lat = float(dest[0]), float(dest[1])
    if (o_lng, o_lat) == (0.0, 0.0) or (d_lng, d_lat) == (0.0, 0.0):
        return {}          # 坐标缺失 → 调用方回退
    straight = _haversine_km(o_lng, o_lat, d_lng, d_lat)
    distance = round(straight * DETOUR_FACTOR, 3)
    duration = round(distance / OFFLINE_SPEED_KMH * 60.0, 2) if distance > 0 else 0.0
    return {
        "distance_km": distance,
        "duration_min": duration,
        "toll_yuan": 0.0,
        "traffic_lights": None,
        "strategy": None,
        "polyline": "",
        "source": "offline_haversine",
        "flag_route_est": 1,
    }


def geocode(name: str, city_hint: str = "") -> Optional[Tuple[float, float]]:
    """地名 → GCJ-02 坐标。失败返回 None（调用方回退离线坐标），不抛异常。

    **不传 city 参数**：实测高德对"苏州昆山集货分拣中心"这类
    「城市 + 区县 + 业务后缀」的网点名，带 city 参数反而报 ENGINE_RESPONSE_DATA_ERROR(30001)，
    不带则正常返回。网点名前两字已经是城市名，city 参数是冗余的。

    **必须按城市校验候选**（关键）：高德对虚构网点名会"字面匹配"到别的城市——
    实测"北京光华散货分拣中心"第 1 条候选是**惠州市博罗县分拣中心**（"分拣中心"字面命中），
    若直接取第 1 条，北京网点会被安到广东坐标上，路线算出 2100 km，闸门 S1 直接报红。
    所以：只接受 `city`/`province` 字段与网点名所属城市一致的候选；
    都不一致就判为不可信（返回 None，由调用方回退城市级近似坐标——
    **宁可粗一点，也不给错坐标**）。

    只做一次，不缓存——缓存由 route_geo.parquet 在批处理层做。
    """
    if not name:
        return None
    payload = _request(GEO_URL, {"address": name})
    if not payload:
        return None
    pois = payload.get("geocodes") or []
    if not pois:
        return None

    want_city = city_hint or _site_to_city(name, _short_city_names(_city_coordinate_table().keys()))
    fallback: Optional[Tuple[float, float]] = None
    for item in pois:
        location = str(item.get("location", ""))
        if "," not in location:
            continue
        lng_text, lat_text = location.split(",", 1)
        try:
            coord = (float(lng_text), float(lat_text))
        except ValueError:
            continue
        if coord[0] == 0.0 and coord[1] == 0.0:
            continue
        got_city = str(item.get("city", "") or "")
        got_province = str(item.get("province", "") or "")
        # 城市一致 → 可信；省级一致（市不同）也算可信（北京乡镇常被归到廊坊）
        if want_city and (got_city.startswith(want_city) or got_province.startswith(want_city)):
            return coord
        if fallback is None:
            fallback = coord
    # 候选城市全不匹配：判为不可信（宁可回退也不给错坐标）
    return None if want_city else fallback


# ---------------------------------------------------------------------------
# 驾车路径规划
# ---------------------------------------------------------------------------

def geocode_batch(names: List[str], city_hint_of: Optional[Dict[str, str]] = None,
                  workers: int = _RATE_WORKERS) -> Dict[str, Tuple[float, float]]:
    """批量地理编码：**并发单条请求**（每个网点一个请求，带 city 参数）。

    为什么不用高德的 batch=true：
        batch 模式返回的 geocodes 里**没有原始地址**，只有 city + formatted_address，
        只能靠"网点名前两字 == 城市名"猜回去——猜错就把**别人的坐标**安到这个网点上，
        路线全错，比慢糟糕得多。宁可多花几秒，不给错坐标。

    并发 6 路（高德个人 key 的 QPS 完全够）：246 个网点从 ~4 分钟压到 ~30 秒。
    返回 {网点名: (lng, lat)}，失败的条目不出现（调用方回退城市级近似坐标）。
    """
    if not names:
        return {}
    city_hint_of = city_hint_of or {}
    result: Dict[str, Tuple[float, float]] = {}
    lock = threading.Lock()
    done = [0]

    def work(site: str) -> None:
        coord = geocode(site, city_hint_of.get(site, ""))
        with lock:
            done[0] += 1
            if coord:
                result[site] = coord
            if done[0] % 40 == 0 or done[0] == len(names):
                print("       %d/%d（成功 %d）" % (done[0], len(names), len(result)))

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(work, names))
    return result


def fetch_driving(origin: Tuple[float, float], dest: Tuple[float, float],
                  strategy: int = 32) -> Optional[Dict[str, Any]]:
    """单 OD 驾车规划 /v3/direction/driving。

    strategy：32=默认（综合）、33=躲避拥堵、34=避开收费、35=不走高速。
    失败/超时/配额耗尽 → None（调用方走 offline_route）。
    """
    payload = _request(DRIVING_URL, {
        "origin": "%.6f,%.6f" % (origin[0], origin[1]),
        "destination": "%.6f,%.6f" % (dest[0], dest[1]),
        "strategy": strategy,
        "extensions": "base",
    })
    if not payload:
        return None
    routes = payload.get("route") or {}
    paths = routes.get("paths") or []
    if not paths:
        return None
    path = paths[0]
    try:
        distance_km = round(float(path.get("distance", 0)) / 1000.0, 3)
        duration_min = round(float(path.get("duration", 0)) / 60.0, 2)
    except (TypeError, ValueError):
        return None
    toll = path.get("tolls")
    try:
        toll_yuan = float(toll) if toll not in (None, "") else 0.0
    except (TypeError, ValueError):
        toll_yuan = 0.0
    lights = path.get("traffic_lights")
    try:
        traffic_lights = int(lights) if lights not in (None, "") else None
    except (TypeError, ValueError):
        traffic_lights = None
    # 高德 /v3/direction/driving 的路径几何在 path.steps[*].polyline，
    # path 顶层通常没有 polyline；之前只读顶层导致 295 条“真路线”全是空线。
    polyline = str(path.get("polyline", "") or "")
    if not polyline:
        steps = path.get("steps") or []
        polyline = ";".join(
            str(step.get("polyline", "") or "")
            for step in steps
            if step.get("polyline")
        )
    polyline = _simplify_polyline(polyline, max_points=400)
    return {
        "distance_km": distance_km,
        "duration_min": duration_min,
        "toll_yuan": toll_yuan,
        "traffic_lights": traffic_lights,
        "strategy": strategy,
        "polyline": polyline,
        "source": "amap_rest",
        "flag_route_est": 0,
    }


def _simplify_polyline(polyline: str, max_points: int = 400) -> str:
    """抽稀高德 polyline，保留首尾点。

    高德一条 1,300 km 路线约 1.2 万个点；310 条全存会到几十 MB。
    页面只需要“看得出走向”，400 个点足够，且不影响距离/时长/碳排。
    """
    chunks = [c for c in str(polyline or "").split(";") if c]
    if len(chunks) <= max_points:
        return ";".join(chunks)
    step = max(1, len(chunks) // max_points)
    sampled = chunks[::step]
    if sampled[-1] != chunks[-1]:
        sampled.append(chunks[-1])
    return ";".join(sampled)


def _polyline_to_points(polyline: str) -> List[List[float]]:
    """高德 polyline（'lng,lat;lng,lat'）→ [[lng, lat], ...]。"""
    if not polyline:
        return []
    points: List[List[float]] = []
    for chunk in str(polyline).split(";"):
        if "," not in chunk:
            continue
        lng_text, lat_text = chunk.split(",", 1)
        try:
            points.append([float(lng_text), float(lat_text)])
        except ValueError:
            continue
    return points


# ---------------------------------------------------------------------------
# OD 清单与坐标补全
# ---------------------------------------------------------------------------

def load_od_list(limit: Optional[int] = None) -> pd.DataFrame:
    """从 od_band.parquet 取网点级 OD 清单（按碳排降序——热点优先有数据）。"""
    if not OD_BAND_PATH.exists():
        return pd.DataFrame()
    band = pd.read_parquet(OD_BAND_PATH)
    if band.empty:
        return band
    needed = ["od_site", "od_pair", "kg_t"]
    for col in needed:
        if col not in band.columns:
            return pd.DataFrame()

    rows: List[Dict[str, Any]] = []
    for _, r in band.iterrows():
        sites = str(r["od_site"]).split("|")
        if len(sites) != 2:
            continue
        rows.append({
            "od_key": str(r["od_site"]),
            "od_site": str(r["od_site"]),
            "od_pair": str(r["od_pair"]),
            "origin": sites[0],
            "dest": sites[1],
            "kg_t": float(r.get("kg_t") or 0.0),
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out = out.sort_values("kg_t", ascending=False).reset_index(drop=True)
    if limit:
        out = out.head(int(limit)).copy()
    return out


def resolve_coordinates(od_df: pd.DataFrame, city_hint_of: Dict[str, str],
                        offline: bool = False) -> pd.DataFrame:
    """补全网点级 GCJ-02 坐标。

    优先级：① 地理编码缓存 route_geo.parquet → ② 高德地理编码 → ③ 城市级近似（离线兜底）。
    城市级近似来自宽表 origin_lat/origin_lon（docs/24 §8 标注为"离线近似坐标"）。
    """
    sites = sorted({s for pair in od_df["origin"].tolist() + od_df["dest"].tolist()
                    for s in [pair]})
    cache = _load_geo_cache()
    city_coords = _load_site_coords()

    todo = [s for s in sites if s not in cache]
    if todo and not offline and key_available():
        print("  [R2] 地理编码 %d 个网点（高德 /v3/geocode/geo，批量 10 个/次）…" % len(todo))
        resolved = geocode_batch(todo, city_hint_of)
        cache.update(resolved)
        print("       成功 %d / %d" % (len(resolved), len(todo)))
        _save_geo_cache(cache)
    elif todo and offline:
        print("  [R2] 离线模式：跳过地理编码，%d 个网点用城市级近似坐标" % len(todo))

    out = od_df.copy()
    o_lng, o_lat, d_lng, d_lat = [], [], [], []
    o_precise, d_precise = [], []
    for _, r in out.iterrows():
        for site, lng_c, lat_c, prec_c in (
            (str(r["origin"]), o_lng, o_lat, o_precise),
            (str(r["dest"]), d_lng, d_lat, d_precise),
        ):
            coord = cache.get(site)
            if coord:
                lng_c.append(coord[0])
                lat_c.append(coord[1])
                prec_c.append("exact")
                continue
            approx = city_coords.get(site)
            if approx:
                lng_c.append(approx[0])
                lat_c.append(approx[1])
                prec_c.append("city_approx")
            else:
                lng_c.append(0.0)
                lat_c.append(0.0)
                prec_c.append("missing")
    out["o_lng"], out["o_lat"] = o_lng, o_lat
    out["d_lng"], out["d_lat"] = d_lng, d_lat
    out["o_precise"], out["d_precise"] = o_precise, d_precise
    return out


def _load_geo_cache() -> Dict[str, Tuple[float, float]]:
    if not GEO_CACHE_PATH.exists():
        return {}
    try:
        df = pd.read_parquet(GEO_CACHE_PATH)
    except Exception:
        return {}
    cache: Dict[str, Tuple[float, float]] = {}
    for _, r in df.iterrows():
        try:
            cache[str(r["site"])] = (float(r["lng"]), float(r["lat"]))
        except (KeyError, TypeError, ValueError):
            continue
    return cache


def _save_geo_cache(cache: Dict[str, Tuple[float, float]]) -> None:
    if not cache:
        return
    df = pd.DataFrame(
        [{"site": k, "lng": v[0], "lat": v[1]} for k, v in sorted(cache.items())]
    )
    PROCESSED.mkdir(parents=True, exist_ok=True)
    df.to_parquet(GEO_CACHE_PATH, index=False)


_city_table_cache: Optional[Dict[str, Tuple[float, float]]] = None


def _city_coordinate_table() -> Dict[str, Tuple[float, float]]:
    """城市名 → 坐标（离线兜底用）。来源：宽表 origin/dest + 城市级近似坐标。

    带进程内缓存——这个函数被频繁调用（每次城市归属判断都要用），
    每次重读 parquet 会把 310 条路线的处理拖慢几十秒。
    """
    global _city_table_cache
    if _city_table_cache is not None:
        return _city_table_cache
    wide_path = PROCESSED / "wide_data.parquet"
    if not wide_path.exists():
        return {}
    try:
        wide = pd.read_parquet(wide_path)
    except Exception:
        return {}
    table: Dict[str, Tuple[float, float]] = {}
    pairs = (
        ("origin", "origin_lat", "origin_lon"),
        ("dest", "dest_lat", "dest_lon"),
    )
    for name_col, lat_col, lon_col in pairs:
        if name_col not in wide.columns:
            continue
        for name, lat, lon in zip(wide[name_col], wide.get(lat_col, []),
                                  wide.get(lon_col, [])):
            try:
                if name is None or str(name) in ("", "nan", "None"):
                    continue
                lat_v, lon_v = float(lat), float(lon)
                # NaN 或 0 都不是有效坐标（宽表里有缺失填充值）
                if math.isnan(lat_v) or math.isnan(lon_v):
                    continue
                if lat_v == 0.0 and lon_v == 0.0:
                    continue
                # 注意：**不能**用 setdefault —— 城市首行可能是 NaN 坐标，
                # setdefault 会占位后再也不更新，导致该城市永远"缺失"。
                # 同一城市的坐标本应唯一，重复出现时保留首个有效值即可。
                table.setdefault(str(name), (lon_v, lat_v))
            except (TypeError, ValueError):
                continue
    _city_table_cache = table
    return table


def _short_city_names(cities: Iterable[str]) -> List[str]:
    """城市短名列表：用于网点名前缀匹配。

    只剥离**真正的行政后缀**（"地区/特别行政区/自治州"），**不能剥"市/州"**——
    "苏州""广州""兰州""杭州"里的"州/广"都是城市名的一部分，剥掉会退化成"苏""广"，
    前缀匹配随即失效。剥不动的名字保持原样即可。

    长名优先（"和田地区" 必须在 "和田" 之前匹配）。
    """
    suffixes = ("特别行政区", "自治州", "地区")
    shorts: List[str] = []
    for city in cities:
        name = str(city)
        for suffix in suffixes:
            if name.endswith(suffix) and len(name) > len(suffix):
                name = name[: -len(suffix)]
                break
        if name and name not in shorts:
            shorts.append(name)
    shorts.sort(key=len, reverse=True)
    return shorts


def _site_to_city(site: str, city_names: List[str]) -> Optional[str]:
    """网点名 → 所属城市（按前缀匹配，如"哈尔滨分拣中心" → "哈尔滨"）。"""
    if not site:
        return None
    for city in city_names:
        if site.startswith(city):
            return city
    return None


def _load_site_coords() -> Dict[str, Tuple[float, float]]:
    """网点名 → 城市级近似坐标（离线兜底用）。

    做法：**按网点名前缀解析城市**，再查城市坐标表。
    比"按宽表行配对 dep_site→origin"稳健——同一网点名在不同运单里可能被挂到不同城市，
    按行配对会随机取到错误坐标；前缀解析则是确定性的。
    """
    wide_path = PROCESSED / "wide_data.parquet"
    if not wide_path.exists():
        return {}
    try:
        wide = pd.read_parquet(wide_path)
    except Exception:
        return {}
    if "dep_site" not in wide.columns or "arr_site" not in wide.columns:
        return {}

    city_table = _city_coordinate_table()
    city_names = _short_city_names(city_table.keys())

    coords: Dict[str, Tuple[float, float]] = {}
    sites = set(wide["dep_site"].astype(str)) | set(wide["arr_site"].astype(str))
    for site in sites:
        if site in ("", "nan", "None"):
            continue
        city = _site_to_city(site, city_names)
        if city and city in city_table:
            coords[site] = city_table[city]
    return coords


def _city_hint_map(od_df: pd.DataFrame) -> Dict[str, str]:
    """网点名 → 所属城市（用于地理编码的 city 参数，提高命中率）。

    与 _load_site_coords 同源：**按网点名前缀解析城市**，不按宽表行配对。
    """
    city_table = _city_coordinate_table()
    city_names = _short_city_names(city_table.keys())
    hints: Dict[str, str] = {}
    sites = set(od_df["origin"].astype(str)) | set(od_df["dest"].astype(str))
    for site in sites:
        city = _site_to_city(site, city_names)
        if city:
            hints[site] = city
    return hints


# ---------------------------------------------------------------------------
# 批量预取
# ---------------------------------------------------------------------------

def _base_distances() -> pd.Series:
    """OD（od_site）→ 历史实测里程中位（km）。

    来自 baseline_carbon.parquet 的 dist_km —— 运单回传的真实里程，
    是判断"高德路线是否匹配错了城市"的锚：路线距离超过实测里程 6 倍就是错配。
    """
    path = PROCESSED / "baseline_carbon.parquet"
    if not path.exists():
        return pd.Series(dtype=float)
    try:
        seg = pd.read_parquet(path, columns=["od_site", "dist_km"])
    except Exception:
        return pd.Series(dtype=float)
    if seg.empty or "od_site" not in seg.columns:
        return pd.Series(dtype=float)
    dist = pd.to_numeric(seg["dist_km"], errors="coerce")
    median = dist.groupby(seg["od_site"].astype(str)).median()
    # 必须显式命名：Series 自带名字（dist_km），merge 后列名会跟着变，
    # 后面 row.get("base_km") 就取不到了
    return median.rename("base_km")


def _same_city(row: Any) -> bool:
    """判断这条 OD 的起点和终点是否属于同一城市（按网点名前缀解析）。"""
    origin = str(row.get("origin", ""))
    dest = str(row.get("dest", ""))
    if not origin or not dest:
        return False
    city_names = _short_city_names(_city_coordinate_table().keys())
    o_city = _site_to_city(origin, city_names)
    d_city = _site_to_city(dest, city_names)
    return bool(o_city) and o_city == d_city


def _implausible(result: Dict[str, Any], row: Any) -> bool:
    """路线是否明显错配（True = 不可信，应作废）。

    两条判据，任一命中即判为错配：
      ① **同城 + 距离过大**：同城市的 OD，路线不可能超过 300 km；
      ② **倍数异常**：路线距离 > 实测里程 × 6（实测里程取该 OD 的历史中位）。
         实测里程是运单回传的真实里程，路线距离是其 6 倍以上只能是匹配错了城市。

    判据 ② 是主力：它不依赖"网点名以城市开头"这个假设——
    实测"嘉兴分拣中心 → 京东南湖城南银河营业部"（终点名不含城市），
    同城判断失效但倍数判断照样能抓住（路线 1222 km vs 实测 23 km）。
    """
    distance = float(result.get("distance_km") or 0.0)
    if distance <= 0:
        return False
    if _same_city(row) and distance > 300.0:
        return True
    base_km = row.get("base_km")
    if base_km is None:
        return False
    try:
        base = float(base_km)
    except (TypeError, ValueError):
        return False
    if base <= 0:
        return False
    return distance > base * 6.0


def batch_fetch(od_df: pd.DataFrame, qps: float = _QPS,
                refresh: bool = False, offline: bool = False,
                cache_path: Path = ROUTE_PATH, workers: int = _RATE_WORKERS) -> pd.DataFrame:
    """批量算路 + 并发限流 + 退避重试 + parquet 缓存。

    - 已有缓存的 od_key 直接跳过（第二次运行零 API 调用）；
    - 任何单点失败只跳过该 OD，不影响整批（走 offline_route 兜底）；
    - 并发受全局令牌桶约束（workers=2, QPS=2.0，实测 100% 成功率）；
    - 不抛异常：函数一定返回一张表。
    """
    if od_df.empty:
        return pd.DataFrame(columns=ROUTE_COLUMNS)

    existing: Dict[str, Dict[str, Any]] = {}
    if cache_path.exists() and not refresh:
        try:
            cached = pd.read_parquet(cache_path)
            for _, r in cached.iterrows():
                existing[str(r["od_key"])] = r.to_dict()
        except Exception:
            existing = {}

    todo = [row for _, row in od_df.iterrows() if str(row["od_key"]) not in existing]
    print("  [R2] 批量算路：待抓 %d 条 / 缓存命中 %d 条"
          % (len(todo), len(existing)))
    if todo and not offline and not key_available():
        print("       ⚠ 未检测到可用 Web 服务 key → 全部走离线兜底（haversine×%.2f）"
              % DETOUR_FACTOR)
        offline = True

    interval = 1.0 / max(qps, 0.1)
    fetched = 0
    lock = threading.Lock()
    counter = [0]

    def work(row: Any) -> Tuple[str, Dict[str, Any]]:
        od_key = str(row["od_key"])
        origin = (float(row["o_lng"]), float(row["o_lat"]))
        dest = (float(row["d_lng"]), float(row["d_lat"]))

        result: Optional[Dict[str, Any]] = None
        if not offline and origin != (0.0, 0.0) and dest != (0.0, 0.0):
            result = fetch_driving(origin, dest)
            # 合理性校验（双保险）：高德对虚构网点名会字面匹配到别的城市，
            # 实测"北京光华散货分拣中心"被安到惠州市，路线算出 2100 km。
            #   ① 同城校验：同城市的 OD 不可能是几百上千公里；
            #   ② 倍数校验：路线距离超过实测里程的 6 倍 → 明显错配。
            # 命中的路线作废，回退离线估算——**宁可粗一点，也不给错路线**。
            if result is not None and _implausible(result, row):
                result = None
        if result is None:
            result = offline_route(origin, dest)
        if not result:
            # 坐标缺失 → 不编数字：距离留空，交由引擎层回退宽表实测里程
            result = {
                "distance_km": None,
                "duration_min": None,
                "toll_yuan": 0.0,
                "traffic_lights": None,
                "strategy": None,
                "polyline": "",
                "source": "missing_coord",
                "flag_route_est": 1,
            }

        record = {
            "od_key": od_key,
            "od_site": str(row.get("od_site", od_key)),
            "od_pair": str(row["od_pair"]),
            "origin": str(row["origin"]),
            "dest": str(row["dest"]),
            "o_lng": origin[0], "o_lat": origin[1],
            "d_lng": dest[0], "d_lat": dest[1],
            "o_precise": str(row.get("o_precise", "")),
            "d_precise": str(row.get("d_precise", "")),
            "distance_km": result["distance_km"],
            "duration_min": result["duration_min"],
            "toll_yuan": result["toll_yuan"],
            "traffic_lights": result["traffic_lights"],
            "strategy": result["strategy"],
            "polyline": result["polyline"],
            "source": result["source"],
            "flag_route_est": result["flag_route_est"],
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with lock:
            counter[0] += 1
            n = counter[0]
            if n % 40 == 0 or n == len(todo):
                online = sum(1 for v in existing.values() if v["source"] == "amap_rest")
                print("       %d/%d（在线 %d）" % (n, len(todo), online))
        return od_key, record

    # 并发抓路：高德个人 key 的 QPS 远够用，6 路并发把 310 条从 ~5 分钟压到 ~40 秒
    if not offline:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for od_key, record in pool.map(work, todo):
                existing[od_key] = record
    else:
        for row in todo:
            od_key, record = work(row)
            existing[od_key] = record
    fetched = len(existing)

    out = pd.DataFrame(list(existing.values()))
    if out.empty:
        return pd.DataFrame(columns=ROUTE_COLUMNS)
    for col in ROUTE_COLUMNS:
        if col not in out.columns:
            out[col] = None
    out = out[ROUTE_COLUMNS]
    PROCESSED.mkdir(parents=True, exist_ok=True)
    out.to_parquet(cache_path, index=False)
    return out


def build_routes(limit: Optional[int] = None, refresh: bool = False,
                 offline: bool = False) -> pd.DataFrame:
    """完整流程：OD 清单 → 坐标补全 → 批量算路 → 落 route_od.parquet。"""
    od_df = load_od_list(limit)
    if od_df.empty:
        print("  [R2] od_band.parquet 缺失或无有效 OD — 跳过路线预取")
        return pd.DataFrame(columns=ROUTE_COLUMNS)

    hints = _city_hint_map(od_df)
    od_df = resolve_coordinates(od_df, hints, offline=offline)

    # 附上该 OD 的历史实测里程中位——用于路线合理性校验（倍数判据）。
    # 实测里程来自运单回传（baseline_carbon.parquet），是判断"路线是否匹配错城市"的锚。
    od_df = od_df.merge(_base_distances(), left_on="od_site", right_index=True,
                        how="left")

    online_ready = sum(1 for _, r in od_df.iterrows()
                       if str(r.get("o_precise")) == "exact"
                       and str(r.get("d_precise")) == "exact")
    print("  [R2] 坐标就绪：%d/%d 个 OD 为网点级精确坐标"
          % (online_ready, len(od_df)))

    out = batch_fetch(od_df, refresh=refresh, offline=offline)
    if out.empty:
        return out
    n_online = int((out["source"] == "amap_rest").sum())
    print("  [R2] 路线表落盘：%s（%d 行）" % (ROUTE_PATH.name, len(out)))
    print("       真路线 %d 条 / 离线兜底 %d 条 / 覆盖率 %.1f%%"
          % (n_online, len(out) - n_online, 100.0 * n_online / max(len(out), 1)))
    return out


def self_test() -> Dict[str, Any]:
    """自测：离线兜底口径必须与 docs/20 §2 一致（×1.28），且不依赖网络。"""
    a = offline_route((116.404, 39.915), (121.470, 31.230))
    straight = _haversine_km(116.404, 39.915, 121.470, 31.230)
    ok_factor = abs(a["distance_km"] - straight * DETOUR_FACTOR) < 0.01
    ok_time = abs(a["duration_min"] - a["distance_km"] / OFFLINE_SPEED_KMH * 60.0) < 0.01
    # 坐标缺失必须返回空字典（不给错数字）
    ok_missing = offline_route((0.0, 0.0), (121.470, 31.230)) == {}
    return {
        "offline_distance_km": round(a["distance_km"], 3),
        "detour_factor_ok": ok_factor,
        "duration_ok": ok_time,
        "missing_coord_guard_ok": ok_missing,
        "key_available": key_available(),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="T7 路线预取：网点级 OD → 高德驾车路线")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 条 OD")
    parser.add_argument("--refresh", action="store_true", help="忽略缓存重抓")
    parser.add_argument("--offline", action="store_true", help="强制离线兜底（无 key 演练）")
    args = parser.parse_args(argv)

    print("=" * 78)
    print("路线预取 route_fetch.py · T7")
    print("=" * 78)
    st = self_test()
    print("  离线兜底自测：距离 %.3f km｜绕行系数口径 %s｜时长反推 %s｜坐标缺失防护 %s"
          % (st["offline_distance_km"],
             "✓" if st["detour_factor_ok"] else "✗",
             "✓" if st["duration_ok"] else "✗",
             "✓" if st["missing_coord_guard_ok"] else "✗"))
    print("  Web 服务 key：%s" % ("已配置" if st["key_available"] else "未配置（走离线兜底）"))
    print("-" * 78)

    out = build_routes(limit=args.limit, refresh=args.refresh, offline=args.offline)
    print("-" * 78)
    if out.empty:
        print("  未生成路线表（主流程不受影响，pipeline 会跳过 route 段）")
        return 0
    print("  完成：%d 条路线 → %s" % (len(out), ROUTE_PATH))
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
