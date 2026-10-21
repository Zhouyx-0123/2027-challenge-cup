"""第 5 页 · 碳会计台账：怎么记、怎么披露、任一数字怎么追到段。

数据契约（只读，不重算）：
- ledger.parquet：三行结论账 × 六字段（pipeline G3）
- disclosure.json：披露六要素（pipeline G4）
- gate_report.json：一致性闸门（ledger_total / kpi_total / disclosure_six）
- baseline_carbon.parquet：33,214 段级明细（追溯终点）

页面口径：年度化换算（E₀ × 3.0417）为展示口径，第 1 页 E₀ 是数据期口径、不乘。
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

layout.setup("5 碳会计台账")
src = services.source()
gate = services.gate()
layout.sidebar_params(services.source_name(), gate, current="5")

mock = services.is_mock()
k = services.kpis()

theme.hero(
    "碳会计台账：怎么记、怎么披露",
    "台账是结论的账，不是流水的账；披露是边界的声明，不是数字的堆砌。"
    "从任何一个披露数字，都能一路查到具体哪一段运单。",
    kicker="05 / CARBON LEDGER",
    badges=[layout.source_badge(services.source_name()), layout.gate_badge(gate),
            "三行账 × 六字段", "披露六要素"],
)
if mock:
    layout.demo_note(str(src.get("reason", "")))

cards.steps([
    {"n": "00", "t": "首页"}, {"n": "01", "t": "基准核算"}, {"n": "02", "t": "高排放诊断"},
    {"n": "03", "t": "替代方案"}, {"n": "04", "t": "成本效益"}, {"n": "05", "t": "碳会计台账"},
], active=5)

led = services.ledger()
disc = services.disclosure()
if led.empty or "scheme" not in led.columns:
    theme.section("台账六字段", "")
    cards.empty_state(
        "台账还没生成",
        "先运行  python -m src.pipeline  生成 ledger.parquet（三行结论账）与 disclosure.json。")
    layout.conclusion("没有台账就不展示——前端只读结果表，不补账。")
    st.stop()

e0_t = float(k.get("e0_t") or 0.0)
annual_factor = float(disc.get("annual_factor") or 0.0)

# ---- 数据可追溯（DA 增量） ----
seg = services.segments()
q = services.quality() or {}
trace_rate = None
if "source_waybill_row" in seg.columns:
    trace = pd.to_numeric(seg["source_waybill_row"], errors="coerce")
    trace_rate = float((trace > 0).mean() * 100.0)
order_rate = 100.0 * float(q.get("order_match_rate", q.get("match_rate", 1.0)) or 0.0)
vehicle_rate = 100.0 * float(q.get("vehicle_match_rate", 1.0) or 0.0)

cards.panel_head("数据可追溯", "台账每个数都能回到 33,214 段中的具体一行", "DA · docs/07")
cards.stats([
    {"label": "台账行", "value": services.fmt(len(led)), "tone": "mint", "sub": "三行结论账"},
    {"label": "源行号可回查", "value": (("%.1f%%" % trace_rate) if trace_rate is not None else "—"),
     "tone": "mint", "sub": "source_waybill_row"},
    {"label": "估算 / 代理", "value": "%s 段" % services.fmt(q.get("estimated_rows")),
     "tone": "coral", "sub": "全表 %s 行（含重复）" % services.fmt(q.get("estimated_rows_wide"))},
    {"label": "订单 / 车型匹配", "value": "%.0f%% / %.0f%%" % (order_rate, vehicle_rate),
     "tone": "mint", "sub": "order_match_rate / vehicle_match_rate"},
], cols=4)
cards.note("DA 增量：61 字段数据字典、source_waybill_row、%s 段（全表 %s 行）估算/代理标记。"
           "台账与披露只读结果表，不在前端补账。"
           % (services.fmt(q.get("estimated_rows")), services.fmt(q.get("estimated_rows_wide"))))

# ================= A · 台账六字段 =================
st.markdown("")
layout.section("台账六字段", "只挂结论：基准一行、主推一行、保守边界一行")

rows, row_cls = [], []
for _, r in led.iterrows():
    rows.append([
        str(r.get("scheme", "")),
        str(r.get("accounting_date", "")),
        str(r.get("boundary", "")),
        services.fmt(r.get("emission_t"), 2),
        str(r.get("quota_gap", "")),
        services.fmt(r.get("book_cost"), 2),
        str(r.get("note", "")),
    ])
    row_cls.append("base" if bool(r.get("is_baseline")) else
                   ("main" if "S5" in str(r.get("scheme", "")) else ""))
cards.htable(["方案", "核算日期", "核算边界", "排放量(tCO₂e)", "配额缺口",
              "履约/投资成本(万元)", "附注"], rows, row_cls=row_cls, num_cols=[3, 5])
cards.note("六字段逐条定义见 docs/15 §2：基准行不是 0（是现状 E₀）；配额缺口没有基准就不编数；"
           "履约成本只放该方案投资额，运营节省在第 4 页分解，避免同一笔钱记两次。")

c1, c2 = st.columns([0.5, 0.5])
with c1:
    st.download_button(
        "下载台账 CSV",
        data=led.to_csv(index=False).encode("utf-8-sig"),
        file_name="ledger.csv", mime="text/csv", width="stretch")
with c2:
    st.download_button(
        "下载披露 JSON",
        data=pd.Series(disc).to_json(force_ascii=False, indent=2).encode("utf-8"),
        file_name="disclosure.json", mime="application/json", width="stretch")

# ================= B · 披露六要素 =================
st.markdown("")
layout.section("披露六要素", "先说边界，再说数字；六要素缺一不可披露")

elements = [
    ("① 核算边界", "boundary", "🗺"),
    ("② 因子来源", "factor_source", "📐"),
    ("③ 关键假设", "assumptions", "🧩"),
    ("④ 数据期与年化", "period", "📅"),
    ("⑤ 年化系数", "annual_factor", "✖️"),
    ("⑥ 局限与未含项", "limitations", "⚠️"),
]
for i in range(0, len(elements), 3):
    cols = st.columns(3)
    for j, (title, key, icon) in enumerate(elements[i:i + 3]):
        with cols[j]:
            value = disc.get(key, "—")
            if key == "annual_factor":
                cards.stats([{"label": "年化系数", "value": "%.4f" % float(value),
                              "tone": "amber", "sub": "= 365 ÷ 120（数据期 120 天）"}], cols=1)
            else:
                cards.panel_head(icon + " " + title, "", "")
                cards.note(str(value))

# ================= C · 年度化与一致性 =================
st.markdown("")
layout.section("年度化与一致性", "两个口径不能混：第 1 页 E₀ 是数据期口径，年度化只在披露时乘")

annual_t = e0_t * annual_factor if annual_factor else None
cards.formula(
    "年度化排放 = E₀ × 年化系数 = <span class='hl'>" + services.fmt(e0_t, 2)
    + " t × " + ("%.4f" % annual_factor if annual_factor else "—") + "</span>"
    " = <span class='hl2'>" + (services.fmt(annual_t, 1) if annual_t else "—")
    + " tCO₂e/年</span>（展示口径）<br>"
    "<span class='dim'>台账与第 1 页用数据期口径；乘年化只在“年度化披露”时发生，"
    "两个口径不能混——docs/15 §3.1。</span>")

gate_items = []
for t in (gate.get("targets") or []):
    if t.get("id") in ("e0_kg", "main_reduction_pct"):
        gate_items.append((str(t.get("label")), t.get("status"), str(t.get("note") or "")))
for s in (gate.get("structural") or []):
    if s.get("id") in ("ledger_total", "kpi_total", "disclosure_six", "ledger_fields"):
        gate_items.append((str(s.get("label")), s.get("status"), str(s.get("detail") or "")))
if gate_items:
    rows = [[name, {"PASS": "✓ 通过", "FAIL": "✗ 未通过", "SKIP": "⏭ 跳过"}.get(st_, str(st_)), note]
            for name, st_, note in gate_items]
    cls = []
    for _, st_, _ in gate_items:
        cls.append("base" if st_ == "PASS" else ("fail" if st_ == "FAIL" else "skip"))
    cards.htable(["一致性闸门项", "判定", "说明"], rows, row_cls=cls)
    cards.note("闸门在 pipeline 出口自动执行；本页只读 gate_report.json。"
               "台账基准行 ×1000 与第 1 页 E₀ 差 ≤ 1 kg（ledger_total），"
               "这就是“台账总碳 = 首页总碳”的机器证明。")
else:
    cards.note("还没有 gate_report.json——运行 python -m src.pipeline 生成。")

# ================= D · 数字追溯 =================
st.markdown("")
layout.section("数字追溯", "从任何一个披露数字，一路查到具体哪一段运单")

scheme_names = led["scheme"].astype(str).tolist()
picked_scheme = st.selectbox("选一个台账行往下追", scheme_names, index=0, key="p5_scheme")
led_row = led[led["scheme"].astype(str) == picked_scheme].iloc[0]
emission = float(led_row.get("emission_t") or 0.0)
is_base = bool(led_row.get("is_baseline"))
sc_row = None
if "scenario" in services.scenarios().columns:
    sc_all = services.scenarios()
    match = sc_all[sc_all["scenario"].astype(str) == picked_scheme]
    sc_row = match.iloc[0] if not match.empty else None

cards.panel_head("第 1 跳 · 台账行", picked_scheme,
                 "基准行" if is_base else "方案行")
if is_base:
    cards.formula(
        "排放量 <span class='hl'>" + services.fmt(emission, 2) + " t</span>"
        " = " + services.fmt(k.get("segments"), 0) + " 段逐段累加（kg_co2 求和 ÷ 1000）<br>"
        "<span class='dim'>口径：法定轨 = 里程 × 因子；因子表 WRZ2024-T8-v1。</span>")
elif sc_row is not None:
    cards.formula(
        "排放量 <span class='hl'>" + services.fmt(emission, 2) + " t</span>"
        " = E₀ " + services.fmt(e0_t, 2) + " t × (1 − 减排率 "
        + "%.2f%%)" % float(sc_row.get("reduction_pct") or 0.0) + "<br>"
        "<span class='dim'>方案行的排放由基准行按静态标定减排率折算；追溯终点与基准行相同。</span>")
else:
    cards.note("该行在方案表中没有对应记录。")

cards.trace_chain([
    {"t": "披露数字", "d": "披露六要素中的 E₀ = " + services.fmt(e0_t, 3) + " t（数据期口径）"},
    {"t": "台账行", "d": picked_scheme + " · " + services.fmt(emission, 2) + " tCO₂e"},
    {"t": "基准 E₀", "d": "33,214 段逐段累加｜总里程 " + services.fmt(k.get("total_km"), 0) + " km",
     "tone": "amber"},
    {"t": "段级明细", "d": "任取一行都能追到原始运单号 / 车牌 / 封解车时间"},
])

seg = services.segments()
if seg.empty or "kg_co2" not in seg.columns:
    cards.empty_state("段级明细表未生成", "追溯终点需要 baseline_carbon.parquet。")
else:
    st.markdown("")
    cards.panel_head("第 2 跳 · E₀ 构成", "基准碳排由哪些车、哪几个月堆出来的",
                     "展示口径 · 段表分组汇总")
    seg = seg.copy()
    seg["_kg"] = pd.to_numeric(seg["kg_co2"], errors="coerce").fillna(0.0)

    f1, f2 = st.columns([0.9, 0.9])
    with f1:
        months = sorted([int(m) for m in seg["month"].dropna().unique()])
        picked_months = st.multiselect("月份", months, default=months, key="p5_months",
                                       placeholder="全月")
    with f2:
        cls_opts = ["轻型", "中型", "重型"]
        picked_cls = st.multiselect("车型大类", cls_opts, placeholder="全部", key="p5_cls")
    view = seg.copy()
    if picked_months and "month" in view.columns:
        view = view[view["month"].isin(picked_months)]
    if picked_cls and "vclass" in view.columns:
        name2cls = {"轻型": 1, "中型": 2, "重型": 3}
        view = view[view["vclass"].isin([name2cls[c] for c in picked_cls])]
    if view.empty:
        st.info("当前筛选下没有路段。")
    else:
        total_kg = float(view["_kg"].sum()) or 1.0
        cards.stats([
            {"label": "筛选后路段", "value": services.fmt(len(view))},
            {"label": "筛选后碳排", "value": services.fmt(total_kg / 1000.0, 2) + " t", "tone": "mint"},
            {"label": "占基准 E₀", "value": "%.2f%%" % (100.0 * total_kg / (e0_t * 1000.0 or 1.0)),
             "tone": "amber"},
            {"label": "估算值行", "value": services.fmt(int(pd.to_numeric(
                view.get("flag_estimated"), errors="coerce").fillna(0).sum())) if "flag_estimated" in view.columns else "—"},
        ], cols=4)

        top_types = (view.groupby("vehicle_type", as_index=False)["_kg"].sum()
                         .sort_values("_kg", ascending=False).head(10))
        top_types["占比"] = top_types["_kg"] / total_kg * 100.0
        rows = [[str(r["vehicle_type"]), services.fmt(r["_kg"], 1),
                 "%.2f%%" % float(r["占比"])] for _, r in top_types.iterrows()]
        cards.htable(["TOP 车型", "碳排(kg)", "占比(%)"], rows, num_cols=[1, 2])
        charts.hbar(top_types.rename(columns={"vehicle_type": "车型", "_kg": "碳排kg"}),
                    "车型", "碳排kg", title="TOP 10 车型碳排（kg）", height=360)

        by_month = (view.groupby("month", as_index=False)["_kg"].sum().sort_values("month"))
        charts.vbar_target(by_month, "month", "_kg", title="月度碳排（kg）", height=300)
        n_mar = int((seg["month"] == 3).sum()) if "month" in seg.columns else 0
        if not by_month.empty:
            peak_m = int(by_month.loc[by_month["_kg"].idxmax(), "month"])
            cards.note("%d 月碳排最高（春节前运力高峰）；数据期以 1/2/11/12 月为主，"
                       "另有 %d 条 3 月记录保留计碳（披露附注已注明）。" % (peak_m, n_mar))

        st.markdown("")
        cards.panel_head("第 3 跳 · 段级明细", "追到具体运单：黄框行 = 估算值，灰底 = 重复",
                         "按碳排降序，前 50 行")
        cols = [c for c in ["waybill_no", "plate", "vehicle_type", "od_pair", "dist_km",
                            "factor_gkm", "kg_co2", "delta_pct", "month"] if c in view.columns]
        flags.flagged_table(view.sort_values("_kg", ascending=False),
                            columns=cols, max_rows=50, show_legend=True)

# ================= E · 制度对标 =================
st.markdown("")
layout.section("制度对标", "口径映射说明——帮助按制度框架审阅，不代表已通过任何认证")

mapping = [
    ["① 核算边界", "自营公路运输 · 范围一（移动源燃烧）",
     "Scope 1 移动源燃烧", "组织/运营边界", "运输运营边界（TTW/WTW）", "核算边界"],
    ["② 因子来源", "吴睿中等（2024）表 8｜版本 WRZ2024-T8-v1",
     "排放因子来源与数据质量", "量化方法与不确定性", "排放强度模型", "因子来源说明"],
    ["③ 关键假设", "载质量口径 / 填补规则 / 异常保留",
     "排除项与假设", "排除量化阈值", "分配与折算规则", "方法说明"],
    ["④ 数据期", "2023 年 1/2/11/12 月，共 120 天",
     "报告期", "报告期", "参考期", "报告期"],
    ["⑤ 年化系数", "3.0417（365/120）",
     "年化说明", "基年/比较年", "年化规则", "年化说明"],
    ["⑥ 局限", "异常/换算/估算计数；未含制冷、空驶、仓储、上游燃料",
     "排除说明", "残余不确定性", "适用限制", "局限说明"],
]
cards.htable(["披露要素", "本系统取值", "GHG Protocol", "ISO 14064-1",
              "ISO 14083", "国内核算指南"], mapping)
cards.note("对标是“我们的披露要素分别回答了各制度框架的哪一问”，是映射说明；"
           "认证与核查不在本项目范围内（范围边界见 README §九）。")

layout.conclusion(
    "台账基准行与第 1 页 E₀ 同源（闸门 ledger_total 机器校验），"
    "任一披露数字可下钻到段级运单；配额缺口没有基准就不编数——"
    "碳账的可信度来自“每个数都能被追”，而不是表做得漂亮。"
)
