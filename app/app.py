"""首页：项目一句话 + 七个数字 + 五页导航 + 数据健康。"""

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import streamlit as st

import services
from components import layout, flags

layout.setup("碳感知智能决策系统")
layout.page_header(
    "物流企业碳感知智能决策系统",
    "给它一批运单，它告诉你这条线该派什么车、多花多少钱、碳账怎么记。",
    badge="v0.1 · 开发中",
)
params = layout.sidebar_params()

k = services.kpis()
total_t = (k.get("total_kg") / 1000.0) if k.get("total_kg") else None

layout.kpi_row([
    {"label": "总碳排放（t）", "value": services.fmt(total_t, 2), "help": "数据期基准 E₀"},
    {"label": "总里程（km）", "value": services.fmt(k.get("total_km"), 2)},
    {"label": "独立路段", "value": services.fmt(k.get("segments"))},
    {"label": "门到门均速（km/h）", "value": services.fmt(k.get("avg_speed"), 2)},
])

st.markdown("")
layout.kpi_row([
    {"label": "重型车碳占比", "value": (("%.1f%%" % (100 * k["heavy_share"])) if k.get("heavy_share") is not None else "—")},
    {"label": "重复行（已去重）", "value": services.fmt(k.get("dup_rows"))},
    {"label": "时间戳异常（保留）", "value": services.fmt(k.get("anomaly_rows"))},
    {"label": "当前 λ（元/t）", "value": services.fmt(params.get("lam"), 0), "help": "侧边栏可调，全站共用"},
])

st.markdown("---")
layout.section("数据状态", "前端只读结果表；缺表不会报错，只会提示。")
avail = services.available()
st.dataframe(
    [{"结果表": name, "文件": services.RESULT_FILES[name], "状态": ("已就绪" if ok else "未生成")}
     for name, ok in avail.items()],
    use_container_width=True,
    hide_index=True,
)

st.markdown("---")
layout.section("五个页面", "一个页面回答一个问题；每页底部都有一句结论，答辩照着念。")
st.markdown(
    "| 页面 | 回答的问题 |\n"
    "|---|---|\n"
    "| 1 基准核算 | 现在排了多少、跑多远、数据多脏 |\n"
    "| 2 高排放诊断 | 减排空间在哪，同线差多少 |\n"
    "| 3 替代方案 | 这条线该派什么车，运营跑得通吗 |\n"
    "| 4 成本效益 | 多花多少钱，几年回本 |\n"
    "| 5 碳会计台账 | 怎么记，怎么披露 |"
)

st.markdown("---")
layout.section("数据健康", "灰底 = 重复行；黄底 = 异常或口径换算。")
seg = services.segments()
if seg.empty:
    layout.empty_state(["baseline_carbon.parquet"])
else:
    st.dataframe(flags.flag_summary(seg), use_container_width=True, hide_index=True)
    flags.flag_legend()

layout.conclusion(
    "系统把每一段运输翻译成可核查的碳排与成本，并把结论落成一张能披露的账。"
)
