"""第 4 页 · 成本效益：多花多少钱、几年回本、λ 一动推荐换不换。

数据契约（只读，不重算）：
- final_report.parquet：capex_wan / opex_save_wan / payback_years / unit_cost_yuan_t /
  emission_t / carbon_saving_t / cost_components_json（E4 逐车组参数化 + legacy 静态标定，
  混合表纪律见 schema：legacy 行约束列一律“未评估”）
- lambda_curve.parquet（D5）：回本年限 × λ 全网格预计算（0–3000 元/t，步长 5）。
  本页一切 λ 相关数字的唯一来源，口径与 evaluator NPV 逐点一致
  （年节省 = 运营节省 + **年度**减排量 × λ）；页面不再自建第二套公式
- gate_report.json：方案排放对回 E₀ 的结构校验结论（原页面级自校验，D4 迁入闸门）
- kpi.json：E₀ 与汇总口径
- 侧栏全局参数：λ（内部碳价）与成本口径（赛题 / 碳价延展）

页面口径：λ 只进入“年度节省/回本年限”，且只经预计算表读取；当前 λ 取最近网格点
（步长 5 元/t，页面注明）。业务口径（成本构成分解 E4）已由曾广逐车组参数化，
未核验的历史标定行在混合表里显式标注“未评估”，不冒充决策推荐。
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import numpy as np
import pandas as pd
import streamlit as st

import services
from components import cards, charts, layout, theme


def _cost_detail(row) -> Dict[str, Any]:
    """读 cost_components_json（只做字段读取，不做任何换算）。"""
    try:
        out = json.loads(row.get("cost_components_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def _group_count(detail: Dict[str, Any], group_id: str) -> str:
    for g in detail.get("groups") or []:
        if str(g.get("group_id")) == group_id:
            return services.fmt(g.get("vehicle_count"), 0)
    return "—"

layout.setup("4 成本效益")
src = services.source()
gate = services.gate()
layout.sidebar_params(services.source_name(), gate, current="4")

mock = services.is_mock()
k = services.kpis()
lam = float(st.session_state.get("lam", 100.0))
basis_mode = str(st.session_state.get("basis", "赛题口径"))

theme.hero(
    "成本效益：多花多少钱、几年回本",
    "决定回本的是两个数：一次性投资与年度节省；λ 把碳变成钱——"
    "它是全站唯一的定价旋钮，一动，回本年限就变。",
    kicker="04 / COST & PAYBACK",
    badges=[layout.source_badge(services.source_name()), layout.gate_badge(gate),
            "λ = " + services.fmt(lam, 0) + " 元/t", "碳价延展口径"],
)
if mock:
    layout.demo_note(str(src.get("reason", "")))

cards.steps([
    {"n": "00", "t": "首页"}, {"n": "01", "t": "基准核算"}, {"n": "02", "t": "高排放诊断"},
    {"n": "03", "t": "替代方案"}, {"n": "04", "t": "成本效益"}, {"n": "05", "t": "碳会计台账"},
], active=4)

sc = services.scenarios()
if sc.empty or "scenario" not in sc.columns:
    theme.section("成本对账", "")
    cards.empty_state(
        "方案表还没生成",
        "先运行  python -m src.pipeline  生成 final_report.parquet（七方案成本对比）。")
    layout.conclusion("没有方案表就不算账——前端只读，不编数字。")
    st.stop()

e0_t = float(k.get("e0_t") or 0.0)
main_row = sc[sc["is_main"] == True].iloc[0] if (sc["is_main"] == True).any() else sc.iloc[-1]
main_name = str(main_row["scenario"])

# ---- 口径与决策资格（曾广增量） ----
cards.panel_head("口径与决策资格", "先看这批方案能不能用，再看回本；λ 只是展示旋钮", "evaluator 增量")
mode_raw = str(main_row.get("calculation_mode", "legacy_calibration"))
param_raw = str(main_row.get("parameter_status", "unverified_legacy_totals"))
oper_raw = str(main_row.get("operational_status", "pending"))
eligible = bool(main_row.get("decision_eligible", False))
boundary = str(main_row.get("boundary", "未逐方案核实"))
parameterized = mode_raw == "parameterized"
cards.chips([
    "calculation_mode = " + mode_raw,
    "parameter_status = " + param_raw,
    "decision_eligible = " + str(eligible),
    "operational_status = " + oper_raw,
    "λ = 展示口径",
], tones=["ok" if parameterized else "warn", "warn",
          "ok" if eligible else "bad", "skip", "info"])
detail = _cost_detail(main_row) if parameterized else {}
if parameterized:
    cards.note("主推 %s 为逐车组参数化（E4）：capex / 运营节省 / 回本全部由段表派生的车组"
               "（轻型 %s 辆、中型 %s 辆、重型 %s 辆）算出，通过 C1–C5 运营约束，"
               "decision_eligible=True。历史标定行仍以 legacy_calibration 单列，"
               "约束列一律“未评估”，不冒充决策推荐。边界：" % (
                   main_name,
                   _group_count(detail, "轻型（vclass 1）"),
                   _group_count(detail, "中型（vclass 2）"),
                   _group_count(detail, "重型（vclass 3）"),
               ) + boundary + "。"
               "λ 只进入“年度节省/回本年限”的展示换算，不改变碳排结果。")
else:
    cards.note("当前方案表是历史标定（legacy_calibration），参数未核验、运营约束待 O6 校验；"
               "4,809 万 / 77.4% / 9.6 年只作静态对标。边界：" + boundary + "。"
               "λ 只进入“年度节省/回本年限”的展示换算，不改变碳排结果。")


# ================= A · λ 敏感性 =================
st.markdown("")
layout.section("λ 敏感性", "唯一旋钮：碳一定价，回本年限全线缩短，推荐序可能翻转")

curve = services.lambda_curve()
grid_info = services.lambda_grid()
if curve.empty or not grid_info:
    cards.empty_state(
        "λ 敏感性曲线还没生成",
        "重跑  python -m src.pipeline  生成 lambda_curve.parquet"
        "（回本年限 × λ 全网格预计算，口径与 evaluator NPV 逐点一致）。")
    layout.conclusion("没有预计算曲线就不画敏感性——前端只读，不建第二套公式。")
    st.stop()

plot = curve[np.isfinite(pd.to_numeric(curve["payback_years"], errors="coerce"))].copy()
at_lam = services.lambda_curve_at(lam)        # 当前 λ 的最近网格行（步长 5 元/t）
at_zero = services.lambda_curve_at(0.0)
at_1000 = services.lambda_curve_at(1000.0)
nearest_lam = float(at_lam["lambda_yuan_t"].iloc[0]) if not at_lam.empty else lam


def _curve_row(frame: pd.DataFrame, name: str):
    hit = frame[frame["scenario"].astype(str) == name]
    return hit.iloc[0] if not hit.empty else None


main_lam = _curve_row(at_lam, main_name)
main_zero = _curve_row(at_zero, main_name)
main_1000 = _curve_row(at_1000, main_name)
fastest = at_lam[at_lam["is_fastest"] == True]
fastest_row = fastest.iloc[0] if not fastest.empty else None

cards.stats([
    {"label": "当前 λ（元/tCO₂e）", "value": services.fmt(lam, 0), "tone": "info",
     "sub": "侧栏全局参数调整｜网格步长 " + services.fmt(grid_info["step"], 0) + " 元/t"},
    {"label": "主推 " + main_name.split(" ")[0] + " 回本",
     "value": services.fmt(main_lam["payback_years"] if main_lam is not None else None, 2) + " 年",
     "tone": "amber",
     "sub": "λ=0 时 " + services.fmt(main_zero["payback_years"] if main_zero is not None else None, 2) + " 年"},
    {"label": "当前回本最快",
     "value": services.fmt(fastest_row["payback_years"] if fastest_row is not None else None, 2) + " 年",
     "tone": "mint",
     "sub": str(fastest_row["scenario"]) if fastest_row is not None else "—"},
    {"label": "λ=1000 时主推回本",
     "value": services.fmt(main_1000["payback_years"] if main_1000 is not None else None, 2) + " 年",
     "tone": "coral", "sub": "碳价越高，减排越值钱"},
], cols=4)

if abs(nearest_lam - lam) > 1e-9:
    cards.note("当前 λ = %s 元/t 不在预计算网格上，已取最近网格点 %s 元/t"
               "（网格 %s–%s 元/t，步长 %s）——曲线是 pipeline 预计算的，页面不补算。"
               % (services.fmt(lam, 0), services.fmt(nearest_lam, 0),
                  services.fmt(grid_info["min"], 0), services.fmt(grid_info["max"], 0),
                  services.fmt(grid_info["step"], 0)))

ymax = 12.0 if pd.to_numeric(plot["payback_years"], errors="coerce").max() <= 12.0 else None
charts.line_multi(
    plot, "lambda_yuan_t", "payback_years", "scenario", vline_x=lam,
    vline_label="当前 λ = " + services.fmt(lam, 0),
    title="回本年限 × λ（预计算口径：年度节省 = 运营节省 + 年度减排量 × λ）",
    height=400, ymax=ymax,
    labels={"lambda_yuan_t": "内部碳价 λ（元/tCO₂e）", "payback_years": "回本年限（年）"})
cards.note("读法：每条线是一个方案；竖线是当前 λ。线越陡，说明该方案对碳价越敏感——"
           "减排量越大，碳价越高时回本改善越多。曲线由 src/evaluator.build_lambda_curve "
           "预计算（0–%s 元/t，步长 %s，共 %s 个网格点），与 evaluator NPV 用的是同一份公式，"
           "页面只读不重算。" % (services.fmt(grid_info["max"], 0),
                              services.fmt(grid_info["step"], 0),
                              services.fmt(grid_info["points"], 0)))

# 当前 λ 下的对账表（全部读预计算表）
rows = []
for _, r in at_lam.sort_values("order").iterrows():
    zero = _curve_row(at_zero, str(r["scenario"]))
    rows.append([
        str(r["scenario"]),
        services.fmt(r.get("capex_wan"), 0),
        services.fmt(r.get("opex_save_wan"), 1),
        services.fmt(r.get("annual_save_wan"), 1),
        services.fmt(zero["payback_years"] if zero is not None else None, 2),
        services.fmt(r.get("payback_years"), 2),
        "✓" if bool(r.get("meets_target")) else "—",
    ])
cards.htable(["方案", "投资(万元)", "运营节省(万元/年)", "含λ节省(万元/年)",
              "回本 λ=0(年)", "回本 当前λ(年)", "达标"],
             rows, num_cols=[1, 2, 3, 4, 5])

# 推荐翻转点（读预计算表的 is_fastest 列，页面不重扫）
fast = curve[curve["is_fastest"] == True].sort_values("lambda_yuan_t")
reigns = []   # [(λ 起点, 方案名)]——冠军更替序列
for _, r in fast.iterrows():
    if not reigns or reigns[-1][1] != str(r["scenario"]):
        reigns.append((float(r["lambda_yuan_t"]), str(r["scenario"])))

if len(reigns) > 1:
    segs = []
    for i, (lam0, name) in enumerate(reigns):
        row = _curve_row(curve, name)
        lam1 = reigns[i + 1][0] if i + 1 < len(reigns) else None
        span = ("λ " + services.fmt(lam0, 0)
                + ("–" + services.fmt(lam1 - grid_info["step"], 0) if lam1 is not None else " 以上"))
        mark = "达标" if (row is not None and bool(row.get("meets_target"))) else "未达标"
        segs.append(span + " <span class='hl2'>" + cards.esc(name) + "</span>（减排 "
                    + services.fmt(row.get("reduction_pct") if row is not None else None, 1)
                    + "% · " + mark + "）")
    cards.formula("推荐翻转点（读预计算表 is_fastest 列）：回本最快的方案随 λ 易主——"
                  + " → ".join(segs) + "。")
    dial_max = None
    for lam0, name in reigns:
        if name == main_name:
            dial_max = lam0
            break
    if dial_max is not None and dial_max <= 1000.0:
        cards.note("主推成为回本最快需 λ ≥ " + services.fmt(dial_max, 0)
                   + " 元/t，在旋钮量程内；但冠军易主不改变推荐——推荐看的是 70% 达标线。")
    else:
        cards.note("旋钮量程只有 0–1000 元/t：量程内主推从来不是回本最快的，"
                   "但易主前的冠军减排率均未达 70% 目标线，进不了推荐集。"
                   "达标集合内主推回本不慢于同价方案，且减排计数更高——同价选多减，主推是 "
                   + main_name.split(" ")[0] + "。")
else:
    cards.note("在 λ ∈ [0, %s] 内回本最快者不随碳价易主——但推荐不只看回本："
               "主推满足 70% 达标线，是达标集合内减排计数最高的方案。"
               % services.fmt(grid_info["max"], 0))

# ================= B · 成本对账 =================
st.markdown("")
layout.section("成本对账", "同一份数据、同一套账：每个方案的排放都能对回 E₀")

rows, row_cls = [], []
for _, r in sc.sort_values("order").iterrows():
    rows.append([
        str(r["scenario"]),
        services.fmt(r.get("capex_wan"), 1),
        services.fmt(r.get("opex_save_wan"), 1),
        services.fmt(r.get("payback_years"), 2),
        services.fmt(r.get("unit_cost_yuan_t"), 0),
        services.fmt(r.get("emission_t"), 2),
        services.fmt(r.get("carbon_saving_t"), 2),
        "✓ 达标" if bool(r.get("meets_target")) else "—",
    ])
    row_cls.append("main" if bool(r.get("is_main")) else ("base" if float(r["reduction_pct"]) <= 0 else ""))
cards.htable(["方案", "投资(万元)", "年节省(万元)", "回本 λ=0(年)", "单位减排成本(元/t)",
              "排放(t)", "减排量(t)", "达标"], rows, row_cls=row_cls,
             num_cols=[1, 2, 3, 4, 5, 6])

# 对拍检查（D4：原页面级自校验迁入闸门，页面只读结论）
emit_item = next((i for i in (gate.get("structural") or [])
                  if str(i.get("id")) == "scheme_emission"), None)
if emit_item is None:
    cards.chips(["闸门还没有“方案排放对回 E₀”这项，重跑 python -m src.pipeline"], tones=["skip"])
elif str(emit_item.get("status")) == "PASS":
    cards.chips(["✓ " + str(emit_item.get("detail"))], tones=["ok"])
else:
    cards.chips(["对账不一致：" + str(emit_item.get("detail"))], tones=["bad"])
cards.note("这是 pipeline 出口闸门结构项 scheme_emission 的结论（原第 4 页的页面级自校验，"
           "D4 迁入闸门）：任何一项对不上，说明结果表被改过——以 gate_report.json 为准。")

# 投资回收曲线（年节省读预计算表，含当前 λ）
st.markdown("")
cards.panel_head("投资回收曲线", "累计节省穿越投资额的年份即回本点",
                 "预计算口径 · 年度节省含 λ")
alt_names = curve["scenario"].astype(str).drop_duplicates().tolist()
pick = st.selectbox("选一个方案看曲线", alt_names,
                    index=alt_names.index(main_name) if main_name in alt_names else 0,
                    key="p4_pick")
specs = []
for name in [pick, main_name, "S7 绿氢 WTW + 电网清洁化"]:
    row = _curve_row(at_lam, name)
    if row is not None and all(s["name"] != name for s in specs):
        specs.append({
            "name": name,
            "capex_wan": float(row.get("capex_wan") or 0.0),
            "annual_save_wan": float(row.get("annual_save_wan") or 0.0),
            "is_main": bool(row.get("is_main")),
        })
s7 = _curve_row(at_lam, "S7 绿氢 WTW + 电网清洁化")
charts.payback_curve(specs, years=15)
if s7 is not None and main_lam is not None:
    cards.note("菱形标记 = 回本点。主推与 S7 同 capex（%s 万）：λ=0 时两条曲线几乎重合"
               "（年节省 %s vs %s 万）；当前 λ > 0，主推因减排量更大，累计节省曲线在上方、"
               "回本更早（%s vs %s 年）。差别在减排计数口径（%s%% vs %s%%），"
               "这正是“保守边界”行的意义。" % (
                   services.fmt(main_lam.get("capex_wan"), 0),
                   services.fmt(main_zero.get("annual_save_wan") if main_zero is not None else None, 1),
                   services.fmt(_curve_row(at_zero, "S7 绿氢 WTW + 电网清洁化")["annual_save_wan"]
                                if _curve_row(at_zero, "S7 绿氢 WTW + 电网清洁化") is not None else None, 1),
                   services.fmt(main_lam.get("payback_years"), 2),
                   services.fmt(s7.get("payback_years"), 2),
                   services.fmt(main_row.get("reduction_pct"), 1),
                   services.fmt(_curve_row(sc, "S7 绿氢 WTW + 电网清洁化").get("reduction_pct")
                                if _curve_row(sc, "S7 绿氢 WTW + 电网清洁化") is not None else None, 1)))
else:
    cards.note("菱形标记 = 回本点；曲线与投资水平线的交叉年份即静态回本点。")

# ================= C · 成本构成与口径 =================
st.markdown("")
layout.section("成本构成与口径",
               "购置净 + 能耗 + 维护 − 补贴：E4 已逐车组参数化" if parameterized
               else "分解表待真车队参数；接表前，标定依据逐条列示")

cards.chips(["成本口径：" + basis_mode,
             "赛题口径 = 购置 + 能耗 + 维护 − 补贴",
             "延展口径 = 排放量 × λ"], tones=["info", "skip", "skip"])

if detail:
    cards.panel_head("成本构成（%s · E4 逐车组）" % main_name,
                     "全部数字出自 evaluator 的 cost_components_json，页面只读",
                     "parameterized")
    wan = 10000.0
    cards.htable(["构成项", "基线（柴油）", "目标（新方案）", "口径"], [
        ["一次性投入 · 购置毛额（万元）", "—",
         services.fmt(detail.get("purchase_yuan", 0) / wan, 0), "底稿表 19 分车型单价"],
        ["一次性投入 · 补贴（万元）", "—",
         "−" + services.fmt(detail.get("subsidy_yuan", 0) / wan, 0), "购置补贴"],
        ["一次性投入 · 基建（万元）", "—",
         services.fmt(detail.get("infrastructure_yuan", 0) / wan, 0), "场站建设"],
        ["一次性投入 · 其他资本支出（万元）", "—",
         services.fmt(detail.get("other_capex_yuan", 0) / wan, 0), "其他一次性投入"],
        ["净投入 capex（万元）", "—", services.fmt(detail.get("net_capex_yuan", 0) / wan, 0),
         "购置 − 补贴 + 基建 + 其他"],
        ["年能耗成本（万元/年）", services.fmt(detail.get("baseline_energy_yuan_year", 0) / wan, 1),
         services.fmt(detail.get("target_energy_yuan_year", 0) / wan, 1), "元/km × 年里程"],
        ["年维护成本（万元/年）", services.fmt(detail.get("baseline_maintenance_yuan_year", 0) / wan, 1),
         services.fmt(detail.get("target_maintenance_yuan_year", 0) / wan, 1), "万元/辆/年 × 车辆数"],
        ["年现金流节省（万元/年）", "—", services.fmt(detail.get("annual_cash_saving_yuan", 0) / wan, 1),
         "能耗 + 维护 + 其他"],
    ], num_cols=[1, 2])
    groups = detail.get("groups") or []
    if groups:
        cards.htable(["车组", "车辆数", "购置(万)", "补贴(万)",
                      "能耗·柴(万/年)", "能耗·新(万/年)", "维护·柴(万/年)", "维护·新(万/年)",
                      "排放·柴(t)", "排放·新(t)"],
                     [[str(g.get("group_id")),
                       services.fmt(g.get("vehicle_count"), 0),
                       services.fmt(g.get("purchase_yuan", 0) / wan, 0),
                       services.fmt(g.get("subsidy_yuan", 0) / wan, 0),
                       services.fmt(g.get("baseline_energy_yuan_year", 0) / wan, 1),
                       services.fmt(g.get("target_energy_yuan_year", 0) / wan, 1),
                       services.fmt(g.get("baseline_maintenance_yuan_year", 0) / wan, 1),
                       services.fmt(g.get("target_maintenance_yuan_year", 0) / wan, 1),
                       services.fmt(g.get("baseline_period_emission_t"), 2),
                       services.fmt(g.get("target_period_emission_t"), 2)]
                      for g in groups],
                     num_cols=list(range(1, 10)))
        cards.note("车辆数 = 数据期运力 ÷ 每车年运力（速度中位数 × 8 h × 300 天）向上取整；"
                   "购置/能耗/维护单价与减排率出处见各车组 source 字段（底稿表 14/表 19 + "
                   "新能源碳排放对比计算结果.xlsx）。重型按使用端零排放口径，与 WTW 口径分列披露。")

rows = [[str(r["scenario"]), services.fmt(r.get("capex_wan"), 1),
         services.fmt(r.get("opex_save_wan"), 1), str(r.get("basis", ""))]
        for _, r in sc.sort_values("order").iterrows()]
cards.htable(["方案", "投资(万元)", "年节省(万元)", "标定依据（verbatim）"], rows,
             num_cols=[1, 2])
cards.note("parameterized 行的成本构成已逐车组参数化（见上表）；legacy_calibration 行仍是"
           "答辩稿表 19–21 静态标定，约束列“未评估”，只作对标不进推荐集。"
           "敏感性归因（电价/氢价/补贴 ±）在 src/evaluator.build_sensitivity，闸门同源自检。")

layout.conclusion(
    "钱的决定因素是两个数：一次性投资与年度节省。λ=0 时主推回本 %s 年，λ=%s 时 %s 年；"
    "达标集合内主推回本不慢于同 capex 的 S7（λ>0 时更快），减排计数也更高——"
    "同价选多减，主推是 %s。"
    % (services.fmt(main_zero["payback_years"] if main_zero is not None else None, 1),
       services.fmt(lam, 0),
       services.fmt(main_lam["payback_years"] if main_lam is not None else None, 1),
       main_name)
)
