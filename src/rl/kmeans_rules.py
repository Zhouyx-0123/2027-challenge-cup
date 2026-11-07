# -*- coding: utf-8 -*-
"""K-means 3 簇碳规则：把上游路段聚类的 cluster_id 变成 Q 成本模型可吃的 EF。

边界
----
1. 只读 jd_segment_master.parquet 的 passed=True 行；碳值仍来自主账
   carbon_kg，本模块不重算因子、不改 E0。
2. 簇 × 载重带 EF 用 1000 * sum(carbon_kg) / sum(distance_km)；某格
   样本少于 MIN_CELL_N（30）时返回 None，由 HistoricalProfile.cell
   回退到原来的 5 档 × 4 载重带，绝不拿簇均值或单样本冒充。
3. 路线级分类是代理映射：用高德候选路线的均速/里程与 OD 中位载重映射到
   历史趟簇；不是逐段聚类，页面必须写明这个限制。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

FEATURE_NAMES: Tuple[str, ...] = ("speed_mean_kmh", "log1p_distance_km", "load_t")
CLUSTER_LOAD_EDGES: Tuple[float, ...] = (0.0, 10.0, 20.0, 30.0, 60.0)
CLUSTER_LOAD_LABELS: Tuple[str, ...] = ("0-10", "10-20", "20-30", "30-60")
CLUSTER_SPEED_EDGES: Tuple[float, ...] = (0.0, 20.0, 40.0, 60.0, 80.0, float("inf"))
CLUSTER_SPEED_LABELS: Tuple[str, ...] = ("<20", "20-40", "40-60", "60-80", ">=80")
CLUSTER_LABELS_3: Tuple[str, ...] = ("短途轻载", "中距中载", "长途重载")
AUDIT_NAME = "vsp_seg_cluster_audit.json"
PROFILES_NAME = "vsp_seg_cluster_profiles.parquet"
MIN_CELL_N = 30  # 与 5 档画像的 low_sample 阈值一致；不足则回退，不用单样本定规则


def _bin_index(values: np.ndarray, edges: Sequence[float]) -> np.ndarray:
    inner = np.asarray(edges[1:-1], dtype=float)
    idx = np.searchsorted(inner, values, side="right")
    return np.clip(idx, 0, len(edges) - 2).astype(int)


def _safe_float(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return out


def _labels_for(centers_raw: np.ndarray) -> Tuple[str, ...]:
    """按里程中心从短到长命名；K=3 用业务画像，其他 K 用簇号。"""
    k = len(centers_raw)
    if k == 3:
        order = np.argsort(np.asarray(centers_raw, dtype=float)[:, 1])
        names = [""] * k
        for rank, index in enumerate(order):
            names[int(index)] = CLUSTER_LABELS_3[rank]
        return tuple(names)
    return tuple("簇%d" % i for i in range(k))


@dataclass(frozen=True)
class ClusterRules:
    """K-means 簇级碳规则；可直接挂到 HistoricalProfile 上。"""

    feature_set: str
    center: Tuple[float, float, float]
    scale: Tuple[float, float, float]
    centers_scaled: np.ndarray
    centers_raw: np.ndarray
    labels: Tuple[str, ...]
    ef_by_cluster: np.ndarray
    n_trips: np.ndarray
    km: np.ndarray
    carbon_kg: np.ndarray
    ef_by_cluster_load: np.ndarray
    n_by_cluster_load: np.ndarray
    km_by_cluster_load: np.ndarray
    carbon_by_cluster_load: np.ndarray
    load_edges: Tuple[float, ...] = CLUSTER_LOAD_EDGES
    source: str = "vsp_seg_cluster_audit"

    @property
    def n_clusters(self) -> int:
        return int(len(self.labels))

    def _index(self, cluster_id: Any) -> Optional[int]:
        try:
            index = int(cluster_id)
        except (TypeError, ValueError):
            return None
        if index < 0 or index >= self.n_clusters:
            return None
        return index

    def label_for(self, cluster_id: Any) -> Optional[str]:
        index = self._index(cluster_id)
        return None if index is None else str(self.labels[index])

    def classify(self, speed_kmh: float, distance_km: float,
                 load_t: float) -> Optional[Dict[str, Any]]:
        """按 K-means 的标准化空间找最近簇。"""
        speed = _safe_float(speed_kmh)
        distance = _safe_float(distance_km)
        load = _safe_float(load_t)
        if not np.isfinite([speed, distance, load]).all() or distance < 0:
            return None
        x = np.asarray([speed, np.log1p(distance), load], dtype=float)
        z = (x - np.asarray(self.center, dtype=float)) / np.asarray(self.scale, dtype=float)
        dist2 = ((self.centers_scaled - z[None, :]) ** 2).sum(axis=1)
        index = int(np.argmin(dist2))
        return {
            "cluster_id": index,
            "cluster_label": str(self.labels[index]),
            "distance": round(float(np.sqrt(max(float(dist2[index]), 0.0))), 6),
            "feature_vector": [round(float(v), 6) for v in x.tolist()],
        }

    def cell(self, cluster_id: Any, speed_kmh: float,
             load_t: float) -> Optional[Dict[str, Any]]:
        """返回簇 × 载重带 EF；该格样本 < MIN_CELL_N 时返回 None，由调用方回退。"""
        index = self._index(cluster_id)
        if index is None:
            return None
        load = _safe_float(load_t)
        speed = _safe_float(speed_kmh)
        if not np.isfinite(load) or not np.isfinite(speed):
            return None
        li = int(_bin_index(np.asarray([load], dtype=float), self.load_edges)[0])
        n = int(self.n_by_cluster_load[index, li])
        km = float(self.km_by_cluster_load[index, li])
        ef = float(self.ef_by_cluster_load[index, li])
        if n < MIN_CELL_N or not np.isfinite(ef) or km <= 0:
            return None
        return {
            "speed_bin": CLUSTER_SPEED_LABELS[
                int(_bin_index(np.asarray([speed], dtype=float), CLUSTER_SPEED_EDGES)[0])],
            "load_bin": CLUSTER_LOAD_LABELS[li],
            "ef_g_km": round(ef, 3),
            "n_trips": n,
            "distance_km": round(km, 3),
            "source": "kmeans_cluster_load",
            "low_sample": bool(n < 30),
            "cluster_id": index,
            "cluster_label": str(self.labels[index]),
            "cluster_ef_g_km": round(float(self.ef_by_cluster[index]), 3),
            "rule_cell": "C%d/%s" % (index, CLUSTER_LOAD_LABELS[li]),
            "cluster_fallback": False,
        }

    def summary_rows(self) -> List[Dict[str, Any]]:
        total_carbon = float(np.nansum(self.carbon_kg))
        rows: List[Dict[str, Any]] = []
        for index in range(self.n_clusters):
            carbon = float(self.carbon_kg[index])
            rows.append({
                "簇": "C%d" % index,
                "画像": str(self.labels[index]),
                "中心速度 km/h": round(float(self.centers_raw[index, 0]), 2),
                "中心里程 km": round(float(self.centers_raw[index, 1]), 1),
                "中心载重 t": round(float(self.centers_raw[index, 2]), 2),
                "EF g/km": round(float(self.ef_by_cluster[index]), 3),
                "趟数": int(self.n_trips[index]),
                "碳占比 %": round(carbon / total_carbon * 100.0, 2) if total_carbon > 0 else 0.0,
                "样本公里": round(float(self.km[index]), 1),
            })
        return rows

    def to_dict(self) -> Dict[str, Any]:
        clusters: List[Dict[str, Any]] = []
        total_carbon = float(np.nansum(self.carbon_kg))
        for index in range(self.n_clusters):
            cells: List[Dict[str, Any]] = []
            for li, _label in enumerate(self.load_edges[:-1]):
                n = int(self.n_by_cluster_load[index, li])
                if n <= 0:
                    continue
                cells.append({
                    "load_bin": CLUSTER_LOAD_LABELS[li],
                    "ef_g_km": round(float(self.ef_by_cluster_load[index, li]), 3),
                    "n_trips": n,
                    "distance_km": round(float(self.km_by_cluster_load[index, li]), 3),
                })
            clusters.append({
                "cluster_id": index,
                "label": str(self.labels[index]),
                "center_raw": [round(float(v), 6) for v in self.centers_raw[index].tolist()],
                "center_scaled": [round(float(v), 6) for v in self.centers_scaled[index].tolist()],
                "ef_g_km": round(float(self.ef_by_cluster[index]), 3),
                "n_trips": int(self.n_trips[index]),
                "distance_km": round(float(self.km[index]), 3),
                "carbon_kg": round(float(self.carbon_kg[index]), 3),
                "carbon_share_pct": round(float(self.carbon_kg[index]) / total_carbon * 100.0, 3)
                if total_carbon > 0 else 0.0,
                "cells": cells,
            })
        return {
            "source": self.source,
            "feature_set": self.feature_set,
            "features": list(FEATURE_NAMES),
            "scaling": {"center": list(self.center), "scale": list(self.scale)},
            "n_clusters": self.n_clusters,
            "labels": list(self.labels),
            "clusters": clusters,
        }


def build_cluster_rules(master: pd.DataFrame, audit: Dict[str, Any],
                        profiles: Optional[pd.DataFrame] = None) -> ClusterRules:
    """从主表与 K-means 审计构建簇规则；缺 cluster_id 直接报错，不伪造。"""
    need = {"trip_id", "passed", "cluster_id", "speed_kmh", "load_t",
            "distance_km", "carbon_kg"}
    missing = need - set(master.columns)
    if missing:
        raise ValueError("主表缺列：%s" % ", ".join(sorted(missing)))
    if not isinstance(audit, dict):
        raise ValueError("K-means 审计必须是 dict")
    feature_set = str(audit.get("feature_set") or "")
    features = list(audit.get("features") or [])
    if features != list(FEATURE_NAMES):
        raise ValueError("K-means 特征不是 proxy_v1：%s" % (features,))
    scaling = audit.get("scaling") or {}
    center = tuple(float(v) for v in scaling.get("center") or [])
    scale = tuple(float(v) for v in scaling.get("scale") or [])
    centers_scaled = np.asarray(audit.get("centers_scaled") or [], dtype=float)
    centers_raw = np.asarray(audit.get("centers_raw") or [], dtype=float)
    k = int(audit.get("k_selected") or len(centers_scaled))
    if len(center) != 3 or len(scale) != 3:
        raise ValueError("K-means 标准化参数不是 3 维")
    if centers_scaled.shape != (k, 3) or centers_raw.shape != (k, 3):
        raise ValueError("K-means 簇心形状与 k 不一致")
    if k != 3:
        raise ValueError("当前产品只接受 K=3 的 K-means 碳规则，实际 %d" % k)
    if any(v == 0 for v in scale):
        raise ValueError("K-means 尺度含 0")

    frame = master.copy()
    frame = frame[frame["passed"].fillna(False).astype(bool)]
    for col in ("cluster_id", "speed_kmh", "load_t", "distance_km", "carbon_kg"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame[np.isfinite(frame["cluster_id"]) & np.isfinite(frame["speed_kmh"]) &
                  np.isfinite(frame["load_t"]) & np.isfinite(frame["distance_km"]) &
                  np.isfinite(frame["carbon_kg"])]
    frame = frame[(frame["distance_km"] > 0) & (frame["carbon_kg"] >= 0)]
    if frame.empty:
        raise ValueError("主表没有可用于簇规则的有效行")
    ids = sorted(int(v) for v in frame["cluster_id"].unique())
    if any(v < 0 or v >= k for v in ids):
        raise ValueError("主表 cluster_id 超出 0..%d：%s" % (k - 1, ids))

    n_trips = np.zeros(k, dtype=np.int64)
    km = np.zeros(k, dtype=float)
    carbon = np.zeros(k, dtype=float)
    for index, block in frame.groupby(frame["cluster_id"].astype(int), sort=False):
        n_trips[int(index)] = int(len(block))
        km[int(index)] = float(block["distance_km"].sum())
        carbon[int(index)] = float(block["carbon_kg"].sum())
    with np.errstate(divide="ignore", invalid="ignore"):
        ef = np.where(km > 0, carbon / km * 1000.0, np.nan)

    li = _bin_index(frame["load_t"].to_numpy(dtype=float), CLUSTER_LOAD_EDGES)
    frame = frame.assign(_li=li)
    n_load = len(CLUSTER_LOAD_EDGES) - 1
    n_by = np.zeros((k, n_load), dtype=np.int64)
    km_by = np.zeros((k, n_load), dtype=float)
    carbon_by = np.zeros((k, n_load), dtype=float)
    for (cid, lidx), block in frame.groupby(["cluster_id", "_li"], sort=False):
        ci, lj = int(cid), int(lidx)
        n_by[ci, lj] = int(len(block))
        km_by[ci, lj] = float(block["distance_km"].sum())
        carbon_by[ci, lj] = float(block["carbon_kg"].sum())
    with np.errstate(divide="ignore", invalid="ignore"):
        ef_by = np.where(km_by > 0, carbon_by / km_by * 1000.0, np.nan)

    labels = _labels_for(centers_raw)
    return ClusterRules(
        feature_set=feature_set,
        center=(float(center[0]), float(center[1]), float(center[2])),
        scale=(float(scale[0]), float(scale[1]), float(scale[2])),
        centers_scaled=centers_scaled,
        centers_raw=centers_raw,
        labels=labels,
        ef_by_cluster=ef,
        n_trips=n_trips,
        km=km,
        carbon_kg=carbon,
        ef_by_cluster_load=ef_by,
        n_by_cluster_load=n_by,
        km_by_cluster_load=km_by,
        carbon_by_cluster_load=carbon_by,
    )


def load_cluster_rules(master: pd.DataFrame,
                       processed_dir: Optional[Path] = None) -> Optional[ClusterRules]:
    """读 data/processed 的 K-means 审计与画像；文件不存在时返回 None。"""
    base = Path(processed_dir) if processed_dir is not None else Path("data/processed")
    audit_path = base / AUDIT_NAME
    if not audit_path.exists():
        return None
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    profiles = None
    profiles_path = base / PROFILES_NAME
    if profiles_path.exists():
        profiles = pd.read_parquet(profiles_path)
    return build_cluster_rules(master, audit, profiles=profiles)


__all__ = ["FEATURE_NAMES", "CLUSTER_LOAD_EDGES", "CLUSTER_LOAD_LABELS",
           "MIN_CELL_N", "ClusterRules", "build_cluster_rules", "load_cluster_rules"]
