# -*- coding: utf-8 -*-
"""情景演算页共用组件（第 7/8 页）：假设值黄框、结论头条、节省计量条、分解图。

边界（docs/20 §11）：
- 本组件**只做展示**：所有数字由调用方从 services（→ src/scenarios.py）取得后传入；
- 颜色/字体走 theme 与 charts 的既有约定，断网可渲染（纯 HTML + Plotly 本地渲染）；
- 假设值一律黄框标注（工程师建议 3 的延伸：估算值黄框，假设值同等待遇）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import plotly.graph_objects as go
import streamlit as st

from . import cards, charts

# 分解图四组件配色（与 theme 的 info/accent/accent-2/coral 对齐）
_COMP_COLORS = {
    "vehicle_net_residual": "#6FB1FF",
    "energy": "#5EE7FF",
    "maintenance": "#FFC46B",
    "insurance": "#FF8A5B",
}
_COMP_NAMES = {
    "vehicle_net_residual": "购置（净残值）",
    "energy": "能耗",
    "maintenance": "维护",
    "insurance": "保险",
}


def assumption_note(items: List[str], title: str = "假设值（黄框=非实测，可改）") -> None:
    """假设值清单：黄框逐条列出，评委一眼能看到哪些数字不是实测。"""
    if not items:
        return
    rows = "".join(
        "<li style='margin:2px 0'>" + cards.esc(x) + "</li>" for x in items)
    st.markdown(
        "<div style='border-left:3px solid var(--accent-2);"
        "background:rgba(245,200,107,0.08);border-radius:0 8px 8px 0;"
        "padding:10px 14px;margin:6px 0'>"
        "<div style='font-size:13px;font-weight:500;color:var(--accent-2)'>"
        + cards.esc(title) + "</div>"
        "<ul style='margin:6px 0 0 0;padding-left:18px;font-size:12px;"
        "color:var(--muted);line-height:1.7'>" + rows + "</ul></div>",
        unsafe_allow_html=True,
    )


def verdict(text: str, tone: str = "mint") -> None:
    """结论头条（ChargeMath 的一句话结论）：大字 + 高亮数字由调用方嵌入。"""
    color = {"mint": "var(--accent)", "amber": "var(--accent-2)",
             "coral": "var(--coral)"}.get(tone, "var(--accent)")
    st.markdown(
        "<div style='font-size:19px;font-weight:500;line-height:1.5;"
        "color:var(--text);margin:4px 0 10px 0'>"
        "<span style='color:" + color + "'>■</span> " + text + "</div>",
        unsafe_allow_html=True,
    )


def savings_meter(ice_label: str, bev_label: str, ice_value: float,
                  bev_value: float, unit: str = "万元/年") -> None:
    """节省计量条（ChargeMath live savings meter）：双横条 + 差额标注。

    ice_value / bev_value 为同单位数值（调用方已换算）；差额自动计算。
    """
    total = max(abs(ice_value) + abs(bev_value), 1e-9)
    w_ice = 100.0 * abs(ice_value) / total
    w_bev = 100.0 * abs(bev_value) / total
    gap = ice_value - bev_value
    st.markdown(
        "<div style='margin:8px 0 4px 0'>"
        "<div style='display:flex;justify-content:space-between;font-size:12px;"
        "color:var(--muted);margin-bottom:4px'>"
        "<span>" + cards.esc(ice_label) + "</span><span>"
        + cards.esc(bev_label) + "</span></div>"
        "<div style='background:var(--panel-2,rgba(24,32,43,0.88));border-radius:8px;"
        "padding:10px 12px'>"
        "<div style='display:flex;align-items:center;gap:10px;margin:4px 0'>"
        "<div style='flex:1;height:14px;background:rgba(255,138,91,0.18);border-radius:4px;overflow:hidden'>"
        "<div style='width:%.1f%%;height:100%%;background:var(--coral)'></div></div>"
        "<div style='width:110px;text-align:right;font-size:13px;color:var(--text)'>%.1f %s</div>"
        "</div>"
        "<div style='display:flex;align-items:center;gap:10px;margin:4px 0'>"
        "<div style='flex:1;height:14px;background:rgba(45,212,167,0.16);border-radius:4px;overflow:hidden'>"
        "<div style='width:%.1f%%;height:100%%;background:var(--accent)'></div></div>"
        "<div style='width:110px;text-align:right;font-size:13px;color:var(--text)'>%.1f %s</div>"
        "</div>"
        "<div style='margin-top:8px;font-size:13px;color:var(--accent-2)'>"
        "差额 %.1f %s（燃油 − 电动）</div>"
        "</div></div>"
        % (w_ice, ice_value, cards.esc(unit), w_bev, bev_value, cards.esc(unit),
           gap, cards.esc(unit)),
        unsafe_allow_html=True,
    )


def npv_stacked(npv_by_scenario: Dict[str, Dict[str, float]],
                labels: Optional[Dict[str, str]] = None,
                title: str = "NPV 四分解（元/辆）", height: int = 300) -> None:
    """NPV 分解对比：横向堆叠条（每场景一根：购置净残值/能耗/维护/保险）。"""
    labels = labels or {}
    names = list(npv_by_scenario.keys())
    fig = go.Figure()
    for comp in ("vehicle_net_residual", "energy", "maintenance", "insurance"):
        values = [npv_by_scenario[n].get(comp, 0.0) / 1e4 for n in names]  # 万元
        fig.add_trace(go.Bar(
            name=_COMP_NAMES[comp], y=names, x=values, orientation="h",
            marker_color=_COMP_COLORS[comp],
            hovertemplate="%{y}｜" + _COMP_NAMES[comp] + "：%{x:.1f} 万元<extra></extra>",
        ))
    fig.update_layout(**charts.BASE, height=height, barmode="stack",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                  xanchor="left", x=0, font=dict(size=11)))
    fig.update_xaxes(**charts.AXIS, title="万元/辆（NPV）")
    fig.update_yaxes(**charts.AXIS)
    fig.update_layout(title=dict(text=title, font=dict(size=13, color="#F3F5F8"),
                                 x=0.01, y=0.97))
    st.plotly_chart(fig, width="stretch")


def cumulative_chart(cumulative: List[Dict[str, Any]],
                     name_map: Optional[Dict[str, str]] = None,
                     title: str = "车队累计成本（万元，名义口径）",
                     height: int = 320) -> None:
    """累计成本曲线（FleetTTO cumulative fleet cost over time）。"""
    import pandas as pd

    name_map = name_map or {}
    rows = []
    for point in cumulative:
        for key, value in point.items():
            if key == "year":
                continue
            rows.append({"year": int(point["year"]),
                         "key": name_map.get(key, key),
                         "cost_wan": float(value) / 1e4})
    df = pd.DataFrame(rows)
    charts.line_multi(df, x="year", y="cost_wan", color="key",
                      title=title, height=height)


def param_slider(label: str, min_value: float, max_value: float, value: float,
                 step: float = 1.0, help_text: str = "",
                 fmt: str = "%.1f", assumed: bool = False) -> float:
    """参数滑块：assumed=True 时标签带黄点（假设值标记）。

    Streamlit 要求 value/min/max 同类：整数边界走整数，小数边界统一转 float。
    """
    if (isinstance(min_value, int) and isinstance(max_value, int)
            and isinstance(step, int)):
        value = int(value)
    else:
        min_value, max_value, step = float(min_value), float(max_value), float(step)
        value = float(value)
    suffix = " <span style='color:var(--accent-2)'>●</span>" if assumed else ""
    st.markdown("<div style='font-size:13px;margin-bottom:2px'>"
                + cards.esc(label) + suffix + "</div>", unsafe_allow_html=True)
    return st.slider(label, min_value=min_value, max_value=max_value,
                     value=value, step=step, help=help_text or None,
                     label_visibility="collapsed", format=fmt)


def result_json(params: Dict[str, Any], result: Dict[str, Any]) -> None:
    """下载本次测算（参数 + 结果），答辩后可追溯。"""
    import json

    payload = {"params": params, "result": result,
               "note": "情景演算结果，非对拍口径（docs/20 §11）"}
    st.download_button(
        "下载本次测算 JSON",
        data=json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
        file_name="what_if_result.json", mime="application/json",
        width="stretch")
