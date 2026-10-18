"""数据读取层：前端唯一的数据入口。

规矩：
1. 只读 data/processed/，绝不调用核心算法；
2. 缺文件、缺依赖、缺列，一律返回空表 + 原因，不抛异常（答辩现场不能红屏）；
3. 页面只能通过本模块取数，不允许直接 pd.read_parquet。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"

# ---- 结果文件清单（C1 定字段会上确认）----
RESULT_FILES: Dict[str, str] = {
    "wide": "wide_data.parquet",
    "segments": "baseline_carbon.parquet",
    "scenarios": "final_report.parquet",
    "hotspots": "hotspots.parquet",
    "od_band": "od_band.parquet",
    "ledger": "ledger.parquet",
    "disclosure": "disclosure.json",
    "kpi": "kpi.json",
}

# ---- 冻结的列名：只增不改 ----
SEGMENT_COLUMNS: List[str] = [
    "waybill_no", "plate", "vehicle_type", "vclass", "load_t", "load_raw_t",
    "mass_basis", "origin", "dest", "od_pair", "dist_km", "month",
    "factor_gkm", "er_gs", "kg_co2", "kg_co2_phys", "delta_pct",
    "flag_dup", "flag_anomaly", "flag_basis",
]

SCENARIO_COLUMNS: List[str] = [
    "scenario", "emission_t", "reduction_pct", "capex_wan",
    "opex_save_wan", "payback_years", "unit_cost_yuan_t", "meets_target",
]

LEDGER_COLUMNS: List[str] = [
    "accounting_date", "boundary", "emission_t", "quota_gap", "book_cost", "note",
]


def path_of(name: str) -> Path:
    return PROCESSED / RESULT_FILES[name]


def _has(df: pd.DataFrame, col: str) -> bool:
    return not df.empty and col in df.columns


def column_ok(df: pd.DataFrame, cols: List[str]) -> List[str]:
    """返回缺失的列名；页面据此显示"该表还没就绪"。"""
    if df is None or df.empty:
        return list(cols)
    return [c for c in cols if c not in df.columns]


@st.cache_data(show_spinner=False)
def available() -> Dict[str, bool]:
    """哪些结果文件已经存在。"""
    return {k: (PROCESSED / f).exists() for k, f in RESULT_FILES.items()}


@st.cache_data(show_spinner=False)
def _read_parquet(filename: str) -> pd.DataFrame:
    p = PROCESSED / filename
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except Exception:
        # 没装 pyarrow 或者文件损坏，都退化成空表
        return pd.DataFrame()


@st.cache_data(show_spinner=False)
def _read_json(filename: str) -> Dict[str, Any]:
    p = PROCESSED / filename
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ---------------- 主表 ----------------

def wide() -> pd.DataFrame:
    return _read_parquet(RESULT_FILES["wide"])


def segments() -> pd.DataFrame:
    return _read_parquet(RESULT_FILES["segments"])


def scenarios() -> pd.DataFrame:
    return _read_parquet(RESULT_FILES["scenarios"])


def hotspots(dim: str | None = None) -> pd.DataFrame:
    df = _read_parquet(RESULT_FILES["hotspots"])
    if dim and _has(df, "dimension"):
        df = df[df["dimension"] == dim]
    return df


def od_band() -> pd.DataFrame:
    return _read_parquet(RESULT_FILES["od_band"])


def ledger() -> pd.DataFrame:
    return _read_parquet(RESULT_FILES["ledger"])


def disclosure() -> Dict[str, Any]:
    return _read_json(RESULT_FILES["disclosure"])


# ---------------- 汇总指标 ----------------

@st.cache_data(show_spinner=False)
def kpis() -> Dict[str, Any]:
    """首页与第 1 页的指标卡。优先读 kpi.json，缺了就从 segments 现算。"""
    d: Dict[str, Any] = dict(_read_json(RESULT_FILES["kpi"]))
    seg = segments()

    if seg.empty:
        for key in ("total_kg", "total_km", "segments", "avg_speed",
                    "heavy_share", "dup_rows", "anomaly_rows", "basis_rows"):
            d.setdefault(key, None)
        return d

    if _has(seg, "kg_co2"):
        d.setdefault("total_kg", float(pd.to_numeric(seg["kg_co2"], errors="coerce").sum()))
    if _has(seg, "dist_km"):
        d.setdefault("total_km", float(pd.to_numeric(seg["dist_km"], errors="coerce").sum()))
    d.setdefault("segments", int(len(seg)))

    if _has(seg, "speed_kmh"):
        s = pd.to_numeric(seg["speed_kmh"], errors="coerce")
        s = s[(s > 1) & (s < 100)]
        d.setdefault("avg_speed", float(s.mean()) if len(s) else None)

    if _has(seg, "vclass") and _has(seg, "kg_co2"):
        vc = pd.to_numeric(seg["vclass"], errors="coerce")
        kg = pd.to_numeric(seg["kg_co2"], errors="coerce")
        total = float(kg.sum()) or 1.0
        d.setdefault("heavy_share", float(kg[vc == 3].sum()) / total)

    for col, key in (("flag_dup", "dup_rows"),
                     ("flag_anomaly", "anomaly_rows"),
                     ("flag_basis", "basis_rows")):
        if _has(seg, col):
            d.setdefault(key, int(pd.to_numeric(seg[col], errors="coerce").fillna(0).sum()))

    return d


def fmt(value: Any, digits: int = 0) -> str:
    """把数字变好看；None 显示为横杠。"""
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if digits == 0:
        return "{:,.0f}".format(v)
    return "{:,.{}f}".format(v, digits)
