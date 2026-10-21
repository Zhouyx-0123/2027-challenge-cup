"""地图组件：离线可用的地理可视化。

为什么不用 mapbox 瓦片：答辩现场可能断网，瓦片加载不出来就是一块灰底。
这里用 Plotly 内置 geo（矢量底图，不联网），保证断网也能演示。
路线预览 route_preview() 则刻意不画假底图，改成“控制台示意图”：只画路线形状、
端点与指标，避免 natural earth 粗多边形在放大后比折线还抽象。
"""

from __future__ import annotations

import math
from typing import Any, Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ACCENT = "#2DD4A7"
ACCENT2 = "#F5C86B"
SCALE = ["#0E3B2E", "#1C8F6B", ACCENT, ACCENT2, "#FF8A5B"]

GEO_LAYOUT = dict(
    showcountries=True, countrycolor="#1E2A36", countrywidth=0.6,
    showcoastlines=True, coastlinecolor="#1E2A36", coastlinewidth=0.6,
    showland=True, landcolor="#0F1620",
    showocean=True, oceancolor="#0A0E13",
    showlakes=False, showframe=False,
    bgcolor="rgba(0,0,0,0)",
)


def _base_layout(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=0, t=6, b=0),
        height=height,
        coloraxis_colorbar=dict(
            thickness=9, len=0.55,
            title=dict(text="tCO₂e", font=dict(size=11)),
            tickfont=dict(size=10),
        ),
    )
    return fig


def bubble_map(
    df: pd.DataFrame,
    lat: str = "origin_lat",
    lon: str = "origin_lon",
    city: str = "origin",
    month: str = "month",
    value: str = "kg_co2",
    animate: bool = True,
    height: int = 560,
) -> Optional[go.Figure]:
    """城市级碳排气泡图；有 month 列就带月份播放轴。"""
    need = [lat, lon, city, value]
    if df is None or df.empty or any(c not in df.columns for c in need):
        return None

    agg = df.groupby([lat, lon, city] + ([month] if animate and month in df.columns else []),
                     as_index=False)[value].sum()
    agg["t"] = agg[value] / 1000.0
    agg = agg[agg["t"] > 0]

    kwargs = dict(
        lat=lat, lon=lon, size="t", color="t",
        hover_name=city, hover_data={"t": ":.1f", lat: False, lon: False},
        color_continuous_scale=SCALE, size_max=46,
        projection="natural earth", labels={"t": "碳排放(t)"},
    )
    if animate and month in agg.columns:
        agg[month] = agg[month].apply(lambda m: "%02d 月" % int(m))
        fig = px.scatter_geo(agg, animation_frame=month, **kwargs)
        if fig.layout.updatemenus:
            fig.layout.updatemenus[0].buttons[0].args[1]["frame"] = {"duration": 700, "redraw": True}
            fig.layout.updatemenus[0].buttons[0].args[1]["transition"] = {"duration": 400}
            fig.layout.updatemenus[0].pad = {"r": 10, "t": 4}
            fig.layout.updatemenus[0].bgcolor = "rgba(19,26,35,0.9)"
    else:
        fig = px.scatter_geo(agg, **kwargs)

    fig.update_geos(**GEO_LAYOUT, fitbounds="locations")
    return _base_layout(fig, height)


def _arc(lat1: float, lon1: float, lat2: float, lon2: float, bend: float = 0.16, n: int = 26):
    dx, dy = lon2 - lon1, lat2 - lat1
    dist = (dx * dx + dy * dy) ** 0.5 or 1.0
    ux, uy = -dy / dist, dx / dist
    lons, lats = [], []
    for i in range(n + 1):
        t = i / n
        off = bend * dist * (1 - abs(2 * t - 1))
        lons.append(lon1 + dx * t + ux * off)
        lats.append(lat1 + dy * t + uy * off)
    return lons, lats


def flow_map(
    df: pd.DataFrame,
    lat_o: str = "origin_lat",
    lon_o: str = "origin_lon",
    lat_d: str = "dest_lat",
    lon_d: str = "dest_lon",
    city_o: str = "origin",
    city_d: str = "dest",
    value: str = "kg_co2",
    top_n: int = 24,
    height: int = 560,
) -> Optional[go.Figure]:
    """OD 流向图：弧线宽度 = 该线路碳排，颜色按重/中/轻大类。"""
    need = [lat_o, lon_o, lat_d, lon_d, value]
    if df is None or df.empty or any(c not in df.columns for c in need):
        return None

    agg = (df.groupby([city_o, city_d, lat_o, lon_o, lat_d, lon_d], as_index=False)[value]
             .sum().sort_values(value, ascending=False).head(top_n))
    if agg.empty:
        return None
    vmax = agg[value].max() or 1.0

    fig = go.Figure()
    for _, r in agg.iterrows():
        lons, lats = _arc(r[lat_o], r[lon_o], r[lat_d], r[lon_d])
        ratio = float(r[value]) / vmax
        fig.add_trace(go.Scattergeo(
            lon=lons, lat=lats, mode="lines",
            line=dict(width=0.6 + 5.0 * ratio,
                      color="rgba(45,212,167," + ("%.2f" % (0.18 + 0.62 * ratio)) + ")"),
            opacity=1.0,
            hoverinfo="text",
            text="%s → %s<br>%.1f tCO₂e" % (r[city_o], r[city_d], float(r[value]) / 1000.0),
            showlegend=False,
        ))
        fig.add_trace(go.Scattergeo(
            lon=[r[lon_d]], lat=[r[lat_d]], mode="markers",
            marker=dict(size=4 + 8 * ratio, color=ACCENT2, opacity=0.85),
            hoverinfo="skip", showlegend=False,
        ))

    cities = df.groupby([city_o, lat_o, lon_o], as_index=False)[value].sum()
    fig.add_trace(go.Scattergeo(
        lon=cities[lon_o], lat=cities[lat_o], mode="markers+text",
        text=cities[city_o], textposition="top center",
        textfont=dict(size=10, color="#8A97A6"),
        marker=dict(size=5, color="#E6EDF3", opacity=0.75),
        hoverinfo="skip", showlegend=False,
    ))

    fig.update_geos(**GEO_LAYOUT, fitbounds="locations")
    return _base_layout(fig, height)


def route_preview(row: Any, height: int = 600) -> Optional[go.Figure]:
    """离线路线示意图：不画假底图，只画“路线形状 + 端点 + 指标”。

    为什么不用 scattergeo：Plotly 的 natural earth 底图在国家/区域尺度是一块块
    粗多边形，放大后既没有道路也没有城市细节，看起来比折线还抽象。
    断网时诚实地画一张“控制台示意图”，比假装是地图更好。

    row 可以是 pandas Series / dict：需要 origin、dest、o_lng、o_lat、d_lng、d_lat、
    polyline（'lng,lat;...'）。缓存几何是抽稀过的，只用于兜底。
    """
    if row is None:
        return None
    get = row.get if hasattr(row, "get") else (lambda k, default=None: default)
    origin = str(get("origin") or get("on") or "起点")
    dest = str(get("dest") or get("dn") or "终点")
    try:
        o_lng, o_lat = float(get("o_lng")), float(get("o_lat"))
        d_lng, d_lat = float(get("d_lng")), float(get("d_lat"))
    except (TypeError, ValueError):
        return None
    if not all(abs(v) > 1e-9 for v in (o_lng, o_lat, d_lng, d_lat)):
        return None

    lons, lats = [], []
    raw = get("polyline") or ""
    if isinstance(raw, str) and raw:
        for chunk in raw.split(";"):
            if "," not in chunk:
                continue
            lng_text, lat_text = chunk.split(",", 1)
            try:
                lons.append(float(lng_text))
                lats.append(float(lat_text))
            except ValueError:
                continue
    if len(lons) < 2:
        lons, lats = [o_lng, d_lng], [o_lat, d_lat]

    mean_lat = sum(lats) / len(lats)
    xs = [lng * math.cos(math.radians(mean_lat)) for lng in lons]
    ys = list(lats)
    span_x = max(xs) - min(xs) or 1e-6
    span_y = max(ys) - min(ys) or 1e-6
    scale = min(82.0 / span_x, 78.0 / span_y)
    mid_x = (max(xs) + min(xs)) / 2.0
    mid_y = (max(ys) + min(ys)) / 2.0
    px = [50.0 + (x - mid_x) * scale for x in xs]
    py = [50.0 + (y - mid_y) * scale for y in ys]

    fig = go.Figure()
    for v in range(10, 100, 10):
        fig.add_shape(type="line", x0=v, y0=4, x1=v, y1=96,
                      line=dict(color="rgba(255,255,255,0.035)", width=1), layer="below")
        fig.add_shape(type="line", x0=4, y0=v, x1=96, y1=v,
                      line=dict(color="rgba(255,255,255,0.035)", width=1), layer="below")

    fig.add_trace(go.Scatter(
        x=px, y=py, mode="lines",
        line=dict(width=12, color="rgba(45,212,167,0.10)"),
        hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(
        x=px, y=py, mode="lines",
        line=dict(width=3.2, color=ACCENT),
        hoverinfo="text",
        text="%s → %s<br>离线缓存几何" % (origin, dest),
        showlegend=False))
    step = max(1, len(px) // 12)
    fig.add_trace(go.Scatter(
        x=px[::step] + [px[-1]], y=py[::step] + [py[-1]], mode="markers",
        marker=dict(size=4, color="#7CF5D3", line=dict(width=0)),
        hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(
        x=[px[0], px[-1]], y=[py[0], py[-1]], mode="markers+text",
        marker=dict(size=[15, 15], color=[ACCENT, ACCENT2],
                    line=dict(width=2, color="#07120F")),
        text=["A · " + origin, "B · " + dest],
        textposition=["top right", "bottom right"],
        textfont=dict(size=10.5, color="#E6EDF3"),
        hoverinfo="skip", showlegend=False))

    km = get("distance_km") or get("km") or 0.0
    mins = get("duration_min") or get("min") or 0.0
    toll = get("toll_yuan") or get("toll") or 0.0
    co2 = get("co2_t") or 0.0
    try:
        km_s = "{:,.1f}".format(float(km))
        min_s = "{:,.0f}".format(float(mins))
        toll_s = "{:,.0f}".format(float(toll))
        co2_s = "{:.2f}".format(float(co2))
    except (TypeError, ValueError):
        km_s, min_s, toll_s, co2_s = "—", "—", "—", "—"

    fig.add_annotation(
        x=0.025, y=0.975, xref="paper", yref="paper", xanchor="left", yanchor="top",
        align="left", showarrow=False,
        text="<b>OFFLINE SCHEMATIC</b>　离线示意<br>"
             "<span style='font-size:11px;color:#8A97A6'>缓存几何 · 非实时 · 无在线底图</span>",
        font=dict(size=11, color="#F5C86B"), bgcolor="rgba(8,12,17,0.55)",
        bordercolor="rgba(245,200,107,0.28)", borderwidth=1, borderpad=8)
    fig.add_annotation(
        x=0.975, y=0.03, xref="paper", yref="paper", xanchor="right", yanchor="bottom",
        align="right", showarrow=False,
        text="{:s} km　·　{:s} min　·　{:s} 元　·　{:s} tCO₂e".format(km_s, min_s, toll_s, co2_s),
        font=dict(size=11, color="#E6EDF3", family="Consolas, monospace"),
        bgcolor="rgba(8,12,17,0.62)", bordercolor="rgba(255,255,255,0.10)",
        borderwidth=1, borderpad=7)
    fig.update_xaxes(visible=False, range=[0, 100], fixedrange=True)
    fig.update_yaxes(visible=False, range=[0, 100], fixedrange=True)
    fig.update_layout(
        height=height, margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(10,15,21,0.92)",
        showlegend=False, hovermode="closest",
    )
    return fig


def render(fig: Optional[go.Figure], height: int = 560) -> None:
    if fig is None:
        st.info("地图数据还没就绪：宽表需要 origin_lat / origin_lon / dest_lat / dest_lon 四列。")
        return
    st.plotly_chart(fig, width="stretch",
                    config={"displayModeBar": False, "scrollZoom": False})
