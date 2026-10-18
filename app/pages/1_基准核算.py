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
src = services.source()
gate = services.gate()
layout.sidebar_params(services.source_name(), gate)

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
badges = [layout.source_badge(services.source_name()), layout.gate_badge(gate),
          "数据期 2023 · 120 天", "双轨计量"]
theme.hero(
    "基准核算：碳从哪里来",
    "逐段计量每一条公路运输，先看清总量、结构与数据的可信度。",
    kicker="01 / BASELINE",
    badges=badges,
)
if mock:
    layout.demo_note(str(src.get("reason", "")))

# ---------------- 筛选条 ----------------
f1, f2, f3 = st.columns([1.1, 1.0, 1.4])
with f1:
    picked_cls = st.multiselect("车型大类", ["轻型", "中型", "重型"], placeholder="全部")
with f2:
    month_opts = sorted([int(m) for m in seg["month"].dropna().unique()]) if "month" in seg.columns else []
    picked_month = st.multiselect("月份", month_opts, placeholder="全月")
with f3:
    only_est = st.checkbox("只看估算值（黄框）", value=False,
                           help="flag_estimated = 1 的行：有字段被历史/行业均值填补")
st.caption("筛选只影响本页展示，不改计算结果；结果表的生成口径在 pipeline 出口已锁定。")

view = seg.copy()
if picked_cls and "vclass" in view.columns:
    name2cls = {"轻型": 1, "中型": 2, "重型": 3}
    view = view[view["vclass"].isin([name2cls[c] for c in picked_cls])]
if picked_month and "month" in view.columns:
    view = view[view["month"].isin(picked_month)]
if only_est and "flag_estimated" in view.columns:
    view = view[view["flag_estimated"] == 1]

if view.empty:
    st.info("当前筛选条件下没有数据。")
    st.stop()

kg = pd.to_numeric(view["kg_co2"], errors="coerce").fillna(0.0)
total_t = float(kg.sum()) / 1000.0
dist_km = float(pd.to_numeric(view["dist_km"], errors="coerce").fillna(0.0).sum())
heavy_share = float(kg[view["vclass"] == 3].sum()) / (float(kg.sum()) or 1.0) if "vclass" in view.columns else None
speed = pd.to_numeric(view["speed_kmh"], errors="coerce") if "speed_kmh" in view.columns else pd.Series(dtype=float)
speed_valid = speed[speed > 0]
avg_speed = float(speed_valid.mean()) if len(speed_valid) else None
speed_clean = speed_valid[speed_valid.between(1, 100)]
avg_speed_clean = float(speed_clean.mean()) if len(speed_clean) else None
est_share = float(pd.to_numeric(view["flag_estimated"], errors="coerce").fillna(0).mean()) if "flag_estimated" in view.columns else None

layout.kpi_row([
    {"label": "总碳排放（tCO₂e）", "value": services.fmt(total_t, 1), "help": "段级碳排逐段累加；重复行已剔除"},
    {"label": "总里程（万 km）", "value": services.fmt(dist_km / 10000.0, 1)},
    {"label": "运输段数", "value": services.fmt(len(view))},
    {"label": "门到门均速（km/h）", "value": services.fmt(avg_speed, 2),
     "help": "有效速度样本均值，口径与闸门一致；>100 km/h 的异常段保留计碳"},
    {"label": "重型车碳占比", "value": (("%.1f%%" % (100 * heavy_share)) if heavy_share is not None else "—")},
    {"label": "估算值占比", "value": (("%.2f%%" % (100 * est_share)) if est_share is not None else "—"),
     "help": "黄框标出的行；只填补、不删除"},
], per_row=6)
st.caption("剔除 >100 km/h 的时间戳异常后均速 %s km/h；异常段保留计碳，只影响速度统计。"
           % services.fmt(avg_speed_clean, 2))

# ---------------- 对拍口径 ----------------
st.markdown("")
layout.section("对拍口径", "闸门在 pipeline 出口自动核对七个数，本页只展示，不重算")
targets = gate.get("targets", []) if gate else []
if targets:
    tdf = pd.DataFrame(targets)
    tdf["判定"] = tdf["status"].map(
        {"PASS": "✓ 通过", "FAIL": "✗ 未通过", "SKIP": "⏭ 跳过"}).fillna(tdf["status"])
    tdf["目标"] = tdf.apply(lambda r: services.fmt(r["expected"], 2) + " " + str(r["unit"]), axis=1)
    tdf["实测"] = tdf.apply(
        lambda r: ("—" if r["actual"] is None else services.fmt(r["actual"], 2)) + " " + str(r["unit"]),
        axis=1)
    st.dataframe(
        tdf[["label", "stage", "目标", "实测", "判定", "note"]].rename(columns={
            "label": "核对项", "stage": "出处", "note": "说明"}),
        use_container_width=True, hide_index=True,
    )
    st.caption("真实数据下 E₀/均速两项标“未通过”，原因在说明列；"
               "33,214 / 4,690,435.81 / 33,213 三项与因子两项已通过。演示模式则跳过依赖真数据的五项。")
else:
    st.info("还没有 gate_report.json——运行 python -m src.pipeline 生成。")

# ---------------- 地图 ----------------
st.markdown("")
layout.section("碳排地图", "气泡大小 = 碳排放量；按月份播放，看春节前的峰")
mode = st.radio("地图视图", ["发货地热力", "收货地热力", "OD 流向"], index=1,
                horizontal=True, label_visibility="collapsed")
if mode == "发货地热力":
    fig = mapview.bubble_map(view)
elif mode == "收货地热力":
    fig = mapview.bubble_map(view, lat="dest_lat", lon="dest_lon", city="dest")
else:
    fig = mapview.flow_map(view)
mapview.render(fig)

origin_counts = view["origin"].astype(str).value_counts()
if len(origin_counts):
    top_name = origin_counts.index[0]
    top_share = origin_counts.iloc[0] / len(view) * 100.0
    st.caption("原始订单发货地集中：%s 占 %.1f%% 段；收货地覆盖 %d 个城市。"
               "默认按收货地看全国分布，切到发货地看上海枢纽。"
               % (top_name, top_share, view["dest"].nunique()))

# ---------------- 排行 + 速度分布 ----------------
left, right = st.columns([0.56, 0.44])
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
    layout.section("速度分布", "异常段保留计碳，但不参与均速")
    charts.hist(view, "speed_kmh", height=360, bins=36, x_label="门到门均速（km/h）")
    st.caption("超过 100 km/h 的段来自封/解车时间戳异常，已打 flag_anomaly，不删除。")

# ---------------- 数据健康 ----------------
st.markdown("")
layout.section("数据健康", "四类标记：灰底=重复，黄底=异常/口径，黄框=估算")
counts = services.flag_counts()
st.dataframe(flags.flag_summary(None, counts), use_container_width=True, hide_index=True)
flags.flag_legend()

est_rows = view[view["flag_estimated"] == 1] if "flag_estimated" in view.columns else view.iloc[0:0]
if not est_rows.empty:
    reasons = (est_rows["estimate_note"].astype(str)
               .str.split("；").explode().str.strip())
    reasons = reasons[reasons != ""].value_counts().head(8).reset_index()
    reasons.columns = ["估算原因", "行数"]
    c1, c2 = st.columns([0.45, 0.55])
    with c1:
        st.dataframe(reasons, use_container_width=True, hide_index=True)
    with c2:
        st.caption("填补顺序：同线历史均值 → 行业绕行系数 → 全样本中位数；"
                   "车型找不到按载重推断；发票匹配不上按同类别历史均价。"
                   "每一种填补都写进 estimate_note，前端黄框标出。")

flags.estimated_panel(view, n=8)

# ---------------- 明细 ----------------
st.markdown("")
layout.section("段级明细", "任取一行都能追到原始运单；黄框行 = 估算值")
columns = ["waybill_no", "vehicle_type", "origin", "dest", "dist_km", "kg_co2",
           "delta_pct", "speed_kmh", "estimate_note"]
available_cols = [c for c in columns if c in view.columns]
flags.flagged_table(view, columns=available_cols, max_rows=200)

layout.conclusion(
    "基准碳排是全部减排率的分子分母——这个数改一个字，全篇结论都要重算；"
    "本页把异常行与估算行全部标出而不是删掉，删了会改口径、对拍必挂。"
)
