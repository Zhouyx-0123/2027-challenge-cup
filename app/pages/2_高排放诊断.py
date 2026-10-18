"""第 2 页 · 高排放诊断：排放集中在哪，同一条线还能省多少。"""

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import pandas as pd
import streamlit as st

import services
from components import layout, charts, flags

layout.setup("2 高排放诊断")
layout.page_header(
    "2 高排放诊断",
    "排放集中在哪？同一条线路上，换一种车能差多少？",
    badge="四维 + 同线带宽",
)
layout.sidebar_params()

seg = services.segments()
if seg.empty:
    layout.empty_state(["baseline_carbon.parquet"])
    st.stop()

kg = pd.to_numeric(seg.get("kg_co2"), errors="coerce")
total = float(kg.sum()) or 1.0

# ---- 指标卡 ----
heavy_share = None
if "vclass" in seg.columns:
    vc = pd.to_numeric(seg["vclass"], errors="coerce")
    heavy_share = float(kg[vc == 3].sum()) / total

longhaul_share = None
if "dist_km" in seg.columns:
    dist = pd.to_numeric(seg["dist_km"], errors="coerce")
    longhaul_share = float(kg[dist > 500].sum()) / total

od_ratio_median = None
od_pairs = 0
if {"od_pair", "factor_gkm"}.issubset(seg.columns):
    g = seg.groupby("od_pair")
    band = g.agg(n=("factor_gkm", "size"),
                 nt=("vehicle_type", "nunique"),
                 fmin=("factor_gkm", "min"),
                 fmax=("factor_gkm", "max"))
    band = band[(band["n"] >= 3) & (band["nt"] >= 2) & (band["fmin"] > 0)]
    if not band.empty:
        band["ratio"] = band["fmax"] / band["fmin"]
        od_ratio_median = float(band["ratio"].median())
        od_pairs = int(len(band))

layout.kpi_row([
    {"label": "重型车碳占比", "value": (("%.1f%%" % (100 * heavy_share)) if heavy_share is not None else "—"),
     "help": "重型车是电动化瓶颈"},
    {"label": "超长途（>500km）碳占比", "value": (("%.1f%%" % (100 * longhaul_share)) if longhaul_share is not None else "—")},
    {"label": "同线碳强度差（中位）", "value": (("%.2f×" % od_ratio_median) if od_ratio_median else "—"),
     "help": "同一 OD 上不同车型的最大/最小因子比"},
    {"label": "可比较线路数", "value": services.fmt(od_pairs)},
])

st.markdown("---")
tab1, tab2, tab3 = st.tabs(["车型维度", "线路维度", "同线带宽"])

with tab1:
    if "vehicle_type" in seg.columns:
        agg = (seg.assign(kg=kg)
                  .groupby("vehicle_type", as_index=False)["kg"].sum()
                  .sort_values("kg", ascending=False).head(12))
        agg["emission_t"] = agg["kg"] / 1000.0
        charts.bar(agg, "vehicle_type", "emission_t", "TOP12 车型碳排放（t）", height=380)
        st.dataframe(agg[["vehicle_type", "emission_t"]], use_container_width=True, hide_index=True)
    else:
        st.caption("宽表还没有 vehicle_type 列")

with tab2:
    if "od_pair" in seg.columns:
        agg = (seg.assign(kg=kg)
                  .groupby("od_pair", as_index=False).agg(kg=("kg", "sum"), n=("kg", "size"))
                  .sort_values("kg", ascending=False).head(15))
        agg["emission_t"] = agg["kg"] / 1000.0
        st.dataframe(agg[["od_pair", "n", "emission_t"]].rename(
            columns={"od_pair": "线路", "n": "段数", "emission_t": "碳排放(t)"}),
            use_container_width=True, hide_index=True)
    else:
        st.caption("宽表还没有 od_pair 列")

with tab3:
    band_all = services.od_band()
    if not band_all.empty:
        st.dataframe(band_all.head(50), use_container_width=True, hide_index=True)
    elif od_pairs:
        show = band.reset_index().sort_values("ratio", ascending=False).head(50)
        show = show.rename(columns={"od_pair": "线路", "n": "段数", "nt": "车型数",
                                    "fmin": "最低因子", "fmax": "最高因子", "ratio": "倍数"})
        st.dataframe(show, use_container_width=True, hide_index=True)
    else:
        st.info("需要 od_pair 与 factor_gkm 两列；等 optimizer 产出 baseline_carbon.parquet 后自动出现")

st.markdown("---")
layout.section("怎么读这张页", "同一条线路上换个车型，碳强度中位差 1.5 倍——**企业自己试线试不出来，因为它试的是走哪条路，不是派什么车**。")

layout.conclusion(
    "减排空间不在选路（只有 0.07%），在同线配车：同一 OD 上最高与最低碳强度中位差 "
    + (("%.2f 倍" % od_ratio_median) if od_ratio_median else "待算")
    + "。"
)
