"""首页：一句话 + 全局指标 + 五页导航 + 数据状态。"""

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import streamlit as st

import services
from components import layout, flags, theme

layout.setup("碳感知智能决策系统")
layout.sidebar_params()

k = services.kpis()
mock = services.is_mock()
badges = ["演示数据"] if mock else ["实测数据"]
badges += ["段级计量", "双轨对账", "可审计碳账"]

theme.hero(
    "物流企业碳感知智能决策系统",
    "给它一批运单，它告诉你这条线该派什么车、多花多少钱、碳账怎么记。",
    kicker="CARBON DECISION SYSTEM",
    badges=badges,
)

total_t = (k.get("total_kg") / 1000.0) if k.get("total_kg") else None
layout.kpi_row([
    {"label": "总碳排放（tCO₂e）", "value": services.fmt(total_t, 1), "help": "数据期基准 E₀"},
    {"label": "总里程（万 km）", "value": services.fmt((k.get("total_km") or 0) / 10000.0, 1)},
    {"label": "独立路段", "value": services.fmt(k.get("segments"))},
    {"label": "门到门均速（km/h）", "value": services.fmt(k.get("avg_speed"), 1)},
    {"label": "重型车碳占比", "value": (("%.1f%%" % (100 * k["heavy_share"])) if k.get("heavy_share") is not None else "—")},
    {"label": "异常行（保留）", "value": services.fmt(k.get("anomaly_rows"))},
], per_row=6)

st.markdown("")
layout.section("五页导航", "一个页面回答一个问题，每页底部都有一句结论")

c1, c2, c3 = st.columns(3)
with c1:
    st.markdown("**① 基准核算**\n\n排了多少碳、跑多远、数据多脏")
    st.markdown("**② 高排放诊断**\n\n空间在哪：四维热点 + 同线带宽")
with c2:
    st.markdown("**③ 替代方案**\n\n这条线该派什么车，运营跑不跑通")
    st.markdown("**④ 成本效益**\n\n多花多少钱、几年回本")
with c3:
    st.markdown("**⑤ 碳会计台账**\n\n怎么记、怎么披露、对接什么制度")
    st.caption("③④⑤ 由杨盛文开发，接口已就绪")

st.markdown("")
layout.section("数据状态", "前端只读结果表；缺表不报错，只提示")

avail = services.available()
st.dataframe(
    [{"结果表": name, "文件": services.RESULT_FILES[name],
      "状态": ("已就绪" if ok else "未生成")}
     for name, ok in avail.items()],
    use_container_width=True, hide_index=True,
)

seg = services.segments()
if not seg.empty:
    st.markdown("")
    layout.section("数据健康", "灰底 = 重复行；黄底 = 异常或口径换算")
    st.dataframe(flags.flag_summary(seg), use_container_width=True, hide_index=True)
    flags.flag_legend()

layout.conclusion("系统把每一段运输翻译成可核查的碳排与成本，并把结论落成一张能披露的账。")
