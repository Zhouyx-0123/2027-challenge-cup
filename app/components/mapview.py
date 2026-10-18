"""地图组件：离线可用的地理可视化。

为什么不用 mapbox 瓦片：答辩现场可能断网，瓦片加载不出来就是一块灰底。
这里用 Plotly 内置 geo（矢量底图，不联网），保证断网也能演示。
"""

from __future__ import annotations

from typing import Optional

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


def render(fig: Optional[go.Figure], height: int = 560) -> None:
    if fig is None:
        st.info("地图数据还没就绪：宽表需要 origin_lat / origin_lon / dest_lat / dest_lon 四列。")
        return
    st.plotly_chart(fig, use_container_width=True,
                    config={"displayModeBar": False, "scrollZoom": False})
