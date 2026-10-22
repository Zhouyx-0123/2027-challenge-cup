"""第 7 页 · 车队电动化测算（情景演算页，docs/20 §11）

定位：ChargeMath 式滑块实时重算 + FleetTCO 式 NPV 分解，全部人民币口径。
- 默认值来自车队真实统计 fleet_stats.json（implied 油耗/电耗假设/购置价差）；
- 假设值一律黄框标注；结果标注"情景推演"，不进闸门、不改对拍口径；
- 浏览器零业务公式：计算在 src/scenarios.py，本页只收集输入与展示。
"""
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import streamlit as st

import services
from components import calcpanel, cards, layout, theme

layout.setup("7 电动化测算")
src = services.source()
gate = services.gate()
params_ui = layout.sidebar_params(services.source_name(), gate, current="7")

fleet = services.fleet_stats()
factors = services.calc_factors()
va = factors["vehicle_assumptions"]
fin = factors["finance"]
mock = services.is_mock()

VCLASS_NAME = {1: "轻型", 2: "中型", 3: "重型"}

theme.hero(
    "车队电动化测算",
    "改一个参数，全表联动：年节省、回本月份、NPV 分解、双口径减碳，一屏看齐。",
    kicker="07 / FLEET TCO · WHAT-IF",
    badges=[layout.source_badge(services.source_name()), layout.gate_badge(gate),
            "情景演算 · 非对拍口径", "人民币口径"],
)
if mock:
    layout.demo_note(str(src.get("reason", "")))

cards.chips([
    "默认油耗取自车队实测（段表 implied L/100km）",
    "黄框＝假设值，可改",
    "结果不进闸门，不碰 E₀ 对拍口径",
], tones=["mint", "warn", "skip"])

# ---------------- ① 参数 ----------------
layout.section("① 调参数", "左侧选车型与车队规模，右侧看结论；假设值带黄点")

vclass = st.radio("车型大类", [1, 2, 3], index=2,
                  format_func=lambda v: VCLASS_NAME[v], horizontal=True,
                  help="车型决定油耗/电耗/购置价差默认值")
fleet_vc = (fleet.get("by_vclass") or {}).get(str(vclass)) or {}
ice_l_default = float(fleet_vc.get("implied_l_per_100km") or 35.0)
bev_kwh_default = float(factors["energy_defaults"]["bev_kwh_per_100km"][str(vclass)])
ice_price_default = float(va["ice_price_yuan"][str(vclass)])
bev_price_default = ice_price_default + float(va["bev_price_delta_yuan"][str(vclass)])
km_default = 60000.0  # 假设值：运单是抽样数据，不推导年均里程（fleet_stats.json note）

c1, c2, c3 = st.columns(3)
with c1:
    n_vehicles = int(calcpanel.param_slider("车队规模（辆）", 1, 100, 10, 1,
                                            "同时电动化替换的车辆数"))
    km_per_veh_year = calcpanel.param_slider(
        "年均里程（km/辆）", 10000, 150000, km_default, 1000,
        "商业重卡典型 3–10 万 km/年；本项为假设值", "%.0f", assumed=True)
    diesel_price = calcpanel.param_slider(
        "柴油价（元/L）", 5.0, 10.0, 7.5, 0.1,
        "展示口径默认值 7.5 元/L（src/optimizer.py）", "%.1f")
with c2:
    ice_l = calcpanel.param_slider(
        "燃油车油耗（L/100km）", max(5.0, ice_l_default * 0.6), ice_l_default * 1.6,
        ice_l_default, 0.5,
        "默认＝本车队 %s 实测 implied 油耗（fleet_stats.json）" % VCLASS_NAME[vclass],
        "%.1f")
    bev_kwh = calcpanel.param_slider(
        "电动车电耗（kWh/100km）", max(10.0, bev_kwh_default * 0.6), bev_kwh_default * 1.6,
        bev_kwh_default, 1.0,
        "默认＝口径配置假设值（%s）" % VCLASS_NAME[vclass], "%.0f", assumed=True)
    elec_price = calcpanel.param_slider(
        "电价（元/kWh）", 0.3, 1.5, 0.8, 0.05,
        "展示口径默认值 0.8 元/kWh（src/optimizer.py）", "%.2f")
with c3:
    st.markdown("<div style='font-size:13px;margin-bottom:2px'>高级参数（假设值）</div>",
                unsafe_allow_html=True)
    horizon_years = int(st.number_input("评价期限（年）", 1, 10,
                                        int(fin["horizon_years"]), 1))
    discount_rate = st.number_input("折现率", 0.0, 0.15, float(fin["discount_rate"]),
                                    0.01, format="%.2f")
    maint_ice = st.number_input("燃油维护（元/km）", 0.0, 5.0,
                                float(va["maint_yuan_per_km"]["ice"]), 0.1, format="%.1f")
    maint_bev = st.number_input("电动维护（元/km）", 0.0, 5.0,
                                float(va["maint_yuan_per_km"]["bev"]), 0.1, format="%.1f")
    ice_price = st.number_input("燃油车购置价（元）", 0, 2000000, int(ice_price_default),
                                10000, format="%d")
    bev_price = st.number_input("电动车购置价（元）", 0, 3000000, int(bev_price_default),
                                10000, format="%d")
    insurance = st.number_input("保险（元/辆/年）", 0, 50000,
                                int(va["insurance_yuan_per_veh_year"]), 1000, format="%d")

calc_params = {
    "n_vehicles": n_vehicles, "vclass": vclass, "km_per_veh_year": km_per_veh_year,
    "ice_l_per_100km": ice_l, "bev_kwh_per_100km": bev_kwh,
    "diesel_price_yuan_per_l": diesel_price, "elec_price_yuan_per_kwh": elec_price,
    "ice_price_yuan": ice_price, "bev_price_yuan": bev_price,
    "maint_ice": maint_ice, "maint_bev": maint_bev,
    "insurance_yuan_per_veh_year": insurance,
    "horizon_years": horizon_years, "discount_rate": discount_rate,
}
try:
    r = services.calc_electrification(calc_params)
except Exception as exc:  # 演算失败不红屏：给提示，不给错（纪律同 A0）
    cards.empty_state("参数组合算不出结果", str(exc))
    layout.conclusion("参数超出合理范围时系统选择不猜——改参数再看。")
    st.stop()

# ---------------- ② 结论 ----------------
layout.section("② 看结论", "一屏结论 + 分解；数字随滑块实时重算")

payback = r["payback"]
payback_txt = ("%.0f 个月" % payback["months"]) if payback["months"] is not None else "运营不省，无法回本"
saving_wan = r["annual"]["saving"] / 1e4
saving_fleet_wan = r["annual"]["saving_fleet"] / 1e4
cut_wtw_t = r["co2"]["cut_wtw_t_per_year"] * n_vehicles
abate = r["abatement"]["wtw_yuan_per_t"]
abate_txt = ("%.0f 元/tCO₂e" % abate) if abate is not None else "—"

verdict_txt = (
    "按当前参数：<b>%d 辆%s电动车</b>每年省 <b>%.1f 万元</b>（全队 %.1f 万元/年），"
    "<b>%s</b>回本；WTW 口径每年减碳 <b>%.0f 吨</b>，减排成本 <b>%s</b>。"
    % (n_vehicles, VCLASS_NAME[vclass], saving_fleet_wan, saving_fleet_wan,
       payback_txt, cut_wtw_t, abate_txt))
tone = "mint" if saving_wan > 0 else "coral"
calcpanel.verdict(verdict_txt, tone=tone)

cards.stats([
    {"label": "年节省（全队）", "value": services.fmt(saving_fleet_wan, 1) + " 万元",
     "sub": "能耗+维护+保险差额", "tone": "mint" if saving_wan > 0 else "coral"},
    {"label": "回本周期", "value": payback_txt, "sub": "静态：购置价差 ÷ 年节省"},
    {"label": "NPV 差额（全队）", "value": services.fmt(r["npv"]["delta"] / 1e4, 1) + " 万元",
     "sub": "%d 年 %d%% 折现" % (horizon_years, round(discount_rate * 100))},
    {"label": "WTW 减碳（全队）", "value": services.fmt(cut_wtw_t, 0) + " t/年",
     "sub": "TTW %.0f t/年" % (r["co2"]["cut_ttw_t_per_year"] * n_vehicles)},
    {"label": "减排成本（WTW）", "value": abate_txt, "sub": "负值＝更省更清洁"},
], cols=5)

calcpanel.savings_meter(
    "燃油车队",
    "电动车队",
    r["annual"]["ice"]["total"] * n_vehicles / 1e4,
    r["annual"]["bev"]["total"] * n_vehicles / 1e4)

# ---- NPV 四分解 ----
layout.section("③ NPV 四分解（万元/辆）", "购置净残值 + 能耗 + 维护 + 保险；与 evaluator 公式口径一致")
npv_by = {"燃油": {k: r["npv"]["ice"][k] for k in
                  ("vehicle_net_residual", "energy", "maintenance", "insurance")},
          "电动": {k: r["npv"]["bev"][k] for k in
                   ("vehicle_net_residual", "energy", "maintenance", "insurance")}}
calcpanel.npv_stacked(npv_by, title="NPV 四分解对比（万元/辆，%d 年 %d%%）"
                      % (horizon_years, round(discount_rate * 100)))
cards.htable(
    ["组件", "燃油（万元/辆）", "电动（万元/辆）", "差额（万元/辆）"],
    [["购置（净残值）", services.fmt(r["npv"]["ice"]["vehicle_net_residual"] / 1e4, 1),
      services.fmt(r["npv"]["bev"]["vehicle_net_residual"] / 1e4, 1),
      services.fmt((r["npv"]["bev"]["vehicle_net_residual"] - r["npv"]["ice"]["vehicle_net_residual"]) / 1e4, 1)],
     ["能耗", services.fmt(r["npv"]["ice"]["energy"] / 1e4, 1),
      services.fmt(r["npv"]["bev"]["energy"] / 1e4, 1),
      services.fmt((r["npv"]["bev"]["energy"] - r["npv"]["ice"]["energy"]) / 1e4, 1)],
     ["维护", services.fmt(r["npv"]["ice"]["maintenance"] / 1e4, 1),
      services.fmt(r["npv"]["bev"]["maintenance"] / 1e4, 1),
      services.fmt((r["npv"]["bev"]["maintenance"] - r["npv"]["ice"]["maintenance"]) / 1e4, 1)],
     ["保险", services.fmt(r["npv"]["ice"]["insurance"] / 1e4, 1),
      services.fmt(r["npv"]["bev"]["insurance"] / 1e4, 1), "0.0"],
     ["合计", services.fmt(r["npv"]["ice"]["total"] / 1e4, 1),
      services.fmt(r["npv"]["bev"]["total"] / 1e4, 1),
      services.fmt(r["npv"]["delta"] / 1e4, 1)]],
    num_cols=[1, 2, 3])

# ---- 双口径碳排 ----
layout.section("④ 双口径减碳", "TTW（尾气管，电动=0）与 WTW（全生命周期，含电网上游）并列")
cards.htable(
    ["口径", "燃油（t/年）", "电动（t/年）", "削减（t/年）", "全队削减（t/年）"],
    [["TTW 尾气管", services.fmt(r["co2"]["ice"]["ttw_t"], 1),
      services.fmt(r["co2"]["bev"]["ttw_t"], 1),
      services.fmt(r["co2"]["cut_ttw_t_per_year"], 1),
      services.fmt(r["co2"]["cut_ttw_t_per_year_fleet"], 0)],
     ["WTW 全生命周期", services.fmt(r["co2"]["ice"]["wtw_t"], 1),
      services.fmt(r["co2"]["bev"]["wtw_t"], 1),
      services.fmt(r["co2"]["cut_wtw_t_per_year"], 1),
      services.fmt(r["co2"]["cut_wtw_t_per_year_fleet"], 0)]],
    num_cols=[1, 2, 3, 4])
cards.formula(
    "减排成本（WTW）＝ NPV 差额 ÷ 全期减排量 ＝ <span class='hl'>%s</span>；"
    "负值表示电动方案<b>更省且更清洁</b>（FleetTCO 的 $/tCO₂e abated，人民币口径）。"
    % ("%s 元/tCO₂e" % ("%.0f" % abate if abate is not None else "—")))
cards.note(
    "三层口径分列：L0 基准账（E₀，只读结果表）｜L1 路线情景（高德距离×同一因子）｜"
    "L2 本页决策演算（参数化 what-if）。三者不混算；本页数字不影响任何对拍口径。",
    sub="电网因子按 config/calc_factors.json 取值，以生态环境部最新年度公告为准。")

calcpanel.assumption_note(r["assumptions"])
calcpanel.result_json(calc_params, r)

layout.conclusion(
    "给它一个车队规模和电价，它告诉你电动化每年省多少、多久回本、减多少碳、"
    "每吨碳值多少钱——假设值全部黄框敞亮，评委可以当场改任何一个数。"
)
