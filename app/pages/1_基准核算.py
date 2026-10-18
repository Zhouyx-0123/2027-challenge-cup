"""第 1 页 · 基准核算：碳排了多少、从哪来、数据有多脏。"""

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import pandas as pd
import streamlit as st

import services
from components import layout, charts, flags, mapview, theme

layout.setup("1 基准核算")
layout.sidebar_params()

seg = services.segments()
if seg.empty:
    theme.hero("基准核算", "还没有结果表", kicker="01 / BASELINE")
    layout.empty_state(["baseline_carbon.parquet"])
    st.stop()

missing = services.column_ok(seg, services.CORE_COLUMNS)
if missing:
    theme.hero("基准核算", "结果表还没有按契约落盘", kicker="01 / BASELINE", badges=["数据检查"])
    st.warning("缺少字段：" + "、".join(missing))
    st.caption("当前字段：" + "、".join([str(c) for c in seg.columns]))
    with st.expander("先看看现有数据"):
        st.dataframe(seg.head(20), use_container_width=True)
    layout.conclusion("数据契约没落地之前，页面不编数字——这是硬规矩。")
    st.stop()

mock = services.is_mock()
badges = (["演示数据"] if mock else ["实测数据"]) + ["数据期 2023 · 120 天", "双轨计量"]
theme.hero(
    "基准核算：碳从哪里来",
    "逐段计量 3 万段公路运输，先看清总量、结构与数据的可信度。",
    kicker="01 / BASELINE",
    badges=badges,
)

# ---------------- 筛选条 ----------------
f1, f2, f3 = st.columns([1.1, 1.1, 1.6])
with f1:
    picked_cls = st.multiselect("车型大类", ["轻型", "中型", "重型"], placeholder="全部")
with f2:
    month_opts = sorted([int(m) for m in seg["month"].dropna().unique()]) if "month" in seg.columns else []
    picked_month = st.multiselect("月份", month_opts, placeholder="全月")
with f3:
    st.caption("筛选只影响本页展示，不改计算结果")

view = seg.copy()
if picked_cls and "vclass" in view.columns:
    name2cls = {"轻型": 1, "中型": 2, "重型": 3}
    view = view[view["vclass"].isin([name2cls[c] for c in picked_cls])]
if picked_month and "month" in view.columns:
    view = view[view["month"].isin(picked_month)]

if view.empty:
    st.info("当前筛选条件下没有数据。")
    st.stop()

kg = pd.to_numeric(view["kg_co2"], errors="coerce").fillna(0.0)
total_t = float(kg.sum()) / 1000.0
dist_km = float(pd.to_numeric(view["dist_km"], errors="coerce").fillna(0.0).sum())
heavy_share = float(kg[view["vclass"] == 3].sum()) / (float(kg.sum()) or 1.0) if "vclass" in view.columns else None
avg_speed = None
if "speed_kmh" in view.columns:
    s = pd.to_numeric(view["speed_kmh"], errors="coerce")
    s = s[(s > 1) & (s < 100)]
    avg_speed = float(s.mean()) if len(s) else None
anomaly_share = None
if "flag_anomaly" in view.columns:
    anomaly_share = float(pd.to_numeric(view["flag_anomaly"], errors="coerce").fillna(0).mean())

layout.kpi_row([
    {"label": "总碳排放（tCO₂e）", "value": services.fmt(total_t, 1), "help": "段级碳排逐段累加"},
    {"label": "总里程（万 km）", "value": services.fmt(dist_km / 10000.0, 1)},
    {"label": "运输段数", "value": services.fmt(len(view))},
    {"label": "门到门均速（km/h）", "value": services.fmt(avg_speed, 1)},
    {"label": "重型车碳占比", "value": ("%.1f%%" % (100 * heavy_share)) if heavy_share is not None else "—"},
    {"label": "时间戳异常占比", "value": ("%.1f%%" % (100 * anomaly_share)) if anomaly_share is not None else "—"},
], per_row=6)

# ---------------- 地图 ----------------
st.markdown("")
layout.section("碳排地图", "气泡大小 = 碳排放量；播放月份轴，看春节前的峰")
mode = st.radio("地图视图", ["城市热力（可播放）", "OD 流向"], horizontal=True, label_visibility="collapsed")

if mode.startswith("城市"):
    fig = mapview.bubble_map(view)
else:
    fig = mapview.flow_map(view)
mapview.render(fig)

# ---------------- 排行 + 数据健康 ----------------
left, right = st.columns([0.58, 0.42])

with left:
    layout.section("车型排行", "碳排最高的 10 个车型")
    if "vehicle_type" in view.columns:
        top = (view.assign(t=kg / 1000.0)
                   .groupby("vehicle_type", as_index=False)["t"].sum()
                   .sort_values("t", ascending=False).head(10)
                   .rename(columns={"vehicle_type": "车型", "t": "碳排放(t)"}))
        charts.hbar(top, "车型", "碳排放(t)", height=360)
    else:
        st.caption("缺 vehicle_type 列")

with right:
    layout.section("数据健康", "三类标记，标出来而不是删掉")
    st.dataframe(flags.flag_summary(view), use_container_width=True, hide_index=True)
    flags.flag_legend()
    st.caption("重复行按 运单+车牌+里程+起点+终点 判定；异常段保留计入碳排，只影响速度统计。")

# ---------------- 明细 ----------------
st.markdown("")
layout.section("段级明细", "任取一行都能追到原始运单")
flags.flagged_table(
    view,
    columns=["waybill_no", "vehicle_type", "origin", "dest", "dist_km", "kg_co2", "delta_pct"],
    max_rows=200,
)

layout.conclusion(
    "基准碳排是所有减排率的分子分母——这个数改一个字，全篇结论都要重算。"
)
