"""版式组件（A1）：页头、指标卡、结论带、空状态、全局参数。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import streamlit as st

from . import theme

ACCENT = "#1F3A5F"
SOFT = "#F4F7FA"


def setup(title: str, icon: str = "📦") -> None:
    """页面配置。每个页面第一行调用，必须在其他 st 调用之前。"""
    st.set_page_config(
        page_title=title,
        page_icon=icon,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    theme.inject()


def page_header(title: str, question: Optional[str] = None, badge: Optional[str] = None) -> None:
    """页头：标题 + 一句话问题 + 右上角状态标签。"""
    left, right = st.columns([0.8, 0.2])
    with left:
        st.markdown("## " + title)
        if question:
            st.caption(question)
    with right:
        if badge:
            st.markdown(
                "<div style='text-align:right;color:#7a8794;font-size:12px;padding-top:14px'>"
                + badge
                + "</div>",
                unsafe_allow_html=True,
            )
    st.markdown("---")


def kpi_row(items: List[Dict[str, Any]], per_row: int = 4) -> None:
    """一行指标卡。items 里每项：{label, value, help}。"""
    if not items:
        return
    for start in range(0, len(items), per_row):
        chunk = items[start:start + per_row]
        cols = st.columns(len(chunk))
        for col, item in zip(cols, chunk):
            with col:
                st.metric(
                    label=str(item.get("label", "")),
                    value=str(item.get("value", "—")),
                    help=item.get("help"),
                )


def section(title: str, desc: Optional[str] = None) -> None:
    theme.section(title, desc or "")


def conclusion(text: str) -> None:
    """页脚结论带。每页必须有一句，答辩照着念。"""
    theme.conclusion(text)


def demo_note(reason: str = "") -> None:
    theme.demo_note(reason)


def source_badge(source: str) -> str:
    return "实测数据" if source == "real" else "演示数据"


def gate_badge(gate: Optional[Dict[str, Any]]) -> str:
    if not gate:
        return "闸门未运行"
    if gate.get("mode") == "error":
        return "闸门：流水线中断"
    if gate.get("passed"):
        return "闸门通过" if gate.get("mode") == "real" else "闸门通过（演示）"
    return "闸门未通过"


def empty_state(need: List[str]) -> None:
    """缺表时的空状态：不报错，告诉对方跑什么。"""
    st.warning("这张表还没生成——先运行  python -m src.pipeline")
    if need:
        st.caption("缺少：" + " / ".join(need))


def sidebar_params(source_name: str = "unknown",
                   gate: Optional[Dict[str, Any]] = None,
                   current: str = "") -> Dict[str, Any]:
    """全局参数：λ 与成本口径。全站只有这一处，其他页面读 session_state。

    current 是页面 3–5 的导航参数；当前布局不强制高亮，保留接口以兼容。
    """
    with st.sidebar:
        st.markdown("### 全局参数")
        lam = st.number_input(
            "内部碳价 λ（元/吨 CO₂e）",
            min_value=0.0,
            max_value=1000.0,
            value=float(st.session_state.get("lam", 100.0)),
            step=10.0,
            help="全站唯一的定价旋钮，第 3–5 页共用。",
        )
        basis = st.radio(
            "成本口径",
            ["赛题口径", "碳价延展口径"],
            index=0 if st.session_state.get("basis", "赛题口径") == "赛题口径" else 1,
            help="赛题口径＝购置+能耗+维护−补贴；延展口径＝排放量 × λ。",
        )
        st.markdown("---")
        st.caption("数据源：" + source_badge(source_name))
        if gate is not None:
            st.caption(gate_badge(gate))
        if st.button("刷新结果表", width="stretch",
                     help="只清缓存、重新读表；不会调用任何算法。"):
            st.cache_data.clear()
            st.rerun()
        st.caption("只读 data/processed；页面不调用核心算法")
    st.session_state["lam"] = lam
    st.session_state["basis"] = basis
    return {"lam": lam, "basis": basis}
