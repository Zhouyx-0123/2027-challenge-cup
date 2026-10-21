# -*- coding: utf-8 -*-
"""契约层：结果文件名、列名、校验规则——**全项目唯一一份**。

为什么要有这个文件
------------------
在 v3.0 之前，同一批列名在三个地方各写一遍：
``src/pipeline.py:WIDE_REQUIRED/SEG_REQUIRED``、``app/services.py:SEGMENT_COLUMNS``、
``src/optimizer.py`` 里的逐列检查。三份拷贝的必然结果是**漂移**：上游加一列，
下游的"缺列即停"检查不会报警，只会悄悄少画一个图。

本文件不导入 pandas 之外的东西，不读文件、不做计算——只放常量与两个校验函数：

* :func:`validate` 返回问题清单（供 pipeline 打印质量报告，不抛异常）；
* :func:`require`  缺列即抛 :class:`SchemaError`（供读取侧硬校验）。

改列名只改这里，并在 ``docs/07_数据字典.md`` 登记。**只增不改**是原规则，保留。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Mapping, Sequence

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"

# --------------------------------------------------------------------------
# 结果文件清单（pipeline 写、app 读，两边共用这一张表）
# --------------------------------------------------------------------------
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
    # T7 · 路线情景（可选；缺失时页面自动回退离线模式）
    "routes": "route_od.parquet",
    "route_carbon": "route_carbon.parquet",
    "route_select": "route_select.parquet",
}

# --------------------------------------------------------------------------
# 宽表 / 段表
# --------------------------------------------------------------------------
WIDE_REQUIRED: List[str] = [
    "waybill_no", "plate", "vehicle_type", "vclass", "load_t", "origin", "dest",
    "od_pair", "od_site", "dist_km", "month",
    "flag_dup", "flag_anomaly", "flag_basis", "flag_estimated",
]

#: 段表在宽表基础上新增的派生列（由 optimizer 写）
SEG_DERIVED: List[str] = ["factor_gkm", "kg_co2", "kg_co2_phys", "delta_pct"]
SEG_REQUIRED: List[str] = WIDE_REQUIRED + SEG_DERIVED

#: 冻结的段表列（只增不改）；页面画图依赖它
SEGMENT_COLUMNS: List[str] = [
    "waybill_no", "plate", "vehicle_type", "vclass", "load_t", "load_raw_t",
    "mass_basis", "origin", "dest", "od_pair", "od_site", "dist_km", "month",
    "factor_gkm", "er_gs", "kg_co2", "kg_co2_phys", "delta_pct", "speed_kmh",
    "vtype_code", "dep_site", "arr_site",
    "cargo_weight_kg", "cargo_volume_cm3", "cargo_pieces", "complete_ts",
    "flag_dup", "flag_anomaly", "flag_basis", "flag_estimated", "estimate_note",
]

#: 页面能不能出数，只看这四列（缺任何一列，页面停在数据检查，不编数字）
CORE_COLUMNS: List[str] = ["waybill_no", "od_pair", "dist_km", "kg_co2"]

# --------------------------------------------------------------------------
# 方案表 / 台账 / 披露
# --------------------------------------------------------------------------
SCENARIO_COLUMNS: List[str] = [
    "scenario", "emission_t", "reduction_pct", "capex_wan",
    "opex_save_wan", "payback_years", "unit_cost_yuan_t", "meets_target",
    # 口径/资格元数据；旧结果表缺这些列时页面仍可跑
    "data_source", "e0_t", "calculation_mode", "parameter_status",
    "operational_status", "decision_eligible", "boundary",
]

#: 台账六字段（docs/15 §2，顺序即表格顺序）
SIX_LEDGER: List[str] = ["accounting_date", "boundary", "emission_t",
                         "quota_gap", "book_cost", "note"]
LEDGER_COLUMNS: List[str] = SIX_LEDGER

#: 披露六要素（docs/15 §3）
SIX_DISCLOSURE: List[str] = ["boundary", "factor_source", "assumptions",
                             "period", "annual_factor", "limitations"]
DISCLOSURE_KEYS: List[str] = SIX_DISCLOSURE

# --------------------------------------------------------------------------
# 四类数据标记（灰底=重复、黄底=异常/口径、黄框=估算）
# --------------------------------------------------------------------------
FLAG_COLUMNS: List[str] = ["flag_dup", "flag_anomaly", "flag_basis", "flag_estimated"]

FLAG_LABELS: Mapping[str, str] = {
    "flag_dup": "重复（已去重，不进基准）",
    "flag_anomaly": "时间戳/速度异常（保留计碳，不参与均速）",
    "flag_basis": "载重口径换算（载质量/总质量/准牵引）",
    "flag_estimated": "估算值（某字段被历史/行业均值填补）",
}


class SchemaError(Exception):
    """读取侧硬校验失败：缺列、空表或类型不可用。"""


def validate(frame: pd.DataFrame | None, required: Sequence[str],
             name: str = "表") -> List[str]:
    """返回问题清单（空列表 = 通过）。不抛异常，供质量报告使用。"""
    if frame is None:
        return [f"{name}：未加载"]
    if frame.empty:
        return [f"{name}：空表"]
    missing = [c for c in required if c not in frame.columns]
    return [f"{name}：缺列 {missing}"] if missing else []


def require(frame: pd.DataFrame | None, required: Sequence[str],
            name: str = "表") -> pd.DataFrame:
    """缺列即抛 SchemaError；错误信息带缺的列与现有列，便于 5 秒定位。"""
    problems = validate(frame, required, name)
    if problems:
        detail = "；".join(problems)
        columns = [] if frame is None else [str(c) for c in frame.columns]
        raise SchemaError(f"{detail}\n  现有列：{columns}\n"
                          f"  处理：确认上游是否按 docs/07 冻结字段；"
                          f"联调期可先跑 python -m src.pipeline")
    return frame


def flag_counts(frame: pd.DataFrame) -> Dict[str, int]:
    """四类标记的计数（只数行，不改口径）。"""
    out: Dict[str, int] = {}
    for col in FLAG_COLUMNS:
        if col in frame.columns:
            out[col] = int(pd.to_numeric(frame[col], errors="coerce").fillna(0).sum())
    return out
