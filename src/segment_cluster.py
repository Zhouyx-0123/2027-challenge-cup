# -*- coding: utf-8 -*-
"""多维 K-means 路段/趟次聚类（并列轨，不改 E0 主账）。

历史趟次: grain=trip, feature_set=proxy_v1 (速度 + log 里程 + 载重)
OBD 段:   grain=segment, feature_set=obd_v1 (速度 + 转速 + 扭矩 + 油耗 + 怠速 + log 里程)

纪律:
* 只写 vsp_seg_clusters / vsp_seg_cluster_profiles / vsp_seg_plate_map /
  vsp_seg_cluster_audit 四张并列件, 不改 E0、28 条主闸门、主账结果表;
* 缺失不插补: 任一特征缺失的样本不参与聚类, cluster_id 留空;
* 车牌只落 plate_id (加盐 sha256 前 16 位), 原车牌只进独立 map 表;
* K 首版固定 3 (用户 2026-11-06 确认); K=2-6 扫描只进 audit;
* 纯 numpy 实现, 不引入 sklearn.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from src import schema

CLUSTER_VERSION = "SEG-CLUSTER-v1"
FEATURE_VERSION = "SEG-FEAT-v1"
K_DEFAULT = 3
K_MIN, K_MAX = 2, 6
# 与 src/vsp_segment.py 的 speed_gates 同口径：负速度与 >110 km/h 视为不可用
SPEED_VALID_MIN_KMH = 0.0
SPEED_VALID_MAX_KMH = 110.0
KMEANS_SEED = 20261106
KMEANS_N_INIT = 10
KMEANS_MAX_ITER = 300
KMEANS_TOL = 1e-6
MIN_VALID_ROWS = 10
PLATE_SALT = "dsh-seg-plate-v1:"

RESULT_CLUSTERS = "vsp_seg_clusters.parquet"
RESULT_PROFILES = "vsp_seg_cluster_profiles.parquet"
RESULT_PLATE_MAP = "vsp_seg_plate_map.parquet"
RESULT_AUDIT = "vsp_seg_cluster_audit.json"

FEATURE_SETS: Dict[str, Dict[str, Any]] = {
    "proxy_v1": {
        "grain": "trip",
        "features": [
            {"name": "speed_mean_kmh", "sources": ("speed_mean_kmh", "speed_kmh")},
            {"name": "log1p_distance_km", "sources": ("distance_km", "dist_km", "trip_km"),
             "transform": "log1p"},
            {"name": "load_t", "sources": ("load_t",)},
        ],
        "description": "历史趟次代理特征: 平均速度 + log(里程) + 载重",
    },
    "obd_v1": {
        "grain": "segment",
        "features": [
            {"name": "speed_mean_kmh", "sources": ("speed_mean_kmh", "speed_kmh")},
            {"name": "rpm_mean", "sources": ("rpm_mean",)},
            {"name": "torque_actual_mean_pct", "sources": ("torque_actual_mean_pct",)},
            {"name": "fuel_rate_mean_lph", "sources": ("fuel_rate_mean_lph",)},
            {"name": "idle_ratio", "sources": ("idle_ratio",)},
            {"name": "log1p_distance_km", "sources": ("distance_km", "dist_km", "trip_km"),
             "transform": "log1p"},
        ],
        "description": "OBD 段特征: 速度 + 转速 + 实际扭矩 + 瞬时油耗 + 怠速占比 + log(里程)",
    },
}

CANONICAL_SOURCES: Dict[str, Tuple[str, ...]] = {
    "speed_mean_kmh": ("speed_mean_kmh", "speed_kmh"),
    "distance_km": ("distance_km", "dist_km", "trip_km"),
    "load_t": ("load_t",),
    "rpm_mean": ("rpm_mean",),
    "torque_actual_mean_pct": ("torque_actual_mean_pct",),
    "fuel_rate_mean_lph": ("fuel_rate_mean_lph",),
    "idle_ratio": ("idle_ratio",),
    "carbon_kg": ("carbon_kg", "kg_co2_phys", "kg_co2"),
    "vehicle_group": ("vehicle_group",),
    "vehicle_type": ("vehicle_type", "vtype_name"),
}


def _first_col(df: pd.DataFrame, names: Sequence[str]) -> Optional[str]:
    for name in names:
        if name in df.columns:
            return name
    return None


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def plate_id(plate: Any) -> Optional[str]:
    """车牌归一化 + 加盐哈希; 只返回 16 位 hex, 不返回原值."""
    if _is_missing(plate):
        return None
    text = str(plate).strip()
    if not text or text.lower() in {"nan", "none", "<na>"}:
        return None
    norm = re.sub(r"\s+", "", text).upper()
    return hashlib.sha256((PLATE_SALT + norm).encode("utf-8")).hexdigest()[:16]


def _json_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(v) else v


def _enrich_from_ledger(df: pd.DataFrame, ledger: Optional[pd.DataFrame]) -> pd.DataFrame:
    """从主账段表补 trip 级 plate/load/里程/速度; 只补缺, 不覆盖已有值."""
    if ledger is None or "trip_id" not in df.columns or "trip_id" not in ledger.columns:
        return df
    led = ledger.copy()
    agg: Dict[str, Any] = {}
    if "plate" in led.columns:
        agg["plate"] = ("plate", "first")
    if "load_t" in led.columns:
        agg["load_t"] = ("load_t", "first")
    if "vehicle_type" in led.columns:
        agg["vehicle_type"] = ("vehicle_type", "first")
    if "trip_km" in led.columns:
        agg["distance_km"] = ("trip_km", "first")
    if "speed_kmh" in led.columns:
        agg["speed_kmh"] = ("speed_kmh", "first")
    if "kg_co2_phys" in led.columns:
        agg["carbon_kg"] = ("kg_co2_phys", "sum")
    if not agg:
        return df
    led_trip = led.groupby("trip_id", as_index=False).agg(**agg)
    merged = df.merge(led_trip, on="trip_id", how="left",
                      suffixes=("", "__ledger"))
    for col in led_trip.columns:
        if col == "trip_id":
            continue
        if col not in merged.columns:
            merged[col] = merged.get(col + "__ledger")
        elif col + "__ledger" in merged.columns:
            merged[col] = merged[col].where(merged[col].notna(),
                                            merged[col + "__ledger"])
        drop = col + "__ledger"
        if drop in merged.columns:
            merged = merged.drop(columns=[drop])
    return merged


def normalise_frame(frame: pd.DataFrame, grain: Optional[str] = None,
                    ledger: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """加 unit_id/grain/canonical 列; 拒绝重复 unit_id."""
    if frame is None or len(frame) == 0:
        raise ValueError("输入为空, 无法聚类")
    df = frame.copy()
    if grain is None:
        grain = "segment" if ("segment_id" in df.columns and "trip_id" not in df.columns) else "trip"
    if grain not in {"trip", "segment"}:
        raise ValueError(f"未知 grain: {grain}")
    unit_src = _first_col(df, ("segment_id", "trip_id", "unit_id") if grain == "segment"
                          else ("trip_id", "segment_id", "unit_id"))
    if unit_src is None:
        raise ValueError(f"{grain} 粒度缺少 id 列 (trip_id/segment_id)")
    df["unit_id"] = df[unit_src]
    if df["unit_id"].isna().any():
        raise ValueError("unit_id 存在空值")
    if df["unit_id"].duplicated().any():
        raise ValueError("unit_id 存在重复; 聚类前必须一单位一行")
    df["grain"] = grain
    df = _enrich_from_ledger(df, ledger)
    for canonical, sources in CANONICAL_SOURCES.items():
        if canonical in df.columns:
            continue
        src = _first_col(df, sources)
        if src is not None:
            df[canonical] = df[src]
    if "plate" in df.columns:
        df["plate_id"] = df["plate"].map(plate_id)
    else:
        df["plate_id"] = pd.NA
    return df


def _feature_matrix(df: pd.DataFrame, feature_set: str
                    ) -> Tuple[np.ndarray, List[str], Dict[str, int]]:
    spec = FEATURE_SETS[feature_set]
    cols: List[np.ndarray] = []
    names: List[str] = []
    missing: Dict[str, int] = {}
    for feat in spec["features"]:
        name = feat["name"]
        src = _first_col(df, feat["sources"])
        if src is None:
            values = np.full(len(df), np.nan)
        else:
            series = pd.to_numeric(df[src], errors="coerce")
            if feat.get("transform") == "log1p":
                raw = series.to_numpy(dtype=float)
                values = np.where(np.isfinite(raw) & (raw >= 0.0),
                                  np.log1p(np.maximum(raw, 0.0)), np.nan)
            else:
                values = series.to_numpy(dtype=float)
        names.append(name)
        cols.append(values)
        missing[name] = int(np.sum(~np.isfinite(values)))
    X = np.column_stack(cols) if cols else np.empty((len(df), 0), dtype=float)
    return X, names, missing


def robust_scale(X: np.ndarray) -> Tuple[np.ndarray, np.ndarray, str]:
    """逐列 robust scaling: 中位数中心 + IQR 尺度; 退化列用 std / 1."""
    center = np.nanmedian(X, axis=0)
    q1 = np.nanpercentile(X, 25, axis=0)
    q3 = np.nanpercentile(X, 75, axis=0)
    scale = q3 - q1
    std = np.nanstd(X, axis=0)
    for j in range(X.shape[1]):
        if not np.isfinite(scale[j]) or scale[j] <= 0:
            scale[j] = std[j] if np.isfinite(std[j]) and std[j] > 0 else 1.0
    return center, scale, "robust_iqr"


def inverse_scale(centers_scaled: np.ndarray, center: np.ndarray,
                  scale: np.ndarray, names: Sequence[str],
                  feature_set: str) -> np.ndarray:
    raw = centers_scaled * scale + center
    spec = {f["name"]: f for f in FEATURE_SETS[feature_set]["features"]}
    out = raw.copy()
    for j, name in enumerate(names):
        if spec.get(name, {}).get("transform") == "log1p":
            out[:, j] = np.expm1(raw[:, j])
    return out


def _sqdist(X: np.ndarray, centers: np.ndarray) -> np.ndarray:
    return ((X[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)


def _kmeans_once(X: np.ndarray, k: int, *, seed: int,
                 max_iter: int = KMEANS_MAX_ITER,
                 tol: float = KMEANS_TOL) -> Tuple[np.ndarray, np.ndarray, float, int]:
    rng = np.random.default_rng(seed)
    n, d = X.shape
    centers = X[[int(rng.integers(n))]].copy()
    while len(centers) < k:
        d2 = _sqdist(X, centers).min(axis=1)
        total = float(d2.sum())
        if total <= 0.0:
            centers = np.vstack([centers, X[int(rng.integers(n))]])
            continue
        pick = int(rng.choice(n, p=(d2 / total)))
        centers = np.vstack([centers, X[pick]])
    labels = np.zeros(n, dtype=int)
    inertia = float("inf")
    n_iter = 0
    for it in range(1, max_iter + 1):
        n_iter = it
        dist = _sqdist(X, centers)
        new_labels = dist.argmin(axis=1)
        counts = np.bincount(new_labels, minlength=k)
        for j in np.flatnonzero(counts == 0):
            far = int(np.argmax(dist[np.arange(n), new_labels]))
            new_labels[far] = int(j)
            counts = np.bincount(new_labels, minlength=k)
        new_centers = centers.copy()
        for j in range(k):
            mask = new_labels == j
            if mask.any():
                new_centers[j] = X[mask].mean(axis=0)
        shift = float(np.linalg.norm(new_centers - centers, axis=1).max())
        inertia = float(((X - new_centers[new_labels]) ** 2).sum())
        labels, centers = new_labels, new_centers
        if shift <= tol:
            break
    return labels, centers, inertia, n_iter


def _sort_centers(labels: np.ndarray, centers: np.ndarray
                  ) -> Tuple[np.ndarray, np.ndarray]:
    """按第一维(速度)优先、其余维度次序重排簇号; labels 同步重映射."""
    if len(centers) == 0:
        return labels, centers
    keys = tuple(centers[:, i] for i in range(centers.shape[1] - 1, -1, -1))
    order = np.lexsort(keys)
    remap = np.empty(len(order), dtype=int)
    remap[order] = np.arange(len(order))
    return remap[labels], centers[order]


def kmeans_nd(X: np.ndarray, k: int, *, seed: int = KMEANS_SEED,
              n_init: int = KMEANS_N_INIT,
              max_iter: int = KMEANS_MAX_ITER,
              tol: float = KMEANS_TOL
              ) -> Tuple[np.ndarray, np.ndarray, float, int, int]:
    """多维 K-means: 返回 (labels, centers, inertia, n_iter, n_init_used)."""
    X = np.asarray(X, dtype=float)
    if X.ndim != 2 or len(X) < k:
        raise ValueError(f"K-means 输入需为二维且样本数 >= k (n={len(X)}, k={k})")
    best: Optional[Tuple[np.ndarray, np.ndarray, float, int]] = None
    used = 0
    for i in range(max(1, int(n_init))):
        labels, centers, inertia, n_iter = _kmeans_once(
            X, k, seed=int(seed) + i * 1009, max_iter=max_iter, tol=tol)
        used += 1
        if best is None or inertia < best[2]:
            best = (labels, centers, inertia, n_iter)
    labels, centers, inertia, n_iter = best  # type: ignore[misc]
    labels, centers = _sort_centers(labels, centers)
    return labels, centers, inertia, n_iter, used


def scan_k(X: np.ndarray, *, seed: int = KMEANS_SEED,
           k_min: int = K_MIN, k_max: int = K_MAX,
           n_init_scan: int = 3) -> List[Dict[str, Any]]:
    """K 扫描只进审计; 返回 inertia、簇规模、相对下降."""
    rows: List[Dict[str, Any]] = []
    prev: Optional[float] = None
    for k in range(int(k_min), int(k_max) + 1):
        if len(X) < k:
            continue
        labels, centers, inertia, n_iter, used = kmeans_nd(
            X, k, seed=seed, n_init=n_init_scan)
        sizes = np.bincount(labels, minlength=k)
        row = {
            "k": int(k),
            "inertia": round(float(inertia), 6),
            "silhouette": _silhouette_score(X, labels, sample_size=2000, seed=seed),
            "n_iter": int(n_iter),
            "n_init": int(used),
            "cluster_sizes": [int(v) for v in sizes],
            "min_cluster_size": int(sizes.min()),
            "relative_inertia_drop_pct": (
                round((prev - float(inertia)) / prev * 100.0, 4)
                if prev and prev > 0 else None),
        }
        rows.append(row)
        prev = float(inertia)
    return rows


def _physical_validity(df: pd.DataFrame) -> Tuple[np.ndarray, Dict[str, int]]:
    """物理可用性门禁：只剔除，不插补。返回 (mask, reason_counts)。

    速度口径与 vsp_segment.speed_gates 对齐：负速度、>110 km/h 不可用。
    里程/载重出现负值同样不可用；缺失交给特征矩阵的 NaN 判定。
    """
    n = len(df)
    mask = np.ones(n, dtype=bool)
    reasons: Dict[str, int] = {}

    def _numeric(col: str) -> Optional[np.ndarray]:
        if col not in df.columns:
            return None
        return pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)

    speed = _numeric("speed_mean_kmh")
    if speed is not None:
        bad = np.isfinite(speed) & ((speed < SPEED_VALID_MIN_KMH) |
                                    (speed > SPEED_VALID_MAX_KMH))
        if bad.any():
            reasons["speed_out_of_range"] = int(bad.sum())
        mask &= ~bad
    distance = _numeric("distance_km")
    if distance is not None:
        bad = np.isfinite(distance) & (distance < 0.0)
        if bad.any():
            reasons["distance_negative"] = int(bad.sum())
        mask &= ~bad
    load = _numeric("load_t")
    if load is not None:
        bad = np.isfinite(load) & (load < 0.0)
        if bad.any():
            reasons["load_negative"] = int(bad.sum())
        mask &= ~bad
    return mask, reasons


def _plate_map(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["plate", "plate_id", "n_units"]
    if "plate" not in df.columns or "plate_id" not in df.columns:
        return pd.DataFrame(columns=cols)
    sub = df[["plate", "plate_id"]].dropna(subset=["plate", "plate_id"]).copy()
    if sub.empty:
        return pd.DataFrame(columns=cols)
    sub["plate"] = sub["plate"].astype(str).str.strip()
    sub = sub[sub["plate"] != ""]
    out = (sub.groupby(["plate", "plate_id"], as_index=False)
              .size().rename(columns={"size": "n_units"}))
    return out.sort_values(["plate_id"], kind="stable").reset_index(drop=True)


def _mode_or_none(series: pd.Series) -> Optional[str]:
    s = series.dropna()
    if s.empty:
        return None
    return str(s.value_counts().index[0])


def _silhouette_score(X: np.ndarray, labels: np.ndarray, *,
                        sample_size: int = 4000,
                        seed: int = KMEANS_SEED) -> Optional[float]:
    """抽样轮廓系数；用分块距离避免 N×N 全矩阵常驻内存。"""
    X = np.asarray(X, dtype=float)
    labels = np.asarray(labels, dtype=int)
    n = len(X)
    uniq = np.unique(labels)
    if n < 3 or len(uniq) < 2:
        return None
    rng = np.random.default_rng(int(seed) + 4241)
    m = min(int(sample_size), n)
    idx = np.sort(rng.choice(n, size=m, replace=False))
    slabels = labels[idx]
    values: List[float] = []
    for start in range(0, m, 256):
        stop = min(start + 256, m)
        block = idx[start:stop]
        D = np.linalg.norm(X[block, None, :] - X[None, :, :], axis=2)
        own_mask = labels[None, :] == slabels[start:stop, None]
        own_count = own_mask.sum(axis=1)
        a = (D * own_mask).sum(axis=1) / np.maximum(own_count - 1, 1)
        a[own_count <= 1] = np.inf
        means = np.empty((len(block), len(uniq)), dtype=float)
        for j, c in enumerate(uniq):
            mask = labels == c
            means[:, j] = D[:, mask].mean(axis=1)
        own = np.searchsorted(uniq, slabels[start:stop])
        means[np.arange(len(block)), own] = np.inf
        b = means.min(axis=1)
        denom = np.maximum(a, b)
        s = np.zeros_like(a)
        ok = np.isfinite(a) & np.isfinite(b) & (denom > 0)
        s[ok] = (b[ok] - a[ok]) / denom[ok]
        values.extend(float(v) for v in s)
    return round(float(np.mean(values)), 6) if values else None


def _adjusted_rand_index(labels_a: np.ndarray, labels_b: np.ndarray) -> float:
    """两套标签的调整兰德指数（不依赖 sklearn）。"""
    a = np.asarray(labels_a, dtype=int)
    b = np.asarray(labels_b, dtype=int)
    if len(a) != len(b) or len(a) < 2:
        return 0.0
    _, ai = np.unique(a, return_inverse=True)
    _, bi = np.unique(b, return_inverse=True)
    table = np.zeros((ai.max() + 1, bi.max() + 1), dtype=float)
    np.add.at(table, (ai, bi), 1.0)
    comb = lambda x: x * (x - 1.0) / 2.0
    sum_comb = comb(table).sum()
    sum_a = comb(table.sum(axis=1)).sum()
    sum_b = comb(table.sum(axis=0)).sum()
    n = float(len(a))
    expected = sum_a * sum_b / comb(n) if comb(n) > 0 else 0.0
    max_index = (sum_a + sum_b) / 2.0
    denom = max_index - expected
    return round(float((sum_comb - expected) / denom), 6) if denom else 1.0


def _profiles(df: pd.DataFrame, X: np.ndarray, names: Sequence[str],
              labels: np.ndarray, centers_raw: np.ndarray,
              centers_scaled: np.ndarray) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    k = len(centers_raw)
    for label in range(k):
        mask = labels == label
        sub = df.loc[mask]
        row: Dict[str, Any] = {
            "cluster_id": int(label),
            "n": int(mask.sum()),
            "share_pct": round(float(mask.sum()) / max(len(labels), 1) * 100.0, 3),
        }
        for j, name in enumerate(names):
            vals = X[mask, j]
            row[f"center_raw_{name}"] = round(float(centers_raw[label, j]), 6)
            row[f"center_scaled_{name}"] = round(float(centers_scaled[label, j]), 6)
            row[f"median_{name}"] = (round(float(np.nanmedian(vals)), 6)
                                     if len(vals) else None)
            row[f"mean_{name}"] = (round(float(np.nanmean(vals)), 6)
                                   if len(vals) else None)
        for col in ("vehicle_group", "vehicle_type"):
            if col in sub.columns:
                row[f"top_{col}"] = _mode_or_none(sub[col])
        if "plate_id" in sub.columns:
            row["n_plates"] = int(sub["plate_id"].dropna().nunique())
            if sub["plate_id"].notna().any():
                top = sub["plate_id"].value_counts().head(5)
                row["top_plate_ids"] = ", ".join(str(v) for v in top.index)
            else:
                row["top_plate_ids"] = None
        for col in ("load_t", "distance_km", "speed_mean_kmh", "carbon_kg"):
            if col in sub.columns:
                vals = pd.to_numeric(sub[col], errors="coerce")
                row[f"median_{col}"] = (round(float(vals.median()), 6)
                                        if vals.notna().any() else None)
                row[f"sum_{col}"] = (round(float(vals.sum()), 6)
                                      if vals.notna().any() else None)
        rows.append(row)
    return pd.DataFrame(rows)


def build_cluster_table(frame: pd.DataFrame, *, grain: Optional[str] = None,
                        feature_set: str = "proxy_v1",
                        ledger: Optional[pd.DataFrame] = None,
                        k: int = K_DEFAULT,
                        seed: int = KMEANS_SEED,
                        n_init: int = KMEANS_N_INIT,
                        min_valid_rows: int = MIN_VALID_ROWS
                        ) -> Dict[str, Any]:
    """输入一单位一行表, 输出 assignments/profiles/plate_map/audit 四件."""
    if feature_set not in FEATURE_SETS:
        raise ValueError(f"未知 feature_set: {feature_set}")
    spec = FEATURE_SETS[feature_set]
    if grain is None:
        grain = spec["grain"]
    df = normalise_frame(frame, grain=grain, ledger=ledger)
    X, names, missing = _feature_matrix(df, feature_set)
    finite = np.isfinite(X).all(axis=1) if X.shape[1] else np.zeros(len(df), dtype=bool)
    physical, invalid_by_reason = _physical_validity(df)
    valid = finite & physical
    n_input = int(len(df))
    n_valid = int(valid.sum())
    n_missing = int((~finite).sum())
    n_invalid = int((finite & ~physical).sum())
    if n_missing:
        invalid_by_reason = dict(invalid_by_reason)
        invalid_by_reason["missing_feature"] = n_missing
    plate_map = _plate_map(df)
    n_plates = int(df["plate_id"].dropna().nunique()) if "plate_id" in df.columns else 0

    base = pd.DataFrame({
        "grain": df["grain"].astype(str),
        "unit_id": df["unit_id"].to_numpy(),
        "feature_set": feature_set,
        "cluster_distance": np.nan,
        "n_missing_features": (~np.isfinite(X)).sum(axis=1).astype(int),
        "plate_id": df["plate_id"].to_numpy(),
    })
    for j, name in enumerate(names):
        base[name] = X[:, j]

    audit: Dict[str, Any] = {
        "cluster_version": CLUSTER_VERSION,
        "feature_version": FEATURE_VERSION,
        "feature_set": feature_set,
        "feature_description": spec["description"],
        "grain": grain,
        "k_selected": int(k),
        "selection": "固定 K=3 (用户 2026-11-06 确认); K 扫描仅审计, 不用肘部法改 K",
        "seed": int(seed),
        "n_init": int(n_init),
        "n_input": n_input,
        "n_valid": n_valid,
        "n_missing": n_missing,
        "n_invalid": n_invalid,
        "n_excluded": n_input - n_valid,
        "exclusion_by_reason": invalid_by_reason,
        "missing_by_feature": {k2: int(v) for k2, v in missing.items()},
        "features": list(names),
        "scaling": {"method": "robust_iqr", "note": "中位数中心 + IQR 尺度; 退化列用 std 或 1; 不做插补"},
        "plate": {
            "n_plates": n_plates,
            "hash": "sha256_hex_16",
            "salt_version": "dsh-seg-plate-v1",
            "raw_in_mapping_only": True,
        },
        "limitations": [
            "车牌在历史趟次表中已被剥离; 仅当输入或主账段表带 plate 时才能出 plate_id",
            "OBD 文本段没有载重字段, obd_v1 因而不含 load_t; 接入车辆主数据后再评估 obd_load_v1",
            "聚类是描述性归档, 不改变 E0 主账、28 条闸门或分档计碳规则",
            "K=3 是业务首版固定值; K 扫描只供审计, 不代表统计最优",
        ],
    }

    if n_valid < max(int(k), int(min_valid_rows)):
        audit.update({
            "status": "skipped",
            "reason": f"有效样本 {n_valid} < 阈值 {max(int(k), int(min_valid_rows))}; 缺失不插补, cluster_id 全部留空",
            "cluster_sizes": [],
            "inertia": None,
            "centers_raw": [],
            "centers_scaled": [],
            "k_scan": [],
        })
        base["cluster_id"] = pd.array([pd.NA] * n_input, dtype="Int64")
        profiles = pd.DataFrame(columns=["cluster_id", "n", "share_pct"])
        return {"assignments": base, "profiles": profiles,
                "plate_map": plate_map, "audit": audit}

    Xv = X[valid]
    center, scale, scaling_method = robust_scale(Xv)
    Z = (Xv - center) / scale
    labels, centers_scaled, inertia, n_iter, n_init_used = kmeans_nd(
        Z, int(k), seed=int(seed), n_init=int(n_init))
    silhouette = _silhouette_score(Z, labels, seed=int(seed))
    stable_labels, _, _, _, _ = kmeans_nd(
        Z, int(k), seed=int(seed) + 777, n_init=min(3, int(n_init)))
    stability_ari = _adjusted_rand_index(labels, stable_labels)
    k_scan = scan_k(Z, seed=int(seed), n_init_scan=min(3, int(n_init)))
    centers_raw = inverse_scale(centers_scaled, center, scale, names, feature_set)

    full_labels = np.full(n_input, -1, dtype=int)
    full_labels[valid] = labels
    distances = np.full(n_input, np.nan, dtype=float)
    distances[valid] = np.linalg.norm(Z - centers_scaled[labels], axis=1)
    base["cluster_id"] = pd.array(
        [int(v) if v >= 0 else pd.NA for v in full_labels], dtype="Int64")
    base["cluster_distance"] = distances

    profiles = _profiles(df.loc[valid].reset_index(drop=True), Xv, names,
                         labels, centers_raw, centers_scaled)
    sizes = [int(v) for v in np.bincount(labels, minlength=int(k))]
    audit.update({
        "status": "ok",
        "scaling": {
            "method": scaling_method,
            "center": [round(float(v), 6) for v in center],
            "scale": [round(float(v), 6) for v in scale],
            "note": "中位数中心 + IQR 尺度; 退化列用 std 或 1; 不做插补",
        },
        "inertia": round(float(inertia), 6),
        "silhouette": silhouette,
        "silhouette_sample": 4000,
        "stability_ari": stability_ari,
        "stability_note": "seed 与 seed+777 两次独立 n_init 的 ARI；不是跨数据稳定性",
        "n_iter": int(n_iter),
        "n_init_used": int(n_init_used),
        "cluster_sizes": sizes,
        "centers_scaled": [[round(float(v), 6) for v in row] for row in centers_scaled],
        "centers_raw": [[round(float(v), 6) for v in row] for row in centers_raw],
        "k_scan": k_scan,
        "profile_file": RESULT_PROFILES,
        "assignment_file": RESULT_CLUSTERS,
        "plate_map_file": RESULT_PLATE_MAP,
        "known_limit": "历史趟次无逐段载重/车牌时用主账 trip 级 first/sum 代理; 不是实测值",
    })
    return {"assignments": base, "profiles": profiles,
            "plate_map": plate_map, "audit": audit}


def attach_cluster_ids(frame: pd.DataFrame, assignments: pd.DataFrame,
                       key: str = "trip_id") -> pd.DataFrame:
    """把 assignments.cluster_id 左连接到输出表; 未匹配/未聚类留空."""
    if key not in frame.columns:
        raise ValueError(f"输出表缺少连接键 {key}")
    if assignments is None or assignments.empty or "cluster_id" not in assignments.columns:
        out = frame.copy()
        out["cluster_id"] = pd.array([pd.NA] * len(out), dtype="Int64")
        return out
    a = assignments[["unit_id", "cluster_id"]].rename(columns={"unit_id": key})
    if a[key].duplicated().any():
        raise ValueError("assignments 的 unit_id 不唯一, 拒绝多对一连接")
    out = frame.drop(columns=["cluster_id"], errors="ignore").merge(
        a, on=key, how="left", validate="m:1")
    out["cluster_id"] = out["cluster_id"].astype("Int64")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="多维 K-means 路段/趟次聚类 (并列轨)")
    parser.add_argument("--in", dest="inp",
                        default="data/processed/obd_text_segments.parquet",
                        help="输入表 (一单位一行)")
    parser.add_argument("--out", default="data/processed", help="输出目录")
    parser.add_argument("--grain", choices=("trip", "segment"), default=None)
    parser.add_argument("--feature-set", choices=tuple(FEATURE_SETS),
                        default="obd_v1")
    parser.add_argument("--ledger", default="data/processed/baseline_carbon.parquet",
                        help="主账段表 (补 plate/load; 不存在则跳过)")
    parser.add_argument("--k", type=int, default=K_DEFAULT)
    args = parser.parse_args()

    frame = pd.read_parquet(args.inp)
    ledger = pd.read_parquet(args.ledger) if Path(args.ledger).exists() else None
    result = build_cluster_table(frame, grain=args.grain,
                                 feature_set=args.feature_set,
                                 ledger=ledger, k=args.k)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    result["assignments"].to_parquet(out / RESULT_CLUSTERS, index=False)
    result["profiles"].to_parquet(out / RESULT_PROFILES, index=False)
    result["plate_map"].to_parquet(out / RESULT_PLATE_MAP, index=False)
    (out / RESULT_AUDIT).write_text(
        json.dumps(result["audit"], ensure_ascii=False, indent=2), encoding="utf-8")
    a = result["audit"]
    print(f"[segment_cluster] {a['status']} grain={a['grain']} "
          f"feature_set={a['feature_set']} n_valid={a['n_valid']}/{a['n_input']}")
    if a["status"] == "ok":
        print(f"[segment_cluster] K={a['k_selected']} sizes={a['cluster_sizes']} "
              f"inertia={a['inertia']} -> {out / RESULT_CLUSTERS}")
    else:
        print(f"[segment_cluster] skipped: {a.get('reason')}")
    for key in (RESULT_CLUSTERS, RESULT_PROFILES, RESULT_PLATE_MAP, RESULT_AUDIT):
        assert key in schema.RESULT_FILES.values(), f"{key} 未在 schema.RESULT_FILES 注册"


if __name__ == "__main__":
    main()
