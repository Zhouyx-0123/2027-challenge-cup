"""版式组件（A1）：品牌侧栏、页头、指标卡、结论带、空状态、全局参数。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import streamlit as st

from . import theme

# 五页主线 + 一个实验室预览页
_NAV_MAIN = [
    ("app.py", "总览"),
    ("pages/1_基准核算.py", "01  基准核算"),
    ("pages/2_高排放诊断.py", "02  高排放诊断"),
    ("pages/3_替代方案.py", "03  替代方案"),
    ("pages/4_成本效益.py", "04  成本效益"),
    ("pages/5_碳会计台账.py", "05  碳会计台账"),
]
_NAV_LAB = [
    ("pages/6_路线沙盘.py", "06  路线沙盘"),
]


def setup(title: str, icon: str = "◈") -> None:
    """页面配置。每个页面第一行调用，必须在其他 st 调用之前。"""
    st.set_page_config(
        page_title=title,
        page_icon=icon,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    theme.inject()


def page_header(title: str, question: Optional[str] = None, badge: Optional[str] = None) -> None:
    """兼容旧页面的页头；新页面统一用 theme.hero。"""
    left, right = st.columns([0.8, 0.2])
    with left:
        st.markdown("## " + title)
        if question:
            st.caption(question)
    with right:
        if badge:
            st.markdown(
                "<div style='text-align:right;color:#63707C;font-size:11px;"
                "letter-spacing:.08em;padding-top:14px'>" + badge + "</div>",
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


def _safe_page_link(page: str, label: str) -> None:
    """真实多页环境可点；单页 AppTest 里退化为文本，不红屏。"""
    try:
        st.page_link(page, label=label)
    except Exception:
        st.markdown("<div class='cm-navitem'>" + label + "</div>", unsafe_allow_html=True)


def _brand() -> None:
    st.markdown(
        "<div class='cm-brand'>"
        "<div class='cm-brand-mark'>CI</div>"
        "<div class='cm-brand-text'>"
        "<div class='eyebrow'>CARBON INTELLIGENCE</div>"
        "<div class='name'>物流碳决策台</div>"
        "<div class='sub'>2027 · DECISION OS</div>"
        "</div></div>",
        unsafe_allow_html=True,
    )


def _nav() -> None:
    st.markdown("<div class='cm-navlabel'>决策链 / DECISION LOOP</div>", unsafe_allow_html=True)
    for page, label in _NAV_MAIN:
        _safe_page_link(page, label)
    st.markdown("<div class='cm-navlabel'>实验室 / LAB</div>", unsafe_allow_html=True)
    for page, label in _NAV_LAB:
        _safe_page_link(page, label)


def _status(source_name: str, gate: Optional[Dict[str, Any]]) -> None:
    ok = bool(gate and gate.get("passed"))
    dot = "ok" if ok else "warn"
    source = source_badge(source_name)
    gate_text = gate_badge(gate)
    st.markdown(
        "<div class='cm-side-status'>"
        "<div><span class='" + dot + "'>●</span> <b>" + source + "</b></div>"
        "<div>" + gate_text + "</div>"
        "<div>数据期 2023 · 120 天</div>"
        "</div>",
        unsafe_allow_html=True,
    )


def sidebar_params(source_name: str = "unknown",
                   gate: Optional[Dict[str, Any]] = None,
                   current: str = "") -> Dict[str, Any]:
    """品牌 + 决策链导航 + 全局参数；current 仅保留兼容。"""
    with st.sidebar:
        _brand()
        _nav()
        st.markdown("<div class='cm-navlabel'>状态 / STATUS</div>", unsafe_allow_html=True)
        _status(source_name, gate)
        st.markdown("<div class='cm-navlabel'>全局参数 / PARAMETERS</div>", unsafe_allow_html=True)
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
        if st.button("刷新结果表", use_container_width=True,
                     help="只清缓存、重新读表；不会调用任何算法。"):
            st.cache_data.clear()
            st.rerun()
        st.caption("只读 data/processed；页面不调用核心算法")
    st.session_state["lam"] = lam
    st.session_state["basis"] = basis
    return {"lam": lam, "basis": basis}
