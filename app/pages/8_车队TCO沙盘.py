"""第 8 页 · 车队 TCO 与脱碳沙盘（情景演算页，docs/20 §11）

定位：FleetTCO 框架的多技术路线同框架对比（人民币口径）+
      EcoTransIT 式口径对照（行业吨公里法 vs 我们段级自底向上）。
- 柴油/电动为实路线（有真实因子）；CNG/HVO 为示意路线（无真实因子，明示）；
- VUF 车辆利用因子、NPV 分解、$/tCO₂e abated、回本，一页看齐；
- 计算在 src/scenarios.py，本页只收集输入与展示；结果不进闸门。
"""
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import streamlit as st

import services
from components import calcpanel, cards, charts, layout, theme

layout.setup("8 车队TCO沙盘")
src = services.source()
gate = services.gate()
params_ui = layout.sidebar_params(services.source_name(), gate, current="8")
lam = float(params_ui.get("lam", 100.0))

fleet = services.fleet_stats()
factors = services.calc_factors()
va = factors["vehicle_assumptions"]
fin = factors["finance"]
co2f = factors["co2_factors"]
mock = services.is_mock()

VCLASS_NAME = {1: "轻型", 2: "中型", 3: "重型"}

theme.hero(
    "车队 TCO 与脱碳沙盘",
    "同一框架比路线：柴油、电动、CNG、HVO，谁的全周期成本低、每吨碳便宜。",
    kicker="08 / FLEET TCO · MULTI-ROUTE",
    badges=[layout.source_badge(services.source_name()), layout.gate_badge(gate),
            "情景演算 · 非对拍口径", "人民币口径"],
)
if mock:
    layout.demo_note(str(src.get("reason", "")))

cards.chips([
    "柴油/电动＝实路线（真实因子）",
    "CNG/HVO＝示意路线（黄框，无真实因子）",
    "VUF 调整口径同 FleetTCO",
], tones=["mint", "warn", "skip"])

# ---------------- ① 参数 ----------------
layout.section("① 调参数", "车队规模、利用因子与路线组合")

vclass = st.radio("车型大类", [1, 2, 3], index=2,
                  format_func=lambda v: VCLASS_NAME[v], horizontal=True,
                  key="p8_vclass", help="决定油耗/电耗/价差默认值")
fleet_vc = (fleet.get("by_vclass") or {}).get(str(vclass)) or {}
ice_l_default = float(fleet_vc.get("implied_l_per_100km") or 35.0)
bev_kwh_default = float(factors["energy_defaults"]["bev_kwh_per_100km"][str(vclass)])
ice_price_default = float(va["ice_price_yuan"][str(vclass)])
bev_delta_default = float(va["bev_price_delta_yuan"][str(vclass)])

c1, c2, c3 = st.columns(3)
with c1:
    n_vehicles = int(calcpanel.param_slider("车队规模（辆）", 1, 100, 25, 1,
                                            "同时评估的车辆数"))
    km_per_veh_year = calcpanel.param_slider(
        "年均里程（km/辆）", 10000, 150000, 60000.0, 1000,
        "商业重卡典型 3–10 万 km/年；本项为假设值", "%.0f", assumed=True)
with c2:
    vuf = calcpanel.param_slider(
        "车辆利用因子 VUF", 0.5, 1.0, 1.0, 0.01,
        "加油/载货/运营可用度（0–1）；有效里程＝名义里程×VUF，"
        "调整成本＝NPV÷有效里程（FleetTCO 口径）", "%.2f")
    base_price = st.number_input("基准车购置价（元）", 0, 2000000,
                                 int(ice_price_default), 10000, format="%d",
                                 help="柴油基准车价格；各路线的价差在此基础上叠加")
with c3:
    horizon_years = int(st.number_input("评价期限（年）", 1, 10,
                                        int(fin["horizon_years"]), 1))
    discount_rate = st.number_input("折现率", 0.0, 0.15,
                                    float(fin["discount_rate"]), 0.01, format="%.2f")

st.markdown("<div style='font-size:13px;margin:8px 0 2px 0'>路线组合</div>",
            unsafe_allow_html=True)
use_bev = st.checkbox("电动（实路线）", value=True)
use_cng = st.checkbox("CNG（示意路线）", value=False)
use_hvo = st.checkbox("HVO 生物柴油（示意路线）", value=False)

with st.expander("路线参数（默认值＝口径配置/车队实测；示意路线可改）"):
    e1, e2, e3, e4 = st.columns(4)
    with e1:
        st.markdown("**柴油（基准）**")
        diesel_price = st.number_input("柴油价（元/L）", 0.1, 20.0, 7.5, 0.1,
                                       format="%.1f", key="p8_dprice")
        ice_l = st.number_input("油耗（L/100km）", 1.0, 80.0, ice_l_default, 0.5,
                                format="%.1f", key="p8_icel")
    with e2:
        st.markdown("**电动**")
        elec_price = st.number_input("电价（元/kWh）", 0.1, 3.0, 0.8, 0.05,
                                     format="%.2f", key="p8_eprice")
        bev_kwh = st.number_input("电耗（kWh/100km）", 10.0, 250.0, bev_kwh_default,
                                  1.0, format="%.0f", key="p8_kwh")
        bev_delta = st.number_input("购置价差（元）", -500000, 1500000,
                                    int(bev_delta_default), 10000, format="%d",
                                    key="p8_delta")
    with e3:
        st.markdown("**CNG（示意）**")
        cng_m3 = st.number_input("气耗（m³/100km）", 5.0, 80.0, 38.0, 1.0,
                                 format="%.0f", key="p8_cngm3")
        cng_price = st.number_input("气价（元/m³）", 1.0, 12.0, 4.5, 0.1,
                                    format="%.1f", key="p8_cngp")
    with e4:
        st.markdown("**HVO（示意）**")
        hvo_l = st.number_input("油耗（L/100km）", 5.0, 80.0, 38.0, 1.0,
                                format="%.0f", key="p8_hvol")
        hvo_price = st.number_input("油价（元/L）", 3.0, 20.0, 9.5, 0.1,
                                    format="%.1f", key="p8_hvop")

scenario_specs = [
    {"key": "diesel", "name": "柴油（基准）", "kind": "real",
     "energy_per_100km": ice_l, "price_per_unit": diesel_price,
     "co2_ttw_kg_per_unit": co2f["diesel_ttw_kg_per_l"]["value"],
     "co2_wtw_kg_per_unit": co2f["diesel_ttw_kg_per_l"]["value"] + co2f["diesel_wtt_kg_per_l"]["value"],
     "capex_delta_yuan": 0.0,
     "maint_yuan_per_km": va["maint_yuan_per_km"]["ice"],
     "residual_rate": va["residual_rate"]["ice"]},
]
if use_bev:
    scenario_specs.append(
        {"key": "bev", "name": "电动", "kind": "real",
         "energy_per_100km": bev_kwh, "price_per_unit": elec_price,
         "co2_ttw_kg_per_unit": 0.0,
         "co2_wtw_kg_per_unit": co2f["grid_wtw_kg_per_kwh"]["value"],
         "capex_delta_yuan": bev_delta,
         "maint_yuan_per_km": va["maint_yuan_per_km"]["bev"],
         "residual_rate": va["residual_rate"]["bev"]})
if use_cng:
    scenario_specs.append(
        {"key": "cng", "name": "CNG（示意）", "kind": "sketch",
         "energy_per_100km": cng_m3, "price_per_unit": cng_price,
         "co2_ttw_kg_per_unit": co2f["cng_ttw_kg_per_m3"]["value"],
         "co2_wtw_kg_per_unit": co2f["cng_ttw_kg_per_m3"]["value"] + co2f["cng_wtt_kg_per_m3"]["value"],
         "capex_delta_yuan": 60000.0,
         "maint_yuan_per_km": va["maint_yuan_per_km"]["ice"],
         "residual_rate": va["residual_rate"]["ice"]})
if use_hvo:
    scenario_specs.append(
        {"key": "hvo", "name": "HVO（示意）", "kind": "sketch",
         "energy_per_100km": hvo_l, "price_per_unit": hvo_price,
         "co2_ttw_kg_per_unit": 0.0,
         "co2_wtw_kg_per_unit": co2f["hvo_wtw_kg_per_l"]["value"],
         "capex_delta_yuan": 0.0,
         "maint_yuan_per_km": va["maint_yuan_per_km"]["ice"],
         "residual_rate": va["residual_rate"]["ice"]})

calc_params = {
    "scenarios": scenario_specs, "n_vehicles": n_vehicles,
    "km_per_veh_year": km_per_veh_year, "vuf": vuf,
    "base_price_yuan": base_price, "horizon_years": horizon_years,
    "discount_rate": discount_rate,
}
try:
    r = services.calc_fleet_tco(calc_params)
except Exception as exc:
    cards.empty_state("参数组合算不出结果", str(exc))
    layout.conclusion("参数超出合理范围时系统选择不猜——改参数再看。")
    st.stop()

# ---------------- ② 结论 ----------------
layout.section("② 看结论", "累计成本、NPV 分解与减排成本排序")

af = r["params"]["annuity_factor"]
best = min((s for s in r["scenarios"] if s["key"] != r["baseline_key"]),
           key=lambda s: s["npv"]["total"], default=None)
best_abate = min((s for s in r["scenarios"]
                  if s["abatement_wtw_yuan_per_t"] is not None),
                 key=lambda s: s["abatement_wtw_yuan_per_t"], default=None)
if best is not None:
    calcpanel.verdict(
        "按当前参数：<b>%d 辆 %s</b>车队 %d 年 NPV 最低的是 <b>%s</b>"
        "（%.1f 万元/辆）；%s"
        % (n_vehicles, VCLASS_NAME[vclass], horizon_years, best["name"],
           best["npv"]["total"] / 1e4,
           ("减排成本最低：%s（%.0f 元/tCO₂e）。"
            % (best_abate["name"], best_abate["abatement_wtw_yuan_per_t"]))
           if best_abate is not None else ""))

name_map = {s["key"]: s["name"] for s in r["scenarios"]}
calcpanel.cumulative_chart(r["cumulative"], name_map,
                           title="车队累计成本（万元，名义口径，%d 辆）" % n_vehicles)

npv_by = {s["name"]: {k: s["npv"][k] for k in
                      ("vehicle_net_residual", "energy", "maintenance", "insurance")}
          for s in r["scenarios"]}
calcpanel.npv_stacked(npv_by, title="NPV 四分解（万元/辆，%d 年 %d%% 折现）"
                      % (horizon_years, round(discount_rate * 100)))

layout.section("③ 路线对比表", "实路线与示意路线分列；内部估值＝现金 NPV + λ×减排×年金系数")
rows = []
for s in r["scenarios"]:
    internal_delta = (s["npv_delta"]
                      + lam * s["cut_wtw_t_per_year"] * af)
    abate = s["abatement_wtw_yuan_per_t"]
    rows.append([
        s["name"],
        "实路线" if s["kind"] == "real" else "示意",
        services.fmt(s["npv"]["total"] / 1e4, 1),
        services.fmt(s["cost_per_effective_km"], 2),
        ("%.1f" % s["payback_years"]) if s["payback_years"] != float("inf") else "不回本",
        services.fmt(s["co2"]["wtw_t"], 1),
        ("%.0f" % abate) if abate is not None else "基准",
        services.fmt(internal_delta / 1e4, 1),
    ])
cards.htable(
    ["路线", "口径", "NPV（万/辆）", "元/有效km", "回本（年）",
     "WTW（t/年）", "减排成本（元/t）", "内部估值差额（万元）"],
    rows, num_cols=[2, 3, 4, 5, 6, 7])
cards.note(
    "内部估值差额＝相对基准的现金 NPV 差额 + λ（%.0f 元/t）× 年减排 × 年金现值系数；"
    "λ 在侧栏全局参数调。示意路线（CNG/HVO）本项目无真实因子，"
    "只用于展示框架可扩展性，不可作为结论引用。" % lam,
    sub="回本为静态口径：价差 ÷ 年运营节省；与 evaluator._payback 一致。")

# ---------------- ④ EcoTransIT 式口径对照 ----------------
layout.section("④ 口径对照：行业吨公里法 vs 我们的段级自底向上",
               "同一批运单，两种算法——我们的模型强在哪（EcoTransIT 方法论对照）")
implied = fleet.get("implied_g_per_tkm")
cmp_result = services.tonne_km_comparison(
    implied or 0.0,
    total_km=float(fleet.get("total_km") or 0.0),
    total_load_tkm=float(fleet.get("total_load_tkm") or 0.0),
    total_co2_kg=float(fleet.get("e0_kg") or 0.0),
)
cards.stats([
    {"label": "行业吨公里默认强度", "value": "%.0f g/t·km" % cmp_result["industry_g_per_tkm"],
     "sub": cmp_result["source"], "tone": "amber"},
    {"label": "我们段级模型强度", "value": ("%.1f g/t·km" % cmp_result["our_g_per_tkm"])
     if implied else "—", "sub": "E₀ ÷（里程×载重），段级实测", "tone": "mint"},
    {"label": "行业法估算总量", "value": (services.fmt(cmp_result["industry_total_t"], 0) + " t")
     if cmp_result["industry_total_t"] else "—", "sub": "默认强度 × 总吨公里"},
    {"label": "我们 E₀（实测）", "value": services.fmt(float(fleet.get("e0_kg") or 0) / 1000.0, 0) + " t",
     "sub": "段级 VSP 模型逐段累加", "tone": "mint"},
], cols=4)
cards.steps([
    {"n": "1", "t": "行业吨公里法", "d": "距离×载重×默认强度，top-down"},
    {"n": "2", "t": "我们：段级自底向上", "d": "VSP 逐段积分，车型/载重/速度全入"},
    {"n": "3", "t": "空驶与装载分摊", "d": "装载率、空驶按回路分摊（EN 16258）"},
    {"n": "4", "t": "标准对标", "d": "GLEC / EN 16258 / ISO 14083 / GHG Protocol"},
], active=-1)
cards.note(
    "吨公里法是“平均值×重量×距离”，车型、载重、速度、路况全部被平均掉；"
    "我们的段级模型把每一段运单的车型、载重、门到门速度算进碳排放率，"
    "所以同线带宽能到 1.50×——这是“碳账能审计”的根。",
    sub="对照只用来说明方法差异；本项目对拍口径一律以段级模型与闸门为准。")

calcpanel.assumption_note(r["assumptions"] + [
    "年均里程、基准购置价为假设值/默认值（黄点标注）",
    "内部估值用侧栏 λ（%.0f 元/t），仅作决策视角展示" % lam,
])
calcpanel.result_json(calc_params, r)

layout.conclusion(
    "同一框架下比路线：NPV、回本、每吨减碳成本一页看齐；"
    "再用行业吨公里法对照，说明我们的段级自底向上模型为什么更细、更可审计。"
)
