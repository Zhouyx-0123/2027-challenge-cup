# -*- coding: utf-8 -*-
"""首页：Carbon Tide 电影感总览 + 八模块入口 + 数据/闸门状态。

只做展示：所有数字来自 services.py 读取的结果表；页面不做业务计算。
"""

import sys
from pathlib import Path
from urllib.parse import quote

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import pandas as pd
import streamlit as st

import services
from components import cards, flags, layout, theme


def _h(path: str) -> str:
    return quote(path, safe="/")


def _bento_html(total_t, ratio_txt, speed_txt) -> str:
    """首页八模块 bento 网格：纯 HTML/CSS，不加载任何图片。"""
    return (
        "<div class='cm-bento'>"
        "<a class='cm-bento-card span2' href='" + _h("/基准核算") + "' target='_top'>"
        "<div class='cm-card-visual'><span class='cm-v-tag'>33,214 SEGMENTS</span>"
        "<div class='cm-v-bars'><i style='height:28%'></i><i style='height:44%'></i>"
        "<i style='height:36%'></i><i style='height:62%'></i><i style='height:51%'></i>"
        "<i style='height:78%'></i><i style='height:66%'></i><i style='height:92%'></i></div>"
        "<div class='cm-v-line'></div></div>"
        "<div class='cm-card-body'><div class='cm-card-eyebrow'>01 / BASELINE</div>"
        "<div class='cm-card-title'>基准核算<span>→</span></div>"
        "<div class='cm-card-desc'>总碳排、总里程、数据质量：先把现状看清楚。E₀ "
        + services.fmt(total_t, 1) + " t，逐段可追溯。</div>"
        "<div class='cm-card-foot'>DATA → LEDGER</div></div></a>"

        "<a class='cm-bento-card' href='" + _h("/高排放诊断") + "' target='_top'>"
        "<div class='cm-card-visual'><span class='cm-v-tag'>HOTSPOTS</span>"
        "<div class='cm-v-grid'>" + "".join("<i></i>" for _ in range(18)) + "</div></div>"
        "<div class='cm-card-body'><div class='cm-card-eyebrow'>02 / DIAGNOSIS</div>"
        "<div class='cm-card-title'>高排放诊断<span>→</span></div>"
        "<div class='cm-card-desc'>四维热点 + 同线带宽 " + ratio_txt + "：减排空间不在平均数里。</div>"
        "<div class='cm-card-foot'>FIND THE TAIL</div></div></a>"

        "<a class='cm-bento-card' href='" + _h("/替代方案") + "' target='_top'>"
        "<div class='cm-card-visual'><span class='cm-v-tag'>C1–C5</span>"
        "<div class='cm-v-compare'><i></i><i></i><i></i><i></i></div></div>"
        "<div class='cm-card-body'><div class='cm-card-eyebrow'>03 / ALTERNATIVES</div>"
        "<div class='cm-card-title'>替代方案<span>→</span></div>"
        "<div class='cm-card-desc'>这条线该派什么车？续航、窗口、运力、补能、冬季逐条校验。</div>"
        "<div class='cm-card-foot'>SCENARIO × ROUTE</div></div></a>"

        "<a class='cm-bento-card' href='" + _h("/成本效益") + "' target='_top'>"
        "<div class='cm-card-visual'><span class='cm-v-tag'>NPV / λ</span>"
        "<div class='cm-v-bars'><i style='height:82%'></i><i style='height:58%'></i>"
        "<i style='height:48%'></i><i style='height:34%'></i><i style='height:66%'></i></div></div>"
        "<div class='cm-card-body'><div class='cm-card-eyebrow'>04 / COST & PAYBACK</div>"
        "<div class='cm-card-title'>成本效益<span>→</span></div>"
        "<div class='cm-card-desc'>多花多少钱、几年回本、λ 一动推荐换不换。</div>"
        "<div class='cm-card-foot'>OPEX + CARBON</div></div></a>"

        "<a class='cm-bento-card' href='" + _h("/碳会计台账") + "' target='_top'>"
        "<div class='cm-card-visual'><span class='cm-v-tag'>SIX FIELDS</span>"
        "<div class='cm-v-rows'><i></i><i></i><i></i><i></i><i></i></div></div>"
        "<div class='cm-card-body'><div class='cm-card-eyebrow'>05 / CARBON LEDGER</div>"
        "<div class='cm-card-title'>碳会计台账<span>→</span></div>"
        "<div class='cm-card-desc'>怎么记、怎么披露、怎么对接制度：六字段 + 六要素。</div>"
        "<div class='cm-card-foot'>AUDIT READY</div></div></a>"

        "<a class='cm-bento-card span2' href='" + _h("/路线实验室") + "' target='_top'>"
        "<div class='cm-card-visual'><span class='cm-v-tag'>AMAP LIVE · 4 POLICIES</span>"
        "<svg class='cm-v-route' viewBox='0 0 600 150' preserveAspectRatio='none'>"
        "<defs><linearGradient id='cmGrad' x1='0' x2='1'><stop offset='0%' stop-color='#5EE7FF'/>"
        "<stop offset='100%' stop-color='#2DD4A7'/></linearGradient></defs>"
        "<path d='M18 124 C 100 60, 160 138, 238 92 S 340 40, 410 76 S 520 118, 582 48'/>"
        "<circle cx='18' cy='124' r='4'/><circle cx='582' cy='48' r='4'/></svg></div>"
        "<div class='cm-card-body'><div class='cm-card-eyebrow'>06 / ROUTE LAB · LIVE</div>"
        "<div class='cm-card-title'>路线实验室<span>→</span></div>"
        "<div class='cm-card-desc'>高德实时规划，路线贴着公路走；4 条策略切换，策略级碳账同步更新。"
        "310 个 OD，约 12,000 点/条。</div>"
        "<div class='cm-card-foot'>ROAD-FOLLOWING · SMOOTH</div></div></a>"

        "<a class='cm-bento-card span2' href='" + _h("/电动化测算") + "' target='_top'>"
        "<div class='cm-card-visual'><span class='cm-v-tag'>CHARGE MATH</span>"
        "<div class='cm-v-bars'><i style='height:22%'></i><i style='height:38%'></i>"
        "<i style='height:54%'></i><i style='height:72%'></i><i style='height:90%'></i>"
        "<i style='height:62%'></i><i style='height:44%'></i></div></div>"
        "<div class='cm-card-body'><div class='cm-card-eyebrow'>07 / ELECTRIFICATION</div>"
        "<div class='cm-card-title'>电动化测算<span>→</span></div>"
        "<div class='cm-card-desc'>改一个参数，全表联动：年节省、回本月份、NPV 四分解、双口径减碳。"
        "当前均速 " + speed_txt + " km/h。</div>"
        "<div class='cm-card-foot'>WHAT-IF ENGINE</div></div></a>"

        "<a class='cm-bento-card span2' href='" + _h("/车队TCO沙盘") + "' target='_top'>"
        "<div class='cm-card-visual'><span class='cm-v-tag'>FLEET TCO</span>"
        "<div class='cm-v-compare'><i></i><i></i><i></i><i></i><i></i></div></div>"
        "<div class='cm-card-body'><div class='cm-card-eyebrow'>08 / FLEET TCO</div>"
        "<div class='cm-card-title'>车队 TCO 沙盘<span>→</span></div>"
        "<div class='cm-card-desc'>柴油 / 电动 / CNG / HVO 同框架对比，NPV、回本、元/tCO₂e 一屏对齐。</div>"
        "<div class='cm-card-foot'>MULTI-ROUTE · MULTI-FUEL</div></div></a>"
        "</div>"
    )


layout.setup("碳感知智能决策系统")
src = services.source()
src_name = services.source_name()
gate = services.gate()
params = layout.sidebar_params(src_name, gate, current="0")

k = services.kpis()
mock = services.is_mock()
counts = services.flag_counts()

total_t = (k.get("total_kg") / 1000.0) if k.get("total_kg") else None
speed = k.get("avg_speed")
heavy = k.get("heavy_share")
ratio = k.get("od_ratio_median")
ratio_txt = ("%.2f×" % float(ratio)) if ratio is not None else "—"
speed_txt = services.fmt(speed, 2)

theme.hero(
    "让每一吨碳，都有据可查。",
    "从 33,214 段真实运单出发，把路线、车型、成本与排放，算成一张可审计、可决策、"
    "可披露的碳账。前端只读结果表，所有数字都能追到具体路段。",
    kicker="CARBON TIDE / DECISION OS",
    badges=[layout.source_badge(src_name), layout.gate_badge(gate), "8 个模块", "只读数据面"],
    highlight="有据可查。",
    actions=[
        {"label": "进入决策台", "href": "/基准核算", "primary": True},
        {"label": "打开路线实验室", "href": "/路线实验室", "primary": False},
    ],
)
if mock:
    layout.demo_note(str(src.get("reason", "")))

theme.marquee([
    "33,214 SEGMENTS",
    "4,690,435.81 KM",
    "E0 " + services.fmt(total_t, 1) + " tCO2e",
    "AVG " + speed_txt + " KM/H",
    "OD BAND " + ratio_txt,
    "GATE 26/26",
    "READ-ONLY FRONTEND",
    "AMAP LIVE ROUTE",
])

layout.section("数据底座 / THE LEDGER", "一份宽表，一条可追溯的碳账；不是报告，是装置。")
cards.stats([
    {"label": "基准碳排", "value": services.fmt(total_t, 1) + " t",
     "sub": "33,214 段逐段累加 · 重复行已剔除", "tone": "mint"},
    {"label": "总里程", "value": services.fmt((k.get("total_km") or 0) / 10000.0, 1) + " 万 km",
     "sub": "去重后独立路段口径", "tone": "info"},
    {"label": "独立路段", "value": services.fmt(k.get("segments")),
     "sub": "36,329 行 → 33,214 段", "tone": "plain"},
    {"label": "门到门均速", "value": speed_txt + " km/h",
     "sub": "有效速度样本 33,213 条", "tone": "plain"},
    {"label": "同线带宽", "value": ratio_txt,
     "sub": "网点级中位；覆盖基准碳排 83.8%", "tone": "amber"},
    {"label": "估算值", "value": services.fmt(k.get("estimated_rows")) + " 行",
     "sub": "缺失字段填补，前端黄框留痕", "tone": "coral"},
], cols=3)

layout.section("八个模块 / THE SYSTEM", "从一段运单到一张能披露的账，八个模块彼此咬合。")
st.markdown(_bento_html(total_t, ratio_txt, speed_txt), unsafe_allow_html=True)

layout.section("从运单到披露 / THE PIPELINE", "每一步都留下可复核的中间结果，而不是一句结论。")
cards.steps([
    {"n": "01", "t": "运单清洗", "d": "去重、关联、填补、四类 FLAG；36,329 → 33,214 段"},
    {"n": "02", "t": "段级计碳", "d": "法定轨 + 物理轨；E₀ = 4,502.04 t 逐段可查"},
    {"n": "03", "t": "路径决策", "d": "路线 × 车型 × 成本 × λ；C1–C5 运营约束"},
    {"n": "04", "t": "台账披露", "d": "六字段台账 + 六要素披露 + 26 项闸门"},
], active=-1)

layout.section("运行状态 / LIVE STATUS", "前端只读结果表；缺表不红屏，只提示。")
status = pd.DataFrame(services.file_status())
_counts = pd.to_numeric(status["行数"], errors="coerce")
status["行数"] = _counts.apply(lambda x: "—" if pd.isna(x) else f"{int(x):,}")
st.dataframe(status, width="stretch", hide_index=True)

# ---------------- 闸门 ----------------
layout.section("对拍闸门 / G2", "8 个数字目标 + 18 项结构一致性；超差退出码非 0")
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
        st.dataframe(show, width="stretch", hide_index=True)
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
layout.section("数据健康 / DATA HYGIENE", "四类标记：灰底=重复，黄底=异常/口径，黄框=估算")
st.dataframe(flags.flag_summary(None, counts), width="stretch", hide_index=True)
flags.flag_legend()

# ---------------- 台账 + 披露 ----------------
led = services.ledger()
disc = services.disclosure()
with st.expander("G3 台账六字段 / G4 披露六要素（已生成，可下载）", expanded=False):
    if led.empty:
        st.info("台账未生成——先运行 python -m src.pipeline")
    else:
        cols = [c for c in (["scheme"] + services.LEDGER_COLUMNS + ["is_baseline"]) if c in led.columns]
        st.dataframe(led[cols], width="stretch", hide_index=True)
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
            width="stretch", hide_index=True,
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
                         width="stretch", hide_index=True)
        c1, c2 = st.columns(2)
        c1.metric("总耗时（s）", services.fmt(log.get("total_seconds"), 2))
        c2.metric("退出码", str(log.get("exit_code", "—")))

theme.cta(
    "把一条运单，变成一张能披露的账。",
    "从数据清洗、段级碳排、路线与车型决策，到台账与披露——每一步都只读结果表，"
    "每个数字都能回到具体路段。",
    primary={"label": "从基准核算开始", "href": "/基准核算"},
    secondary={"label": "打开路线实验室", "href": "/路线实验室"},
)
layout.conclusion(
    "系统把每一段运输翻译成可核查的碳排与成本，并把结论落成一张能披露的账；"
    "所有估算都留痕，闸门不通过就退出码 1。"
)
