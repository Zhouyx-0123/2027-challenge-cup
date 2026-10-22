"""图表封装：统一 Plotly 暗色风格。页面只调这里的函数，不直接写 px。

口径：所有图表只消费结果表字段（或页面已标“展示口径”的换算列），
不做业务重算；颜色走 SCALE，断网可渲染（Plotly 本地渲染，无外部瓦片）。
"""

from __future__ import annotations

from typing import List, Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ACCENT = "#5EE7FF"
ACCENT2 = "#FFC46B"
SCALE = ["#08252B", "#0E6A78", "#5EE7FF", ACCENT, ACCENT2]

BASE = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(size=12, color="#98A3B3", family="Inter, Segoe UI, Microsoft YaHei, sans-serif"),
    margin=dict(l=54, r=14, t=64, b=48),
    coloraxis_showscale=False,
    hoverlabel=dict(bgcolor="#11151C", bordercolor="#5EE7FF", font_size=12),
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
        fig.update_layout(title=dict(text=title, font=dict(size=13, color="#F3F5F8"), x=0.01, y=0.995, yanchor="top"))
    return fig


def bar(df: pd.DataFrame, x: str, y: str, title: Optional[str] = None, height: int = 320) -> None:
    if df is None or df.empty or x not in df.columns or y not in df.columns:
        st.caption("暂无数据")
        return
    data = df[[x, y]].dropna()
    fig = px.bar(data, x=x, y=y, color=y, color_continuous_scale=SCALE)
    fig.update_traces(marker_line_width=0)
    st.plotly_chart(_finish(fig, title, height), width="stretch",
                    config={"displayModeBar": False})


def hist(df: pd.DataFrame, x: str, title: Optional[str] = None, bins: int = 30,
         height: int = 300, x_label: Optional[str] = None) -> None:
    """直方图：速度分布、偏差分布这类“看形状”的图用它。"""
    if df is None or df.empty or x not in df.columns:
        st.caption("暂无数据")
        return
    data = pd.to_numeric(df[x], errors="coerce").dropna()
    if data.empty:
        st.caption("暂无数据")
        return
    fig = px.histogram(data, nbins=bins, color_discrete_sequence=[ACCENT])
    fig.update_traces(marker_line_width=0)
    fig.update_layout(bargap=0.06)
    if x_label:
        fig.update_xaxes(title_text=x_label)
    fig.update_yaxes(title_text="段数")
    st.plotly_chart(_finish(fig, title, height), width="stretch",
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
    st.plotly_chart(_finish(fig, title, height), width="stretch",
                    config={"displayModeBar": False})


def line(df: pd.DataFrame, x: str, y: str, title: Optional[str] = None, height: int = 300) -> None:
    if df is None or df.empty or x not in df.columns or y not in df.columns:
        st.caption("暂无数据")
        return
    fig = px.line(df[[x, y]].dropna(), x=x, y=y)
    fig.update_traces(line=dict(color=ACCENT, width=2.4), marker=dict(size=6, color=ACCENT2))
    st.plotly_chart(_finish(fig, title, height), width="stretch",
                    config={"displayModeBar": False})


def scatter(df: pd.DataFrame, x: str, y: str, title: Optional[str] = None, height: int = 320) -> None:
    if df is None or df.empty or x not in df.columns or y not in df.columns:
        st.caption("暂无数据")
        return
    fig = px.scatter(df[[x, y]].dropna(), x=x, y=y, opacity=0.55)
    fig.update_traces(marker=dict(size=7, color=ACCENT, line=dict(width=0)))
    st.plotly_chart(_finish(fig, title, height), width="stretch",
                    config={"displayModeBar": False})


def bubble(df: pd.DataFrame, x: str, y: str, size: str, color: Optional[str] = None,
           hover: Optional[List[str]] = None, title: Optional[str] = None,
           height: int = 380, ymax: Optional[float] = None) -> None:
    """气泡图：x/y 定位，size 定量，color 分类着色（达标/未达标）。"""
    if df is None or df.empty or any(c not in df.columns for c in (x, y, size)):
        st.caption("暂无数据")
        return
    keep = [x, y, size] + ([color] if color else [])
    keep += [c for c in (hover or []) if c not in keep]
    data = df[keep].dropna()
    if data.empty:
        st.caption("暂无数据")
        return
    palette = {"达标": ACCENT, "未达标": "#5C6875"}
    kwargs = dict(x=x, y=y, size=size, hover_data=hover or [x, y, size],
                  color_discrete_sequence=[ACCENT, "#5C6875"])
    if color and color in data.columns:
        data = data.copy()
        data[color] = data[color].astype(str)
        kwargs["color"] = color
        kwargs["color_discrete_map"] = {k: v for k, v in palette.items()}
    fig = px.scatter(data, **kwargs)
    fig.update_traces(marker=dict(line=dict(width=1, color="rgba(255,255,255,0.25)")),
                      opacity=0.9)
    fig.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                  xanchor="right", x=1, title_text=""))
    if ymax is not None:
        fig.update_yaxes(range=[0, ymax])
    st.plotly_chart(_finish(fig, title, height), width="stretch",
                    config={"displayModeBar": False})


def vbar_target(df: pd.DataFrame, x: str, y: str, target: Optional[float] = None,
                target_label: str = "达标线", title: Optional[str] = None,
                height: int = 340, color: Optional[str] = None) -> None:
    """纵向柱 + 达标线。color 列用于按状态着色（达标/未达标）。"""
    if df is None or df.empty or x not in df.columns or y not in df.columns:
        st.caption("暂无数据")
        return
    data = df[[x, y] + ([color] if color else [])].dropna()
    if data.empty:
        st.caption("暂无数据")
        return
    kwargs = dict(x=x, y=y)
    if color and color in data.columns:
        kwargs["color"] = color
        kwargs["color_discrete_map"] = {"达标": ACCENT, "未达标": "#3D4A57"}
    else:
        kwargs["color_discrete_sequence"] = [ACCENT]
    fig = px.bar(data, **kwargs)
    fig.update_traces(marker_line_width=0, width=0.62)
    if target is not None:
        fig.add_hline(y=target, line=dict(color=ACCENT2, width=1.4, dash="dash"),
                      annotation_text=target_label, annotation_font_color=ACCENT2,
                      annotation_position="top left", annotation_textangle=0)
    top = float(pd.to_numeric(data[y]).max())
    if target is not None:
        top = max(top, float(target))
    fig.update_yaxes(range=[0, max(top * 1.18, 1)])
    st.plotly_chart(_finish(fig, title, height), width="stretch",
                    config={"displayModeBar": False})


def line_multi(df: pd.DataFrame, x: str, y: str, color: str, vline_x: Optional[float] = None,
               vline_label: str = "", title: Optional[str] = None, height: int = 360,
               ymax: Optional[float] = None, labels: Optional[dict] = None) -> None:
    """多线图：color 列为系列名；vline_x 画当前值竖线（如当前 λ）。"""
    if df is None or df.empty or any(c not in df.columns for c in (x, y, color)):
        st.caption("暂无数据")
        return
    data = df[[x, y, color]].dropna()
    if data.empty:
        st.caption("暂无数据")
        return
    fig = px.line(data, x=x, y=y, color=color, labels=labels or {},
                  color_discrete_sequence=[ACCENT, ACCENT2, "#6FB1FF", "#FF8A5B",
                                           "#B58CFF", "#7BD0FF", "#FF7A7A"])
    fig.update_traces(line=dict(width=2.2))
    fig.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                  xanchor="right", x=1, title_text=""))
    if vline_x is not None:
        fig.add_vline(x=vline_x, line=dict(color="#F3F5F8", width=1.2, dash="dot"),
                      annotation_text=vline_label, annotation_font_color="#F3F5F8",
                      annotation_position="top")
    if ymax is not None:
        fig.update_yaxes(range=[0, ymax])
    st.plotly_chart(_finish(fig, title, height), width="stretch",
                    config={"displayModeBar": False})


def payback_curve(specs: List[dict], years: int = 15, height: int = 380) -> None:
    """回本曲线：累计节省 vs 一次性投资，交叉点即回本点。

    specs: [{name, capex_wan, annual_save_wan, is_main}]——annual_save_wan 已含 λ 口径换算。
    """
    if not specs:
        st.caption("暂无数据")
        return
    fig = go.Figure()
    xs = list(range(0, years + 1))
    seen_capex: List[float] = []
    for spec in specs:
        capex = float(spec["capex_wan"])
        annual = float(spec["annual_save_wan"])
        if annual <= 0:
            continue
        ys = [min(x * annual, capex * 1.35) for x in xs]
        color = ACCENT2 if spec.get("is_main") else ACCENT
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines", name=str(spec["name"]),
            line=dict(color=color, width=2.4 if spec.get("is_main") else 1.8),
            hovertemplate="第 %{x} 年｜累计节省 %{y:,.0f} 万元<extra>" + str(spec["name"]) + "</extra>",
        ))
        cross = capex / annual
        if cross <= years:
            fig.add_trace(go.Scatter(
                x=[cross], y=[capex], mode="markers",
                marker=dict(size=11, color=color, symbol="diamond",
                            line=dict(width=1.5, color="#07080A")),
                name="回本 %.1f 年" % cross, showlegend=False,
                hovertemplate="回本 %.1f 年<extra></extra>" % cross,
            ))
        if capex > 0 and capex not in seen_capex:
            seen_capex.append(capex)
            fig.add_hline(y=capex, line=dict(color="#FF7A7A", width=1.2, dash="dash"),
                          annotation_text="一次性投资 {:,.0f} 万元".format(capex),
                          annotation_font_color="#FFB3B3", annotation_position="top left")
    fig.update_xaxes(title_text="年份", dtick=2)
    fig.update_yaxes(title_text="累计节省（万元）")
    st.plotly_chart(_finish(fig, "投资回收曲线（展示口径：年度节省 = 运营节省 + 减排量 × λ）",
                            height), width="stretch",
                    config={"displayModeBar": False})
