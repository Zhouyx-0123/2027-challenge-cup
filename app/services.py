"""数据读取层（A0）：前端唯一的数据入口。

规矩：
1. 只读 data/processed/，绝不 import src.*、绝不调用核心算法；
2. 缺文件、缺列、文件损坏一律返回空表 + 原因，不抛异常（答辩现场不能红屏）；
3. 页面只能通过本模块取数，不允许直接 pd.read_parquet；
4. 结果表由 python -m src.pipeline 预先生成，页面里没有一行业务公式。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"

# ---- 结果文件清单（与 src/pipeline.py 输出一一对应）----
RESULT_FILES: Dict[str, str] = {
    "wide": "wide_data.parquet",
    "segments": "baseline_carbon.parquet",
    "scenarios": "final_report.parquet",
    "hotspots": "hotspots.parquet",
    "od_band": "od_band.parquet",
    "ledger": "ledger.parquet",
    "disclosure": "disclosure.json",
    "kpi": "kpi.json",
    "quality": "data_quality.json",
    "gate": "gate_report.json",
    "run_log": "run_log.json",
    "source": "_source.json",
}

# ---- 冻结的列名：只增不改 ----
SEGMENT_COLUMNS: List[str] = [
    "waybill_no", "plate", "vehicle_type", "vclass", "load_t", "load_raw_t",
    "mass_basis", "origin", "dest", "od_pair", "od_site", "dist_km", "month",
    "factor_gkm", "er_gs", "kg_co2", "kg_co2_phys", "delta_pct", "speed_kmh",
    "vtype_code", "dep_site", "arr_site",
    "cargo_weight_kg", "cargo_volume_cm3", "cargo_pieces", "complete_ts",
    "flag_dup", "flag_anomaly", "flag_basis", "flag_estimated", "estimate_note",
]

# 页面能不能出数，只看这四列在不在（缺任何一列，页面停在数据检查，不编数字）
CORE_COLUMNS: List[str] = ["waybill_no", "od_pair", "dist_km", "kg_co2"]

SCENARIO_COLUMNS: List[str] = [
    "scenario", "emission_t", "reduction_pct", "capex_wan",
    "opex_save_wan", "payback_years", "unit_cost_yuan_t", "meets_target",
]

LEDGER_COLUMNS: List[str] = [
    "accounting_date", "boundary", "emission_t", "quota_gap", "book_cost", "note",
]

DISCLOSURE_KEYS: List[str] = [
    "boundary", "factor_source", "assumptions", "period", "annual_factor", "limitations",
]


def path_of(name: str) -> Path:
    return PROCESSED / RESULT_FILES.get(name, "")


def _has(df: pd.DataFrame, col: str) -> bool:
    return df is not None and not df.empty and col in df.columns


def column_ok(df: pd.DataFrame, cols: List[str]) -> List[str]:
    """返回缺失的列名；页面据此显示“该表还没就绪”。"""
    if df is None or df.empty:
        return list(cols)
    return [c for c in cols if c not in df.columns]


@st.cache_data(show_spinner=False)
def available() -> Dict[str, bool]:
    return {k: (PROCESSED / f).exists() for k, f in RESULT_FILES.items()}


@st.cache_data(show_spinner=False)
def _read_parquet(filename: str) -> pd.DataFrame:
    p = PROCESSED / filename
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except Exception:
        # 没装 pyarrow 或文件损坏，都退化成空表；页面显示空状态而不是红屏
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


def quality() -> Dict[str, Any]:
    return _read_json(RESULT_FILES["quality"])


def gate() -> Dict[str, Any]:
    return _read_json(RESULT_FILES["gate"])


def run_log() -> Dict[str, Any]:
    return _read_json(RESULT_FILES["run_log"])


def source() -> Dict[str, Any]:
    return _read_json(RESULT_FILES["source"])


def source_name() -> str:
    return str(source().get("source", "unknown"))


def is_mock() -> bool:
    """演示数据：_source.json 说 demo，或旧版 _mock.flag 还在。"""
    if source_name() == "demo":
        return True
    return (PROCESSED / "_mock.flag").exists() and source_name() != "real"


# ---------------- 汇总指标 ----------------

@st.cache_data(show_spinner=False)
def kpis() -> Dict[str, Any]:
    """优先读 kpi.json（pipeline 已算好）；缺了才从段表做展示性汇总。"""
    d: Dict[str, Any] = dict(_read_json(RESULT_FILES["kpi"]))
    seg = segments()

    if seg.empty:
        for key in ("total_kg", "total_km", "segments", "avg_speed", "avg_speed_clean",
                    "heavy_share", "dup_rows", "anomaly_rows", "basis_rows",
                    "estimated_rows", "od_ratio_median", "od_pairs",
                    "main_reduction_pct", "rows_wide", "valid_speed_rows"):
            d.setdefault(key, None)
        return d

    if _has(seg, "kg_co2"):
        d.setdefault("total_kg", float(pd.to_numeric(seg["kg_co2"], errors="coerce").fillna(0).sum()))
    if "total_kg" in d and d.get("e0_t") is None:
        d["e0_t"] = float(d["total_kg"] or 0) / 1000.0
    if _has(seg, "dist_km"):
        d.setdefault("total_km", float(pd.to_numeric(seg["dist_km"], errors="coerce").fillna(0).sum()))
    d.setdefault("segments", int(len(seg)))

    if _has(seg, "speed_kmh"):
        s = pd.to_numeric(seg["speed_kmh"], errors="coerce")
        valid = s.notna() & (s > 0)
        clean = valid & s.between(1, 100)
        d.setdefault("valid_speed_rows", int(valid.sum()))
        d.setdefault("avg_speed", float(s[valid].mean()) if valid.any() else None)
        d.setdefault("avg_speed_clean", float(s[clean].mean()) if clean.any() else None)

    if _has(seg, "vclass") and _has(seg, "kg_co2"):
        vc = pd.to_numeric(seg["vclass"], errors="coerce")
        kg = pd.to_numeric(seg["kg_co2"], errors="coerce")
        total = float(kg.sum()) or 1.0
        d.setdefault("heavy_share", float(kg[vc == 3].sum()) / total)

    for col, key in (("flag_dup", "dup_rows"),
                     ("flag_anomaly", "anomaly_rows"),
                     ("flag_basis", "basis_rows"),
                     ("flag_estimated", "estimated_rows")):
        if _has(seg, col):
            d.setdefault(key, int(pd.to_numeric(seg[col], errors="coerce").fillna(0).sum()))

    band = od_band()
    if not band.empty and "ratio" in band.columns:
        d.setdefault("od_ratio_median", float(pd.to_numeric(band["ratio"], errors="coerce").median()))
        d.setdefault("od_pairs", int(len(band)))

    schemes = scenarios()
    if not schemes.empty and "is_main" in schemes.columns and "reduction_pct" in schemes.columns:
        main = schemes[schemes["is_main"] == True]
        if not main.empty:
            d.setdefault("main_reduction_pct", float(main.iloc[0]["reduction_pct"]))

    return d


def flag_counts() -> Dict[str, int]:
    """四类 FLAG 的计数：优先质量报告（去重后口径），再退回 kpi / 段表。"""
    q = quality()
    k = kpis()
    wide_df = wide()
    seg = segments()

    def pick(qkey: str, kkey: str, col: str, df: pd.DataFrame, default: int = 0) -> int:
        for value in (q.get(qkey), k.get(kkey)):
            try:
                if value is not None:
                    return int(value)
            except (TypeError, ValueError):
                pass
        if _has(df, col):
            return int(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())
        return default

    return {
        "rows_wide": int(q.get("rows_wide") or k.get("rows_wide") or len(wide_df) or 0),
        "rows_dedup": int(q.get("rows_dedup") or k.get("segments") or len(seg) or 0),
        "dup_rows": pick("dup_rows", "dup_rows", "flag_dup", wide_df),
        "anomaly_rows": pick("anomaly_rows", "anomaly_rows", "flag_anomaly", seg),
        "basis_rows": pick("basis_rows", "basis_rows", "flag_basis", seg),
        "estimated_rows": pick("estimated_rows", "estimated_rows", "flag_estimated", seg),
    }


# ---------------- 文件状态 ----------------

@st.cache_data(show_spinner=False)
def file_status() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for key, filename in RESULT_FILES.items():
        p = PROCESSED / filename
        exists = p.exists()
        count: Optional[int] = None
        if exists and filename.endswith(".parquet"):
            count = len(_read_parquet(filename))
        updated = "—"
        if exists:
            try:
                updated = datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M:%S")
            except Exception:
                updated = "—"
        rows.append({
            "结果表": key,
            "文件": filename,
            "状态": "已就绪" if exists else "未生成",
            "行数": count if count is not None else "—",
            "更新时间": updated,
        })
    return rows


def fmt(value: Any, digits: int = 0) -> str:
    """把数字变好看；None 显示为横杠。"""
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if v != v:
        return "—"
    if digits == 0:
        return "{:,.0f}".format(v)
    return "{:,.{}f}".format(v, digits)


def pct(value: Any, digits: int = 1) -> str:
    try:
        return "{:.{}f}%".format(float(value) * 100.0, digits)
    except (TypeError, ValueError):
        return "—"
