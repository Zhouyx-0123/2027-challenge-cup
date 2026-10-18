"""首页：一句话 + 全局指标 + 五页导航 + 数据状态 + 闸门 + 台账披露入口。"""

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import pandas as pd
import streamlit as st

import services
from components import layout, flags, theme

layout.setup("碳感知智能决策系统")
src = services.source()
src_name = services.source_name()
gate = services.gate()
layout.sidebar_params(src_name, gate)

k = services.kpis()
mock = services.is_mock()
counts = services.flag_counts()

badges = [
    layout.source_badge(src_name),
    layout.gate_badge(gate),
    "段级计量",
    "双轨对账",
    "可审计碳账",
]
theme.hero(
    "物流企业碳感知智能决策系统",
    "给它一批运单，它告诉你这条线该派什么车、多花多少钱、碳账怎么记。",
    kicker="CARBON DECISION SYSTEM",
    badges=badges,
)
if mock:
    layout.demo_note(str(src.get("reason", "")))

total_t = (k.get("total_kg") / 1000.0) if k.get("total_kg") else None
speed = k.get("avg_speed")

layout.kpi_row([
    {"label": "总碳排放（tCO₂e）", "value": services.fmt(total_t, 1),
     "help": "数据期基准 E₀：段级碳排逐段累加，重复行已剔除"},
    {"label": "总里程（万 km）", "value": services.fmt((k.get("total_km") or 0) / 10000.0, 1)},
    {"label": "独立路段", "value": services.fmt(k.get("segments"))},
    {"label": "门到门均速（km/h）", "value": services.fmt(speed, 2),
     "help": "有效速度样本均值，口径与闸门一致；>100 km/h 的异常段保留计碳"},
    {"label": "重型车碳占比",
     "value": (("%.1f%%" % (100 * k["heavy_share"])) if k.get("heavy_share") is not None else "—"),
     "help": "重型车是电动化瓶颈，也是减排空间"},
    {"label": "估算值（黄框）", "value": services.fmt(k.get("estimated_rows")),
     "help": "缺失字段按历史/行业均值填补的行数；前端用黄框标出"},
], per_row=6)

# ---------------- 五页导航 ----------------
st.markdown("")
layout.section("五页导航", "一个页面回答一个问题；1–2 页由 PM 完成，3–5 页待杨盛文接表")

c1, c2, c3 = st.columns(3)
with c1:
    st.page_link("pages/1_基准核算.py", label="① 基准核算", icon="📊")
    st.caption("排了多少碳、跑多远、数据多脏")
    st.page_link("pages/2_高排放诊断.py", label="② 高排放诊断", icon="🔥")
    st.caption("四维热点 + 同线带宽")
with c2:
    st.markdown("**③ 替代方案**")
    st.caption("这条线该派什么车，运营跑不跑通")
    st.caption("杨盛文 · 结果表已就绪：services.scenarios()")
    st.markdown("**④ 成本效益**")
    st.caption("多花多少钱、几年回本、λ 一动推荐换不换")
    st.caption("杨盛文 · 结果表已就绪：services.scenarios()")
with c3:
    st.markdown("**⑤ 碳会计台账**")
    st.caption("怎么记、怎么披露、对接什么制度")
    st.caption("杨盛文 · 结果表已就绪：services.ledger() / services.disclosure()")
    st.caption("台账与披露已在本页下方可查，不等页面。")

# ---------------- 数据状态 ----------------
st.markdown("")
layout.section("数据状态", "前端只读结果表；缺表不报错，只提示")
status = pd.DataFrame(services.file_status())
_counts = pd.to_numeric(status["行数"], errors="coerce")
status["行数"] = _counts.apply(lambda x: "—" if pd.isna(x) else f"{int(x):,}")
st.dataframe(status, use_container_width=True, hide_index=True)

# ---------------- 闸门 ----------------
st.markdown("")
layout.section("对拍闸门（G2）", "七个数 + 结构一致性；超差退出码非 0")
if gate and gate.get("targets"):
    items = pd.DataFrame(gate["targets"])
    if not items.empty:
        items["判定"] = items["status"].map(
            {"PASS": "✓ 通过", "FAIL": "✗ 未通过", "SKIP": "⏭ 跳过"}).fillna(items["status"])
        items["目标"] = items.apply(lambda r: services.fmt(r["expected"], 2) + " " + str(r["unit"]), axis=1)
        items["实测"] = items.apply(
            lambda r: ("—" if r["actual"] is None else services.fmt(r["actual"], 2)) + " " + str(r["unit"]),
            axis=1)
        show = items[["label", "stage", "目标", "实测", "判定", "note"]].rename(columns={
            "label": "核对项", "stage": "出处", "note": "说明",
        })
        st.dataframe(show, use_container_width=True, hide_index=True)
    c = gate.get("counts", {})
    st.caption("模式：%s｜PASS %s｜FAIL %s｜SKIP %s｜%s"
               % (gate.get("mode", "—"), c.get("pass", 0), c.get("fail", 0),
                  c.get("skip", 0), "闸门通过" if gate.get("passed") else "闸门未通过"))
    bad = [x for x in gate.get("structural", []) if x.get("status") != "PASS"]
    if bad:
        for item in bad:
            st.error("结构项未通过：" + str(item.get("label")) + "｜" + str(item.get("detail")))
else:
    st.info("还没有 gate_report.json——运行 python -m src.pipeline 生成。")

# ---------------- 数据健康 ----------------
st.markdown("")
layout.section("数据健康", "四类标记：灰底=重复，黄底=异常/口径，黄框=估算")
st.dataframe(flags.flag_summary(None, counts), use_container_width=True, hide_index=True)
flags.flag_legend()

# ---------------- 台账 + 披露 ----------------
led = services.ledger()
disc = services.disclosure()
with st.expander("G3 台账六字段 / G4 披露六要素（已生成，可下载）", expanded=False):
    if led.empty:
        st.info("台账未生成——先运行 python -m src.pipeline")
    else:
        cols = [c for c in (["scheme"] + services.LEDGER_COLUMNS + ["is_baseline"]) if c in led.columns]
        st.dataframe(led[cols], use_container_width=True, hide_index=True)
        st.caption("台账基准行的排放量 = 第 1 页 E₀（同一数据源，不二次计算）")
        st.download_button(
            "下载台账 CSV",
            data=led.to_csv(index=False).encode("utf-8-sig"),
            file_name="ledger.csv",
            mime="text/csv",
        )
    if disc:
        keys = [k for k in services.DISCLOSURE_KEYS if k in disc]
        st.dataframe(
            pd.DataFrame([{"要素": k, "内容": str(disc[k])} for k in keys]),
            use_container_width=True, hide_index=True,
        )
        st.download_button(
            "下载披露 JSON",
            data=pd.Series(disc).to_json(force_ascii=False, indent=2).encode("utf-8"),
            file_name="disclosure.json",
            mime="application/json",
        )

# ---------------- 运行日志 ----------------
log = services.run_log()
if log:
    with st.expander("G1 运行日志（哪一段失败、各段耗时）", expanded=False):
        stages = pd.DataFrame(log.get("stages", []))
        if not stages.empty:
            stages = stages.rename(columns={
                "name": "阶段", "status": "状态", "seconds": "耗时(s)", "error": "错误",
            })
            st.dataframe(stages[["阶段", "状态", "耗时(s)", "错误"]],
                         use_container_width=True, hide_index=True)
        c1, c2 = st.columns(2)
        c1.metric("总耗时（s）", services.fmt(log.get("total_seconds"), 2))
        c2.metric("退出码", str(log.get("exit_code", "—")))

layout.conclusion(
    "系统把每一段运输翻译成可核查的碳排与成本，并把结论落成一张能披露的账；"
    "所有估算都留痕，闸门不通过就退出码 1。"
)
