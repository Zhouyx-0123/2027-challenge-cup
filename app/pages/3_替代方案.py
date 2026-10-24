"""第 3 页 · 替代方案：这条线该派什么车、运营跑不跑通。

数据契约（只读，不重算）：
- final_report.parquet：七方案减排/投资/节省/回本/单位成本（真实数据下主推 S5 为逐车组参数化，其余为历史标定）
- scenario_route_constraint.parquet：方案 × 哪条线的逐线路 C1–C5 结论（O6；仅参数化方案产出）
- od_band.parquet：网点级可比较 OD 的同线碳强度带宽（当前 310 条，结构性空间）
- baseline_carbon.parquet：33,214 条运单关联（分摊/下钻），归到 12,167 趟物理行程
- 运营约束 C1–C5：docs/20 §7 规则文本；O6 结论列接表后本页自动渲染（services.constraint_contract）

页面口径：所有换算都在页面上标注“展示口径”；业务口径只在 src/ 四文件里。
"""

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import pandas as pd
import streamlit as st

import services
from components import cards, charts, flags, layout, theme

layout.setup("3 替代方案")
src = services.source()
gate = services.gate()
layout.sidebar_params(services.source_name(), gate, current="3")

mock = services.is_mock()
k = services.kpis()
lam = float(st.session_state.get("lam", 100.0))

sc = services.scenarios()
main_row = (sc[sc["is_main"] == True].iloc[0]
            if (not sc.empty and (sc["is_main"] == True).any())
            else (sc.iloc[-1] if not sc.empty else None))
band_all = services.od_band()

theme.hero(
    "替代方案：这条线该派什么车",
    ("%d 个方案共用同一本碳账；换车空间来自 %d 条可比较线路的实算——"
     "能不能换，还要过五道运营约束。" % (len(sc), len(band_all)))
    if not sc.empty else "方案表读取中……",
    kicker="03 / ALTERNATIVES",
    badges=[layout.source_badge(services.source_name()), layout.gate_badge(gate),
            "%d 方案 · 同一本账" % len(sc),
            ("主推 " + cards.esc(str(main_row["scenario"]).split(" ")[0]) + " · 减排 "
             + "%.1f%%" % float(main_row["reduction_pct"])) if main_row is not None else "方案表未生成"],
)
if mock:
    layout.demo_note(str(src.get("reason", "")))

cards.steps([
    {"n": "00", "t": "首页"}, {"n": "01", "t": "基准核算"}, {"n": "02", "t": "高排放诊断"},
    {"n": "03", "t": "替代方案"}, {"n": "04", "t": "成本效益"}, {"n": "05", "t": "碳会计台账"},
], active=3)
cards.note("决策闭环：计量 → 诊断 → 配车 → 算账 → 入账 → 校准回流。本页是第 ③ 步。")

if sc.empty or "scenario" not in sc.columns:
    theme.section("方案总览", "")
    cards.empty_state(
        "方案表还没生成",
        "先运行  python -m src.pipeline  生成 final_report.parquet（七方案对比）。")
    layout.conclusion("没有方案表就不编方案——这是前端只读的硬规矩。")
    st.stop()

e0_t = float(k.get("e0_t") or 0.0)
if not e0_t and "e0_t" in sc.columns:
    e0_t = float(sc["e0_t"].iloc[0])

# ================= 0 · 评估器口径（曾广增量） =================
st.markdown("")
layout.section("评估器口径", "曾广增量：先看这批方案能不能作为决策依据，再看减排与投资")

def _eval_field(row, key: str, default: str = "—") -> str:
    value = row.get(key, default) if hasattr(row, "get") else default
    return "—" if value is None else str(value)

mode_raw = _eval_field(main_row, "calculation_mode", "legacy_calibration")
param_raw = _eval_field(main_row, "parameter_status", "unverified_legacy_totals")
oper_raw = _eval_field(main_row, "operational_status", "pending")
eligible = bool(main_row.get("decision_eligible", False)) if hasattr(main_row, "get") else False
boundary = _eval_field(main_row, "boundary", "未逐方案核实")

mode_label = {"legacy_calibration": "历史标定（legacy）",
              "parameterized": "逐车组参数化"}.get(mode_raw, mode_raw)
param_label = {"unverified_legacy_totals": "未核验历史总量",
               "caller_supplied_unreviewed": "调用方参数（未复核）",
               "draft_sourced_pending_review": "底稿参数 · 待团队复核"}.get(param_raw, param_raw)
oper_label = {"pending": "待 O6 校验", "pass": "已通过", "fail": "未通过",
              "baseline": "基准行（不适用）"}.get(oper_raw, oper_raw)

cards.stats([
    {"label": "计算模式", "value": mode_label, "tone": "amber", "sub": "calculation_mode"},
    {"label": "参数状态", "value": param_label, "tone": "amber", "sub": "parameter_status"},
    {"label": "决策资格", "value": ("可用" if eligible else "不可用"), "tone": ("mint" if eligible else "bad"),
     "sub": "decision_eligible = " + str(eligible)},
    {"label": "运营约束", "value": oper_label, "tone": "skip", "sub": "operational_status"},
], cols=4)
if mode_raw == "parameterized":
    cards.note("边界：" + boundary + "。主推「%s」为逐车组参数化复算：车组由段表实时派生"
               "（年里程 × 年化系数、每车运力 = 类别速度中位数 × 8 h × 300 天，"
               "购置/补贴/能耗/维护按底稿表 19），减排率按表 14 替换矩阵；"
               "83.25%% / 378.34 t / 4,809 万 / 9.6 年由参数与物理段表算出，与闸门一致。"
               "其余方案仍是历史标定（decision_eligible=False），不可对外称已核验。"
               % str(main_row["scenario"]))
else:
    cards.note("边界：" + boundary + "。4,809 万 / 83.25% / 9.6 年是历史标定，"
               "未经底稿复核或 O6 约束校验；本页只做静态对标，不把它当最终决策。")

# ================= A · 方案总览 =================
st.markdown("")
layout.section("方案总览", "同一份数据、同一本账：减排率按 E₀ 等比缩放，投资为静态标定")

n_meet = int((sc["meets_target"] == True).sum())
meet_df = sc[sc["meets_target"] == True].sort_values("reduction_pct", ascending=False)
meet_desc = "｜".join("%s %.1f%%" % (str(r["scenario"]).split(" ")[0],
                                    float(r["reduction_pct"]))
                      for _, r in meet_df.iterrows()) or "—"
cards.stats([
    {"label": "方案数", "value": services.fmt(len(sc)), "sub": "S0 基准 + 六个替代路径"},
    {"label": "达标方案（≥70%）", "value": services.fmt(n_meet), "tone": "mint",
     "sub": meet_desc},
    {"label": "主推减排率", "value": "%.1f%%" % float(main_row["reduction_pct"]), "tone": "amber",
     "sub": str(main_row["scenario"])},
    {"label": "基准 E₀（tCO₂e）", "value": services.fmt(e0_t, 1),
     "sub": "第 1 页同源，方案排放按它缩放"},
], cols=4)

chart_df = sc.copy()
chart_df["达标"] = chart_df["meets_target"].map({True: "达标", False: "未达标"}).fillna("未达标")

left, right = st.columns([0.44, 0.56])
with left:
    charts.vbar_target(
        chart_df.sort_values("order"), "scenario", "reduction_pct",
        target=70.0, target_label="达标线 70%",
        title="各方案减排率（%，相对基准 E₀）", height=380, color="达标")
with right:
    charts.bubble(
        chart_df, "capex_wan", "reduction_pct", "opex_save_wan", color="达标",
        hover=["scenario", "payback_years", "unit_cost_yuan_t"],
        title="减排率 × 投资（气泡 = 年节省，万元/年）", height=380, ymax=100)

rows = []
row_cls = []
for _, r in chart_df.iterrows():
    rows.append([
        str(r["scenario"]),
        "%.2f" % float(r["reduction_pct"]),
        services.fmt(r.get("emission_t"), 1),
        services.fmt(r.get("carbon_saving_t"), 1),
        services.fmt(r.get("capex_wan"), 1),
        services.fmt(r.get("opex_save_wan"), 1),
        services.fmt(r.get("payback_years"), 2),
        services.fmt(r.get("unit_cost_yuan_t"), 0),
        "✓ 达标" if bool(r.get("meets_target")) else "—",
    ])
    row_cls.append("main" if bool(r.get("is_main")) else ("base" if float(r["reduction_pct"]) <= 0 else ""))
cards.htable(
    ["方案", "减排率(%)", "剩余排放(t)", "减排量(t)", "投资(万元)", "年节省(万元)",
     "回本(年)", "单位减排成本(元/t)", "达标"],
    rows, row_cls=row_cls, num_cols=[1, 2, 3, 4, 5, 6, 7])
if mode_raw == "parameterized":
    cards.note("参数化行（主推 S5）由段表实时车组复算，与底稿表 14/18/19 标称值一致"
               "（闸门用两套独立来源互证，改了任一边都会红）；"
               "legacy 行是未取得车组底稿的历史标定，约束列显式“未评估”。"
               "替换真车队参数后重跑 pipeline 即更新，本页不用动。")
else:
    cards.note("方案减排率/投资/年节省为答辩稿表 19–21 静态标定（曾广口径），排放量按本次 E₀ 等比缩放；"
               "闸门只校验主方案减排率 83.25% 未被改动。替换真车队参数后重跑 pipeline 即更新，本页不用动。")

# ================= B · 主推方案 + λ 视角 =================
st.markdown("")
layout.section("主推方案 · " + str(main_row["scenario"]),
               "轻中卡电动 + 重卡氢能：达标方案里单位减排成本最低")

b1, b2 = st.columns([0.52, 0.48])
with b1:
    cards.panel_head(str(main_row["scenario"]),
                     ("逐车组参数化 · 底稿表 14/18/19 + 段表实时车组"
                      if mode_raw == "parameterized" else "静态标定 · 详见下方标定依据"),
                     "final_report.parquet")
    cards.kv_table([
        {"k": "减排率", "v": "%.2f%%" % float(main_row["reduction_pct"]), "big": True},
        {"k": "剩余排放", "v": services.fmt(main_row.get("emission_t"), 2) + " tCO₂e"},
        {"k": "减排量", "v": services.fmt(main_row.get("carbon_saving_t"), 2) + " tCO₂e"},
        {"k": "一次性投资", "v": services.fmt(main_row.get("capex_wan"), 0) + " 万元"},
        {"k": "年运营节省", "v": services.fmt(main_row.get("opex_save_wan"), 1) + " 万元/年"},
        {"k": "回本年限", "v": services.fmt(main_row.get("payback_years"), 2) + " 年（λ=0 口径）"},
        {"k": "单位减排成本", "v": services.fmt(main_row.get("unit_cost_yuan_t"), 0) + " 元/tCO₂e"},
        {"k": "标定依据", "v": str(main_row.get("basis", ""))},
    ])
with b2:
    cards.panel_head("λ 视角", "内部碳价把减排变成钱", "预计算口径 · 数据全部来自结果表")
    # 年度减排量、含 λ 的年节省与回本全部读 lambda_curve.parquet（D5 唯一口径，
    # 与 evaluator 的 internal_value / break_even_lambda 同源）——页面不再自算。
    saving_t = float(main_row.get("carbon_saving_t") or 0.0)
    annual_saving_t = float(main_row.get("annual_carbon_saving_t") or 0.0)
    annual_factor = float(main_row.get("annualization_factor") or 0.0)
    capex = float(main_row.get("capex_wan") or 0.0)
    opex = float(main_row.get("opex_save_wan") or 0.0)
    curve_main = services.lambda_curve_at(lam)
    curve_main = (curve_main[curve_main["scenario"].astype(str) == str(main_row["scenario"])].iloc[0]
                  if not curve_main.empty
                  and (curve_main["scenario"].astype(str) == str(main_row["scenario"])).any() else None)
    carbon_gain_wan = float(curve_main["annual_save_wan"]) - opex if curve_main is not None else None
    annual_with_lam = float(curve_main["annual_save_wan"]) if curve_main is not None else None
    payback_lam = float(curve_main["payback_years"]) if curve_main is not None else None
    if annual_saving_t <= 0 and curve_main is not None:
        # legacy 行没有年化减排列：展示 λ 曲线里的同一口径值（不自己乘年化系数）
        annual_saving_t = float(curve_main.get("annual_carbon_t") or 0.0)
    cards.formula(
        "年度减排量 = 数据期减排 × 年化系数 = <span class='hl'>"
        + services.fmt(saving_t, 1) + " × " + services.fmt(annual_factor, 4) + "</span>"
        " = <span class='hl'>" + services.fmt(annual_saving_t, 1) + " t/年</span><br>"
        "年度碳价收益 = 年度减排 × λ = <span class='hl'>"
        + services.fmt(annual_saving_t, 1) + " t × " + services.fmt(lam, 0) + " 元/t</span>"
        " = <span class='hl2'>" + services.fmt(carbon_gain_wan, 1) + " 万元/年</span><br>"
        "含碳收益年节省 = " + services.fmt(opex, 1) + " + " + services.fmt(carbon_gain_wan, 1)
        + " = <span class='hl'>" + services.fmt(annual_with_lam, 1) + " 万元/年</span>"
        " → 回本 <span class='hl2'>" + services.fmt(payback_lam, 2) + " 年</span>"
        "<br><span class='dim'>λ 在左侧全局参数里调；含 λ 的年节省与回本读 "
        "lambda_curve.parquet 预计算（网格步长 5 元/t），与第 4 页、evaluator NPV 同一份公式。</span>")
    cards.note("赛题口径只看运营节省（λ=0 → %s 年）；碳价延展口径把年度减排量按 λ 计价。"
               "两个口径都展示，推荐结论以达标线 + 单位减排成本为准。"
               % services.fmt(main_row.get("payback_years"), 2))

# ================= C · 路线实验室入口（不重复画地图） =================
st.markdown("")
layout.section("路线实验室（第 6 页）",
               "高德负责路，本页负责车；路径级实时路线统一放到独立实验室")

route_df = services.routes()
if route_df.empty:
    cards.chips(["路线表未生成", "第 3 页其余内容不受影响", "去第 6 页看空状态"],
                tones=["warn", "skip", "skip"])
else:
    n_online = int((route_df["source"] == "amap_rest").sum()) if "source" in route_df.columns else 0
    n_est = int((route_df.get("flag_route_est", pd.Series(dtype=int)) == 1).sum())
    cards.chips(
        ["路线表 %d 条" % len(route_df),
         "高德真路线 %d 条" % n_online,
         "离线兜底 %d 条" % n_est,
         "实时路线 · 可切换策略"],
        tones=["ok", "mint" if n_online else "warn", "skip", "skip"])
st.caption("侧边栏 → ⑥ 路线实验室；地图与路径级指标只在那一页画一次。")
cards.note("第 3 页只保留“派什么车 + 运营约束”；地图与路径级指标统一放第 6 页，"
           "避免同一张高德图在两条导航路径里重复出现。")

# ================= D · 换车候选线路 =================
st.markdown("")
ratio_max = (float(pd.to_numeric(band_all["ratio"], errors="coerce").max())
             if not band_all.empty and "ratio" in band_all.columns else 0.0)
layout.section("换车候选线路",
               ("同线带宽是结构性空间：同一条 OD，车型因子最多差 %.2f 倍" % ratio_max)
               if ratio_max else "同线带宽是结构性空间：同一条 OD，不同车型碳强度差异显著")

band = band_all
if band.empty or "od_pair" not in band.columns:
    cards.empty_state(
        "同线带宽表未生成",
        "先运行  python -m src.pipeline  生成 od_band.parquet（可比较 OD 清单）。")
else:
    seg = services.segments()
    c1, c2, c3 = st.columns([0.9, 0.9, 1.6])
    with c1:
        sort_by = st.selectbox("排序", ["按碳强度倍数", "按线路碳排量"], index=0,
                               key="p3_sort")
    with c2:
        top_n = st.selectbox("看前几条", [10, 20, 30], index=1, key="p3_topn")
    with c3:
        options = (band.sort_values("ratio", ascending=False) if sort_by == "按碳强度倍数"
                   else band.sort_values("kg_t", ascending=False))
        options = options.head(60)
        picked = st.selectbox("选一条线路下钻", options["od_pair"].astype(str).tolist(),
                              index=0, key="p3_od")

    show = options.head(top_n).copy()
    rows, row_cls = [], []
    for _, r in show.iterrows():
        rows.append([str(r["od_pair"]), services.fmt(r.get("n")), services.fmt(r.get("nt")),
                     services.fmt(r.get("fmin"), 2), services.fmt(r.get("fmax"), 2),
                     "%.2f×" % float(r.get("ratio") or 0.0),
                     services.fmt(r.get("kg_t"), 2), "%.2f%%" % float(r.get("coverage_pct") or 0.0)])
        row_cls.append("main" if str(r["od_pair"]) == str(picked) else "")
    cards.htable(["线路", "段数", "车型数", "最低因子(g/km)", "最高因子(g/km)", "倍数",
                  "碳排(t)", "占基准碳排(%)"], rows, row_cls=row_cls,
                 num_cols=[1, 2, 3, 4, 5, 6, 7])
    cards.note("可比较线路 = 该 OD 至少 3 段、至少 2 种车型；倍数 = 最高因子 ÷ 最低因子。"
               "共 %d 条，覆盖基准碳排 %.1f%%——不是边角样本。"
               % (len(band), float(k.get("od_coverage_pct") or 0.0)))

    # ---- 下钻：选中 OD 的车型构成（读 od_vehicle_mix 预计算表，页面不聚合） ----
    picked_row = options[options["od_pair"].astype(str) == str(picked)].iloc[0]
    od_site = str(picked_row.get("od_site") or "")
    mix = services.od_vehicle_mix(od_site)
    if mix.empty:
        cards.empty_state("这条线的车型构成还没预计算",
                          "重跑  python -m src.pipeline  生成 od_vehicle_mix.parquet"
                          "（可比较 OD 的逐车型构成，页面只查表）。")
    else:
        st.markdown("")
        layout.section("下钻 · " + str(picked), "这条线上实际跑了哪些车、各排多少碳")
        cards.stats([
            {"label": "路段数", "value": services.fmt(picked_row.get("n"))},
            {"label": "车型数", "value": services.fmt(picked_row.get("nt"))},
            {"label": "因子带宽", "value": "%.2f×" % float(picked_row.get("ratio") or 0.0),
             "tone": "coral",
             "sub": "%.2f → %.2f g/km" % (float(picked_row.get("fmin") or 0.0),
                                        float(picked_row.get("fmax") or 0.0))},
            {"label": "该线碳排", "value": services.fmt(picked_row.get("kg_t"), 2) + " t",
             "tone": "amber", "sub": "占基准碳排 %.2f%%" % float(picked_row.get("coverage_pct") or 0.0)},
        ], cols=4)

        rows = [[str(r["vehicle_type"]), services.fmt(r.get("段数")),
                 services.fmt(r.get("平均载重"), 1), services.fmt(r.get("因子"), 2),
                 services.fmt(r.get("碳排kg"), 1), "%.1f%%" % float(r.get("占比") or 0.0)]
                for _, r in mix.iterrows()]
        cards.htable(["车型", "段数", "平均载重(t)", "因子(g/km)", "碳排(kg)", "占比(%)"],
                     rows, num_cols=[1, 2, 3, 4, 5])
        charts.hbar(mix.rename(columns={"vehicle_type": "车型", "碳排kg": "碳排kg"}),
                    "车型", "碳排kg", title="该线路各车型碳排（kg）",
                    height=max(300, 46 * len(mix) + 90))
        cards.note("同一 OD、同一段里程：因子由载重档位决定——这就是“换车”而不是“选路”的空间。"
                   "最高因子车型承运的段，若能改由最低因子档位承运，结构性空间最大；"
                   "能不能换要看下面五道运营约束。构成表由 od_vehicle_mix.parquet 预计算。")

        seg = services.segments()
        if not seg.empty and "od_site" in seg.columns:
            sub = seg[seg["od_site"].astype(str) == od_site]
            cols = [c for c in ["waybill_no", "plate", "vehicle_type", "dist_km",
                                "factor_gkm", "kg_co2", "month"] if c in sub.columns]
            if not sub.empty and cols:
                flags.flagged_table(sub.sort_values("kg_co2", ascending=False),
                                    columns=cols, max_rows=30, show_legend=True)

# ================= E · 运营约束校验 C1–C5 =================
st.markdown("")
layout.section("运营约束校验", "带宽 → 可兑现减排，中间隔着五道约束；结论由引擎层 O6 产出")

contract = services.constraint_contract()
route_contract = services.route_constraint_contract()
if contract.get("available"):
    cards.chips(["O6 约束结论已接表", "前端只读，不重算"], tones=["ok", "skip"])
    rows = []
    for item in contract["rows"]:
        rows.append([str(item.get("scenario", ""))]
                    + [str(item.get(c, "—")) for c in contract["columns"]])
    cards.htable(["方案"] + [str(c) for c in contract["columns"]], rows)
    cards.note("场景级裁决口径（docs/08 §8）：operational_feasible = 单程层全部通过 C1–C4；"
               "班次化层按底稿 §6.5 干线班次化运营（条件可行，依赖沿线补能网络，未核实）；"
               "C5 冬季复检为信息项，不达标线路冬季转入班次化层。"
               "历史标定行的约束结论显式“未评估”，不作为决策依据。")

    # ---- 方案 × 哪条线：逐线路 C1–C5（route_constraints 契约） ----
    if route_contract.get("available"):
        st.markdown("")
        cards.panel_head("方案 × 哪条线 · 逐线路 C1–C5",
                         "一行一个（方案, 类别, 线路）；只有参数化方案有结论",
                         "scenario_route_constraint.parquet")
        summary = route_contract.get("summary") or {}
        chips = ["%d 行（方案 × 类别 × 线路）" % int(summary.get("rows") or 0)]
        by_tier = summary.get("by_tier") or {}
        chips += ["%s %d 条" % (str(k), int(v)) for k, v in by_tier.items()]
        cards.chips(chips, tones=["ok"] + ["skip"] * (len(chips) - 1))
        rrows = route_contract["rows"]
        rdf = pd.DataFrame(rrows)
        f1, f2, f3 = st.columns([0.8, 1.0, 1.4])
        with f1:
            tier_opts = ["全部"] + sorted(rdf["deploy_tier"].astype(str).unique().tolist()) \
                if "deploy_tier" in rdf.columns else ["全部"]
            tier_pick = st.selectbox("部署层", tier_opts, index=0, key="p3_tier")
        with f2:
            vc_opts = ["全部"] + sorted(rdf["vclass_label"].astype(str).unique().tolist()) \
                if "vclass_label" in rdf.columns else ["全部"]
            vc_pick = st.selectbox("车组", vc_opts, index=0, key="p3_vclass")
        with f3:
            status_opts = ["全部", "只看未通过"] + (
                sorted(rdf["constraint_status"].astype(str).unique().tolist())
                if "constraint_status" in rdf.columns else [])
            status_pick = st.selectbox("约束结论", status_opts, index=0, key="p3_cstatus")
        view = rdf
        if tier_pick != "全部" and "deploy_tier" in view.columns:
            view = view[view["deploy_tier"].astype(str) == tier_pick]
        if vc_pick != "全部" and "vclass_label" in view.columns:
            view = view[view["vclass_label"].astype(str) == vc_pick]
        if status_pick == "只看未通过" and "constraint_status" in view.columns:
            view = view[view["constraint_status"].astype(str) != "pass"]
        elif status_pick not in ("全部", "只看未通过") and "constraint_status" in view.columns:
            view = view[view["constraint_status"].astype(str) == status_pick]
        if view.empty:
            cards.empty_state("当前筛选没有线路", "换个部署层/车组/约束结论看看。")
        else:
            show_cols = [c for c in ("vclass_label", "vehicle", "od_label", "route_km",
                                     "usable_range_km", "avg_speed_kmh", "max_cargo_t",
                                     "deploy_tier", "constraint_status", "km_share_pct")
                         if c in view.columns]
            header_map = {"vclass_label": "车组", "vehicle": "目标车型", "od_label": "线路",
                          "route_km": "单程里程(km)", "usable_range_km": "可用续航(km)",
                          "avg_speed_kmh": "均速(km/h)", "max_cargo_t": "最大单票货重(t)",
                          "deploy_tier": "部署层", "constraint_status": "结论",
                          "km_share_pct": "里程占比(%)"}
            top_n = st.selectbox("展示条数（按里程占比降序）", [20, 50, 100],
                                 index=1, key="p3_route_topn")
            view = view.sort_values("km_share_pct", ascending=False).head(top_n) \
                if "km_share_pct" in view.columns else view.head(top_n)
            table_rows = []
            row_cls = []
            for _, r in view.iterrows():
                table_rows.append([services.fmt(r.get(c), 2) if isinstance(r.get(c), float)
                                   else str(r.get(c, "")) for c in show_cols])
                row_cls.append("bad" if str(r.get("constraint_status")) == "fail" else "")
            cards.htable([header_map.get(c, c) for c in show_cols], table_rows, row_cls=row_cls)
            cards.note("判定口径：行驶速度取**类别**速度中位数（door-to-door 段均速含装卸等待，"
                       "不能当行驶速度）；货重取该线实际单票货重（底稿 §6.4 轻抛小件论证），"
                       "绝不用旧车额定载重比新车额定；C4 按“无站”计（沿线补能网络未核实）。"
                       "每条线路的完整五条结论在表内 constraint_status / c1–c5 列。")
else:
    cards.chips(["O6 约束求解未接表", "规则文本已定稿 v0.1（docs/20 §7）",
                 "O6 待接表（杨）"], tones=["warn", "skip", "skip"])
    rules = [
        ("C1", "续航衰减", "可用续航 = 标称 × (1 − 2%/年 × 车龄) × 冬季 0.7；线路里程 ≤ 可用续航 × 0.8",
         "标“续航不达标”，给出缺口 km"),
        ("C2", "14 h 驾驶窗口", "行驶时长 + 补能时长 ≤ 14 h；每 4 h 强制休息 20 min",
         "标“窗口不达标”，给出超时 min"),
        ("C3", "运力匹配", "车辆额定载重 ≥ 该线历史最大单票载重 × 1.05",
         "标“运力不匹配”，给出差 t"),
        ("C4", "补能可达", "线路 30 km 缓冲区内有充电/加氢站；无站则判不可行",
         "标“补能不可达”"),
        ("C5", "冬季/山区修正", "气温 < −10℃ 续航 ×0.7；平均坡度 > 4% 能耗 + 10%",
         "标“工况修正后不达标”"),
    ]
    cols = st.columns(2)
    for i, (code, name, rule, fail) in enumerate(rules):
        with cols[i % 2]:
            cards.panel_head(code + " · " + name, "", "")
            cards.note("规则：" + rule)
            cards.note("不通过时：" + fail)
    cards.note("O6 把逐条约束结论写进结果表（constraint_status / c1–c5 列）后，"
               "本页自动按方案渲染，前端不做任何约束求解——这是 docs/20 §7 的约定。")

cheapest = (meet_df.sort_values("unit_cost_yuan_t").iloc[0]
            if not meet_df.empty and "unit_cost_yuan_t" in meet_df.columns else None)
if cheapest is not None and str(cheapest["scenario"]) == str(main_row["scenario"]):
    cost_note = ("主推 %s 在达标方案里单位减排成本最低（%s 元/tCO₂e）"
                 % (str(main_row["scenario"]).split(" ")[0],
                    services.fmt(main_row.get("unit_cost_yuan_t"), 0)))
elif cheapest is not None:
    cost_note = ("达标方案集合内 %s 单位减排成本最低"
                 % str(cheapest["scenario"]).split(" ")[0])
else:
    cost_note = "达标方案见上表"

layout.conclusion(
    "方案不是拍出来的：带宽来自 %d 条可比较 OD 的实算（覆盖 %.1f%% 碳排），"
    "%s；结构性带宽要过 C1–C5 才能变成可兑现减排。"
    % (int(k.get("od_pairs") or 0), float(k.get("od_coverage_pct") or 0.0), cost_note)
)
