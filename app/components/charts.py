"""图表封装：统一 Plotly 暗色风格。页面只调这四个函数，不直接写 px。"""

from __future__ import annotations

from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ACCENT = "#2DD4A7"
ACCENT2 = "#F5C86B"
SCALE = ["#0E3B2E", "#1C8F6B", ACCENT, ACCENT2, "#FF8A5B"]

BASE = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(size=12, color="#C9D4E0"),
    margin=dict(l=8, r=8, t=28, b=8),
    coloraxis_showscale=False,
    hoverlabel=dict(bgcolor="#131A23", bordercolor="#2DD4A7", font_size=12),
)

AXIS = dict(
    showgrid=True, gridcolor="rgba(255,255,255,0.06)", zeroline=False,
    showline=False, ticks="outside", tickcolor="rgba(255,255,255,0.15)",
)


def _finish(fig: go.Figure, title: Optional[str], height: int) -> go.Figure:
    fig.update_layout(**BASE, height=height)
    fig.update_xaxes(**AXIS)
    fig.update_yaxes(**AXIS)
    if title:
        fig.update_layout(title=dict(text=title, font=dict(size=13, color="#E6EDF3"), x=0.01, y=0.97))
    return fig


def bar(df: pd.DataFrame, x: str, y: str, title: Optional[str] = None, height: int = 320) -> None:
    if df is None or df.empty or x not in df.columns or y not in df.columns:
        st.caption("暂无数据")
        return
    data = df[[x, y]].dropna()
    fig = px.bar(data, x=x, y=y, color=y, color_continuous_scale=SCALE)
    fig.update_traces(marker_line_width=0)
    st.plotly_chart(_finish(fig, title, height), use_container_width=True,
                    config={"displayModeBar": False})


def hbar(df: pd.DataFrame, y: str, x: str, title: Optional[str] = None, height: int = 340) -> None:
    """横向条形：排行类图表用它，标签好读。"""
    if df is None or df.empty or x not in df.columns or y not in df.columns:
        st.caption("暂无数据")
        return
    data = df[[x, y]].dropna().sort_values(x, ascending=True)
    fig = px.bar(data, x=x, y=y, orientation="h", color=x, color_continuous_scale=SCALE)
    fig.update_traces(marker_line_width=0)
    fig.update_layout(yaxis=dict(showgrid=False))
    st.plotly_chart(_finish(fig, title, height), use_container_width=True,
                    config={"displayModeBar": False})


def line(df: pd.DataFrame, x: str, y: str, title: Optional[str] = None, height: int = 300) -> None:
    if df is None or df.empty or x not in df.columns or y not in df.columns:
        st.caption("暂无数据")
        return
    fig = px.line(df[[x, y]].dropna(), x=x, y=y)
    fig.update_traces(line=dict(color=ACCENT, width=2.4), marker=dict(size=6, color=ACCENT2))
    st.plotly_chart(_finish(fig, title, height), use_container_width=True,
                    config={"displayModeBar": False})


def scatter(df: pd.DataFrame, x: str, y: str, title: Optional[str] = None, height: int = 320) -> None:
    if df is None or df.empty or x not in df.columns or y not in df.columns:
        st.caption("暂无数据")
        return
    fig = px.scatter(df[[x, y]].dropna(), x=x, y=y, opacity=0.55)
    fig.update_traces(marker=dict(size=7, color=ACCENT, line=dict(width=0)))
    st.plotly_chart(_finish(fig, title, height), use_container_width=True,
                    config={"displayModeBar": False})
