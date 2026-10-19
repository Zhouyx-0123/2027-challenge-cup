# -*- coding: utf-8 -*-
"""E1–E6 分析与财务模块，Owner：曾广。

兼容 build_analysis() / build_schemes(seg, source) 及既有三张结果表。
默认保留历史标定并明确标识未核验；原始成本参数齐备后启用参数化计算。
CLI 默认只读：python -m src.evaluator
纯计算自测：python -m src.evaluator --self-test
详细输入单位、公式和接线说明见 docs/08_口径与假设清单.md 的 COST 节。
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
from typing import Any, Dict, List

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


def _build_calibrated_schemes(seg: pd.DataFrame, source: str) -> pd.DataFrame:
    e0_t = float(seg["kg_co2"].sum()) / 1000.0
    rows = []
    for spec in SCENARIOS:
        reduction = float(spec["reduction_pct"])
        emission = e0_t * (1.0 - reduction / 100.0)
        capex = float(spec["capex_wan"])
        opex = float(spec["opex_save_wan"])
        saving = e0_t - emission
        payback = _payback(capex, opex)
        unit = (capex * 10000.0 / saving) if saving > 0 else 0.0
        rows.append({
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
            "is_main": bool(spec["is_main"]),
            "basis": spec["basis"],
            "data_source": source,
        })
    out = pd.DataFrame(rows).sort_values("order").reset_index(drop=True)
    out["e0_t"] = round(e0_t, 3)
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
            if field.name not in {"group_id", "source", "baseline_energy", "target_energy", "vehicle_count"}:
                _number(field.name, getattr(group, field.name), 0)
        for energy in (group.baseline_energy, group.target_energy):
            if energy not in {"diesel", "electricity", "hydrogen"}:
                raise ValueError("energy 仅支持 diesel / electricity / hydrogen")
        km = count * group.annual_km_per_vehicle
        components.append({
            "group_id": group.group_id, "vehicle_count": count,
            "purchase_yuan": count * group.purchase_yuan_per_vehicle,
            "subsidy_yuan": count * group.subsidy_yuan_per_vehicle,
            "disposal_yuan": count * group.disposal_yuan_per_vehicle,
            "baseline_energy_yuan_year": km / 100 * group.baseline_units_per_100km * group.baseline_price_yuan_per_unit,
            "target_energy_yuan_year": km / 100 * group.target_units_per_100km * group.target_price_yuan_per_unit,
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
        "calculation_mode": "parameterized", "parameter_status": "caller_supplied_unreviewed",
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
    """保留旧签名；参数齐全时使用车组模型，否则明确输出历史标定表。"""
    _validate_seg(seg)
    specs = COST_SCENARIOS if cost_scenarios is None else cost_scenarios
    settings = FINANCE_SETTINGS if settings is None else settings
    if not specs:
        if settings is not None:
            raise ValueError("提供了财务设置却没有车组方案，拒绝静默回退")
        out = _build_calibrated_schemes(seg, source)
        out["calculation_mode"] = "legacy_calibration"
        out["parameter_status"] = "unverified_legacy_totals"
        out["operational_status"] = "pending"
        out["decision_eligible"] = False
        out["boundary"] = "未逐方案核实，禁止直接跨边界排名"
        out["basis"] = out["basis"] + "；历史代码标定，未核验原底稿；is_main 仅为原主推标签"
        return out
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
        "calculation_mode": "parameterized", "parameter_status": "caller_supplied_unreviewed",
        "boundary": settings.boundary, "vehicle_count": 0,
        "annualization_factor": settings.annualization_factor, "annual_carbon_saving_t": 0.0,
        "lambda_yuan_t": settings.lambda_yuan_t, "horizon_years": settings.horizon_years,
        "discount_rate": settings.discount_rate, "cash_npv_wan": 0.0,
        "internal_value_npv_wan": 0.0, "annual_internal_carbon_value_wan": 0.0,
        "levelized_abatement_cost_yuan_t": float("nan"), "break_even_lambda_yuan_t": float("nan"),
        "operational_status": "baseline", "decision_eligible": settings.target_reduction_pct == 0,
        "cash_payback_within_horizon": True, "cost_components_json": "{}",
        "settings_source": settings.source,
    }
    # 仅在运营约束明确通过且达标时按内部估值选择；未达标不强推。
    # 全部方案 NPV 为负仍可能是达标约束下的最优，不能称为有利润。
    candidates = [row for row in [baseline, *rows] if row["decision_eligible"]]
    if candidates:
        winner = max(candidates, key=lambda r: (r["internal_value_npv_wan"], -r["order"]))
        winner["is_main"] = True
    return pd.DataFrame([baseline, *rows]).sort_values("order").reset_index(drop=True)


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


def build_analysis(
    *, baseline_path: Path | str | None = None, output_dir: Path | str | None = None,
    write: bool = True, cost_scenarios: tuple[ScenarioInput, ...] | None = None,
    settings: FinanceSettings | None = None,
) -> Dict[str, Any]:
    """pipeline 的无参调用仍写三表；CLI 默认只读，用 --write 显式落盘。"""
    baseline_path = Path(baseline_path) if baseline_path is not None else BASELINE_PATH
    output_dir = Path(output_dir) if output_dir is not None else PROCESSED_DIR
    if not baseline_path.exists():
        raise FileNotFoundError("缺少段级碳排文件：" + str(baseline_path))
    seg = pd.read_parquet(baseline_path)
    _validate_seg(seg, analysis=True)
    source = _source(baseline_path.parent / "_source.json")
    hotspots = build_hotspots(seg)
    band = build_od_band(seg)
    schemes = build_schemes(seg, source, cost_scenarios, settings)
    if write:
        for frame, name in ((hotspots, "hotspots.parquet"), (band, "od_band.parquet"),
                            (schemes, "final_report.parquet")):
            _save_parquet(frame, output_dir / name)
    total_kg = float(seg["kg_co2"].sum())
    median = float(band["ratio"].median()) if not band.empty else None
    coverage = float(band["kg_t"].sum() * 1000 / total_kg * 100) if total_kg > 0 else 0.0
    main = schemes[schemes["is_main"]]
    mode = str(schemes.iloc[0]["calculation_mode"])
    print(f"[E1] 四维热点 {len(hotspots)} 行；[E2] 同线带宽 {len(band)} 条，覆盖 {coverage:.1f}%")
    print(f"[E3/E4] {len(schemes)} 个方案；模式：{mode}；{'已写三表' if write else '只读检查，未写文件'}")
    if mode == "legacy_calibration":
        print("注意：4809 万/77.4%/9.6 年仍为历史标定，不代表底稿复核或运营约束通过。")
    return {
        "od_ratio_median": median, "od_pairs": int(len(band)), "od_coverage_pct": coverage,
        "e0_t": total_kg / 1000,
        "main_reduction_pct": float(main.iloc[0]["reduction_pct"]) if not main.empty else None,
        "scenarios": int(len(schemes)), "calculation_mode": mode, "written": write,
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
