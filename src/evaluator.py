# -*- coding: utf-8 -*-
"""E1–E6 分析与财务模块，Owner：曾广。

兼容 build_analysis() / build_schemes(seg, source) 及既有三张结果表。
默认保留历史标定并明确标识未核验；原始成本参数齐备后启用参数化计算。
CLI 默认只读：python -m src.evaluator
纯计算自测：python -m src.evaluator --self-test
详细输入单位、公式和接线说明见 docs/08_口径与假设清单.md 的 COST 节。

E4 接线（2026-10-22）：真实数据（_source.json = real）时，build_cost_scenarios
由段表实时派生主推方案 S5 的逐车组参数（底稿 表14/表18/表19 链），
build_schemes 输出**混合表**——参数化行 + 未取得车组底稿的历史标定行
（decision_eligible=False、约束列"未评估"）；同时产出
scenario_route_constraint.parquet（方案 × 哪条线，O6 逐线 C1–C5 结论）。
演示/未知来源一律回退历史标定，不用演示数据冒充参数化结果。
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from dataclasses import dataclass, fields, replace
from numbers import Real
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = ROOT / "data" / "processed"
BASELINE_PATH = PROCESSED_DIR / "baseline_carbon.parquet"
HOTSPOTS_PATH = PROCESSED_DIR / "hotspots.parquet"
OD_BAND_PATH = PROCESSED_DIR / "od_band.parquet"
SCHEMES_PATH = PROCESSED_DIR / "final_report.parquet"
SOURCE_PATH = PROCESSED_DIR / "_source.json"

# 方案表：减排率 / 投资 / 年节省来自答辩稿表 19–21 的静态标定；
# 排放量按本次数据期的 E₀ 等比缩放，保证“同一份数据、同一套账”。
# 单位：capex_wan = 万元；opex_save_wan = 万元/年。
SCENARIOS: List[Dict[str, Any]] = [
    {
        "order": 0, "scenario": "S0 柴油基准（现状）",
        "reduction_pct": 0.0, "capex_wan": 0.0, "opex_save_wan": 0.0,
        "meets_target": False, "is_main": False,
        "basis": "现状基准：与第 1 页 E₀ 同源，不做事后调整",
    },
    {
        "order": 1, "scenario": "S1 全电动",
        "reduction_pct": 31.11, "capex_wan": 17012.3, "opex_save_wan": 3123.0,
        "meets_target": False, "is_main": False,
        "basis": "静态标定（答辩稿表 19–21）：全车队电动化，含补能改造",
    },
    {
        "order": 2, "scenario": "S2 轻中电 + 重灰氢",
        "reduction_pct": 31.80, "capex_wan": 9000.0, "opex_save_wan": 1500.0,
        "meets_target": False, "is_main": False,
        "basis": "静态标定（答辩稿表 19–21）：重卡灰氢，轻中卡电动",
    },
    {
        "order": 3, "scenario": "S3 轻中电 + 重蓝氢",
        "reduction_pct": 56.50, "capex_wan": 9500.0, "opex_save_wan": 1600.0,
        "meets_target": False, "is_main": False,
        "basis": "静态标定（答辩稿表 19–21）：蓝氢带 CCS，减排按使用端计",
    },
    {
        "order": 4, "scenario": "S4 轻中电 + 重绿氢 WTW",
        "reduction_pct": 68.90, "capex_wan": 10500.0, "opex_save_wan": 1700.0,
        "meets_target": False, "is_main": False,
        "basis": "静态标定（答辩稿表 19–21）：绿氢全生命周期口径",
    },
    {
        "order": 5, "scenario": "S5 轻中电 + 重氢（主推）",
        "reduction_pct": 77.40, "capex_wan": 4809.0, "opex_save_wan": 500.8,
        "meets_target": True, "is_main": True,
        "basis": "静态标定（答辩稿表 19–21）：117 辆口径；4,809 万 / 9.6 年",
    },
    {
        "order": 7, "scenario": "S7 绿氢 WTW + 电网清洁化",
        "reduction_pct": 71.50, "capex_wan": 4809.0, "opex_save_wan": 500.8,
        "meets_target": True, "is_main": False,
        "basis": "静态标定（答辩稿表 19–21）：保守口径，用于边界对照",
    },
]

TARGET_REDUCTION_PCT = 70.0
DIST_EDGES = [0.0, 100.0, 300.0, 600.0, 1000.0, float("inf")]
DIST_LABELS = ["0–100 km", "100–300 km", "300–600 km", "600–1000 km", ">1000 km"]


def _save_parquet(df: pd.DataFrame, path: Path, tries: int = 4) -> None:
    """先写同目录临时文件，再原子替换单张表；不留下半张 parquet。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        df.to_parquet(temporary, index=False)
        for attempt in range(tries):
            try:
                os.replace(temporary, path)
                return
            except OSError:
                if attempt == tries - 1:
                    raise
                time.sleep(0.25 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def _source(path: Path | None = None) -> str:
    try:
        payload = json.loads((path or SOURCE_PATH).read_text(encoding="utf-8"))
        return str(payload.get("source", "unknown"))
    except (OSError, ValueError, AttributeError):
        return "unknown"


def _hot(frame: pd.DataFrame, dimension: str, key: str, top: int | None = None) -> pd.DataFrame:
    g = (frame.groupby(key, dropna=False, as_index=False, observed=True)
              .agg(metric_kg=("kg_co2", "sum"), n=("kg_co2", "size")))
    g["metric_t"] = g["metric_kg"] / 1000.0
    total = float(g["metric_t"].sum()) or 1.0
    g["share_pct"] = g["metric_t"] / total * 100.0
    g["dimension"] = dimension
    g["value"] = g[key].astype(str)
    g = g.sort_values(["metric_t", "value"], ascending=[False, True])
    if top:
        g = g.head(top)
    return g[["dimension", "value", "metric_t", "share_pct", "n"]]


def build_hotspots(seg: pd.DataFrame) -> pd.DataFrame:
    _validate_seg(seg, analysis=True)
    frames = [
        _hot(seg, "车型", "vehicle_type", top=20),
        _hot(seg, "线路", "od_pair", top=20),
        _hot(seg, "月份", "month"),
    ]
    dist = pd.to_numeric(seg["dist_km"], errors="coerce").fillna(0.0)
    bins = np.select(
        [(dist <= 100), (dist <= 300), (dist <= 600), (dist <= 1000)],
        DIST_LABELS[:4], default=DIST_LABELS[4])
    seg_dist = seg.assign(_dist_bin=bins)
    frames.append(_hot(seg_dist, "里程段", "_dist_bin"))
    out = pd.concat(frames, ignore_index=True)
    out["metric_t"] = out["metric_t"].round(3)
    out["share_pct"] = out["share_pct"].round(3)
    return out


def _legacy_row(spec: Dict[str, Any], e0_t: float, source: str,
                mark_constraints: bool = False) -> Dict[str, Any]:
    """单行历史标定：无底稿车组拆分的方案保留原值，并明确标记未核验、未过约束。

    mark_constraints=True 时补 O6 约束列（混合表用：参数化行有结论，历史行显式"未评估"）；
    纯 legacy 表不加这些列，页面据此继续只展示规则框架（与接线前行为一致）。
    """
    reduction = float(spec["reduction_pct"])
    emission = e0_t * (1.0 - reduction / 100.0)
    capex = float(spec["capex_wan"])
    opex = float(spec["opex_save_wan"])
    saving = e0_t - emission
    payback = _payback(capex, opex)
    unit = (capex * 10000.0 / saving) if saving > 0 else 0.0
    row = {
        "order": int(spec["order"]),
        "scenario": spec["scenario"],
        "emission_t": round(emission, 3),
        "reduction_pct": reduction,
        "carbon_saving_t": round(saving, 3),
        "capex_wan": round(capex, 2),
        "opex_save_wan": round(opex, 2),
        "payback_years": round(payback, 3),
        "unit_cost_yuan_t": round(unit, 2),
        "meets_target": bool(reduction >= TARGET_REDUCTION_PCT),
        # 混合表（mark_constraints=True）里 is_main 只由"达标且过运营约束"的胜者产生，
        # 历史标定行的原主推标签必须清零，不能把未核验方案伪装成决策推荐。
        "is_main": False if mark_constraints else bool(spec["is_main"]),
        "basis": (spec["basis"] + "；历史代码标定，未核验原底稿；is_main 仅为原主推标签"),
        "data_source": source,
        "e0_t": round(e0_t, 3),
        "calculation_mode": "legacy_calibration",
        "parameter_status": "unverified_legacy_totals",
        "operational_status": "pending",
        "decision_eligible": False,
        "boundary": "未逐方案核实，禁止直接跨边界排名",
    }
    if mark_constraints:
        row.update({
            "constraint_status": "未评估",
            "constraint_note": "历史标定行：未做逐车组/逐线路约束评估，不可作为决策依据",
            "c1_range_ok": None, "c2_window_ok": None, "c3_capacity_ok": None,
            "c4_charging_ok": None, "c5_winter_ok": None,
        })
    return row


def _build_calibrated_schemes(seg: pd.DataFrame, source: str) -> pd.DataFrame:
    e0_t = float(seg["kg_co2"].sum()) / 1000.0
    rows = [_legacy_row(spec, e0_t, source) for spec in SCENARIOS]
    out = pd.DataFrame(rows).sort_values("order").reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------
# E3/E4：显式参数的车组成本模型。未取得原始成本底稿前，不填真实项目参数。
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VehicleGroup:
    """同一方案内互不重叠的车组；成本为单车元，里程为单车 km/年。

    能耗分别为 L、kWh 或 kg / 100 km，单价单位与能源一致。
    两个排放量均为整个车组的数据期 tCO2e，须由上游在相同边界计算。
    purchase 为替换购置支出；若基准本就需要购车，应仅填增量购置支出。
    额外设施费在 ScenarioInput 填一次；期末残值为相对基准的净回收额。

    ``baseline_yuan_per_km`` / ``target_yuan_per_km`` 为可选的**直接元/km 口径**：
    赛题表 1 与底稿表 19 的能耗单价就是元/km（电动 0.35/0.48、氢能 1.05、
    柴油 0.75/0.95/1.20），填了它就优先用它算能耗成本，``units/price`` 两列留空。
    维护费同样支持元/km 直填；底稿给的是万/辆/年，由调用方按单车年里程折算。
    """

    group_id: str
    vehicle_count: int
    annual_km_per_vehicle: float
    purchase_yuan_per_vehicle: float
    subsidy_yuan_per_vehicle: float
    disposal_yuan_per_vehicle: float
    baseline_energy: str
    target_energy: str
    baseline_units_per_100km: float
    target_units_per_100km: float
    baseline_price_yuan_per_unit: float
    target_price_yuan_per_unit: float
    baseline_maintenance_yuan_per_km: float
    target_maintenance_yuan_per_km: float
    baseline_period_emission_t: float
    target_period_emission_t: float
    source: str
    # 可选直接元/km 口径（None = 用 units/price 路径，保持旧行为不变）
    baseline_yuan_per_km: float | None = None
    target_yuan_per_km: float | None = None


@dataclass(frozen=True)
class ScenarioInput:
    order: int
    scenario: str
    groups: tuple[VehicleGroup, ...]
    infrastructure_yuan: float
    other_capex_yuan: float
    other_annual_saving_yuan: float  # 相对基准，可为负（额外运营支出）
    terminal_residual_yuan: float  # 相对基准的期末净回收额，可为负
    boundary: str
    source: str
    # None 表示未收到 optimizer 的运营约束结论，不能当作通过。
    operational_feasible: bool | None = None
    # O6 约束结论摘要（build_route_constraint_verdict 的产出）；None = 未评估。
    # 落列为 constraint_status / constraint_note / c1–c5，供页面按契约渲染。
    constraint_summary: Dict[str, Any] | None = None


@dataclass(frozen=True)
class FinanceSettings:
    """调用方显式确认年化和评价期限；不把观察到的日期跨度自动当数据期。"""

    annualization_factor: float
    horizon_years: int
    discount_rate: float
    lambda_yuan_t: float
    target_reduction_pct: float
    boundary: str
    source: str


# 真实底稿确认后，在此集中录入；保持空值时继续输出兼容的历史标定表。
# 不在模块 import 时读文件，也不把下方 self_test 的虚构样例作为项目参数。
COST_SCENARIOS: tuple[ScenarioInput, ...] = ()
FINANCE_SETTINGS: FinanceSettings | None = None


# ---------------------------------------------------------------------------
# E4 · 逐车组成本模型（2026-10-22 录入；底稿链见各常量 source）
# ---------------------------------------------------------------------------
# 边界必须与 pipeline.BOUNDARY 一致；pipeline 结构闸门有漂移检查（scheme_boundary）。
E4_BOUNDARY = "自营公路运输 · 范围一（移动源燃烧）"

ANNUALIZATION_FACTOR = 3.0417      # 365/120：底稿 §8.2 年化系数（与 pipeline.ANNUAL_FACTOR 同值）
FLEET_WORK_HOURS_PER_DAY = 8.0     # 底稿 表18：每车年运力 = 速度中位数 × 8 h × 300 天
FLEET_WORK_DAYS_PER_YEAR = 300
FLEET_HORIZON_YEARS = 10           # 底稿 §8.4：10 年全周期成本对照
DISCOUNT_RATE = 0.06               # config/calc_factors.json finance.discount_rate
LAMBDA_YUAN_T = 100.0              # app 侧栏「内部碳价」默认值（第 3–5 页共用旋钮）
TARGET_REDUCTION_PCT = 70.0        # 赛题要求：2026 年底减排 70%

# 电动化目标排放率（g/km）：《新能源碳排放对比计算结果.xlsx》「分车型明细」，μ=0.565。
# 轻型取 4.2 米厢式、中型取 9.6 米厢式——底稿 表14 替换矩阵的代表车型。
ELECTRIC_TARGET_GKM: Dict[int, float] = {1: 166.33, 2: 540.02}
DIESEL_BASE_GKM: Dict[int, float] = {1: 394.56, 2: 759.24}
GRID_MU = 0.565                    # 电网排放因子基准（同上「计算依据」，温惠英等式）
GRID_MU_CLEAN = 0.50               # 2026 电网清洁化情景（完整方案 §5.3 敏感性）
HYDROGEN_WTW_RATE: Dict[str, float] = {"gray": 0.30, "blue": 0.68, "green": 0.87}

#: 车队替换矩阵：底稿 表14（车型替换）+ 表19（分车型成本）+ 赛题表 1（VEHICLE_SPECS）。
#: 重型按使用端零排放口径（底稿 §5.2：氢气不含碳，题目未给制氢侧参数，预期口径即使用端）。
FLEET_PLAN: Dict[int, Dict[str, Any]] = {
    1: {
        "label": "轻型", "vehicle": "比亚迪T5/吉利远程 E200", "target_energy": "electricity",
        "purchase_wan": 18.0, "subsidy_wan": 2.0,
        "new_energy_yuan_km": 0.35, "diesel_energy_yuan_km": 0.75,
        "new_maint_wan_year": 1.2, "diesel_maint_wan_year": 1.5,
        "target_rate": 1.0 - ELECTRIC_TARGET_GKM[1] / DIESEL_BASE_GKM[1],
        "source": "底稿表14/表19：电动 3-5t（比亚迪T5）18−2 万、0.35 元/km、维护 1.2 万/辆/年；"
                  "柴油分档 0.75 元/km、1.5 万/辆/年；减排率 57.84% 出 新能源碳排放对比计算结果.xlsx"
                  " 分车型明细（4.2 米，μ=0.565）",
    },
    2: {
        "label": "中型", "vehicle": "南京金龙 D10/东风凯普特 e星", "target_energy": "electricity",
        "purchase_wan": 28.0, "subsidy_wan": 2.0,
        "new_energy_yuan_km": 0.48, "diesel_energy_yuan_km": 0.95,
        "new_maint_wan_year": 1.8, "diesel_maint_wan_year": 2.5,
        "target_rate": 1.0 - ELECTRIC_TARGET_GKM[2] / DIESEL_BASE_GKM[2],
        "source": "底稿表14/表19：电动 8-10t（南京金龙D10）28−2 万、0.48 元/km、维护 1.8 万/辆/年；"
                  "柴油分档 0.95 元/km、2.5 万/辆/年；减排率 28.87% 出 新能源碳排放对比计算结果.xlsx"
                  " 分车型明细（9.6 米，μ=0.565）",
    },
    3: {
        "label": "重型", "vehicle": "上汽红岩氢燃料重卡", "target_energy": "hydrogen",
        "purchase_wan": 95.0, "subsidy_wan": 20.0,
        "new_energy_yuan_km": 1.05, "diesel_energy_yuan_km": 1.20,
        "new_maint_wan_year": 3.5, "diesel_maint_wan_year": 4.0,
        "target_rate": 1.0,
        "source": "底稿表14/表19：氢能 12-15t（上汽红岩）95−20 万、1.05 元/km、维护 3.5 万/辆/年；"
                  "柴油分档 1.20 元/km、4.0 万/辆/年；减排率 100% 为使用端零排放口径（底稿 §5.2）",
    },
}

MAIN_SCENARIO_NAME = "S5 轻中电 + 重氢（主推）"
MAIN_SCENARIO_ORDER = 5


def default_finance_settings() -> FinanceSettings:
    """E4 默认财务设置：年化/期限/折现率/λ 全部有出处，调用方也可显式覆盖。"""
    return FinanceSettings(
        annualization_factor=ANNUALIZATION_FACTOR,
        horizon_years=FLEET_HORIZON_YEARS,
        discount_rate=DISCOUNT_RATE,
        lambda_yuan_t=LAMBDA_YUAN_T,
        target_reduction_pct=TARGET_REDUCTION_PCT,
        boundary=E4_BOUNDARY,
        source="底稿 §8.2/§8.4（年化 3.0417、10 年全周期）+ config/calc_factors.json"
               "（discount 0.06）+ app 侧栏默认 λ=100 元/t + 赛题 70% 目标",
    )


def _od_group_labels(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """OD 分组标签与显示名：od_site（网点级）→ od_key（城市级）→ od_pair，与 build_od_band 同口径。"""
    label = pd.Series("", index=frame.index, dtype="object")
    display = pd.Series("", index=frame.index, dtype="object")
    for key in ("od_site", "od_key", "od_pair"):
        if key not in frame:
            continue
        raw = frame[key].fillna("").astype(str).str.strip()
        usable = ~raw.str.lower().isin(["", "nan", "none", "null", "<na>"])
        choose = label.eq("") & usable
        label[choose] = key + ":" + raw[choose]
        display[choose] = raw[choose]
    return label, display


def _class_speed_median(seg: pd.DataFrame) -> Dict[int, float]:
    """类别实测速度中位数（底稿 表18 同口径：>1 且 ≤130 km/h 的有效速度）。"""
    out: Dict[int, float] = {}
    for vclass in (1, 2, 3):
        sub = seg[pd.to_numeric(seg["vclass"], errors="coerce") == vclass]
        speed = pd.to_numeric(sub["speed_kmh"], errors="coerce") if "speed_kmh" in sub.columns else pd.Series(dtype=float)
        speed = speed[(speed > 1.0) & (speed <= 130.0)]
        out[vclass] = float(speed.median()) if speed.notna().any() else 0.0
    return out


def build_cost_scenarios(
    seg: pd.DataFrame, source: str,
) -> tuple[tuple[ScenarioInput, ...] | None, FinanceSettings | None]:
    """由段表**实时派生**主推方案（S5）的车组与财务设置。

    车组口径与底稿 表18/表19 逐项对应：年里程 = 数据期里程 × 3.0417；每车年运力 =
    类别速度中位数 × 8 h × 300 天；车辆数 = ceil(年里程 ÷ 每车年运力)（34/40/43 = 117 辆）；
    购置/补贴/能耗（元/km）/维护（万/辆/年 折 元/km）按底稿 表19 录入；目标排放率按
    表14 替换矩阵（轻 57.84% / 中 28.87% / 重 100% 使用端零排放）。

    段表缺 vclass/speed 等列（演示数据）或任一类为空时返回 (None, None)，
    调用方据此回退 legacy_calibration，**不用演示数据冒充参数化结果**。
    """
    needed = {"vclass", "dist_km", "kg_co2", "speed_kmh"}
    if seg is None or seg.empty or not needed.issubset(seg.columns):
        return None, None
    if str(source) != "real":
        # 演示/未知来源一律回退历史标定：参数化数字只能来自真实附件的段表。
        return None, None
    verdict = build_route_constraint_verdict(seg)
    groups: List[VehicleGroup] = []
    fleet: Dict[int, Any] = {}
    for vclass in (1, 2, 3):
        sub = seg[pd.to_numeric(seg["vclass"], errors="coerce") == vclass]
        if sub.empty:
            return None, None
        km = float(pd.to_numeric(sub["dist_km"], errors="coerce").fillna(0).sum())
        kg = float(pd.to_numeric(sub["kg_co2"], errors="coerce").fillna(0).sum())
        speed = pd.to_numeric(sub["speed_kmh"], errors="coerce")
        speed = speed[(speed > 1.0) & (speed <= 130.0)]
        med_speed = float(speed.median()) if speed.notna().any() else 0.0
        if km <= 0 or kg <= 0 or med_speed <= 0:
            return None, None
        plan = FLEET_PLAN[vclass]
        annual_km = km * ANNUALIZATION_FACTOR                     # 底稿 表18
        capacity = med_speed * FLEET_WORK_HOURS_PER_DAY * FLEET_WORK_DAYS_PER_YEAR
        count = int(np.ceil(annual_km / capacity))                # 最少车辆数
        per_vehicle_km = annual_km / count
        # 维护 万/辆/年 → 元/km（按单车年里程折算，保持底稿 表19 的按车口径）
        new_maint_yuan_km = plan["new_maint_wan_year"] * 1e4 / per_vehicle_km
        diesel_maint_yuan_km = plan["diesel_maint_wan_year"] * 1e4 / per_vehicle_km
        rate = float(plan["target_rate"])
        groups.append(VehicleGroup(
            group_id="%s（vclass %d）" % (plan["label"], vclass),
            vehicle_count=count,
            annual_km_per_vehicle=per_vehicle_km,
            purchase_yuan_per_vehicle=plan["purchase_wan"] * 1e4,
            subsidy_yuan_per_vehicle=plan["subsidy_wan"] * 1e4,
            disposal_yuan_per_vehicle=0.0,
            baseline_energy="diesel",
            target_energy=plan["target_energy"],
            baseline_units_per_100km=0.0, target_units_per_100km=0.0,
            baseline_price_yuan_per_unit=0.0, target_price_yuan_per_unit=0.0,
            baseline_yuan_per_km=plan["diesel_energy_yuan_km"],
            target_yuan_per_km=plan["new_energy_yuan_km"],
            baseline_maintenance_yuan_per_km=diesel_maint_yuan_km,
            target_maintenance_yuan_per_km=new_maint_yuan_km,
            baseline_period_emission_t=kg / 1000.0,
            target_period_emission_t=kg / 1000.0 * (1.0 - rate),
            source=plan["source"],
        ))
        fleet[vclass] = {
            "label": plan["label"], "vehicle": plan["vehicle"],
            "period_km": km, "period_kg": kg, "median_speed_kmh": med_speed,
            "annual_km": annual_km, "capacity_km": capacity,
            "vehicle_count": count, "target_rate": rate,
        }
    spec = ScenarioInput(
        order=MAIN_SCENARIO_ORDER, scenario=MAIN_SCENARIO_NAME,
        groups=tuple(groups),
        infrastructure_yuan=0.0, other_capex_yuan=0.0,
        other_annual_saving_yuan=0.0, terminal_residual_yuan=0.0,
        boundary=E4_BOUNDARY,
        source="完整方案.md §8 表18/表19/表14（京彩杯底稿）；车组由段表实时派生",
        # None = 未评估（直传，勿转 False：False 语义是"已评估未通过"）
        operational_feasible=verdict.get("operational_feasible"),
        constraint_summary=verdict,
    )
    return (spec,), default_finance_settings()


# ---------------------------------------------------------------------------
# O6 · 方案 × 哪条线：逐线路 C1–C5 评估与场景级裁决（docs/08 §8、docs/20 §7）
# ---------------------------------------------------------------------------
# 判定口径（写进 docs/08 §8 与披露，答辩按这个说）：
#   tier「单程」  route_km ≤ 可用续航（标称 × 0.8）：一次出车、两端配送中心补能，
#                C1–C5 直接判定；任何一条不过即给出缺口。
#   tier「班次化」route_km > 可用续航：单程必然不过 C1/C2；按底稿 §6.5 以
#                「干线班次化」运营——多日行程、单日 ≤14 h 窗口、沿线补能 1–2 次。
#                沿线补能站是**未核实的实施假设**（底稿 §6.5 引社会加氢站 542 座），
#                故 C4 按「无站」计，条件写入 note 与披露，不冒充已验证。
#   行驶速度取**类别**速度中位数（底稿 表18 同口径）：段级 door-to-door 均速含
#                装卸等待，城市段会低到 1–5 km/h，不能当行驶速度用。
#   货重取该线**实际单票货重**（附件1 实测）：底稿 §6.4 论证轻抛小件（均值 1.42 kg、
#                最大 51.6 kg），载重远非约束；绝不能用旧车额定载重（load_t）比新车额定。
#   season=winter：数据期为 1/2/11/12 月，C5 按 −10 ℃ 折减复检（假设待实测，单独披露）。
# 场景级裁决：operational_feasible = 全部「单程」线路通过 C1–C4
#   （C5 冬季复检不通过的线路只是冬季转入班次化层，属信息项，不当一票否决）；
#   「班次化」层的条件可行性与「仅配送中心补能」反事实一并写入 note 与披露。
ROUTE_TIER_SINGLE = "单程"
ROUTE_TIER_TRUNK = "干线班次化"
TRUNK_DEPLOY_NOTE = ("单程不可行；按底稿 §6.5 干线班次化运营（多日行程、单日 ≤14 h、"
                     "沿线补能 1–2 次）——沿线补能网络为必要外部条件，未核实")


def _evaluate_fleet_routes(seg: pd.DataFrame) -> pd.DataFrame:
    """车队级逐线评估：一行一个 (vclass, OD)；不含方案列（所有参数化方案共用车队）。"""
    from src.optimizer import VEHICLE_SPECS, route_constraint

    labels, displays = _od_group_labels(seg)
    frame = seg.assign(_od_group=labels, _od_display=displays)
    speeds = _class_speed_median(seg)
    total_km = float(pd.to_numeric(frame["dist_km"], errors="coerce").fillna(0).sum())
    has_cargo = "cargo_weight_kg" in frame.columns
    rows: List[Dict[str, Any]] = []
    for vclass in (1, 2, 3):
        sub = frame[pd.to_numeric(frame["vclass"], errors="coerce") == vclass]
        if sub.empty:
            continue
        plan = FLEET_PLAN[vclass]
        vehicle_spec = VEHICLE_SPECS[plan["vehicle"]]
        usable = float(vehicle_spec["range_km"]) * 0.8
        for od, group in sub.groupby("_od_group", sort=True):
            if not str(od):
                continue
            route_km = float(pd.to_numeric(group["dist_km"], errors="coerce").median())
            cargo_t = None
            if has_cargo:
                cargo = pd.to_numeric(group["cargo_weight_kg"], errors="coerce")
                if cargo.notna().any():
                    cargo_t = float(cargo.max()) / 1000.0
            result = route_constraint(
                route_km, vehicle_spec["range_km"], charging_ok=False,
                cargo_t=cargo_t, payload_t=vehicle_spec["payload_t"],
                avg_speed_kmh=speeds.get(vclass) or 50.0, season="winter",
            )
            tier = ROUTE_TIER_SINGLE if route_km <= usable else ROUTE_TIER_TRUNK
            note = result["constraint_note"]
            if tier == ROUTE_TIER_TRUNK:
                note = note + "｜" + TRUNK_DEPLOY_NOTE
            rows.append({
                "vclass": vclass, "vclass_label": plan["label"], "vehicle": plan["vehicle"],
                "od_key": str(od), "od_label": str(group["_od_display"].iloc[0]),
                "n_seg": int(len(group)),
                "km": float(pd.to_numeric(group["dist_km"], errors="coerce").fillna(0).sum()),
                "kg": float(pd.to_numeric(group["kg_co2"], errors="coerce").fillna(0).sum()),
                "route_km": route_km, "avg_speed_kmh": speeds.get(vclass),
                "max_cargo_t": cargo_t, "usable_range_km": usable,
                "deploy_tier": tier,
                "c1_range_ok": result["c1_range_ok"],
                "c2_window_ok": result["c2_window_ok"],
                "c3_capacity_ok": result["c3_capacity_ok"],
                "c4_charging_ok": result["c4_charging_ok"],
                "c5_winter_ok": result["c5_winter_ok"],
                "constraint_status": result["constraint_status"],
                "constraint_note": note,
            })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["km_share_pct"] = out["km"] / total_km * 100.0 if total_km > 0 else 0.0
    return out.sort_values(["vclass", "km"], ascending=[True, False]).reset_index(drop=True)


def build_route_constraint_verdict(seg: pd.DataFrame) -> Dict[str, Any]:
    """场景级 O6 裁决摘要（写进 final_report 的 constraint_* 列与披露）。"""
    routes = _evaluate_fleet_routes(seg)
    if routes.empty:
        return {"operational_feasible": None, "status": "pending",
                "note": "段表无法按 OD 分组，约束未评估", "route_rows": 0}
    total_km = float(routes["km"].sum())
    total_kg = float(routes["kg"].sum())
    single = routes[routes["deploy_tier"] == ROUTE_TIER_SINGLE]
    trunk = routes[routes["deploy_tier"] == ROUTE_TIER_TRUNK]
    # C1/C2/C4 必判；C3 未提供货重时为 None（未评估，不计 fail）
    hard_mask = (single["c1_range_ok"].fillna(True).eq(False)
                 | single["c2_window_ok"].fillna(True).eq(False)
                 | single["c4_charging_ok"].fillna(True).eq(False)
                 | single["c3_capacity_ok"].fillna(True).eq(False))
    hard_fail = single[hard_mask]
    winter_fail = single[single["c5_winter_ok"].fillna(True).eq(False)]
    # 仅配送中心补能的反事实：班次化层全部保留柴油，单程层按类别减排率
    depot_saving_t = 0.0
    for vclass in (1, 2, 3):
        part = single[single["vclass"] == vclass]
        if not part.empty:
            depot_saving_t += float(part["kg"].sum()) / 1000.0 * float(FLEET_PLAN[vclass]["target_rate"])
    depot_pct = depot_saving_t / (total_kg / 1000.0) * 100.0 if total_kg > 0 else None
    feasible = bool(hard_fail.empty) and not single.empty
    if single.empty:
        note = ("无单程层线路（全部 route_km 超可用续航）：%.1f%% 里程按底稿 §6.5 "
                "干线班次化运营，依赖沿线补能网络（未核实，最大外部依赖）；"
                "无单程层达标证据，场景级运营可行判 fail；"
                "若仅允许配送中心补能，减排率仅 %.1f%%（远不达标）"
                % (trunk["km"].sum() / total_km * 100.0,
                   depot_pct if depot_pct is not None else float("nan")))
    else:
        note = ("单程层 %d 条线路（%.1f%% 里程、%.1f%% 碳排）C1–C4 全部通过；"
                "班次化层 %.1f%% 里程按底稿 §6.5 干线班次化运营，依赖沿线补能网络（未核实，最大外部依赖）；"
                "冬季复检 %.1f%% 里程不达标，冬季转入班次化层；"
                "若仅允许配送中心补能，减排率仅 %.1f%%（远不达标）"
                % (len(single), single["km"].sum() / total_km * 100.0,
                   single["kg"].sum() / total_kg * 100.0,
                   trunk["km"].sum() / total_km * 100.0,
                   winter_fail["km"].sum() / total_km * 100.0,
                   depot_pct if depot_pct is not None else float("nan")))
    if not hard_fail.empty:
        note = ("单程层有 %d 条线路未过 C1–C4（%.2f%% 里程）："
                % (len(hard_fail), hard_fail["km"].sum() / total_km * 100.0)) + note
    return {
        "operational_feasible": feasible,
        "status": "pass" if feasible else "fail",
        "note": note,
        "route_rows": int(len(routes)),
        "single_trip_routes": int(len(single)),
        "single_trip_km_pct": round(single["km"].sum() / total_km * 100.0, 2),
        "single_trip_carbon_pct": round(single["kg"].sum() / total_kg * 100.0, 2),
        "trunk_km_pct": round(trunk["km"].sum() / total_km * 100.0, 2),
        "trunk_carbon_pct": round(trunk["kg"].sum() / total_kg * 100.0, 2),
        "winter_c5_fail_km_pct": round(winter_fail["km"].sum() / total_km * 100.0, 2),
        "depot_only_reduction_pct": round(depot_pct, 2) if depot_pct is not None else None,
        "hard_fail_routes": int(len(hard_fail)),
        "c1_range_ok": bool(single["c1_range_ok"].fillna(False).all()) if not single.empty else None,
        "c2_window_ok": bool(single["c2_window_ok"].fillna(False).all()) if not single.empty else None,
        "c3_capacity_ok": (bool(single["c3_capacity_ok"].fillna(True).all())
                           if not single.empty else None),
        "c4_charging_ok": bool(single["c4_charging_ok"].fillna(False).all()) if not single.empty else None,
        "c5_winter_ok": bool(single["c5_winter_ok"].fillna(False).all()) if not single.empty else None,
        "rule": ("单程层（route_km ≤ 标称续航 ×0.8）全部通过 C1–C4；"
                 "班次化层按底稿 §6.5 干线班次化运营（条件可行，依赖沿线补能网络）；"
                 "C5 冬季复检为信息项，不达标线路冬季转入班次化层"),
        "basis": "docs/20 §7 规则文本 + optimizer.route_constraint + 底稿 §6.4/§6.5",
    }


def build_route_constraints(
    seg: pd.DataFrame, specs: tuple[ScenarioInput, ...] = (), source: str = "unknown",
) -> tuple[pd.DataFrame, Dict[str, Any]]:
    """方案 × 哪条线：给每个参数化方案打上同一份车队级逐线结论，返回 (表, 裁决)。

    表的一行是 (方案, 类别, 线路)；裁决对所有参数化方案相同（共用同一替换车队），
    按 order 建索引供 build_schemes 取用。specs 为空（演示/legacy 路径）时返回空表。
    """
    if not specs:
        return pd.DataFrame(), {"operational_feasible": None, "status": "pending",
                                "note": "未提供参数化方案，约束未评估", "route_rows": 0}
    routes = _evaluate_fleet_routes(seg)
    verdict = build_route_constraint_verdict(seg)
    if routes.empty:
        return routes, verdict
    frames = []
    for spec in specs:
        part = routes.copy()
        part.insert(0, "scenario", spec.scenario)
        part.insert(0, "order", int(spec.order))
        frames.append(part)
    out = pd.concat(frames, ignore_index=True)
    return out, verdict


def _number(name: str, value: Any, minimum: float | None = None) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f"{name} 必须是有限数值，不能是布尔值或字符串")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是有限数值") from exc
    if not np.isfinite(result) or (minimum is not None and result < minimum):
        raise ValueError(f"{name} 必须为有限数值且 >= {minimum}")
    return result


def _text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 不能为空")


def _integer(name: str, value: Any, minimum: int) -> int:
    number = _number(name, value, minimum)
    if not number.is_integer():
        raise ValueError(f"{name} 必须是整数")
    return int(number)


def _validate_settings(settings: FinanceSettings) -> None:
    if _number("annualization_factor", settings.annualization_factor, 0) == 0:
        raise ValueError("annualization_factor 必须大于零")
    _integer("horizon_years", settings.horizon_years, 1)
    _number("discount_rate", settings.discount_rate, 0)
    _number("lambda_yuan_t", settings.lambda_yuan_t, 0)
    if _number("target_reduction_pct", settings.target_reduction_pct, 0) > 100:
        raise ValueError("减排目标必须在 0–100%")
    _text("boundary", settings.boundary)
    _text("settings.source", settings.source)


def _validate_seg(seg: pd.DataFrame, *, analysis: bool = False) -> None:
    required = ["kg_co2"]
    if analysis:
        required += ["factor_gkm", "vehicle_type", "od_pair", "dist_km", "month"]
    missing = sorted(set(required) - set(seg.columns))
    if missing:
        raise ValueError("段级碳排缺少契约列：" + ", ".join(missing))
    if seg.empty:
        raise ValueError("段级碳排为空，无法做分析")
    for name in [c for c in ("kg_co2", "dist_km", "factor_gkm") if c in required]:
        values = pd.to_numeric(seg[name], errors="coerce")
        if not np.isfinite(values.to_numpy(dtype=float, na_value=np.nan)).all() or (values < 0).any():
            raise ValueError(name + " 含缺失、非数值、无穷或负数，请先修复上游数据")
        if not pd.api.types.is_numeric_dtype(seg[name]) or pd.api.types.is_bool_dtype(seg[name]):
            raise ValueError(name + " 必须为数值列，请先修复上游字段类型")


def _payback(capex_yuan: float, annual_saving_yuan: float) -> float:
    if capex_yuan <= 0:
        return 0.0  # 无待收回的期初净投入；不表示未来运营有利。
    return capex_yuan / annual_saving_yuan if annual_saving_yuan > 0 else float("inf")


def _constraint_field(spec: ScenarioInput, key: str, default: Any = None) -> Any:
    """从 ScenarioInput.constraint_summary 取 O6 结论字段；未评估时返回默认值。"""
    if not spec.constraint_summary:
        return default
    value = spec.constraint_summary.get(key, default)
    return default if value is None else value


def evaluate_scenario(
    e0_t: float, spec: ScenarioInput, settings: FinanceSettings, source: str = "unknown"
) -> Dict[str, Any]:
    """按车组计算单方案，不读写文件，不进行跨边界排名。

    现金 NPV = -净投入 + 年运营节省×年金现值系数 + 期末净回收额现值。
    内部估值 NPV = 现金 NPV + λ×年减排量×年金现值系数。
    内部碳价没有现金交易证明，绝不加进现金节省或现金回本期。
    """
    e0_t = _number("e0_t", e0_t, 0)
    if e0_t == 0:
        raise ValueError("参数化减排评估要求基准排放大于零")
    _validate_settings(settings)
    _integer("order", spec.order, 1)  # 0 保留给基准方案
    _text("scenario", spec.scenario)
    _text("scenario.source", spec.source)
    if spec.boundary != settings.boundary:
        raise ValueError("方案与基准核算边界不一致，不能直接比较")
    if spec.operational_feasible is not None and type(spec.operational_feasible) is not bool:
        raise ValueError("operational_feasible 只能是 True、False 或 None")
    if not spec.groups:
        raise ValueError("替代方案至少需要一个车组")
    components = []
    seen = set()
    base_t = target_t = 0.0
    for group in spec.groups:
        _text("group_id", group.group_id)
        _text("group.source", group.source)
        if group.group_id in seen:
            raise ValueError("同一方案出现重复车组：" + group.group_id)
        seen.add(group.group_id)
        count = _integer("vehicle_count", group.vehicle_count, 1)
        for field in fields(VehicleGroup):
            if field.name not in {"group_id", "source", "baseline_energy", "target_energy",
                                  "vehicle_count", "baseline_yuan_per_km", "target_yuan_per_km"}:
                _number(field.name, getattr(group, field.name), 0)
        for name in ("baseline_yuan_per_km", "target_yuan_per_km"):
            direct = getattr(group, name)
            if direct is not None:
                _number(name, direct, 0)
        for energy in (group.baseline_energy, group.target_energy):
            if energy not in {"diesel", "electricity", "hydrogen"}:
                raise ValueError("energy 仅支持 diesel / electricity / hydrogen")
        km = count * group.annual_km_per_vehicle
        base_energy = (km * group.baseline_yuan_per_km if group.baseline_yuan_per_km is not None
                       else km / 100 * group.baseline_units_per_100km * group.baseline_price_yuan_per_unit)
        target_energy_cost = (km * group.target_yuan_per_km if group.target_yuan_per_km is not None
                              else km / 100 * group.target_units_per_100km * group.target_price_yuan_per_unit)
        components.append({
            "group_id": group.group_id, "vehicle_count": count,
            "purchase_yuan": count * group.purchase_yuan_per_vehicle,
            "subsidy_yuan": count * group.subsidy_yuan_per_vehicle,
            "disposal_yuan": count * group.disposal_yuan_per_vehicle,
            "baseline_energy_yuan_year": base_energy,
            "target_energy_yuan_year": target_energy_cost,
            "baseline_maintenance_yuan_year": km * group.baseline_maintenance_yuan_per_km,
            "target_maintenance_yuan_year": km * group.target_maintenance_yuan_per_km,
            "baseline_period_emission_t": group.baseline_period_emission_t,
            "target_period_emission_t": group.target_period_emission_t,
            "source": group.source,
        })
        base_t += group.baseline_period_emission_t
        target_t += group.target_period_emission_t
    if base_t > e0_t + 1e-9:
        raise ValueError("替换车组基准排放合计超过全体 E0，可能重复计算或口径不一致")
    infrastructure = _number("infrastructure_yuan", spec.infrastructure_yuan, 0)
    other_capex = _number("other_capex_yuan", spec.other_capex_yuan, 0)
    other_saving = _number("other_annual_saving_yuan", spec.other_annual_saving_yuan)
    residual = _number("terminal_residual_yuan", spec.terminal_residual_yuan)
    totals = {key: sum(row[key] for row in components) for key in (
        "purchase_yuan", "subsidy_yuan", "disposal_yuan",
        "baseline_energy_yuan_year", "target_energy_yuan_year",
        "baseline_maintenance_yuan_year", "target_maintenance_yuan_year",
    )}
    capex = totals["purchase_yuan"] + infrastructure + other_capex - totals["subsidy_yuan"] - totals["disposal_yuan"]
    annual_saving = (totals["baseline_energy_yuan_year"] - totals["target_energy_yuan_year"]
                     + totals["baseline_maintenance_yuan_year"] - totals["target_maintenance_yuan_year"] + other_saving)
    period_saving = base_t - target_t
    emission = max(0.0, e0_t - base_t) + target_t
    annual_carbon = period_saving * settings.annualization_factor
    years = int(settings.horizon_years)
    annuity = sum((1 + settings.discount_rate) ** -year for year in range(1, years + 1))
    pv_residual = residual / (1 + settings.discount_rate) ** years
    cash_npv = -capex + annual_saving * annuity + pv_residual
    valued_npv = cash_npv + settings.lambda_yuan_t * annual_carbon * annuity
    reduction = period_saving / e0_t * 100
    payback = _payback(capex, annual_saving)
    eligible = reduction >= settings.target_reduction_pct and spec.operational_feasible is True
    breakdown = dict(totals, infrastructure_yuan=infrastructure, other_capex_yuan=other_capex,
                     other_annual_saving_yuan=other_saving, net_capex_yuan=capex,
                     annual_cash_saving_yuan=annual_saving, terminal_residual_yuan=residual,
                     groups=components, source=spec.source)
    return {
        "order": int(spec.order), "scenario": spec.scenario,
        "emission_t": emission, "reduction_pct": reduction, "carbon_saving_t": period_saving,
        "capex_wan": capex / 10000, "opex_save_wan": annual_saving / 10000,
        "payback_years": payback,
        # 旧列保持原含义：一次性净投入/数据期减排，不是全寿命减排成本。
        "unit_cost_yuan_t": capex / period_saving if period_saving > 0 else float("nan"),
        "meets_target": bool(reduction >= settings.target_reduction_pct),
        "is_main": False, "e0_t": e0_t, "data_source": source,
        "basis": "参数化计算；输入依据待团队复核：" + spec.source,
        "calculation_mode": "parameterized", "parameter_status": "draft_sourced_pending_review",
        "boundary": settings.boundary, "vehicle_count": sum(c["vehicle_count"] for c in components),
        "annualization_factor": settings.annualization_factor,
        "annual_carbon_saving_t": annual_carbon, "lambda_yuan_t": settings.lambda_yuan_t,
        "horizon_years": years, "discount_rate": settings.discount_rate,
        "cash_npv_wan": cash_npv / 10000, "internal_value_npv_wan": valued_npv / 10000,
        "annual_internal_carbon_value_wan": settings.lambda_yuan_t * annual_carbon / 10000,
        "levelized_abatement_cost_yuan_t": -cash_npv / (annual_carbon * annuity) if annual_carbon > 0 else float("nan"),
        "break_even_lambda_yuan_t": max(0.0, -cash_npv / (annual_carbon * annuity)) if annual_carbon > 0 else float("nan"),
        "operational_status": {True: "pass", False: "fail", None: "pending"}[spec.operational_feasible],
        "decision_eligible": bool(eligible),
        "cash_payback_within_horizon": bool(payback <= years),
        "cost_components_json": json.dumps(breakdown, ensure_ascii=False, allow_nan=False),
        "settings_source": settings.source,
        # O6 约束结论列（app/services.CONSTRAINT_COLUMNS 契约；None = 未评估）
        "constraint_status": _constraint_field(spec, "status", "pending"),
        "constraint_note": _constraint_field(spec, "note", "未做逐线路约束评估"),
        "c1_range_ok": _constraint_field(spec, "c1_range_ok"),
        "c2_window_ok": _constraint_field(spec, "c2_window_ok"),
        "c3_capacity_ok": _constraint_field(spec, "c3_capacity_ok"),
        "c4_charging_ok": _constraint_field(spec, "c4_charging_ok"),
        "c5_winter_ok": _constraint_field(spec, "c5_winter_ok"),
    }


def build_sensitivity(
    e0_t: float, spec: ScenarioInput, settings: FinanceSettings,
    parameter: str, values: List[float], source: str = "unknown",
) -> pd.DataFrame:
    """单因素敏感性：λ 或某能源单价倍数；每点完整复算，不修改原参数。

    价格乘数同时作用于基准与替代组中相应能源价格；不改变排放量。
    前端应读取后端导出的此表，不能在页面把数据期减排直接当年减排。
    """
    energy_parameters = {"diesel_price_multiplier": "diesel",
                         "electricity_price_multiplier": "electricity",
                         "hydrogen_price_multiplier": "hydrogen"}
    if parameter not in {"lambda_yuan_t", *energy_parameters}:
        raise ValueError("不支持的敏感性参数：" + parameter)
    if not values:
        raise ValueError("敏感性取值列表不能为空")
    rows = []
    for value in values:
        value = _number(parameter, value, 0)
        changed_spec, changed_settings = spec, settings
        if parameter == "lambda_yuan_t":
            changed_settings = replace(settings, lambda_yuan_t=value)
        else:
            energy = energy_parameters[parameter]
            changed_spec = replace(spec, groups=tuple(replace(
                g,
                baseline_price_yuan_per_unit=g.baseline_price_yuan_per_unit * (value if g.baseline_energy == energy else 1),
                target_price_yuan_per_unit=g.target_price_yuan_per_unit * (value if g.target_energy == energy else 1),
            ) for g in spec.groups))
        row = evaluate_scenario(e0_t, changed_spec, changed_settings, source)
        rows.append(dict(row, sensitivity_parameter=parameter, sensitivity_value=value))
    return pd.DataFrame(rows)


def share_boundary(
    e0_t: float, emission_at_zero_t: float, emission_at_one_t: float,
    target_reduction_pct: float,
) -> Dict[str, Any]:
    """灰氢/外协等份额 x∈[0,1] 的达标区间：E(x)=E(0)+x×[E(1)-E(0)]。

    两端点必须来自同边界、同运输任务、同数据期。外协排放需保留在
    比较边界内，不能把转出企业的排放自动视为消失。不硬填 4.4%。
    """
    e0_t = _number("e0_t", e0_t, 0)
    if e0_t == 0:
        raise ValueError("基准排放必须大于零")
    e_zero = _number("emission_at_zero_t", emission_at_zero_t, 0)
    e_one = _number("emission_at_one_t", emission_at_one_t, 0)
    target = _number("target_reduction_pct", target_reduction_pct, 0)
    if target > 100:
        raise ValueError("减排目标不得超过 100%")
    limit = e0_t * (1 - target / 100)
    if e_zero <= limit and e_one <= limit:
        return {"status": "all", "min_share": 0.0, "max_share": 1.0}
    if e_zero > limit and e_one > limit:
        return {"status": "none", "min_share": None, "max_share": None}
    cross = min(1.0, max(0.0, (limit - e_zero) / (e_one - e_zero)))
    return {"status": "partial", "min_share": 0.0 if e_one > e_zero else cross,
            "max_share": cross if e_one > e_zero else 1.0}


def charging_loss_boundary(
    e0_t: float, emission_at_zero_loss_t: float, electricity_emission_t: float,
    target_reduction_pct: float,
) -> Dict[str, Any]:
    """充电能量损失率 l∈[0,1)：E(l)=E(0)+E电×l/(1-l)。

    E电是零损失情景内受损失影响的用电排放，不是全车队排放。
    本函数只测碳目标，不替代 optimizer 的补能时长/场站容量约束。
    蓝图的“补能9.8%”定义尚未核实，不能直接等同于这里的损失率。
    """
    # 复用目标/排放的严格校验。
    initial = share_boundary(e0_t, emission_at_zero_loss_t, emission_at_zero_loss_t, target_reduction_pct)
    electric = _number("electricity_emission_t", electricity_emission_t, 0)
    if electric > emission_at_zero_loss_t:
        raise ValueError("用电排放不能超过情景总排放")
    if initial["status"] == "none":
        return {"status": "none", "max_loss_rate": None, "upper_inclusive": False}
    if electric == 0:
        return {"status": "all", "max_loss_rate": 1.0, "upper_inclusive": False}
    margin = e0_t * (1 - target_reduction_pct / 100) - emission_at_zero_loss_t
    return {"status": "partial", "max_loss_rate": margin / (electric + margin), "upper_inclusive": True}


def build_schemes(
    seg: pd.DataFrame, source: str,
    cost_scenarios: tuple[ScenarioInput, ...] | None = None,
    settings: FinanceSettings | None = None,
) -> pd.DataFrame:
    """保留旧签名；参数齐全时输出**混合表**：参数化行 + 未取得车组底稿的历史标定行。

    混合纪律：只有拿到逐车组底稿的方案才参数化（calculation_mode=parameterized），
    其余方案保留历史标定并明确标记（legacy_calibration / decision_eligible=False），
    绝不让"部分参数化"伪装成"全部已核验"。
    """
    _validate_seg(seg)
    specs = COST_SCENARIOS if cost_scenarios is None else cost_scenarios
    settings = FINANCE_SETTINGS if settings is None else settings
    if not specs:
        if settings is not None:
            raise ValueError("提供了财务设置却没有车组方案，拒绝静默回退")
        return _build_calibrated_schemes(seg, source)
    if settings is None:
        raise ValueError("参数化方案必须提供 FinanceSettings，不能猜测年化/期限/边界")
    if len({s.order for s in specs}) != len(specs) or len({s.scenario for s in specs}) != len(specs):
        raise ValueError("方案 order 和 scenario 必须分别唯一")
    e0_t = float(seg["kg_co2"].sum()) / 1000
    rows = [evaluate_scenario(e0_t, spec, settings, source) for spec in specs]
    # 同边界基准参与比较；达标或不达标仅表示碳目标，不代表运营可行。
    baseline = {
        "order": 0, "scenario": "S0 柴油基准（现状）", "emission_t": e0_t,
        "reduction_pct": 0.0, "carbon_saving_t": 0.0, "capex_wan": 0.0,
        "opex_save_wan": 0.0, "payback_years": 0.0, "unit_cost_yuan_t": 0.0,
        "meets_target": settings.target_reduction_pct == 0, "is_main": False,
        "basis": "同数据期、同边界基准", "data_source": source, "e0_t": e0_t,
        "calculation_mode": "parameterized", "parameter_status": "draft_sourced_pending_review",
        "boundary": settings.boundary, "vehicle_count": 0,
        "annualization_factor": settings.annualization_factor, "annual_carbon_saving_t": 0.0,
        "lambda_yuan_t": settings.lambda_yuan_t, "horizon_years": settings.horizon_years,
        "discount_rate": settings.discount_rate, "cash_npv_wan": 0.0,
        "internal_value_npv_wan": 0.0, "annual_internal_carbon_value_wan": 0.0,
        "levelized_abatement_cost_yuan_t": float("nan"), "break_even_lambda_yuan_t": float("nan"),
        "operational_status": "baseline", "decision_eligible": settings.target_reduction_pct == 0,
        "cash_payback_within_horizon": True, "cost_components_json": "{}",
        "settings_source": settings.source,
        "constraint_status": "baseline",
        "constraint_note": "基准行：约束结论只对替代方案有意义",
        "c1_range_ok": None, "c2_window_ok": None, "c3_capacity_ok": None,
        "c4_charging_ok": None, "c5_winter_ok": None,
    }
    # 未取得车组底稿的方案：保留历史标定行，显式标记未评估（混合表纪律）。
    # order 0（S0 基准）由上方基准行替代，不重复出行。
    covered = {spec.order for spec in specs}
    legacy_rows = [_legacy_row(spec, e0_t, source, mark_constraints=True)
                   for spec in SCENARIOS
                   if int(spec["order"]) not in covered and int(spec["order"]) != 0]
    table = pd.DataFrame([baseline, *rows, *legacy_rows]).sort_values("order").reset_index(drop=True)
    # 仅在运营约束明确通过且达标时按内部估值选择；未达标不强推。
    # 全部方案 NPV 为负仍可能是达标约束下的最优，不能称为有利润。
    candidates = [row for row in table.to_dict(orient="records") if row["decision_eligible"]]
    if candidates:
        winner = max(candidates, key=lambda r: (r["internal_value_npv_wan"], -r["order"]))
        table.loc[table["scenario"] == winner["scenario"], "is_main"] = True
    return table


def build_lambda_curve(
    schemes: pd.DataFrame, settings: FinanceSettings | None = None,
    grid: Tuple[float, float, float] = (0.0, 3000.0, 5.0),
) -> pd.DataFrame:
    """λ 敏感性曲线（D5 闭环）：回本年限 × λ 的全网格**预计算**，页面只读不再自算。

    年节省口径与 ``evaluate_scenario`` 的内部估值完全一致：
    年节省 = 运营节省 + **年度**减排量 × λ（年度减排 = 数据期减排 × 年化系数）。
    legacy 行用同一公式（carbon_saving_t × 年化系数）——**公式全项目只有这一份**，
    页面不能再实现第二套（旧第 4 页用数据期减排 × λ，与 evaluator 的 NPV 口径不一致）。

    ``is_fastest`` = 该 λ 下回本最快（capex>0 的方案集合内，并列取 order 最小），
    供页面渲染"推荐翻转点"，不在页面重扫。
    """
    if schemes is None or schemes.empty or "scenario" not in schemes.columns:
        return pd.DataFrame()
    if "capex_wan" not in schemes.columns or "opex_save_wan" not in schemes.columns:
        return pd.DataFrame()

    def _num(value: Any, default: float = 0.0) -> float:
        """None / NaN / 非数字一律回落默认值——不能用 ``or``（NaN 是 truthy）。"""
        try:
            out = float(value)
        except (TypeError, ValueError):
            return default
        return out if pd.notna(out) else default

    factor = float(settings.annualization_factor) if settings is not None else ANNUALIZATION_FACTOR
    start, stop, step = (float(grid[0]), float(grid[1]), float(grid[2]))
    if not (stop > start and step > 0):
        raise ValueError("λ 网格必须满足 stop > start 且 step > 0")
    lambdas = np.arange(start, stop + step / 2.0, step)
    alt = schemes[(pd.to_numeric(schemes["capex_wan"], errors="coerce") > 0)
                  & (pd.to_numeric(schemes["reduction_pct"], errors="coerce") > 0)].copy()
    alt = alt.sort_values("order").reset_index(drop=True)
    rows: List[Dict[str, Any]] = []
    for _, r in alt.iterrows():
        capex = _num(r["capex_wan"])
        opex = _num(r.get("opex_save_wan"))
        annual_carbon = _num(r.get("annual_carbon_saving_t"))
        if annual_carbon <= 0:  # legacy 行没有年化减排列：用同一口径补算
            annual_carbon = _num(r.get("carbon_saving_t")) * factor
        for lam in lambdas:
            annual = opex + annual_carbon * float(lam) / 10000.0
            payback = capex / annual if annual > 0 else float("inf")
            rows.append({
                "scenario": str(r["scenario"]), "order": int(r["order"]),
                "lambda_yuan_t": float(lam),
                "annual_save_wan": annual, "payback_years": payback,
                "annual_carbon_t": annual_carbon,
                "capex_wan": capex, "opex_save_wan": opex,
                "meets_target": bool(r.get("meets_target")),
                "reduction_pct": _num(r.get("reduction_pct")),
                "is_main": bool(r.get("is_main")),
                "calculation_mode": str(r.get("calculation_mode") or "legacy_calibration"),
            })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    # 每个 λ 取回本最快者；并列时 order 小者优先（与旧页面扫描口径一致）。
    out["_rank"] = out.groupby("lambda_yuan_t")["payback_years"].rank(method="first")
    out["is_fastest"] = out["_rank"] == 1
    return out.drop(columns="_rank").reset_index(drop=True)


def build_od_band(seg: pd.DataFrame) -> pd.DataFrame:
    """按网点 OD 优先、城市 OD 兜底分组；未知 OD 不互相拼成一条线路。"""
    required = {"factor_gkm", "vehicle_type", "od_pair", "kg_co2"}
    if not required.issubset(seg.columns):
        raise ValueError("同线带宽缺列：" + ", ".join(sorted(required - set(seg.columns))))
    _validate_seg(seg)
    factors = pd.to_numeric(seg["factor_gkm"], errors="coerce")
    if not np.isfinite(factors.to_numpy(dtype=float, na_value=np.nan)).all() or (factors < 0).any():
        raise ValueError("factor_gkm 含无效值")
    frame = seg.copy()
    frame["factor_gkm"] = factors
    frame["_od_group"] = ""
    frame["_od_label"] = ""
    frame["_route_label"] = frame["od_pair"].fillna("").astype(str)
    for key in ("od_site", "od_key", "od_pair"):
        if key not in frame:
            continue
        raw = frame[key].fillna("").astype(str).str.strip()
        usable = ~raw.str.lower().isin(["", "nan", "none", "null", "<na>"])
        choose = frame["_od_group"].eq("") & usable
        frame.loc[choose, "_od_group"] = key + ":" + raw[choose]
        frame.loc[choose, "_od_label"] = raw[choose]
    if {"dep_site", "arr_site"}.issubset(frame.columns):
        dep = frame["dep_site"].fillna("").astype(str).str.strip()
        arr = frame["arr_site"].fillna("").astype(str).str.strip()
        valid = dep.ne("") & arr.ne("") & frame["_od_group"].str.startswith("od_site:")
        frame.loc[valid, "_route_label"] = dep[valid] + " → " + arr[valid]
    total_kg = float(frame["kg_co2"].sum())
    g = frame[frame["_od_group"].ne("")].groupby("_od_group", as_index=False).agg(
        od_site=("_od_label", "first"), od_pair=("_route_label", "first"),
        n=("factor_gkm", "size"), nt=("vehicle_type", "nunique"),
        fmin=("factor_gkm", "min"), fmax=("factor_gkm", "max"), kg=("kg_co2", "sum"),
    )
    g = g[(g["n"] >= 3) & (g["nt"] >= 2) & (g["fmin"] > 0)].copy()
    g["ratio"] = g["fmax"] / g["fmin"]
    g["kg_t"] = g["kg"] / 1000
    g["coverage_pct"] = g["kg"] / total_kg * 100 if total_kg > 0 else 0.0
    g = g.sort_values(["ratio", "_od_group"], ascending=[False, True]).reset_index(drop=True)
    result = g[["od_site", "od_pair", "n", "nt", "fmin", "fmax", "ratio", "kg_t", "coverage_pct"]]
    return result.round({key: 4 for key in ("fmin", "fmax", "ratio", "kg_t", "coverage_pct")})


def build_od_vehicle_mix(seg: pd.DataFrame, band: pd.DataFrame) -> pd.DataFrame:
    """可比较 OD 的逐车型构成（D4）：第 3 页“下钻 · 这条线实际跑了哪些车”的预计算。

    原页面在页面上做 groupby sum / mean / 占比，全部迁到这里——页面按 od_key 查表显示。
    只覆盖同线带宽表里的 OD（下钻入口就是那张表），行数 = OD 数 × 车型数。
    """
    if seg is None or seg.empty or band is None or band.empty:
        return pd.DataFrame()
    if "od_site" not in seg.columns or "od_site" not in band.columns:
        return pd.DataFrame()
    keys = band["od_site"].astype(str).unique().tolist()
    sub = seg[seg["od_site"].astype(str).isin(keys)]
    if sub.empty:
        return pd.DataFrame()
    need = {"vehicle_type", "kg_co2"}
    if not need.issubset(sub.columns):
        return pd.DataFrame()
    frame = sub.copy()
    frame["_kg"] = pd.to_numeric(frame["kg_co2"], errors="coerce").fillna(0.0)
    agg: Dict[str, Any] = {"段数": ("_kg", "size"), "碳排kg": ("_kg", "sum")}
    if "load_t" in frame.columns:
        agg["平均载重"] = ("load_t", "mean")
    if "factor_gkm" in frame.columns:
        agg["因子"] = ("factor_gkm", "mean")
    g = (frame.assign(_vt=frame["vehicle_type"].astype(str), _od=frame["od_site"].astype(str))
         .groupby(["_od", "_vt"], as_index=False).agg(**agg))
    g["占比"] = g["碳排kg"] / g.groupby("_od")["碳排kg"].transform("sum") * 100.0
    g = g.rename(columns={"_od": "od_site", "_vt": "vehicle_type"})
    g = g.sort_values(["od_site", "碳排kg"], ascending=[True, False]).reset_index(drop=True)
    return g.round({"平均载重": 3, "因子": 3, "碳排kg": 3, "占比": 3})


def build_analysis(
    *, baseline_path: Path | str | None = None, output_dir: Path | str | None = None,
    write: bool = True, cost_scenarios: tuple[ScenarioInput, ...] | None = None,
    settings: FinanceSettings | None = None,
) -> Dict[str, Any]:
    """pipeline 的无参调用仍写三表；CLI 默认只读，用 --write 显式落盘。

    cost_scenarios/settings 均为 None 时（pipeline 路径）：真实数据自动派生主推方案
    车组（build_cost_scenarios），演示数据回退历史标定；同时产出
    scenario_route_constraint.parquet（方案 × 哪条线，O6 逐线结论）。
    """
    baseline_path = Path(baseline_path) if baseline_path is not None else BASELINE_PATH
    output_dir = Path(output_dir) if output_dir is not None else PROCESSED_DIR
    if not baseline_path.exists():
        raise FileNotFoundError("缺少段级碳排文件：" + str(baseline_path))
    seg = pd.read_parquet(baseline_path)
    _validate_seg(seg, analysis=True)
    source = _source(baseline_path.parent / "_source.json")
    if cost_scenarios is None:
        cost_scenarios, settings = build_cost_scenarios(seg, source)
    hotspots = build_hotspots(seg)
    band = build_od_band(seg)
    od_mix = build_od_vehicle_mix(seg, band)
    schemes = build_schemes(seg, source, cost_scenarios, settings)
    route_table, verdict = build_route_constraints(seg, cost_scenarios or (), source)
    curve = build_lambda_curve(schemes, settings)
    if write:
        for frame, name in ((hotspots, "hotspots.parquet"), (band, "od_band.parquet"),
                            (schemes, "final_report.parquet"),
                            (curve, "lambda_curve.parquet"),
                            (od_mix, "od_vehicle_mix.parquet")):
            _save_parquet(frame, output_dir / name)
        if not route_table.empty:
            _save_parquet(route_table, output_dir / "scenario_route_constraint.parquet")
    total_kg = float(seg["kg_co2"].sum())
    median = float(band["ratio"].median()) if not band.empty else None
    coverage = float(band["kg_t"].sum() * 1000 / total_kg * 100) if total_kg > 0 else 0.0
    main = schemes[schemes["is_main"]]
    mode = str(schemes.iloc[0]["calculation_mode"])
    n_parameterized = int((schemes["calculation_mode"] == "parameterized").sum())
    print(f"[E1] 四维热点 {len(hotspots)} 行；[E2] 同线带宽 {len(band)} 条，覆盖 {coverage:.1f}%")
    print(f"[E3/E4] {len(schemes)} 个方案；模式：{mode}"
          f"（参数化 {n_parameterized} 行 / 历史标定 {len(schemes) - n_parameterized} 行）；"
          f"{'已写五表' if write else '只读检查，未写文件'}")
    if mode == "legacy_calibration":
        print("注意：4809 万/77.4%/9.6 年仍为历史标定，不代表底稿复核或运营约束通过。")
    elif not route_table.empty:
        print("      [O6] 方案 × 线路约束：%d 行；单程层 %.1f%% 里程 / 班次化层 %.1f%% 里程；"
              "场景裁决 operational_feasible=%s"
              % (len(route_table), verdict.get("single_trip_km_pct") or 0.0,
                 verdict.get("trunk_km_pct") or 0.0, verdict.get("operational_feasible")))
    print("      [D5] λ 曲线 %d 行（0–3000 元/t，步长 5；年节省含年度减排 × λ）" % len(curve))
    print("      [D4] OD 车型构成 %d 行（可比较 OD %d 条的逐车型预计算）" % (len(od_mix), len(band)))
    return {
        "od_ratio_median": median, "od_pairs": int(len(band)), "od_coverage_pct": coverage,
        "e0_t": total_kg / 1000,
        "main_reduction_pct": float(main.iloc[0]["reduction_pct"]) if not main.empty else None,
        "scenarios": int(len(schemes)), "calculation_mode": mode, "written": write,
        "parameterized_rows": n_parameterized,
        "route_constraint_rows": int(len(route_table)),
        "lambda_curve_rows": int(len(curve)),
        "od_vehicle_mix_rows": int(len(od_mix)),
        "operational_feasible": verdict.get("operational_feasible"),
        "single_trip_km_pct": verdict.get("single_trip_km_pct"),
        "trunk_km_pct": verdict.get("trunk_km_pct"),
        "depot_only_reduction_pct": verdict.get("depot_only_reduction_pct"),
    }


def self_test() -> bool:
    """E6：纯合成手算样例，绝不读写项目数据；数值不代表项目参数。"""
    import unittest

    class EvaluatorTests(unittest.TestCase):
        def setUp(self):
            self.group = VehicleGroup(
                group_id="synthetic-only", vehicle_count=2, annual_km_per_vehicle=10000,
                purchase_yuan_per_vehicle=100000, subsidy_yuan_per_vehicle=10000,
                disposal_yuan_per_vehicle=5000, baseline_energy="diesel", target_energy="electricity",
                baseline_units_per_100km=10, target_units_per_100km=20,
                baseline_price_yuan_per_unit=8, target_price_yuan_per_unit=1,
                baseline_maintenance_yuan_per_km=0.2, target_maintenance_yuan_per_km=0.1,
                baseline_period_emission_t=80, target_period_emission_t=20, source="合成测试，非项目数据",
            )
            self.spec = ScenarioInput(1, "合成电动车方案", (self.group,), 20000, 10000,
                                      1000, 0, "test-boundary", "合成测试", True)
            self.settings = FinanceSettings(3, 10, 0, 0, 50, "test-boundary", "合成测试")
            self.seg = pd.DataFrame({"kg_co2": [100000.0]})

        def test_hand_calculated_cashflow(self):
            row = evaluate_scenario(100, self.spec, self.settings)
            # 净投入20万，年省能源1.2万+维护0.2万+其他0.1万。
            self.assertAlmostEqual(row["capex_wan"], 20)
            self.assertAlmostEqual(row["opex_save_wan"], 1.5)
            self.assertAlmostEqual(row["payback_years"], 200000 / 15000)
            self.assertAlmostEqual(row["cash_npv_wan"], -5)
            self.assertAlmostEqual(row["emission_t"], 40)
            self.assertAlmostEqual(row["annual_carbon_saving_t"], 180)
            detail = json.loads(row["cost_components_json"])
            self.assertAlmostEqual(detail["net_capex_yuan"], row["capex_wan"] * 10000)

        def test_lambda_not_cash_and_period_is_annualized(self):
            table = build_sensitivity(100, self.spec, self.settings, "lambda_yuan_t", [0, 100])
            self.assertAlmostEqual(table.iloc[1]["internal_value_npv_wan"], 13)
            self.assertEqual(table.iloc[0]["payback_years"], table.iloc[1]["payback_years"])
            self.assertEqual(table.iloc[0]["cash_npv_wan"], table.iloc[1]["cash_npv_wan"])

        def test_discount_and_terminal_residual(self):
            row = evaluate_scenario(100, replace(self.spec, terminal_residual_yuan=10000),
                                    replace(self.settings, horizon_years=2, discount_rate=0.1))
            expected = -200000 + 15000 / 1.1 + 25000 / 1.1**2
            self.assertAlmostEqual(row["cash_npv_wan"] * 10000, expected)

        def test_energy_price_sensitivity(self):
            table = build_sensitivity(100, self.spec, self.settings, "electricity_price_multiplier", [1, 2])
            self.assertAlmostEqual(table.iloc[1]["opex_save_wan"], 1.1)
            self.assertEqual(self.spec.groups[0].target_price_yuan_per_unit, 1)
            self.assertEqual(table.iloc[0]["emission_t"], table.iloc[1]["emission_t"])

        def test_nonrecoverable_payback(self):
            row = evaluate_scenario(100, replace(self.spec, other_annual_saving_yuan=-100000), self.settings)
            self.assertTrue(np.isinf(row["payback_years"]))
            self.assertFalse(row["cash_payback_within_horizon"])

        def test_missing_operational_check_not_recommended(self):
            out = build_schemes(self.seg, "test", (replace(self.spec, operational_feasible=None),), self.settings)
            self.assertFalse(out["is_main"].any())
            self.assertTrue(out.iloc[1]["meets_target"])
            out = build_schemes(self.seg, "test", (self.spec,), self.settings)
            self.assertTrue(out.iloc[1]["is_main"])

        def test_reject_invalid_or_duplicate_groups(self):
            for invalid in (replace(self.group, vehicle_count=-1), replace(self.group, vehicle_count=1.5),
                            replace(self.group, annual_km_per_vehicle=float("nan")),
                            replace(self.group, baseline_period_emission_t=101),
                            replace(self.group, source="")):
                with self.subTest(invalid=invalid):
                    with self.assertRaises(ValueError):
                        evaluate_scenario(100, replace(self.spec, groups=(invalid,)), self.settings)
            with self.assertRaises(ValueError):
                evaluate_scenario(100, replace(self.spec, groups=(self.group, self.group)), self.settings)

        def test_reject_boundary_and_settings_errors(self):
            for setting in (replace(self.settings, boundary="other"),
                            replace(self.settings, annualization_factor=0),
                            replace(self.settings, horizon_years=1.5), replace(self.settings, lambda_yuan_t=-1)):
                with self.subTest(setting=setting):
                    with self.assertRaises(ValueError):
                        evaluate_scenario(100, self.spec, setting)
            with self.assertRaises(ValueError):
                build_schemes(self.seg, "test", (), self.settings)

        def test_share_boundaries_both_directions(self):
            self.assertAlmostEqual(share_boundary(100, 20, 70, 70)["max_share"], 0.2)
            self.assertAlmostEqual(share_boundary(100, 70, 20, 70)["min_share"], 0.8)
            self.assertEqual(share_boundary(100, 20, 20, 70)["status"], "all")
            self.assertEqual(share_boundary(100, 40, 40, 70)["status"], "none")

        def test_charging_boundary(self):
            self.assertAlmostEqual(charging_loss_boundary(100, 20, 10, 70)["max_loss_rate"], 0.5)
            self.assertEqual(charging_loss_boundary(100, 40, 10, 70)["status"], "none")
            self.assertFalse(charging_loss_boundary(100, 20, 0, 70)["upper_inclusive"])

        def test_od_city_fallback_and_unknown_exclusion(self):
            seg = pd.DataFrame({"kg_co2": [1.0] * 6, "factor_gkm": [100, 150, 100] * 2,
                                "vehicle_type": ["A", "B", "A"] * 2,
                                "od_pair": ["X-Y"] * 3 + [None] * 3})
            result = build_od_band(seg)
            self.assertEqual(len(result), 1)
            self.assertAlmostEqual(result.iloc[0]["ratio"], 1.5)
            self.assertAlmostEqual(result.iloc[0]["coverage_pct"], 50)

        def test_legacy_marked_and_unchanged(self):
            out = build_schemes(self.seg, "test", ())
            row = out[out["is_main"]].iloc[0]
            self.assertEqual(len(out), 7)
            self.assertEqual(row["capex_wan"], 4809)
            self.assertEqual(row["reduction_pct"], 77.4)
            self.assertEqual(row["calculation_mode"], "legacy_calibration")
            self.assertFalse(row["decision_eligible"])

        # ---------------- E4 逐车组成本模型（合成样例，非项目参数） ----------------

        def test_direct_yuan_per_km_energy_cost(self):
            group = replace(
                self.group, baseline_yuan_per_km=0.75, target_yuan_per_km=0.35,
                # units/price 路径若被误用会得到 20000/100×10×8=16000，与直填 15000 不同。
                baseline_units_per_100km=10, target_units_per_100km=20,
                baseline_price_yuan_per_unit=8, target_price_yuan_per_unit=1,
            )
            row = evaluate_scenario(100, replace(self.spec, groups=(group,)), self.settings)
            detail = json.loads(row["cost_components_json"])
            g = detail["groups"][0]
            self.assertAlmostEqual(g["baseline_energy_yuan_year"], 20000 * 0.75)
            self.assertAlmostEqual(g["target_energy_yuan_year"], 20000 * 0.35)
            # 能耗省 8000 + 维护省 2000 + 其他 1000（self.spec 自带）
            self.assertAlmostEqual(row["opex_save_wan"], 1.1)
            self.assertAlmostEqual(row["payback_years"], 200000 / 11000)

        def test_constraint_columns_from_summary(self):
            summary = {"status": "pass", "note": "合成结论",
                       "c1_range_ok": True, "c2_window_ok": True, "c3_capacity_ok": None,
                       "c4_charging_ok": True, "c5_winter_ok": False}
            row = evaluate_scenario(100, replace(self.spec, constraint_summary=summary), self.settings)
            self.assertEqual(row["constraint_status"], "pass")
            self.assertEqual(row["constraint_note"], "合成结论")
            self.assertTrue(row["c1_range_ok"])
            self.assertIsNone(row["c3_capacity_ok"])  # 未评估保持 None，不假填
            self.assertFalse(row["c5_winter_ok"])     # 冬季复检为信息项，也透传
            bare = evaluate_scenario(100, self.spec, self.settings)
            self.assertEqual(bare["constraint_status"], "pending")
            self.assertEqual(bare["constraint_note"], "未做逐线路约束评估")
            self.assertIsNone(bare["c1_range_ok"])

        def test_cost_scenarios_derivation_from_synthetic_segments(self):
            seg = pd.DataFrame({
                "vclass": [1, 1, 2, 2, 3, 3],
                "dist_km": [50000.0, 50000.0, 100000.0, 100000.0, 150000.0, 150000.0],
                "kg_co2": [20000.0, 20000.0, 75000.0, 75000.0, 200000.0, 200000.0],
                "speed_kmh": [40.0, 40.0, 50.0, 50.0, 60.0, 60.0],
                "od_pair": ["A-B", "A-B", "C-D", "C-D", "E-F", "E-F"],
                "cargo_weight_kg": [30.0, 30.0, 200.0, 200.0, 5000.0, 5000.0],
            })
            specs, settings = build_cost_scenarios(seg, "real")
            self.assertEqual(len(specs), 1)
            self.assertEqual(specs[0].scenario, MAIN_SCENARIO_NAME)
            self.assertEqual(settings, default_finance_settings())
            counts = [g.vehicle_count for g in specs[0].groups]
            self.assertEqual(counts, [4, 6, 7])  # ceil(年里程 ÷ (中位速度×8h×300d))
            row = evaluate_scenario(float(seg["kg_co2"].sum()) / 1000, specs[0], settings, "real")
            self.assertAlmostEqual(row["capex_wan"], 745.0)            # 4×16+6×26+7×75 万
            self.assertAlmostEqual(row["opex_save_wan"], 63.34643)
            self.assertAlmostEqual(row["payback_years"], 11.760726, places=6)
            self.assertAlmostEqual(row["carbon_saving_t"], 466.448088)
            self.assertAlmostEqual(row["reduction_pct"], 79.058998)
            self.assertAlmostEqual(row["unit_cost_yuan_t"], 15971.77, places=1)
            self.assertEqual(row["vehicle_count"], 17)
            self.assertEqual(row["calculation_mode"], "parameterized")
            # 演示/未知来源与缺列段表一律回退，不用演示数据冒充参数化结果
            self.assertEqual(build_cost_scenarios(seg, "demo"), (None, None))
            self.assertEqual(build_cost_scenarios(seg[["kg_co2"]], "real"), (None, None))

        def test_mixed_table_marks_legacy_and_requires_constraints(self):
            main_group = replace(self.group, baseline_period_emission_t=80,
                                 target_period_emission_t=10)  # 70/100 = 达标线
            main_spec = replace(
                self.spec, order=5, scenario="S5 合成主推", groups=(main_group,),
                operational_feasible=True,
                constraint_summary={"status": "pass", "note": "合成：全线路通过",
                                    "c1_range_ok": True, "c2_window_ok": True,
                                    "c3_capacity_ok": True, "c4_charging_ok": True,
                                    "c5_winter_ok": True},
            )
            out = build_schemes(self.seg, "test", (main_spec,), self.settings)
            self.assertEqual(len(out), 7)  # 基准 1 + 参数化 1 + 历史标定 5
            legacy = out[out["calculation_mode"] == "legacy_calibration"]
            self.assertEqual(len(legacy), 5)
            self.assertTrue((legacy["constraint_status"] == "未评估").all())
            self.assertTrue(legacy["c1_range_ok"].isna().all())
            self.assertFalse(legacy["decision_eligible"].any())
            row = out[out["order"] == 5].iloc[0]
            self.assertTrue(row["is_main"])
            self.assertTrue(row["decision_eligible"])
            self.assertEqual(row["constraint_status"], "pass")
            # 未过运营约束（None）时不强推：达标也不当主推
            pending = build_schemes(self.seg, "test",
                                    (replace(main_spec, operational_feasible=None),), self.settings)
            self.assertFalse(pending["is_main"].any())
            self.assertFalse(pending[pending["order"] == 5].iloc[0]["decision_eligible"])

        def test_lambda_curve_matches_evaluate_and_marks_fastest(self):
            """D5：λ 曲线口径必须与 evaluate_scenario 逐点一致，且翻转点只在表内标记。"""
            schemes = build_schemes(self.seg, "test", (self.spec,), self.settings)
            row = schemes[schemes["order"] == 1].iloc[0]
            curve = build_lambda_curve(schemes, self.settings, grid=(0.0, 100.0, 50.0))
            own = curve[curve["order"] == 1]
            self.assertEqual(len(own), 3)  # 混合表：参数化行 3 个网格点
            at_zero = own[own["lambda_yuan_t"] == 0.0].iloc[0]
            self.assertAlmostEqual(at_zero["payback_years"], row["payback_years"], places=9)
            self.assertAlmostEqual(at_zero["annual_save_wan"], row["opex_save_wan"], places=9)
            at_hundred = own[own["lambda_yuan_t"] == 100.0].iloc[0]
            expect_annual = row["opex_save_wan"] + row["annual_carbon_saving_t"] * 100.0 / 10000.0
            self.assertAlmostEqual(at_hundred["annual_save_wan"], expect_annual, places=9)
            self.assertAlmostEqual(at_hundred["payback_years"], row["capex_wan"] / expect_annual, places=9)
            # 每个 λ 只标一个最快者
            for lam in (0.0, 50.0, 100.0):
                self.assertEqual(int((curve[curve["lambda_yuan_t"] == lam]["is_fastest"]).sum()), 1)
            # legacy 行没有年化减排列：用 carbon_saving_t × 年化系数补，且不得出 NaN/inf
            mixed = build_schemes(self.seg, "test",
                                  (replace(self.spec, order=5, operational_feasible=True,
                                           constraint_summary={"status": "pass"}),), self.settings)
            curve2 = build_lambda_curve(mixed, self.settings, grid=(0.0, 50.0, 50.0))
            self.assertFalse(curve2["annual_save_wan"].isna().any())
            self.assertFalse(np.isinf(curve2["payback_years"].to_numpy(dtype=float)).any())
            legacy = mixed[mixed["calculation_mode"] == "legacy_calibration"].iloc[0]
            legacy_curve = curve2[(curve2["scenario"] == legacy["scenario"])
                                 & (curve2["lambda_yuan_t"] == 0.0)].iloc[0]
            self.assertAlmostEqual(legacy_curve["annual_carbon_t"],
                                   legacy["carbon_saving_t"] * self.settings.annualization_factor,
                                   places=9)
            # legacy 行 payback_years 是历史标定的三位小数 verbatim 值，曲线按同一公式重算，
            # 允许 <0.001 年的显示级差异（不悄悄改历史数字）。
            self.assertAlmostEqual(legacy_curve["payback_years"], legacy["payback_years"], places=3)
            # 网格参数非法要报错，不静默出空表
            with self.assertRaises(ValueError):
                build_lambda_curve(schemes, self.settings, grid=(0.0, 0.0, 5.0))

    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(EvaluatorTests))
    return result.wasSuccessful()


def main() -> int:
    parser = argparse.ArgumentParser(description="E1–E6 分析财务模块；默认只读检查，不覆盖结果")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--self-test", action="store_true", help="运行合成手算样例，不读写数据")
    action.add_argument("--write", action="store_true", help="显式写出三张结果表")
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    parser.add_argument("--output-dir", type=Path, default=PROCESSED_DIR)
    args = parser.parse_args()
    if args.self_test:
        return 0 if self_test() else 1
    summary = build_analysis(baseline_path=args.baseline, output_dir=args.output_dir, write=args.write)
    print(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
