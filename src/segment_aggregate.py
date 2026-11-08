# -*- coding: utf-8 -*-
"""trip_proxy 主表的**唯一聚合入口**（把 docs/60 的口径变成可执行约束）。

设计目的
--------
主表一行=一趟，下游最容易犯的错是：
  · 对 `ef_g_km` / `speed_kmh` 直接 `mean()`；
  · 把 131 条 `passed=False` 的趟当 0 混进分母；
  · 把 `n_waybills` 当碳权重；
  · 报“平均速度”却不说是趟等权、时间加权还是距离加权。

本模块只提供一种安全聚合：**先过滤、只加可加字段、比率先加后除**。
"""
from __future__ import annotations

from typing import List, Sequence, Union

import pandas as pd

try:  # 包内导入（pytest / python -m）
    from .segment_master import PAPER_BIN_ORDER
except ImportError:  # 直接把 src 加进 sys.path 的调用方
    from segment_master import PAPER_BIN_ORDER

#: 允许的分组键（都是主表里的稳定维度）
ALLOWED_KEYS = {
    "trip_id", "plate_id", "month", "paper_bin", "cluster_id",
    "vehicle_group", "vehicle_type",
}

#: 聚合前必须存在的列
REQUIRED_COLUMNS = {
    "trip_id", "passed", "distance_km", "duration_h", "carbon_kg",
    "carbon_kg_phys", "n_waybills", "speed_kmh", "load_t", "plate_id",
}

#: 输出列（顺序固定，方便前端/台账直接消费）
AGGREGATE_COLUMNS = [
    "trips", "n_waybills", "distance_km", "duration_h",
    "carbon_kg", "carbon_kg_phys", "ef_g_km",
    "avg_speed_trip_kmh", "avg_speed_kmh", "avg_speed_dist_weighted_kmh",
    "avg_load_t", "avg_load_trip_t", "carbon_share_pct", "n_plates",
]


def _normalise_keys(by: Union[str, Sequence[str]]) -> List[str]:
    keys = [by] if isinstance(by, str) else list(by)
    if not keys:
        raise ValueError("至少需要一个分组键")
    unknown = sorted(set(keys) - ALLOWED_KEYS)
    if unknown:
        raise ValueError(f"不允许的分组键: {unknown}; 允许: {sorted(ALLOWED_KEYS)}")
    if len(set(keys)) != len(keys):
        raise ValueError(f"分组键重复: {keys}")
    return keys


def aggregate_master(
    master: pd.DataFrame,
    by: Union[str, Sequence[str]],
    *,
    include_invalid: bool = False,
) -> pd.DataFrame:
    """按 `by` 聚合主表，返回可加字段 + 重算的比率字段。

    Parameters
    ----------
    master:
        `jd_segment_master.parquet` 的 DataFrame。
    by:
        分组键，如 `"paper_bin"`、`"cluster_id"`、`["month", "plate_id"]`。
    include_invalid:
        默认 False，只聚合 `passed=True`；True 时把 131 条未通过门禁的趟
        也算进来（此时 `carbon_share_pct` 的分母是全量，必须显式说明）。
    """
    missing = sorted(REQUIRED_COLUMNS - set(master.columns))
    if missing:
        raise ValueError(f"主表缺列: {missing}")
    keys = _normalise_keys(by)

    data = master.copy()
    if not include_invalid:
        data = data[data["passed"].fillna(False).astype(bool)]
    # 缺碳/缺里程的趟不能悄悄当 0；登记在调用方，聚合时剔除
    data = data[data["carbon_kg"].notna() & data["distance_km"].notna()].copy()
    if data.empty:
        return pd.DataFrame(columns=keys + AGGREGATE_COLUMNS)

    total_carbon = float(data["carbon_kg"].sum())
    grouped = data.groupby(keys, dropna=False, observed=True)
    out = grouped.agg(
        trips=("trip_id", "size"),
        n_waybills=("n_waybills", "sum"),
        distance_km=("distance_km", "sum"),
        duration_h=("duration_h", "sum"),
        carbon_kg=("carbon_kg", "sum"),
        carbon_kg_phys=("carbon_kg_phys", "sum"),
        n_plates=("plate_id", "nunique"),
    ).reset_index()

    # 非可加字段：先加后除 / 趟等权
    means = grouped.agg(
        avg_speed_trip_kmh=("speed_kmh", "mean"),
        avg_load_trip_t=("load_t", "mean"),
    ).reset_index()
    weighted = (
        data.assign(
            _speed_km=data["speed_kmh"] * data["distance_km"],
            _load_tkm=data["load_t"] * data["distance_km"],
        )
        .groupby(keys, dropna=False, observed=True)
        .agg(_speed_km=("_speed_km", "sum"), _load_tkm=("_load_tkm", "sum"))
        .reset_index()
    )
    out = out.merge(means, on=keys, how="left", validate="one_to_one")
    out = out.merge(weighted, on=keys, how="left", validate="one_to_one")

    out["ef_g_km"] = (
        out["carbon_kg"] * 1000.0 / out["distance_km"]
    ).where(out["distance_km"] > 0)
    out["avg_speed_kmh"] = (
        out["distance_km"] / out["duration_h"]
    ).where(out["duration_h"] > 0)
    out["avg_speed_dist_weighted_kmh"] = (
        out["_speed_km"] / out["distance_km"]
    ).where(out["distance_km"] > 0)
    out["avg_load_t"] = (
        out["_load_tkm"] / out["distance_km"]
    ).where(out["distance_km"] > 0)
    out["carbon_share_pct"] = (
        out["carbon_kg"] / total_carbon * 100.0 if total_carbon else pd.NA
    )

    out = out[keys + AGGREGATE_COLUMNS].copy()
    if "paper_bin" in keys:
        order = out["paper_bin"].map({b: i for i, b in enumerate(PAPER_BIN_ORDER)})
        if order.notna().all():
            out["_bin_order"] = order.astype(int)
            sort_cols = ["_bin_order" if k == "paper_bin" else k for k in keys]
            out = out.sort_values(sort_cols, kind="mergesort")
            out = out.drop(columns="_bin_order")
            return out.reset_index(drop=True)
    out = out.sort_values(keys, kind="mergesort").reset_index(drop=True)
    return out
