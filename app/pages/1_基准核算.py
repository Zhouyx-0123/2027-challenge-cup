"""第 1 页 · 基准核算：现在排了多少碳、跑了多少路、数据有多脏。"""

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import pandas as pd
import streamlit as st

import services
from components import layout, charts, flags

layout.setup("1 基准核算")
layout.page_header(
    "1 基准核算",
    "我的车队现在排了多少碳、跑了多少路、数据有多脏？",
    badge="数据期 2023 年 1/2/11/12 月",
)
layout.sidebar_params()

seg = services.segments()
if seg.empty:
    layout.empty_state(["baseline_carbon.parquet"])
    st.stop()

k = services.kpis()
total_t = (k.get("total_kg") / 1000.0) if k.get("total_kg") else None

layout.kpi_row([
    {"label": "总碳排放（tCO₂）", "value": services.fmt(total_t, 2), "help": "33,214 段逐段累加，口径见 docs/17 §1.5"},
    {"label": "总里程（km）", "value": services.fmt(k.get("total_km"), 2)},
    {"label": "独立路段", "value": services.fmt(k.get("segments"))},
    {"label": "门到门均速（km/h）", "value": services.fmt(k.get("avg_speed"), 2)},
])

st.markdown("---")
layout.section("数据健康", "三类标记：哪些行被去重、哪些行的时间戳有问题、哪些载重经过换算。")
st.dataframe(flags.flag_summary(seg), use_container_width=True, hide_index=True)
flags.flag_legend()

st.markdown("---")
layout.section("排放分布", "先看总量在哪个月、哪类车型上。")
left, right = st.columns(2)

with left:
    if "month" in seg.columns and "kg_co2" in seg.columns:
        tmp = seg.copy()
        tmp["emission_t"] = pd.to_numeric(tmp["kg_co2"], errors="coerce") / 1000.0
        by_month = (
            tmp.groupby("month", as_index=False)["emission_t"].sum()
            .assign(month_label=lambda d: d["month"].astype(str) + " 月")
        )
        charts.bar(by_month, "month_label", "emission_t", "分月份碳排放（t）")
    else:
        st.caption("宽表还没有 month 列")

with right:
    if "vclass" in seg.columns and "kg_co2" in seg.columns:
        tmp = seg.copy()
        tmp["emission_t"] = pd.to_numeric(tmp["kg_co2"], errors="coerce") / 1000.0
        tmp["vclass_label"] = tmp["vclass"].map({1: "轻型", 2: "中型", 3: "重型"}).fillna("未分类")
        by_class = tmp.groupby("vclass_label", as_index=False)["emission_t"].sum()
        charts.bar(by_class, "vclass_label", "emission_t", "分车型大类碳排放（t）")
    else:
        st.caption("宽表还没有 vclass 列")

st.markdown("---")
layout.section("段级明细", "灰底 = 重复行（已去重）；黄底 = 时间戳异常或载重换算（保留）。")
flags.flagged_table(
    seg,
    columns=["waybill_no", "vehicle_type", "origin", "dest", "dist_km", "kg_co2", "delta_pct"],
    max_rows=200,
)

layout.conclusion(
    "基准碳排就是后面所有减排率的分子分母——这个数改一个字，全篇结论都要重算。"
)
