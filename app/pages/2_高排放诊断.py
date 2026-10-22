"""第 2 页 · 高排放诊断：排放集中在哪，同一条线还能省多少。

数据契约（只读，不重算，D4）：
- kpi.json：重型车碳占比 / 超长途碳占比 / 同线带宽中位 / 可比较线路数 / 覆盖率
- hotspots.parquet：四维热点（车型 / 线路 / 月份 / 里程段）
- od_band.parquet：同线带宽（evaluator 预计算）
"""

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import pandas as pd
import streamlit as st

import services
from components import cards, layout, charts, theme

layout.setup("2 高排放诊断")
src = services.source()
gate = services.gate()
layout.sidebar_params(services.source_name(), gate, current="2")

seg = services.segments()
if seg.empty:
    layout.empty_state(["baseline_carbon.parquet"])
    st.stop()

mock = services.is_mock()
theme.hero(
    "高排放诊断：空间在哪",
    "四维热点告诉你排放集中在哪；同线带宽告诉你同一条线换车能差多少。",
    kicker="02 / DIAGNOSIS",
    badges=[layout.source_badge(services.source_name()), layout.gate_badge(gate),
            "四维热点", "同线带宽"],
)
if mock:
    layout.demo_note(str(src.get("reason", "")))

k = services.kpis()
heavy_share = k.get("heavy_share")
longhaul_share = k.get("longhaul_share")
band = services.od_band()
ratio_median = k.get("od_ratio_median")
od_pairs = k.get("od_pairs") or (len(band) if not band.empty else 0)
coverage = k.get("od_coverage_pct")

layout.kpi_row([
    {"label": "重型车碳占比", "value": (("%.1f%%" % (100 * heavy_share)) if heavy_share is not None else "—"),
     "help": "重型车是电动化瓶颈（kpi.json 预计算）"},
    {"label": "超长途（>500km）碳占比",
     "value": (("%.1f%%" % (100 * longhaul_share)) if longhaul_share is not None else "—"),
     "help": "kpi.json 预计算（原页面公式已迁入 pipeline）"},
    {"label": "同线碳强度差（中位）", "value": (("%.2f×" % ratio_median) if ratio_median else "—"),
     "help": "同一 OD 上不同车型的最大/最小因子比"},
    {"label": "可比较线路数", "value": services.fmt(od_pairs)},
    {"label": "碳排覆盖率", "value": (("%.1f%%" % coverage) if coverage is not None else "—"),
     "help": "可比较线路覆盖的碳排占基准碳排比例"},
], per_row=5)

st.markdown("---")
st.caption("可比较线路 = 同一 起点网点→终点网点 至少 3 段、至少 2 种车型；"
           "倍数 = 最高因子 / 最低因子。结果表由 evaluator.py 预先生成，本页只读不重算。"
           "当前 %d 条可比较 OD、中位 %.2f×、覆盖 %.1f%% 碳排。"
           % (od_pairs, ratio_median or 0, coverage or 0))

tabs = st.tabs(["车型维度", "线路维度", "月份维度", "里程段维度"])
DIMS = [
    ("车型", "vehicle_type", "TOP 车型碳排放（t）"),
    ("线路", "od_pair", "TOP 线路碳排放（t）"),
    ("月份", "month", "月份碳排放（t）"),
    ("里程段", "dist_bin", "里程段碳排放（t）"),
]

for tab, (dim, _key, title) in zip(tabs, DIMS):
    with tab:
        hot = services.hotspots(dim)
        if hot.empty:
            st.info("热点表还没有这一维——先运行 python -m src.pipeline")
            continue
        hot = hot.copy()
        if dim == "月份":
            hot["_sort"] = pd.to_numeric(hot["value"], errors="coerce")
            hot = hot.sort_values("_sort")
            hot = hot.drop(columns=["_sort"])
        else:
            hot["_sort"] = pd.to_numeric(hot["metric_t"], errors="coerce")
            hot = hot.sort_values("_sort", ascending=False).drop(columns=["_sort"])
        top = hot.head(12)
        charts.hbar(top, "value", "metric_t", title=title, height=380)
        show = top[["value", "metric_t", "share_pct", "n"]].rename(columns={
            "value": "类别", "metric_t": "碳排放(t)", "share_pct": "占比(%)", "n": "段数"})
        cards.data_table(show, title=dim + " · TOP12", desc="热点表预计算，前端只展示", max_rows=12)

# ---------------- 同线带宽 ----------------
st.markdown("---")
layout.section("同线带宽", "同一 OD、不同车型的碳强度倍数——这是“换车”而不是“选路”的空间")
if band.empty:
    st.info("同线带宽表未生成：需要 od_band.parquet（先运行 python -m src.pipeline）。")
else:
    show = band.copy()
    show["od_pair"] = show["od_pair"].astype(str)
    top = show.sort_values("ratio", ascending=False).head(15)
    charts.hbar(top, "od_pair", "ratio", title="同线碳强度差 TOP15（倍）", height=420)
    cols = [c for c in ["od_pair", "n", "nt", "fmin", "fmax", "ratio", "kg_t", "coverage_pct"]
            if c in show.columns]
    table = show[cols].rename(columns={
        "od_pair": "线路", "n": "段数", "nt": "车型数", "fmin": "最低因子(g/km)",
        "fmax": "最高因子(g/km)", "ratio": "倍数", "kg_t": "碳排(t)", "coverage_pct": "覆盖率(%)",
    })
    cards.data_table(table.head(50), title="同线带宽 · TOP50",
                     desc="倍数 = 同 OD 最高/最低碳强度", max_rows=50)
    st.caption("倍数高不代表一定能换：还要过续航、14 小时驾驶窗口、补能与运力匹配四道约束（第 3 页）。"
               "这里的倍数是结构性带宽，不是可立即兑现的减排量。")

layout.conclusion(
    "减排空间不在选路，在同线配车：同一 OD 上最高与最低碳强度中位差 "
    + (("%.2f 倍" % ratio_median) if ratio_median else "待算")
    + "，覆盖 " + (("%.1f%%" % coverage) if coverage is not None else "—")
    + " 的基准碳排。"
)
if mock:
    st.caption("演示数据提示：真数据口径的同线带宽中位目标为 1.50×（docs/17 §1.4），"
               "接真数据后以闸门与 docs/18 复核为准。")
