# -*- coding: utf-8 -*-
"""碳因子口径：数据层与算法层共用的唯一因子实现。

因子不再是算法层私有常量。`preprocess` 在宽表落盘前调用
:func:`attach_carbon_columns` 一次算清，`optimizer` 只消费宽表中的碳列。
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd

FACTOR_VERSION = "JCB2023-BASE-v1"
FACTOR_SOURCE = "吴睿中等（2024）VSP 排放率；v=50 km/h；2023年基准碳排放计算结果.xlsx 因子表"

LOAD_ANCHORS = np.array([4.5, 12.0, 18.0, 30.0, 40.0, 45.0], dtype=float)
FACTOR_ANCHORS = np.array([394.56, 544.752, 759.24, 1055.952, 1374.672, 1581.84], dtype=float)
FACTOR_BY_CLASS_LOAD: Dict[Tuple[int, float], float] = {
    (1, 1.0): 394.560000, (1, 1.5): 394.560000, (1, 2.0): 394.560000,
    (1, 4.5): 394.560000, (1, 6.0): 394.560000, (1, 8.0): 394.560000,
    (2, 10.0): 458.928000, (2, 12.0): 544.752000, (2, 18.0): 759.240000,
    (2, 25.0): 896.592000,
    (3, 30.0): 1055.952000, (3, 32.0): 1119.696000, (3, 35.0): 1215.312000,
    (3, 38.0): 1310.928000, (3, 40.0): 1374.672000, (3, 45.0): 1581.840000,
    (3, 49.0): 1581.840000,
}
VSP_X = np.array([0.0, 1.0, 2.0, 3.0, 4.0], dtype=float)
VSP_MULT = np.array([0.82, 0.92, 1.00, 1.1268, 1.24], dtype=float)
REF_SPEED_KMH = 50.0
SECONDS_PER_HOUR = 3600.0
INDUSTRY_SPEED_KMH = 50.0
CARBON_COLUMNS = [
    "factor_gkm", "er_gs", "kg_co2", "kg_co2_phys",
    "delta_pct", "factor_version", "flag_factor_x",
]


def factor_lookup(load_t: Any) -> np.ndarray:
    arr = np.asarray(load_t, dtype=float)
    return np.interp(np.nan_to_num(arr, nan=30.0), LOAD_ANCHORS, FACTOR_ANCHORS)


def er_at(load_t: float, vsp: float = 2.0) -> float:
    base = float(factor_lookup(load_t)) / 72.0
    return base * float(np.interp(vsp, VSP_X, VSP_MULT))


def vsp_from_speed(speed_kmh: Any) -> np.ndarray:
    arr = np.asarray(speed_kmh, dtype=float)
    return np.clip(1.0 + (arr - 30.0) / 20.0, 0.0, 4.0)


def self_test() -> Dict[str, float]:
    er2 = er_at(30.0, 2.0)
    er3 = er_at(30.0, 3.0)
    return {
        "er30_vsp2": round(er2, 4),
        "er30_vsp3": round(er3, 4),
        "vsp3_dev_pct": round((er3 - er2) / er2 * 100.0, 4),
    }


def attach_carbon_columns(wide: pd.DataFrame) -> pd.DataFrame:
    """给任意宽表子集补齐因子与双轨碳排。"""
    if wide is None or len(wide) == 0:
        raise ValueError("宽表为空，无法计算基准碳排")
    df = wide.copy()
    for col in ("dist_km", "load_t", "speed_kmh", "vclass"):
        if col not in df.columns:
            raise ValueError("宽表缺少契约列：" + col)

    dist = pd.to_numeric(df["dist_km"], errors="coerce").fillna(0.1).clip(lower=0.1)
    load = pd.to_numeric(df["load_t"], errors="coerce").fillna(12.0).clip(lower=1.0, upper=60.0)
    speed = pd.to_numeric(df["speed_kmh"], errors="coerce")
    speed = speed.where(speed > 1.0, INDUSTRY_SPEED_KMH).clip(lower=1.0, upper=130.0)
    vclass = pd.to_numeric(df["vclass"], errors="coerce").fillna(2).astype(int).to_numpy()

    keys = list(zip(vclass, np.round(load, 2)))
    factor = np.array([FACTOR_BY_CLASS_LOAD.get((int(c), float(l)), np.nan)
                       for c, l in keys], dtype=float)
    missing = np.isnan(factor)
    if missing.any():
        factor[missing] = factor_lookup(load[missing])
    kg_legal = factor * dist / 1000.0
    vsp = vsp_from_speed(speed)
    er = factor / 72.0 * np.interp(vsp, VSP_X, VSP_MULT)
    kg_phys = er * (dist / speed) * SECONDS_PER_HOUR / 1000.0
    delta = np.where(kg_legal > 0, (kg_phys - kg_legal) / kg_legal * 100.0, 0.0)
    out_range = np.array([(int(c), float(l)) not in FACTOR_BY_CLASS_LOAD for c, l in keys])

    df["factor_gkm"] = np.round(factor, 6)
    df["er_gs"] = np.round(er, 6)
    df["kg_co2"] = kg_legal.astype(float)
    df["kg_co2_phys"] = kg_phys.astype(float)
    df["delta_pct"] = np.round(delta, 4)
    df["factor_version"] = FACTOR_VERSION
    df["flag_factor_x"] = out_range.astype(int)
    return df


__all__ = [
    "FACTOR_VERSION", "FACTOR_SOURCE", "FACTOR_BY_CLASS_LOAD",
    "LOAD_ANCHORS", "FACTOR_ANCHORS", "VSP_X", "VSP_MULT",
    "CARBON_COLUMNS", "factor_lookup", "er_at", "vsp_from_speed",
    "self_test", "attach_carbon_columns",
]
