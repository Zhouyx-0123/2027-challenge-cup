"""展示组件（A1 扩展）：纯 HTML 构件，供 3–5 页复用。

约束：
- 只做展示，不算业务（页面的展示级换算在各页自己写并标口径）；
- 所有输入先 html.escape，防止数据里的尖括号把布局撑坏；
- 不引外部资源，断网可演示。
"""

from __future__ import annotations

import html
from typing import Any, Dict, Iterable, List, Optional

TONE = {"mint", "amber", "coral", "red", "info", "ok", "warn", "bad", "skip", "main", "plain"}


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def esc(value: Any) -> str:
    """公开转义工具：页面拼 formula / panel_head 等富文本时，先过一遍再插入。"""
    return html.escape(str(value), quote=True)


def panel_head(title: str, desc: str = "", right: str = "") -> None:
    """面板头：标题 + 说明 + 右侧附加信息。与下方 Streamlit 原生内容配套使用。"""
    import streamlit as st
    right_html = ("<span class='r'>" + _esc(right) + "</span>") if right else ""
    st.markdown(
        "<div class='cm-panel-h'><span class='t'>" + _esc(title) + "</span>"
        + ("<span class='d'>" + _esc(desc) + "</span>" if desc else "")
        + right_html + "</div>",
        unsafe_allow_html=True,
    )


def stats(items: List[Dict[str, Any]], cols: int = 4) -> None:
    """统计网格。items: {label, value, sub, tone}。tone ∈ mint/amber/coral/red/info。"""
    import streamlit as st
    cells = []
    for item in items:
        tone = item.get("tone", "plain")
        tone_cls = (" " + tone) if tone in TONE and tone != "plain" else ""
        cells.append(
            "<div class='cm-stat" + tone_cls + "'>"
            "<div class='l'>" + _esc(item.get("label", "")) + "</div>"
            "<div class='v'>" + _esc(item.get("value", "—")) + "</div>"
            + ("<div class='s'>" + _esc(item.get("sub", "")) + "</div>" if item.get("sub") else "")
            + "</div>"
        )
    grid = "grid-template-columns: repeat(" + str(max(1, cols)) + ", 1fr);"
    st.markdown(
        "<div class='cm-stats' style='" + grid + "'>" + "".join(cells) + "</div>",
        unsafe_allow_html=True,
    )


def chips(items: Iterable[Any], tones: Optional[List[str]] = None) -> None:
    """一行 chip。items 为文本；tones 可给每个 chip 指定色系。"""
    import streamlit as st
    tones = list(tones or [])
    spans = []
    for i, item in enumerate(items):
        tone = tones[i] if i < len(tones) and tones[i] in TONE else ""
        cls = ("cm-chip " + tone) if tone else "cm-chip"
        spans.append("<span class='" + cls + "'>" + _esc(item) + "</span>")
    st.markdown("<div style='margin:2px 0 6px 0'>" + "".join(spans) + "</div>",
                unsafe_allow_html=True)


def kv_table(rows: List[Dict[str, Any]]) -> None:
    """KV 表：rows: {k, v, tone?, big?}。"""
    import streamlit as st
    body = ["<table class='cm-kv'>"]
    for row in rows:
        v = row.get("v", "—")
        if row.get("big"):
            v = "<span class='big'>" + _esc(v) + "</span>"
        else:
            v = _esc(v)
        body.append("<tr><td class='k'>" + _esc(row.get("k", "")) + "</td>"
                    + "<td class='v'>" + v + "</td></tr>")
    body.append("</table>")
    st.markdown("".join(body), unsafe_allow_html=True)


def note(text: str, sub: str = "") -> None:
    """灰色小注。"""
    import streamlit as st
    st.markdown(
        "<div class='cm-note'>" + _esc(text)
        + ("<br>" + _esc(sub) if sub else "") + "</div>",
        unsafe_allow_html=True,
    )


def formula(text: str) -> None:
    """公式条。text 支持 <span class='hl'>…</span> 高亮（调用方自行转义）。"""
    import streamlit as st
    st.markdown("<div class='cm-formula'>" + text + "</div>", unsafe_allow_html=True)


def steps(items: List[Dict[str, Any]], active: int = 0) -> None:
    """决策闭环步骤条。items: {n, t, d}；active 为高亮下标（-1 全不高亮）。"""
    import streamlit as st
    parts = []
    for i, item in enumerate(items):
        on = " on" if i == active else ""
        parts.append(
            "<div class='cm-step" + on + "'>"
            "<span class='n'>" + _esc(item.get("n", "")) + "</span>"
            "<span class='t'>" + _esc(item.get("t", "")) + "</span>"
            + ("<span class='d' style='display:block;font-size:11px;margin-top:2px'>"
               + _esc(item.get("d", "")) + "</span>" if item.get("d") else "")
            + "</div>"
        )
        if i < len(items) - 1:
            parts.append("<div class='cm-step-arrow'>→</div>")
    st.markdown("<div class='cm-steps'>" + "".join(parts) + "</div>",
                unsafe_allow_html=True)


def trace_chain(nodes: List[Dict[str, Any]]) -> None:
    """追溯链。nodes: {t, d, tone?}，tone ∈ amber/muted。"""
    import streamlit as st
    out = ["<div class='cm-trace'>"]
    for i, node in enumerate(nodes):
        tone = node.get("tone", "")
        dot_cls = "dot" + ((" " + tone) if tone in ("amber", "muted") else "")
        out.append(
            "<div class='node'><span class='" + dot_cls + "'></span>"
            "<div class='body'><span class='t'>" + _esc(node.get("t", "")) + "</span>"
            + ("<br><span class='d'>" + _esc(node.get("d", "")) + "</span>"
               if node.get("d") else "")
            + "</div></div>"
        )
        if i < len(nodes) - 1:
            out.append("<div class='link'></div>")
    out.append("</div>")
    st.markdown("".join(out), unsafe_allow_html=True)


def empty_state(title: str, hint: str = "") -> None:
    import streamlit as st
    st.markdown(
        "<div class='cm-empty'><div class='t'>" + _esc(title) + "</div>"
        + ("<div style='margin-top:6px'>" + _esc(hint) + "</div>" if hint else "")
        + "</div>",
        unsafe_allow_html=True,
    )


def htable(headers: List[str], rows: List[List[Any]], row_cls: Optional[List[str]] = None,
           num_cols: Optional[Iterable[int]] = None) -> None:
    """通用 HTML 表。headers: 列名；rows: 字符串/已转义片段；row_cls: 每行附加类。"""
    import streamlit as st
    num_cols = set(num_cols or [])
    head = "".join(
        "<th class='" + ("num" if i in num_cols else "") + "'>" + _esc(h) + "</th>"
        for i, h in enumerate(headers))
    body = []
    for r, row in enumerate(rows):
        cls = (row_cls[r] if row_cls and r < len(row_cls) and row_cls[r] else "")
        tds = []
        for i, cell in enumerate(row):
            tds.append("<td class='" + ("num" if i in num_cols else "") + "'>"
                       + _esc(cell) + "</td>")
        body.append("<tr" + ((" class='" + cls + "'") if cls else "") + ">"
                    + "".join(tds) + "</tr>")
    st.markdown(
        "<div style='overflow-x:auto'><table class='cm-htable'><thead><tr>"
        + head + "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>",
        unsafe_allow_html=True,
    )
