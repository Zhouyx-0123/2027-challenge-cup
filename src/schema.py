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
    # D4 · 可比较 OD 的逐车型构成（第 3 页下钻预计算）
    "od_vehicle_mix": "od_vehicle_mix.parquet",
    # D5 · λ 敏感性曲线（回本年限 × λ 预计算网格，第 4 页只读）
    "lambda_curve": "lambda_curve.parquet",
    # O6 · 方案 × 哪条线（逐线路 C1–C5 结论；仅参数化方案产出）
    "route_constraints": "scenario_route_constraint.parquet",
    "ledger": "ledger.parquet",
    "disclosure": "disclosure.json",
    "kpi": "kpi.json",
    # D4 · E₀ 构成下钻（月份子集 × 车型大类子集预计算，第 5 页只读）
    "composition": "composition.json",
    "quality": "data_quality.json",
    "gate": "gate_report.json",
    "run_log": "run_log.json",
    "source": "_source.json",
    # 情景演算页（docs/20 §11）：车队真实统计，计算器默认值来源
    "fleet_stats": "fleet_stats.json",
    # T7 · 路线情景（可选；缺失时页面自动回退离线模式）
    "routes": "route_od.parquet",
    "route_carbon": "route_carbon.parquet",
    "route_carbon_od": "route_carbon_od.parquet",
    "route_select": "route_select.parquet",
    "route_candidates": "route_candidates.parquet",
    "route_candidate_carbon": "route_candidate_carbon.parquet",
    # 上海—北京 OSM 碳基线（docs/35 D2/D3）：非实时 · 固定因子 · 道路等级 proxy
    "route_baseline_steps": "route_baseline_sh_bj_steps.parquet",
    "route_baseline_summary": "route_baseline_sh_bj_summary.json",
    # HYC 物理行程审计（docs/39）：只读审计层，不替代 E0 主口径
    "vsp_audit": "vsp_audit.json",
    "vsp_physical_trips": "vsp_physical_trips.parquet",
    "vsp_vehicle_groups": "vsp_vehicle_groups.parquet",
    # OBD+GPS 实测碳排轨（docs/52）：**独立轨道**，不进 E₀ 主口径、不参与主闸门
    "obd_points": "obd_points.parquet",
    "obd_vehicle_carbon": "obd_vehicle_carbon.parquet",
    "obd_trips": "obd_trips.parquet",
    "obd_summary": "obd_summary.json",
    # VSP 模型 × OBD 碳平衡实测 对照验证轨（docs/57）：回答"测的和估的对不对得上"
    "obd_vsp_crosscheck": "obd_vsp_crosscheck.parquet",
    "obd_vsp_crosscheck_points": "obd_vsp_crosscheck_points.parquet",
    "obd_vsp_crosscheck_summary": "obd_vsp_crosscheck_summary.json",
    # 碳会计 A0–A6 交付轨（docs/54）：**审计轨**，WTW 口径独立于工程侧 TTW 主口径；
    # 由 src/carbon_ledger_export.py 从 carbon_ledger/ 的 Excel 导出，前端只读
    "carbon_ledger_trips": "carbon_ledger_trips.parquet",
    "carbon_ledger_evidence": "carbon_ledger_evidence.parquet",
    "carbon_ledger_summary": "carbon_ledger_summary.json",
    "carbon_ledger_gates": "carbon_ledger_gates.json",
    "carbon_ledger_quota": "carbon_ledger_quota.json",
    "carbon_ledger_activity_readiness": "carbon_ledger_activity_readiness.json",
    "carbon_ledger_rules": "carbon_ledger_rules.json",
}

# --------------------------------------------------------------------------
# 宽表 / 段表
# --------------------------------------------------------------------------
#: 数据层一次固化的碳因子与双轨碳排列
WIDE_CARBON_COLUMNS: List[str] = [
    "factor_gkm", "er_gs", "kg_co2", "kg_co2_phys",
    "delta_pct", "factor_version", "flag_factor_x",
]

WIDE_REQUIRED: List[str] = [
    "waybill_no", "plate", "vehicle_type", "vclass", "load_t", "origin", "dest",
    "od_pair", "od_site", "dist_km", "month",
    "flag_dup", "flag_anomaly", "flag_basis", "flag_estimated",
    *WIDE_CARBON_COLUMNS,
]

#: 段表在宽表基础上新增的派生列（分摊层写入，不再重复算因子）
SEG_DERIVED: List[str] = ["kg_co2_gross", "trip_id", "allocation_share"]
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
#: O6 约束结论列（final_report 契约列的子集；None = 未评估）
CONSTRAINT_COLUMNS: List[str] = [
    "constraint_status", "constraint_note",
    "c1_range_ok", "c2_window_ok", "c3_capacity_ok", "c4_charging_ok", "c5_winter_ok",
]

SCENARIO_COLUMNS: List[str] = [
    "scenario", "emission_t", "reduction_pct", "capex_wan",
    "opex_save_wan", "payback_years", "unit_cost_yuan_t", "meets_target",
    # 口径/资格元数据；旧结果表缺这些列时页面仍可跑
    "data_source", "e0_t", "calculation_mode", "parameter_status",
    "operational_status", "decision_eligible", "boundary",
    # O6 · 运营约束结论列（None = 未评估；legacy 行为是"未评估"字符串 + None）
    *CONSTRAINT_COLUMNS,
]

#: 方案 × 线路约束表（scenario_route_constraint.parquet）契约列
ROUTE_CONSTRAINT_COLUMNS: List[str] = [
    "order", "scenario", "vclass", "vclass_label", "vehicle",
    "od_key", "od_label", "n_seg", "km", "kg", "route_km",
    "avg_speed_kmh", "max_cargo_t", "usable_range_km", "deploy_tier",
    "c1_range_ok", "c2_window_ok", "c3_capacity_ok", "c4_charging_ok", "c5_winter_ok",
    "constraint_status", "constraint_note", "km_share_pct",
]

#: 上海—北京 OSM 碳基线 · 逐 step 表契约列（docs/35 §5.1）
BASELINE_STEP_COLUMNS: List[str] = [
    "od_key", "provider", "traffic_mode", "step_index", "distance_km",
    "duration_min", "speed_kmh", "road_name", "road_ref", "road_class",
    "road_class_source", "road_class_confidence", "toll_flag", "toll_yuan",
    "traffic_lights", "carbon_kg", "factor_version", "factor_mode", "vehicle",
    "load_t", "geometry_geojson",
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
