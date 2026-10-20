# -*- coding: utf-8 -*-
"""情景演算内核：第 7/8 页（电动化测算 / 车队 TCO 沙盘）的唯一计算入口。

**为什么有这个文件**（docs/20 §11 情景演算页纪律）：
    工程师的铁律是"前端只读不算"，但评委现场改一个参数就要看到结论——
    这正是竞赛叙事要的"给它一笔运单，它自己报警"。折中办法：
    演算逻辑全部收口到本文件（src/ 侧、纯函数、可单测），app/ 页面只收集
    输入、调用本模块、展示输出。**业务逻辑仍在 src/，app/ 不写一行公式。**

三条纪律（与 docs/20 §4 前端只读证明并列）：
    1. 只读 ``config/calc_factors.json``（口径）与调用方传入参数；
       不读宽表、不写任何结果表、不 import preprocess/optimizer 的取数逻辑；
    2. 输出必带 ``assumptions`` 列表，页面据此给假设值打黄框（工程师建议 3）；
    3. 演算结果**不得**进入 kpi.json / gate_report.json——对拍口径与情景
       推演永远分列（README §六 口径红线的延伸）。

**公式口径与 src/evaluator.evaluate_scenario 一致**（NPV、回本、单位减排成本），
由 tools/scenarios_smoke_test.py 对拍保证；价格默认值取自 optimizer 的展示
口径常量，不在此重复维护第二套。

人民币口径：金额＝元；里程＝km/年；油耗＝L/100km；电耗＝kWh/100km；
碳排＝tCO₂e；减排成本＝元/tCO₂e。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import optimizer

ROOT = Path(__file__).resolve().parents[1]
FACTORS_PATH = ROOT / "config" / "calc_factors.json"

# 与 evaluator 对齐的评价期限默认值（页面可改）
DEFAULT_HORIZON_YEARS = 5
DEFAULT_DISCOUNT_RATE = 0.06


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _num(name: str, value: Any, minimum: Optional[float] = None,
         maximum: Optional[float] = None) -> float:
    """有限数值校验（纪律同 evaluator._number：拒绝布尔/字符串/NaN/inf）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s 必须是数值，得到 %r" % (name, value))
    result = float(value)
    if result != result or result in (float("inf"), float("-inf")):
        raise ValueError("%s 必须是有限数" % name)
    if minimum is not None and result < minimum:
        raise ValueError("%s 必须 >= %s" % (name, minimum))
    if maximum is not None and result > maximum:
        raise ValueError("%s 必须 <= %s" % (name, maximum))
    return result


def _int(name: str, value: Any, minimum: int) -> int:
    result = _num(name, value, minimum)
    if not float(result).is_integer():
        raise ValueError("%s 必须是整数" % name)
    return int(result)


def load_factors() -> Dict[str, Any]:
    """读口径配置（每次调用重读：改 JSON 即改口径，无需重启）。"""
    return json.loads(FACTORS_PATH.read_text(encoding="utf-8"))


def annuity_factor(rate: float, years: int) -> float:
    """年金现值系数 (1-(1+r)^-n)/r；r=0 时退化为 n。"""
    if rate <= 0:
        return float(years)
    return (1.0 - (1.0 + rate) ** (-years)) / rate


def _payback_years(capex_delta_yuan: float, annual_saving_yuan: float) -> float:
    """静态回本期（年）。口径同 evaluator._payback：无净投入记 0，无节省记 inf。"""
    if capex_delta_yuan <= 0:
        return 0.0
    if annual_saving_yuan <= 0:
        return float("inf")
    return capex_delta_yuan / annual_saving_yuan


# ---------------------------------------------------------------------------
# 单车/小车队经济性分量（两个演算页共用）
# ---------------------------------------------------------------------------

def _annual_costs(km_year: float, energy_per_100km: float, price_per_unit: float,
                  maint_yuan_per_km: float, insurance_yuan: float) -> Dict[str, float]:
    """年运营成本（元/年）：能耗 + 维护 + 保险。energy_per_100km 与 price_per_unit
    的单位必须配对（L/100km×元/L，kWh/100km×元/kWh，m³/100km×元/m³）。"""
    energy = km_year * energy_per_100km / 100.0 * price_per_unit
    maint = km_year * maint_yuan_per_km
    return {"energy": energy, "maint": maint, "insurance": insurance_yuan,
            "total": energy + maint + insurance_yuan}


def _npv_of(price_yuan: float, residual_rate: float, annual: Dict[str, float],
            rate: float, years: int) -> Dict[str, float]:
    """NPV 四分解：购置净残值 + 能耗 + 维护 + 保险（元，单辆）。"""
    af = annuity_factor(rate, years)
    vehicle_net = price_yuan - price_yuan * residual_rate / (1.0 + rate) ** years
    return {
        "vehicle_net_residual": vehicle_net,
        "energy": annual["energy"] * af,
        "maintenance": annual["maint"] * af,
        "insurance": annual["insurance"] * af,
        "total": vehicle_net + (annual["energy"] + annual["maint"] + annual["insurance"]) * af,
    }


def _co2_annual(km_year: float, energy_per_100km: float,
                ttw_kg_per_unit: float, wtw_kg_per_unit: float) -> Dict[str, float]:
    """年碳排（tCO₂e/年）：TTW（尾气管）与 WTW（全生命周期，含上游）。"""
    units = km_year * energy_per_100km / 100.0
    return {
        "ttw_t": units * ttw_kg_per_unit / 1000.0,
        "wtw_t": units * wtw_kg_per_unit / 1000.0,
    }


# ---------------------------------------------------------------------------
# 第 7 页 · 电动化测算
# ---------------------------------------------------------------------------

def calc_electrification(params: Dict[str, Any]) -> Dict[str, Any]:
    """电动车队测算：燃油基准 vs 电动，输出年节省、回本、NPV 分解、双口径减碳。

    参数（全部可缺省——缺省值来自口径配置与 optimizer 展示口径常量）：
        n_vehicles            车辆数（>=1）
        km_per_veh_year       年均里程（km，>0；默认取车队实测）
        vclass                车型大类 0/1/2（决定购置价差与电耗默认值）
        ice_l_per_100km       燃油车油耗（L/100km，>0；默认取车队实测）
        bev_kwh_per_100km     电动车电耗（kWh/100km，>0；缺省用口径配置假设值）
        diesel_price_yuan_per_l   柴油价（默认 optimizer.DIESEL_PRICE_YUAN_PER_L）
        elec_price_yuan_per_kwh   电价（默认 optimizer.ENERGY_PRICE_YUAN_PER_KWH）
        ice_price_yuan / bev_price_yuan   购置价（>=0）
        maint_ice / maint_bev 维护费（元/km）
        insurance_yuan_per_veh_year  保险（元/辆/年）
        horizon_years / discount_rate
    返回 dict：annual / capex / payback / npv / co2 / abatement / cumulative / assumptions
    """
    f = load_factors()
    va = f["vehicle_assumptions"]
    fin = f["finance"]
    co2f = f["co2_factors"]

    n = _int("n_vehicles", params.get("n_vehicles", 10), 1)
    vclass = _int("vclass", params.get("vclass", 3), 1)
    if vclass not in (1, 2, 3):
        raise ValueError("vclass 必须是 1（轻）/2（中）/3（重）")
    km = _num("km_per_veh_year", params.get("km_per_veh_year", 60000.0), 0.001)
    ice_l = _num("ice_l_per_100km", params.get("ice_l_per_100km", 35.0), 0.001)
    bev_kwh = _num("bev_kwh_per_100km",
                   params.get("bev_kwh_per_100km",
                              f["energy_defaults"]["bev_kwh_per_100km"][str(vclass)]), 0.001)
    p_diesel = _num("diesel_price_yuan_per_l",
                    params.get("diesel_price_yuan_per_l", optimizer.DIESEL_PRICE_YUAN_PER_L), 0.001)
    p_elec = _num("elec_price_yuan_per_kwh",
                  params.get("elec_price_yuan_per_kwh", optimizer.ENERGY_PRICE_YUAN_PER_KWH), 0.001)
    ice_price = _num("ice_price_yuan",
                     params.get("ice_price_yuan", va["ice_price_yuan"][str(vclass)]), 0.0)
    bev_price = _num("bev_price_yuan", params.get("bev_price_yuan",
                                                  ice_price + va["bev_price_delta_yuan"][str(vclass)]), 0.0)
    maint_ice = _num("maint_ice", params.get("maint_ice", va["maint_yuan_per_km"]["ice"]), 0.0)
    maint_bev = _num("maint_bev", params.get("maint_bev", va["maint_yuan_per_km"]["bev"]), 0.0)
    insurance = _num("insurance_yuan_per_veh_year",
                     params.get("insurance_yuan_per_veh_year", va["insurance_yuan_per_veh_year"]), 0.0)
    years = _int("horizon_years", params.get("horizon_years", fin["horizon_years"]), 1)
    rate = _num("discount_rate", params.get("discount_rate", fin["discount_rate"]), 0.0, 0.5)

    assumptions: List[str] = []
    if "km_per_veh_year" not in params:
        assumptions.append("年均里程为默认估计值，应替换为车队实测 fleet_stats.json")
    if "ice_l_per_100km" not in params:
        assumptions.append("油耗为默认估计值，应替换为车队实测")
    if "bev_kwh_per_100km" not in params:
        assumptions.append("电耗为假设值（口径配置 energy_defaults）")
    if "ice_price_yuan" not in params or "bev_price_yuan" not in params:
        assumptions.append("购置价/价差为假设值（口径配置 vehicle_assumptions）")
    assumptions.append("维护费、保险、残值率、折现率均为假设值")
    assumptions.append("电网排放因子按口径配置取值，以最新年度公告为准")

    # ---- 年运营成本（单辆/年）----
    a_ice = _annual_costs(km, ice_l, p_diesel, maint_ice, insurance)
    a_bev = _annual_costs(km, bev_kwh, p_elec, maint_bev, insurance)
    saving_year = a_ice["total"] - a_bev["total"]

    # ---- 回本 ----
    capex_delta = bev_price - ice_price
    payback_years = _payback_years(capex_delta, saving_year)
    payback_months = (payback_years * 12.0) if payback_years != float("inf") else None

    # ---- NPV 四分解（单辆 + 车队）----
    npv_ice = _npv_of(ice_price, va["residual_rate"]["ice"], a_ice, rate, years)
    npv_bev = _npv_of(bev_price, va["residual_rate"]["bev"], a_bev, rate, years)
    for key in list(npv_ice):
        npv_ice[key + "_fleet"] = npv_ice[key] * n
        npv_bev[key + "_fleet"] = npv_bev[key] * n
    npv_delta = npv_bev["total"] - npv_ice["total"]

    # ---- 双口径碳排（单辆/年 → 车队/年 → 全期）----
    ttw = co2f["diesel_ttw_kg_per_l"]["value"]
    wtt = co2f["diesel_wtt_kg_per_l"]["value"]
    grid = co2f["grid_wtw_kg_per_kwh"]["value"]
    c_ice = _co2_annual(km, ice_l, ttw, ttw + wtt)
    c_bev = {"ttw_t": 0.0, "wtw_t": _co2_annual(km, bev_kwh, 0.0, grid)["wtw_t"]}
    cut_ttw = c_ice["ttw_t"] - c_bev["ttw_t"]
    cut_wtw = c_ice["wtw_t"] - c_bev["wtw_t"]

    # ---- 减排成本（元/tCO₂e）：全期净成本增量 ÷ 全期减排量；负值＝更省更清洁 ----
    lifetime_cut_ttw = cut_ttw * years
    lifetime_cut_wtw = cut_wtw * years
    abatement_ttw = (npv_delta / lifetime_cut_ttw) if lifetime_cut_ttw > 1e-9 else None
    abatement_wtw = (npv_delta / lifetime_cut_wtw) if lifetime_cut_wtw > 1e-9 else None

    # ---- 名义累计成本曲线（0..years 年，单辆）----
    cumulative = []
    cum_ice, cum_bev = float(ice_price), float(bev_price)
    cumulative.append({"year": 0, "ice": cum_ice, "bev": cum_bev})
    for y in range(1, years + 1):
        cum_ice += a_ice["total"]
        cum_bev += a_bev["total"]
        cumulative.append({"year": y, "ice": cum_ice, "bev": cum_bev})

    return {
        "params": {"n_vehicles": n, "vclass": vclass, "km_per_veh_year": km,
                   "horizon_years": years, "discount_rate": rate},
        "annual": {"ice": a_ice, "bev": a_bev, "saving": saving_year,
                   "saving_fleet": saving_year * n},
        "capex_delta_yuan": capex_delta,
        "payback": {"years": payback_years, "months": payback_months},
        "npv": {"ice": npv_ice, "bev": npv_bev, "delta": npv_delta},
        "co2": {"ice": c_ice, "bev": c_bev,
                "cut_ttw_t_per_year": cut_ttw, "cut_wtw_t_per_year": cut_wtw,
                "cut_ttw_t_per_year_fleet": cut_ttw * n, "cut_wtw_t_per_year_fleet": cut_wtw * n},
        "abatement": {"ttw_yuan_per_t": abatement_ttw, "wtw_yuan_per_t": abatement_wtw},
        "cumulative": cumulative,
        "assumptions": assumptions,
    }


# ---------------------------------------------------------------------------
# 第 8 页 · 车队 TCO 沙盘（多路线同框架对比）
# ---------------------------------------------------------------------------

def calc_fleet_tco(params: Dict[str, Any]) -> Dict[str, Any]:
    """多技术路线同框架对比（FleetTCO 框架，人民币口径）。

    参数：
        scenarios   路线列表，第一条为基准（柴油）。每条：
            {key, name, kind: "real"|"sketch", energy_per_100km, price_per_unit,
             co2_ttw_kg_per_unit, co2_wtw_kg_per_unit, capex_delta_yuan,
             maint_yuan_per_km, residual_rate}
            未给字段按口径配置假设值补齐；kind="sketch" 的路线页面必须标注示意。
        n_vehicles / km_per_veh_year / vuf / insurance / horizon_years / discount_rate
        vuf         车辆利用因子 0–1（FleetTCO：加油/载货/运营可用度几何均值），
                    有效里程 = 名义里程 × VUF，调整后成本 = NPV ÷ 有效里程。
    """
    f = load_factors()
    va = f["vehicle_assumptions"]
    fin = f["finance"]

    raw = params.get("scenarios") or []
    if not raw:
        raise ValueError("至少需要一条路线（第一条为基准）")
    n = _int("n_vehicles", params.get("n_vehicles", 25), 1)
    km = _num("km_per_veh_year", params.get("km_per_veh_year", 60000.0), 0.001)
    vuf = _num("vuf", params.get("vuf", 1.0), 0.05, 1.0)
    insurance = _num("insurance_yuan_per_veh_year",
                     params.get("insurance_yuan_per_veh_year", va["insurance_yuan_per_veh_year"]), 0.0)
    years = _int("horizon_years", params.get("horizon_years", fin["horizon_years"]), 1)
    rate = _num("discount_rate", params.get("discount_rate", fin["discount_rate"]), 0.0, 0.5)

    base_price = _num("base_price_yuan", params.get("base_price_yuan", 450000.0), 0.0)
    af = annuity_factor(rate, years)

    scenarios: List[Dict[str, Any]] = []
    for i, s in enumerate(raw):
        name = str(s.get("name") or ("路线 %d" % (i + 1)))
        kind = str(s.get("kind", "real"))
        energy = _num(name + ".energy_per_100km", s.get("energy_per_100km"), 0.0)
        price_unit = _num(name + ".price_per_unit", s.get("price_per_unit"), 0.0)
        ttw = _num(name + ".co2_ttw_kg_per_unit", s.get("co2_ttw_kg_per_unit", 0.0), 0.0)
        wtw = _num(name + ".co2_wtw_kg_per_unit", s.get("co2_wtw_kg_per_unit", ttw), 0.0)
        capex_delta = _num(name + ".capex_delta_yuan", s.get("capex_delta_yuan", 0.0))
        maint = _num(name + ".maint_yuan_per_km", s.get("maint_yuan_per_km", va["maint_yuan_per_km"]["ice"]), 0.0)
        residual = _num(name + ".residual_rate", s.get("residual_rate", va["residual_rate"]["ice"]), 0.0, 1.0)

        price = base_price + capex_delta
        annual = _annual_costs(km, energy, price_unit, maint, insurance)
        npv = _npv_of(price, residual, annual, rate, years)
        co2 = _co2_annual(km, energy, ttw, wtw)
        scenarios.append({
            "key": str(s.get("key") or name), "name": name, "kind": kind,
            "annual": annual, "npv": npv, "co2": co2,
            "capex_delta_yuan": capex_delta,
            "cost_per_km_nominal": npv["total"] / max(km * years, 1e-9),
            "cost_per_effective_km": npv["total"] / max(km * vuf * years, 1e-9),
        })

    base = scenarios[0]
    for s in scenarios:
        s["cut_ttw_t_per_year"] = base["co2"]["ttw_t"] - s["co2"]["ttw_t"]
        s["cut_wtw_t_per_year"] = base["co2"]["wtw_t"] - s["co2"]["wtw_t"]
        s["npv_delta"] = s["npv"]["total"] - base["npv"]["total"]
        s["payback_years"] = _payback_years(s["capex_delta_yuan"],
                                            base["annual"]["total"] - s["annual"]["total"])
        life_cut_wtw = s["cut_wtw_t_per_year"] * years
        s["abatement_wtw_yuan_per_t"] = (s["npv_delta"] / life_cut_wtw) if life_cut_wtw > 1e-9 else None
        life_cut_ttw = s["cut_ttw_t_per_year"] * years
        s["abatement_ttw_yuan_per_t"] = (s["npv_delta"] / life_cut_ttw) if life_cut_ttw > 1e-9 else None

    # 名义累计成本曲线（车队口径，0..years）
    cumulative: List[Dict[str, Any]] = []
    cum = {s["key"]: s["npv"]["vehicle_net_residual"] * n for s in scenarios}
    cumulative.append({"year": 0, **cum})
    for y in range(1, years + 1):
        for s in scenarios:
            cum[s["key"]] += s["annual"]["total"] * n
        cumulative.append({"year": y, **{k: v for k, v in cum.items()}})

    return {
        "params": {"n_vehicles": n, "km_per_veh_year": km, "vuf": vuf,
                   "horizon_years": years, "discount_rate": rate, "annuity_factor": af},
        "scenarios": scenarios,
        "baseline_key": base["key"],
        "cumulative": cumulative,
        "assumptions": [
            "各路线能耗/价差/维护/残值为口径配置假设值或用户填写，页面按路线逐项标注",
            "CNG/HVO 为示意路线（kind=sketch）：本项目无真实因子，仅演示框架可扩展性",
            "VUF 调整口径：有效里程 = 名义里程 × VUF；调整成本 = NPV ÷ 有效里程",
        ],
    }


# ---------------------------------------------------------------------------
# EcoTransIT 式口径对照：行业吨公里法 vs 我们的段级自底向上
# ---------------------------------------------------------------------------

def tonne_km_comparison(our_implied_g_per_tkm: float,
                        industry_g_per_tkm: Optional[float] = None,
                        total_km: float = 0.0, total_load_tkm: float = 0.0,
                        total_co2_kg: float = 0.0) -> Dict[str, Any]:
    """同一批运单：行业吨公里默认强度 vs 我们段级模型实测强度。

    我们的一侧全部由调用方从结果表聚合传入（kg_co2 / dist_km / load_t），
    本函数只做对照展示，不重新计算碳排。
    """
    f = load_factors()
    if industry_g_per_tkm is None:
        industry_g_per_tkm = f["comparison_defaults"]["industry_g_per_tkm"]
    industry = _num("industry_g_per_tkm", industry_g_per_tkm, 0.0)
    ours = _num("our_implied_g_per_tkm", our_implied_g_per_tkm, 0.0)
    industry_total_t = industry * total_load_tkm / 1e6 if total_load_tkm > 0 else None
    return {
        "industry_g_per_tkm": industry,
        "our_g_per_tkm": ours,
        "industry_total_t": industry_total_t,
        "our_total_t": total_co2_kg / 1000.0 if total_co2_kg else None,
        "diff_pct": (100.0 * (ours - industry) / industry) if industry > 0 else None,
        "source": f["comparison_defaults"]["industry_source"],
    }
