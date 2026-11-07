# -*- coding: utf-8 -*-
"""历史碳画像与真实 OD 目录：把京东趟级主表变成 Q-learning 可吃的输入。

设计边界
--------
1. 只读 jd_segment_master.parquet 的 passed=True 行；碳值仍来自主账
   carbon_kg，本模块不重算因子、不改 E0。
2. 历史 EF 是“速度档 × 载重带”的里程加权强度（g/km），不是逐秒排放曲线。
   速度档沿用论文 5 档，载重带用 0/10/20/30/60 t。
3. OD 目录只收录有高德真实 polyline 的 OD；策略指标来自
   route_candidates.parquet 的四条高德实算方案。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .amap_network import parse_polyline

SPEED_EDGES: Tuple[float, ...] = (0.0, 20.0, 40.0, 60.0, 80.0, float("inf"))
LOAD_EDGES: Tuple[float, ...] = (0.0, 10.0, 20.0, 30.0, 60.0)
SPEED_LABELS: Tuple[str, ...] = ("<20", "20-40", "40-60", "60-80", ">=80")
LOAD_LABELS: Tuple[str, ...] = ("0-10", "10-20", "20-30", "30-60")
DEFAULT_STRATEGIES: Tuple[int, ...] = (0, 1, 4, 6)
DIESEL_KG_CO2_PER_L = 2.63  # 与主账 E0 的 TTW 口径一致；不要用 Q 模块的 3.16 WTW


def _bin_index(values: np.ndarray, edges: Sequence[float]) -> np.ndarray:
    inner = np.asarray(edges[1:-1], dtype=float)
    idx = np.searchsorted(inner, values, side="right")
    return np.clip(idx, 0, len(edges) - 2).astype(int)


@dataclass(frozen=True)
class HistoricalProfile:
    """速度 × 载重的历史排放强度画像。"""

    speed_edges: Tuple[float, ...]
    load_edges: Tuple[float, ...]
    ef_g_km_matrix: np.ndarray
    n_trips_matrix: np.ndarray
    km_matrix: np.ndarray
    carbon_kg_matrix: np.ndarray
    ef_by_load: np.ndarray
    ef_by_speed: np.ndarray
    global_ef_g_km: float
    n_trips: int
    total_km: float
    total_carbon_kg: float
    source: str = "jd_segment_master.passed"
    cluster_rules: Any = None

    def cell(self, speed_kmh: float, load_t: float,
             cluster_id: Optional[int] = None) -> Dict[str, Any]:
        extra: Dict[str, Any] = {}
        if cluster_id is not None and self.cluster_rules is not None:
            hit = self.cluster_rules.cell(cluster_id, speed_kmh, load_t)
            if hit is not None:
                return hit
            try:
                cluster_ef = float(self.cluster_rules.ef_by_cluster[int(cluster_id)])
            except (IndexError, KeyError, TypeError, ValueError):
                cluster_ef = None
            extra = {
                "cluster_id": int(cluster_id),
                "cluster_label": self.cluster_rules.label_for(cluster_id),
                "cluster_ef_g_km": None if cluster_ef is None else round(cluster_ef, 3),
                "rule_cell": None,
                "cluster_fallback": True,
            }

        si = int(_bin_index(np.asarray([float(speed_kmh)]), self.speed_edges)[0])
        li = int(_bin_index(np.asarray([float(load_t)]), self.load_edges)[0])
        ef = float(self.ef_g_km_matrix[si, li])
        n = int(self.n_trips_matrix[si, li])
        km = float(self.km_matrix[si, li])
        exact = bool(np.isfinite(ef) and n > 0)
        if exact:
            source = "cell"
        elif np.isfinite(self.ef_by_load[li]):
            ef, source = float(self.ef_by_load[li]), "load_fallback"
        elif np.isfinite(self.ef_by_speed[si]):
            ef, source = float(self.ef_by_speed[si]), "speed_fallback"
        else:
            ef, source = float(self.global_ef_g_km), "global_fallback"
        result = {
            "speed_bin": SPEED_LABELS[si],
            "load_bin": LOAD_LABELS[li],
            "ef_g_km": round(ef, 3),
            "n_trips": n,
            "distance_km": round(km, 3),
            "source": source,
            "low_sample": bool(n < 30),
        }
        result.update(extra)
        return result

    def ef_g_km(self, speed_kmh: float, load_t: float,
                cluster_id: Optional[int] = None) -> float:
        return float(self.cell(speed_kmh, load_t, cluster_id=cluster_id)["ef_g_km"])

    def fuel_l_per_km(self, speed_kmh: float, load_t: float,
                      cluster_id: Optional[int] = None) -> float:
        return self.ef_g_km(speed_kmh, load_t, cluster_id=cluster_id) / (
            DIESEL_KG_CO2_PER_L * 1000.0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "speed_edges": [0, 20, 40, 60, 80, 999],
            "load_edges": list(self.load_edges),
            "speed_labels": list(SPEED_LABELS),
            "load_labels": list(LOAD_LABELS),
            "ef_g_km": [[None if not np.isfinite(v) else round(float(v), 3)
                         for v in row] for row in self.ef_g_km_matrix],
            "n_trips": [[int(v) for v in row] for row in self.n_trips_matrix],
            "distance_km": [[round(float(v), 3) for v in row] for row in self.km_matrix],
            "carbon_kg": [[round(float(v), 3) for v in row] for row in self.carbon_kg_matrix],
            "ef_by_load": [None if not np.isfinite(v) else round(float(v), 3)
                           for v in self.ef_by_load],
            "ef_by_speed": [None if not np.isfinite(v) else round(float(v), 3)
                            for v in self.ef_by_speed],
            "global_ef_g_km": round(self.global_ef_g_km, 3),
            "n_trips": int(self.n_trips),
            "total_km": round(self.total_km, 3),
            "total_carbon_kg": round(self.total_carbon_kg, 3),
            "cluster_rules": (self.cluster_rules.to_dict()
                              if self.cluster_rules is not None else None),
        }


def build_profile(master: pd.DataFrame,
                  speed_edges: Sequence[float] = SPEED_EDGES,
                  load_edges: Sequence[float] = LOAD_EDGES,
                  source: str = "jd_segment_master.passed",
                  cluster_rules: Any = None) -> HistoricalProfile:
    """从趟级主表建立 EF 画像；只使用 passed=True 且数值有效的行。"""
    need = {"trip_id", "speed_kmh", "load_t", "distance_km", "carbon_kg", "passed"}
    missing = need - set(master.columns)
    if missing:
        raise ValueError("主表缺列：%s" % ", ".join(sorted(missing)))
    frame = master.copy()
    frame = frame[frame["passed"].fillna(False).astype(bool)]
    for col in ("speed_kmh", "load_t", "distance_km", "carbon_kg"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame[np.isfinite(frame["speed_kmh"]) & np.isfinite(frame["load_t"]) &
                  np.isfinite(frame["distance_km"]) & np.isfinite(frame["carbon_kg"])]
    frame = frame[(frame["distance_km"] > 0) & (frame["carbon_kg"] >= 0)]
    if frame.empty:
        raise ValueError("主表没有可用于 EF 画像的有效行")

    si = _bin_index(frame["speed_kmh"].to_numpy(dtype=float), speed_edges)
    li = _bin_index(frame["load_t"].to_numpy(dtype=float), load_edges)
    frame = frame.assign(_si=si, _li=li)
    n_speed, n_load = len(speed_edges) - 1, len(load_edges) - 1
    n_trips = np.zeros((n_speed, n_load), dtype=np.int64)
    km = np.zeros((n_speed, n_load), dtype=float)
    carbon = np.zeros((n_speed, n_load), dtype=float)
    grouped = frame.groupby(["_si", "_li"], sort=False).agg(
        n=("trip_id", "size"), km=("distance_km", "sum"), carbon=("carbon_kg", "sum"))
    for (s_idx, l_idx), row in grouped.iterrows():
        n_trips[int(s_idx), int(l_idx)] = int(row["n"])
        km[int(s_idx), int(l_idx)] = float(row["km"])
        carbon[int(s_idx), int(l_idx)] = float(row["carbon"])

    with np.errstate(divide="ignore", invalid="ignore"):
        ef = np.where(km > 0, carbon / km * 1000.0, np.nan)
        ef_by_load = np.where(km.sum(axis=0) > 0, carbon.sum(axis=0) / km.sum(axis=0) * 1000.0, np.nan)
        ef_by_speed = np.where(km.sum(axis=1) > 0, carbon.sum(axis=1) / km.sum(axis=1) * 1000.0, np.nan)
        global_ef = float(carbon.sum() / km.sum() * 1000.0) if km.sum() > 0 else float("nan")
    return HistoricalProfile(
        speed_edges=tuple(float(x) for x in speed_edges),
        load_edges=tuple(float(x) for x in load_edges),
        ef_g_km_matrix=ef, n_trips_matrix=n_trips, km_matrix=km,
        carbon_kg_matrix=carbon, ef_by_load=ef_by_load, ef_by_speed=ef_by_speed,
        global_ef_g_km=global_ef, n_trips=int(n_trips.sum()),
        total_km=float(km.sum()), total_carbon_kg=float(carbon.sum()), source=source,
        cluster_rules=cluster_rules)


@dataclass(frozen=True)
class RouteCandidate:
    strategy: int
    strategy_name: str
    distance_km: float
    duration_min: float
    toll_yuan: float
    traffic_lights: int
    source: str = "amap_rest"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "strategy_name": self.strategy_name,
            "distance_km": round(self.distance_km, 3),
            "duration_min": round(self.duration_min, 3),
            "toll_yuan": round(self.toll_yuan, 2),
            "traffic_lights": int(self.traffic_lights),
            "source": self.source,
        }


@dataclass(frozen=True)
class ODOption:
    od_key: str
    od_site: str
    od_pair: str
    origin: str
    dest: str
    o_lng: float
    o_lat: float
    d_lng: float
    d_lat: float
    polyline: str
    trips: int
    total_km: float
    total_carbon_kg: float
    load_t: float
    speed_kmh: float
    duration_min: float
    candidates: Tuple[RouteCandidate, ...]
    departure_minute: float = 480.0

    @property
    def avg_speed_kmh(self) -> float:
        return self.speed_kmh

    def to_dict(self) -> Dict[str, Any]:
        return {
            "od_key": self.od_key, "od_pair": self.od_pair,
            "origin": self.origin, "dest": self.dest,
            "trips": int(self.trips), "total_km": round(self.total_km, 3),
            "total_carbon_kg": round(self.total_carbon_kg, 3),
            "load_t": round(self.load_t, 3), "speed_kmh": round(self.speed_kmh, 3),
            "duration_min": round(self.duration_min, 3),
            "departure_minute": round(self.departure_minute, 1),
            "candidates": [c.to_dict() for c in self.candidates],
        }


def build_od_catalog(master: pd.DataFrame, baseline: pd.DataFrame,
                     route_od: pd.DataFrame, route_candidates: pd.DataFrame,
                     *, amap_only: bool = True,
                     strategies: Sequence[int] = DEFAULT_STRATEGIES) -> Tuple[List[ODOption], Dict[str, Any]]:
    """把历史趟、310 个高德 OD 与四策略指标合成演示目录。"""
    need_master = {"trip_id", "passed", "distance_km", "carbon_kg", "load_t", "speed_kmh", "duration_h"}
    need_base = {"trip_id", "od_site"}
    missing = need_master - set(master.columns) if isinstance(master, pd.DataFrame) else need_master
    if missing:
        raise ValueError("主表缺列：%s" % ", ".join(sorted(missing)))
    missing = need_base - set(baseline.columns) if isinstance(baseline, pd.DataFrame) else need_base
    if missing:
        raise ValueError("主账缺列：%s" % ", ".join(sorted(missing)))

    frame = master[master["passed"].fillna(False).astype(bool)].copy()
    for col in ("distance_km", "carbon_kg", "load_t", "speed_kmh", "duration_h"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    base_cols = ["trip_id", "od_site"] + (["depart_ts"] if "depart_ts" in baseline.columns else [])
    base = baseline[base_cols].drop_duplicates("trip_id")
    if "depart_ts" in base.columns:
        ts = pd.to_datetime(base["depart_ts"], errors="coerce")
        base = base.assign(_depart_minute=(ts.dt.hour * 60 + ts.dt.minute).where(ts.notna()))
    else:
        base = base.assign(_depart_minute=np.nan)
    frame = frame.merge(base, on="trip_id", how="left")
    frame = frame[np.isfinite(frame["distance_km"]) & np.isfinite(frame["carbon_kg"]) &
                  np.isfinite(frame["load_t"]) & np.isfinite(frame["speed_kmh"])]
    frame = frame[frame["od_site"].notna()]

    grouped = frame.groupby("od_site", sort=False).agg(
        trips=("trip_id", "size"), total_km=("distance_km", "sum"),
        total_carbon_kg=("carbon_kg", "sum"), load_t=("load_t", "median"),
        speed_kmh=("speed_kmh", "median"), duration_min=("duration_h", "median"),
        departure_minute=("_depart_minute", "median"))
    grouped["duration_min"] = pd.to_numeric(grouped["duration_min"], errors="coerce") * 60.0
    grouped = grouped.reset_index()

    od = route_od.copy()
    for col in ("o_lng", "o_lat", "d_lng", "d_lat", "distance_km", "duration_min", "toll_yuan"):
        od[col] = pd.to_numeric(od[col], errors="coerce")
    od = od.drop_duplicates("od_site")
    def _geom_points(value: Any) -> int:
        try:
            return len(parse_polyline(value))
        except (TypeError, ValueError):
            return 0
    od["_geom_len"] = od["polyline"].fillna("").astype(str).str.len()
    od["_geom_n"] = od["polyline"].map(_geom_points)
    if amap_only:
        od = od[(od["source"].astype(str) == "amap_rest") & (od["_geom_len"] > 10)]
    else:
        od = od[od["_geom_len"] > 10]
    od = od[(od["_geom_n"] >= 2) &
            np.isfinite(od["o_lng"]) & np.isfinite(od["o_lat"]) &
            np.isfinite(od["d_lng"]) & np.isfinite(od["d_lat"])]

    merged = grouped.merge(
        od[["od_key", "od_site", "od_pair", "origin", "dest", "o_lng", "o_lat",
            "d_lng", "d_lat", "polyline", "source"]], on="od_site", how="inner")
    if merged.empty:
        return [], {"n_od": 0, "n_trips": 0, "n_od_total": int(route_od["od_site"].nunique())}

    cand = route_candidates.copy()
    for col in ("distance_km", "duration_min", "toll_yuan", "traffic_lights"):
        cand[col] = pd.to_numeric(cand[col], errors="coerce")
    cand = cand[cand["strategy"].isin(list(strategies))]
    cand = cand[np.isfinite(cand["distance_km"]) & np.isfinite(cand["duration_min"])]
    cand_by_key: Dict[str, Dict[int, dict]] = {}
    for key, block in cand.groupby("od_key"):
        rows: Dict[int, dict] = {}
        for _, row in block.iterrows():
            strategy = int(row["strategy"])
            rows[strategy] = RouteCandidate(
                strategy=strategy,
                strategy_name=str(row.get("strategy_name") or ""),
                distance_km=float(row["distance_km"]),
                duration_min=float(row["duration_min"]),
                toll_yuan=max(0.0, float(row["toll_yuan"])) if np.isfinite(row["toll_yuan"]) else 0.0,
                traffic_lights=int(row["traffic_lights"]) if np.isfinite(row["traffic_lights"]) else 0,
                source=str(row.get("source") or "amap_rest")).to_dict()
        if set(rows) >= set(strategies):
            cand_by_key[str(key)] = rows

    options: List[ODOption] = []
    for _, row in merged.iterrows():
        key = str(row["od_key"])
        rows = cand_by_key.get(key)
        if not rows:
            continue
        ordered = tuple(RouteCandidate(
            strategy=int(rows[s]["strategy"]), strategy_name=str(rows[s]["strategy_name"]),
            distance_km=float(rows[s]["distance_km"]), duration_min=float(rows[s]["duration_min"]),
            toll_yuan=float(rows[s]["toll_yuan"]), traffic_lights=int(rows[s]["traffic_lights"]),
            source=str(rows[s]["source"])) for s in strategies)
        options.append(ODOption(
            od_key=key, od_site=str(row["od_site"]), od_pair=str(row["od_pair"]),
            origin=str(row["origin"]), dest=str(row["dest"]),
            o_lng=float(row["o_lng"]), o_lat=float(row["o_lat"]),
            d_lng=float(row["d_lng"]), d_lat=float(row["d_lat"]),
            polyline=str(row["polyline"]), trips=int(row["trips"]),
            total_km=float(row["total_km"]), total_carbon_kg=float(row["total_carbon_kg"]),
            load_t=float(row["load_t"]), speed_kmh=float(row["speed_kmh"]),
            duration_min=float(row["duration_min"]), candidates=ordered,
            departure_minute=(float(row["departure_minute"])
                              if np.isfinite(row["departure_minute"]) else 480.0)))
    options.sort(key=lambda item: (-item.trips, item.od_key))
    meta = {
        "n_od": len(options),
        "n_trips": int(sum(o.trips for o in options)),
        "n_od_total": int(route_od["od_site"].nunique()),
        "amap_only": bool(amap_only),
        "strategies": [int(s) for s in strategies],
        "note": "几何=高德缓存主路线；四策略指标=高德四种策略实算；四策略共享几何。",
    }
    return options, meta


__all__ = ["SPEED_EDGES", "LOAD_EDGES", "SPEED_LABELS", "LOAD_LABELS", "DIESEL_KG_CO2_PER_L",
           "HistoricalProfile", "build_profile", "RouteCandidate", "ODOption",
           "build_od_catalog"]
