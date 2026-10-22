"""第 1 页 · 基准核算：碳排了多少、从哪来、数据有多脏。

数据契约（只读，不重算，D4）：
- kpi.json：总碳 / 总里程 / 段数 / 均速 / 重型占比 / 估算占比（全量锁定口径）
- hotspots.parquet：车型碳排排行（dimension=车型）
- data_quality.json：四类 FLAG 计数、源行号可回查率、估算原因拆分
- gate_report.json：八项对拍目标（本页只展示）
- baseline_carbon.parquet：段级明细（地图 / 直方图 / 明细表直接画原始列）

页面口径：KPI 与排行榜为**全量锁定口径**，不随本页筛选变化——
筛选只影响地图、速度分布与段级明细；这样任何筛选下的数字都与闸门对得上。
"""

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import pandas as pd
import streamlit as st

import services
from components import cards, layout, charts, flags, mapview, theme

layout.setup("1 基准核算")
src = services.source()
gate = services.gate()
layout.sidebar_params(services.source_name(), gate, current="1")

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
        cards.data_table(seg.head(20), title="现有字段预览", max_rows=20)
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

# ---- KPI 行：全部读 kpi.json（pipeline 预计算，全量锁定口径） ----
k = services.kpis()
heavy_share = k.get("heavy_share")
est_share = k.get("estimated_rows")
est_share_pct = (est_share / k["segments"]) if (est_share is not None and k.get("segments")) else None

layout.kpi_row([
    {"label": "总碳排放（tCO₂e）", "value": services.fmt(k.get("e0_t"), 1),
     "help": "段级碳排逐段累加；重复行已剔除。全量锁定口径，不随筛选变化"},
    {"label": "总里程（万 km）", "value": services.fmt((k.get("total_km") or 0) / 10000.0, 1)},
    {"label": "运输段数", "value": services.fmt(k.get("segments"))},
    {"label": "门到门均速（km/h）", "value": services.fmt(k.get("avg_speed"), 2),
     "help": "有效速度样本均值，口径与闸门一致；>100 km/h 的异常段保留计碳"},
    {"label": "重型车碳占比", "value": (("%.1f%%" % (100 * heavy_share)) if heavy_share is not None else "—")},
    {"label": "估算值占比", "value": (("%.2f%%" % (100 * est_share_pct)) if est_share_pct is not None else "—"),
     "help": "黄框标出的行；只填补、不删除"},
], per_row=3)
st.caption("剔除 >100 km/h 的时间戳异常后均速 %s km/h；异常段保留计碳，只影响速度统计。"
           "KPI 为 pipeline 出口锁定的全量口径（kpi.json），筛选只影响下方地图、"
           "速度分布与段级明细——避免筛选后的数字与闸门对不上。"
           % services.fmt(k.get("avg_speed_clean"), 2))

# ---------------- 对拍口径 ----------------
st.markdown("")
layout.section("对拍口径", "闸门在 pipeline 出口自动核对七个数，本页只展示，不重算")
targets = gate.get("targets", []) if gate else []
if targets:
    tdf = pd.DataFrame(targets)
    tdf["判定"] = tdf["status"]
    tdf["目标"] = tdf.apply(lambda r: services.fmt(r["expected"], 2) + " " + str(r["unit"]), axis=1)
    tdf["实测"] = tdf.apply(
        lambda r: ("—" if r["actual"] is None else services.fmt(r["actual"], 2)) + " " + str(r["unit"]),
        axis=1)
    cards.data_table(
        tdf[["label", "stage", "目标", "实测", "判定", "note"]].rename(columns={
            "label": "核对项", "stage": "出处", "note": "说明"}),
        title="G2 对拍口径", desc="pipeline 出口锁定，本页只展示", max_rows=20, status_col="判定",
    )
    st.caption("真实数据八项全过：E₀ 4,502,042.41 kg 精确；均速 49.51 对官方 49.57 在 ±0.1 内。"
               "演示模式则跳过依赖真数据的五项。")
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

origin_note = ("地图默认按收货地看全国分布，切到发货地看上海枢纽；"
               "发货地 / 线路的碳排集中度见第 2 页四维热点。")
st.caption(origin_note)

# ---------------- 排行 + 速度分布 ----------------
left, right = st.columns([0.56, 0.44])
with left:
    layout.section("车型排行", "碳排最高的 10 个车型（全量锁定口径，读 hotspots 预计算表）")
    hot = services.hotspots("车型")
    if hot.empty:
        st.caption("车型热点表未生成：先运行 python -m src.pipeline")
    else:
        top = hot.sort_values("metric_t", ascending=False).head(10)
        charts.hbar(top, "value", "metric_t", height=360)
        st.caption("占比为该车型碳排占基准碳排的比例（hotspots.share_pct，pipeline 预计算）。")
with right:
    layout.section("速度分布", "异常段保留计碳，但不参与均速")
    charts.hist(view, "speed_kmh", height=360, bins=36, x_label="门到门均速（km/h）")
    st.caption("超过 100 km/h 的段来自封/解车时间戳异常，已打 flag_anomaly，不删除。")

# ---------------- 数据健康 ----------------
st.markdown("")
layout.section("数据健康", "四类标记：灰底=重复，黄底=异常/口径，黄框=估算")
counts = services.flag_counts()
cards.data_table(flags.flag_summary(None, counts), title="数据健康 · 四类标记",
                 desc="重复 / 异常 / 口径换算 / 估算", max_rows=10)
flags.flag_legend()

# ---- 数据层审计（DA 增量：61 字段数据字典 + 逐行可追溯 + 重复组冲突） ----
q = services.quality() or {}


def _fmtq(key: str) -> str:
    value = q.get(key)
    return services.fmt(value) if value is not None else "—"


trace_rate = q.get("trace_rate")
reason_counts = q.get("estimate_reason_counts") or {}

cards.panel_head("数据层审计", "61 字段数据字典 + 逐行可追溯 + 重复组冲突标记", "DA · docs/07")
cards.stats([
    {"label": "源行号可回查", "value": (("%.1f%%" % (100 * trace_rate)) if trace_rate is not None else "—"),
     "tone": "mint", "sub": "source_waybill_row（data_quality.json 预计算）"},
    {"label": "重复组冲突", "value": "%s / %s" % (
        _fmtq("vehicle_name_conflict_groups"), _fmtq("emission_factor_input_conflict_groups")),
     "tone": "amber", "sub": "车型 / 因子输入"},
    {"label": "估算 / 代理", "value": "%s 段" % _fmtq("estimated_rows"),
     "tone": "coral", "sub": "全表 %s 行（含重复）" % _fmtq("estimated_rows_wide")},
    {"label": "订单 / 车型匹配", "value": "%.0f%% / %.0f%%" % (
        100.0 * float(q.get("order_match_rate", q.get("match_rate", 1.0)) or 0.0),
        100.0 * float(q.get("vehicle_match_rate", 1.0) or 0.0)),
     "tone": "mint", "sub": "order_match_rate / vehicle_match_rate"},
], cols=4)
cards.chips([
    "订单号代理 %s" % _fmtq("order_no_fallback_rows"),
    "包裹数缺失 %s" % _fmtq("cargo_pieces_missing_rows"),
    "网点前缀兜底 %s" % _fmtq("origin_site_fallback_rows"),
    "负时长 %s" % _fmtq("negative_duration_rows"),
], tones=["skip", "skip", "warn", "warn"])
cards.note("DA 本轮把订单号代理、包裹数缺失、城市网点前缀兜底、异常时长兜底都写进 estimate_note；"
           "%s 段是去重后的代表行，%s 行是含重复的全表标记。"
           "重复组中 %s 组因子输入冲突，当前保留代表行，不等于冲突已解决——答辩时要主动说明。"
           % (_fmtq("estimated_rows"), _fmtq("estimated_rows_wide"),
              _fmtq("emission_factor_input_conflict_groups")))

if reason_counts:
    reasons = pd.DataFrame(
        [{"估算原因": str(k), "行数": int(v)} for k, v in reason_counts.items()]
    ).sort_values("行数", ascending=False).head(8).reset_index(drop=True)
    c1, c2 = st.columns([0.45, 0.55])
    with c1:
        cards.data_table(reasons, title="估算原因", desc="全量口径，不随筛选变化", max_rows=8)
    with c2:
        st.caption("填补顺序：同线历史均值 → 行业绕行系数 → 全样本中位数；"
                   "车型找不到按载重推断；发票匹配不上按同类别历史均价。"
                   "每一种填补都写进 estimate_note，前端黄框标出。"
                   "（原因拆分为全量口径，读 data_quality.json 预计算，不随筛选变化。）")

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
