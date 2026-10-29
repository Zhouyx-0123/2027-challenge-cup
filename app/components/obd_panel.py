# -*- coding: utf-8 -*-
"""OBD + GPS 实测碳排面板：云边端硬件监控页的第二条数据通道。

定位：**只读展示**。所有数字都来自 ``data/processed/`` 的 OBD 结果表
（由 ``python -m src.obd_pipeline`` 预计算），本模块不 import 守恒法算法、
不在页面里做任何碳排计算 —— 守住项目「前端只读不算」的铁律。

纪律：
    · 结果表来自演示序列时，页面**显式标注「演示数据」**，绝不冒充真实读数；
    · 该通道**不进 E₀ 主口径、不参与主闸门对拍**，页面顶部常驻这条声明，
      避免评委误以为主账被改过；
    · 因子来源与守恒推导对拍偏差必须显示出来（可核查性）。
"""
from __future__ import annotations

import html
import json
from typing import Any, Dict, List, Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots
from streamlit.components import v1 as components

import live_services
import services
from components import theme

#: 轨迹地图高度
MAP_HEIGHT = 380

CSS = """
<style>
.ob-status{display:flex;gap:14px;align-items:center;flex-wrap:wrap;
  padding:13px 18px;border-radius:14px;background:rgba(255,255,255,.055);
  box-shadow:0 0 0 1px rgba(255,255,255,.09);margin:2px 0 14px;
  font:12px/1.6 'JetBrains Mono',monospace;color:#9AA2AD}
.ob-tag{padding:3px 10px;border-radius:100px;font-weight:700;letter-spacing:.04em}
.ob-live{background:rgba(95,227,210,.14);color:#8BF3E6;
  box-shadow:0 0 0 1px rgba(95,227,210,.28)}
.ob-demo{background:rgba(245,176,76,.14);color:#FFC46B;
  box-shadow:0 0 0 1px rgba(245,176,76,.28)}
.ob-frozen{background:rgba(124,147,255,.14);color:#A9B8FF;
  box-shadow:0 0 0 1px rgba(124,147,255,.28)}
.ob-status b{color:#C7CDD6;font-weight:600}
.ob-note{padding:11px 15px;border-radius:12px;background:rgba(70,183,255,.08);
  box-shadow:0 0 0 1px rgba(70,183,255,.18);font-size:12.5px;line-height:1.85;
  color:#9AA2AD;margin:10px 0}
.ob-note b{color:#A9DDFF}
.ob-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));
  gap:12px;margin:6px 0 18px}
.ob-cell{padding:15px 16px;border-radius:14px;background:rgba(255,255,255,.05);
  box-shadow:0 0 0 1px rgba(255,255,255,.08)}
.ob-cell .k{font:10px/1.4 'JetBrains Mono',monospace;letter-spacing:.12em;
  color:#5B636E;text-transform:uppercase}
.ob-cell .v{margin-top:7px;font:700 25px/1.15 'Hanken Grotesk',sans-serif;
  color:#F3F5F8;font-variant-numeric:tabular-nums}
.ob-cell .u{margin-top:3px;font:11px/1.4 'Inter',sans-serif;color:#5B636E}
.ob-cell.ob-blue .v{color:#7CCBFF}
.ob-cell.ob-mint .v{color:#8BF3E6}
.ob-cell.ob-amber .v{color:#FFC46B}
.ob-cell.ob-coral .v{color:#FF8A5B}
</style>
"""


def _e(value: Any) -> str:
    return html.escape(str(value))


def _f(value: Any, digits: int = 2, dash: str = "—") -> str:
    """安全数字格式化；缺失显示破折号，**不编数字**。"""
    try:
        if value is None:
            return dash
        number = float(value)
    except (TypeError, ValueError):
        return dash
    if number != number:  # NaN
        return dash
    return ("%%.%df" % digits) % number


# ---------------------------------------------------------------------------
# 状态条与指标卡
# ---------------------------------------------------------------------------

def _status_strip(summary: Dict[str, Any]) -> None:
    """通道状态条：来源、契约版本、因子版本、与主账的边界。"""
    is_demo = bool(summary.get("is_demo"))
    source_text = str(summary.get("source") or "")
    contract = str(summary.get("contract_version") or "—")
    factor_ver = str(summary.get("factor_version") or "—")
    calc_ver = str(summary.get("calc_factor_version") or "—")
    st.markdown(
        "<div class='ob-status'>"
        "<span class='ob-tag %s'>%s</span>"
        "<span>契约 <b>%s</b></span>"
        "<span>守恒因子 <b>%s</b></span>"
        "<span>口径因子 <b>%s</b></span>"
        "<span class='ob-tag ob-frozen'>不进 E₀ 主账 · 不参与主闸门</span>"
        "<span>%s</span>"
        "</div>" % (
            "ob-demo" if is_demo else "ob-live",
            "演示数据" if is_demo else "真实设备数据",
            _e(contract), _e(factor_ver), _e(calc_ver),
            _e("来源：" + source_text) if source_text else ""),
        unsafe_allow_html=True)


def _kpi_row(summary: Dict[str, Any], vehicles: pd.DataFrame) -> None:
    """车队级指标卡。数字全部来自结果表。"""
    cells = [
        ("ob-blue", "VEHICLES", str(int(summary.get("n_vehicles") or len(vehicles))), "台在测车辆"),
        ("ob-mint", "TOTAL CARBON", _f(summary.get("total_t_co2e"), 6), "tCO₂e（实测守恒法）"),
        ("ob-coral", "CARBON INTENSITY", _f(summary.get("fleet_co2_gkm"), 1), "gCO₂/km 车队碳强度"),
        ("ob-amber", "DISTANCE", _f(summary.get("total_dist_km"), 1), "km 累计里程"),
        ("", "FUEL", _f(summary.get("total_fuel_l"), 2), "L 累计消耗"),
    ]
    html_parts = ["<div class='ob-grid'>"]
    for tone, key, value, unit in cells:
        html_parts.append(
            "<div class='ob-cell %s'><div class='k'>%s</div><div class='v'>%s</div>"
            "<div class='u'>%s</div></div>" % (tone, _e(key), _e(value), _e(unit)))
    html_parts.append("</div>")
    st.markdown("".join(html_parts), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# 图表
# ---------------------------------------------------------------------------

def _base_layout(figure: go.Figure, height: int = 330,
                 y_title: str = "") -> go.Figure:
    figure.update_layout(
        template="plotly_dark",
        height=height, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Manrope,Microsoft YaHei", color="#C7CDD6"),
        margin=dict(l=52, r=18, t=18, b=34), hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        xaxis=dict(gridcolor="rgba(255,255,255,.08)", zeroline=False),
        yaxis=dict(title=y_title, gridcolor="rgba(255,255,255,.08)", zeroline=False),
    )
    return figure


def build_carbon_figure(points: pd.DataFrame) -> go.Figure:
    """累计碳排曲线（kgCO₂）+ 瞬时排放率（g/s）双轴。

    排放率用**平滑列**（内核算的移动平均）；逐点原始值受采样噪声主导，
    直接画会糊成色块。明细表里仍是原始值。
    """
    figure = go.Figure()
    if points.empty:
        return _base_layout(figure)
    cumulative = pd.to_numeric(points.get("kg_co2"), errors="coerce").fillna(0.0).cumsum()
    figure.add_trace(go.Scatter(
        x=points["ts"], y=cumulative, name="累计碳排 kgCO₂",
        line=dict(color="#5FE3D2", width=2), fill="tozeroy",
        fillcolor="rgba(95,227,210,.12)",
        hovertemplate="累计 %{y:.4f} kgCO₂<extra></extra>"))
    er_source = points.get("er_gs_smooth")
    if er_source is None:
        er_source = points.get("er_gs")
    figure.add_trace(go.Scatter(
        x=points["ts"], y=pd.to_numeric(er_source, errors="coerce"),
        name="瞬时排放率 g/s（30 s 平滑）", yaxis="y2", line=dict(color="#FF8A5B", width=1.6),
        hovertemplate="%{y:.3f} g/s<extra></extra>"))
    figure.update_layout(
        yaxis2=dict(title="g/s", overlaying="y", side="right",
                    gridcolor="rgba(255,255,255,.04)", zeroline=False))
    return _base_layout(figure, 330, "kgCO₂")


def build_duty_figure(points: pd.DataFrame) -> go.Figure:
    """发动机工况三联图：车速 / 瞬时油耗 / 转速（共享时间轴）。

    三个量纲不同，**分三行共享 X 轴**，比叠在一张图上更好读。
    """
    figure = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.07)
    if points.empty:
        return _base_layout(figure)
    rows = [
        ("vehicle_speed_kmh", "车速 (km/h)", "#46B7FF", 1),
        ("instant_fuel_lph", "瞬时油耗 (L/h)", "#FFC46B", 2),
        ("engine_rpm", "发动机转速 (rpm)", "#FF6FA5", 3),
    ]
    for field, label, color, row in rows:
        if field not in points.columns:
            continue
        figure.add_trace(go.Scatter(
            x=points["ts"], y=pd.to_numeric(points[field], errors="coerce"),
            name=label, line=dict(color=color, width=1.5), fill="tozeroy",
            fillcolor="rgba(255,255,255,.04)",
            hovertemplate="%{y:.1f} " + label + "<extra></extra>"), row=row, col=1)
    figure.update_layout(
        template="plotly_dark",
        height=430, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Manrope,Microsoft YaHei", color="#C7CDD6"),
        margin=dict(l=56, r=18, t=16, b=34), hovermode="x unified",
        showlegend=False,
    )
    figure.update_xaxes(gridcolor="rgba(255,255,255,.06)", zeroline=False)
    figure.update_yaxes(gridcolor="rgba(255,255,255,.08)", zeroline=False)
    for annotation in figure.layout.annotations:
        annotation.font = dict(size=11, color="#9AA2AD")
    return figure


def build_intensity_figure(points: pd.DataFrame) -> go.Figure:
    """碳强度（gCO₂/km）曲线，**每 1 km 距离窗口**口径。

    为什么用距离窗口而不是逐点：逐点 g/km 在停车起步时（单点里程趋近 0）
    会爆到几万，那是除法噪声不是物理量。距离窗口给出的是「每跑 1 km 累计
    排了多少碳」——怠速段因为不跑里程却持续排碳，会以曲线抬升的形式显出来，
    这正是这台车「怠速占比」高不高最直观的证据。
    """
    figure = go.Figure()
    if points.empty:
        return _base_layout(figure)
    source = points.get("co2_gkm_window")
    if source is None:
        source = points.get("co2_gkm")
    figure.add_trace(go.Scatter(
        x=points["ts"], y=pd.to_numeric(source, errors="coerce"),
        name="碳强度 gCO₂/km（每 1 km 窗口）", line=dict(color="#7C93FF", width=1.5),
        fill="tozeroy", fillcolor="rgba(124,147,255,.10)",
        hovertemplate="%{y:.1f} gCO₂/km<extra></extra>"))
    return _base_layout(figure, 300, "gCO₂/km")


def build_trajectory_figure(points: pd.DataFrame) -> go.Figure:
    """GPS 轨迹（经纬度散点 + 连线）。没有高德 Key 时的兜底画法。"""
    figure = go.Figure()
    if points.empty or "lat" not in points.columns:
        return _base_layout(figure, MAP_HEIGHT, "纬度")
    lat = pd.to_numeric(points.get("lat"), errors="coerce")
    lng = pd.to_numeric(points.get("lng"), errors="coerce")
    figure.add_trace(go.Scatter(
        x=lng, y=lat, mode="lines+markers", name="GPS 轨迹",
        line=dict(color="#46B7FF", width=2), marker=dict(size=4, color="#5FE3D2"),
        hovertemplate="经度 %{x:.5f}<br>纬度 %{y:.5f}<extra></extra>"))
    figure.update_layout(
        xaxis=dict(title="经度", gridcolor="rgba(255,255,255,.08)", zeroline=False),
        yaxis=dict(title="纬度", gridcolor="rgba(255,255,255,.08)", zeroline=False,
                   scaleanchor="x", scaleratio=1),
        height=MAP_HEIGHT)
    return figure


# ---------------------------------------------------------------------------
# GPS 轨迹地图（高德 JS API 2.0）
# ---------------------------------------------------------------------------

MAP_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<style>
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;
  background:#0B0D10;font-family:'Inter','Noto Sans SC','Microsoft YaHei',sans-serif}
#map{width:100%;height:100%}
.ob-badge{position:absolute;left:12px;top:12px;z-index:9;padding:6px 12px;
  border-radius:100px;background:rgba(13,17,23,.86);box-shadow:0 0 0 1px rgba(255,255,255,.12);
  color:#8BF3E6;font:600 11px/1 'JetBrains Mono',monospace;letter-spacing:.06em}
</style></head><body>
<div id="map"></div><div class="ob-badge" id="badge">__BADGE__</div>
<script type="text/javascript">window._AMapSecurityConfig={securityJsCode:"__SECURITY__"};</script>
<script src="https://webapi.amap.com/maps?v=2.0&key=__KEY__"></script>
<script>
var payload=__PAYLOAD__;
var map=new AMap.Map('map',{viewMode:'2D',zoom:11,center:payload.center,
  mapStyle:'amap://styles/darkblue',showLabel:true});
var line=new AMap.Polyline({path:payload.path,strokeColor:'#46B7FF',strokeWeight:5,
  strokeOpacity:.92,lineJoin:'round',showDir:true});
map.add(line);
if(payload.path.length>1){
  var start=new AMap.Marker({position:payload.path[0],title:'起点'});
  var end=new AMap.Marker({position:payload.path[payload.path.length-1],title:'终点'});
  map.add([start,end]);
  map.setFitView();
}
</script></body></html>
"""


def build_trajectory_map_html(points: pd.DataFrame, key: str, security: str,
                              badge: str = "") -> str:
    """构造 GPS 轨迹地图 HTML（高德 JS API 2.0）。"""
    if points.empty or "lat" not in points.columns:
        raise ValueError("没有可绘制的 GPS 轨迹")
    lat = pd.to_numeric(points.get("lat"), errors="coerce")
    lng = pd.to_numeric(points.get("lng"), errors="coerce")
    path: List[List[float]] = []
    for a, b in zip(lng.tolist(), lat.tolist()):
        if a is None or b is None:
            continue
        if a != a or b != b:  # NaN
            continue
        path.append([round(float(a), 6), round(float(b), 6)])
    if len(path) < 2:
        raise ValueError("GPS 轨迹点不足两个")
    # 抽稀：地图不需要几千个点
    if len(path) > 400:
        step = len(path) // 400 + 1
        path = path[::step]
        if path[-1] != [round(float(lng.iloc[-1]), 6), round(float(lat.iloc[-1]), 6)]:
            path.append([round(float(lng.iloc[-1]), 6), round(float(lat.iloc[-1]), 6)])

    payload = {
        "center": path[0],
        "path": path,
    }
    return (MAP_TEMPLATE
            .replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False))
            .replace("__KEY__", str(key))
            .replace("__SECURITY__", str(security))
            .replace("__BADGE__", html.escape(badge or "GPS TRAJECTORY")))


# ---------------------------------------------------------------------------
# 表格
# ---------------------------------------------------------------------------

VEHICLE_TABLE_COLUMNS = [
    ("vehicle_id", "车辆编号"),
    ("plate", "车牌"),
    ("fuel_type", "燃料"),
    ("duration_h", "时长(h)"),
    ("dist_km", "里程(km)"),
    ("fuel_l", "油耗(L)"),
    ("kg_co2", "碳排(kgCO₂)"),
    ("t_co2e", "碳排(tCO₂e)"),
    ("co2_gkm", "碳强度(g/km)"),
    ("co2_g_tkm", "碳强度(g/t·km)"),
    ("idle_ratio", "怠速占比"),
    ("er_gs_mean", "均排放率(g/s)"),
    ("er_gs_max", "峰值(g/s)"),
    ("valid_rate", "有效率"),
    ("n_flagged", "质控标记"),
]


def build_vehicle_table(vehicles: pd.DataFrame) -> pd.DataFrame:
    """车级碳排表（展示用中文列名 + 单位换算，不改口径）。"""
    if vehicles.empty:
        return pd.DataFrame()
    rows: List[Dict[str, Any]] = []
    for _, row in vehicles.iterrows():
        item: Dict[str, Any] = {}
        for field, label in VEHICLE_TABLE_COLUMNS:
            value = row.get(field)
            if field == "idle_ratio":
                item[label] = ("%.1f%%" % (float(value) * 100.0)
                               if value is not None and pd.notna(value) else "—")
            elif field == "valid_rate":
                item[label] = _f(value, 4)
            elif field in ("duration_h",):
                item[label] = _f(value, 4)
            elif field in ("kg_co2", "co2_gkm", "co2_g_tkm", "er_gs_mean", "er_gs_max"):
                item[label] = _f(value, 3)
            elif field in ("t_co2e",):
                item[label] = _f(value, 6)
            elif field in ("plate", "fuel_type", "vehicle_id"):
                item[label] = str(value) if value else "—"
            else:
                item[label] = _f(value, 2)
        rows.append(item)
    return pd.DataFrame(rows)


def _engine_stats(points: pd.DataFrame) -> pd.DataFrame:
    """发动机参数统计（读数表的描述统计，不做二次计算）。"""
    if points.empty:
        return pd.DataFrame()
    fields = [
        ("engine_rpm", "发动机转速 (rpm)"),
        ("engine_load_pct", "发动机负荷 (%)"),
        ("throttle_pos_pct", "节气门开度 (%)"),
        ("engine_temp_c", "冷却液温度 (°C)"),
        ("vehicle_speed_kmh", "车速 (km/h)"),
        ("instant_fuel_lph", "瞬时油耗 (L/h)"),
        ("fuel_level_pct", "燃油液位 (%)"),
    ]
    rows: List[Dict[str, Any]] = []
    for field, label in fields:
        if field not in points.columns:
            continue
        series = pd.to_numeric(points[field], errors="coerce").dropna()
        if series.empty:
            continue
        rows.append({
            "参数": label,
            "最小": round(float(series.min()), 2),
            "均值": round(float(series.mean()), 2),
            "最大": round(float(series.max()), 2),
            "P95": round(float(series.quantile(0.95)), 2),
            "有效样本": int(len(series)),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 主渲染
# ---------------------------------------------------------------------------

def _empty_state() -> None:
    """没有实测数据时的空态：说清楚差什么、怎么补。"""
    st.markdown(
        "<div class='ob-note'>"
        "<b>OBD + GPS 实测碳排通道尚未接入。</b><br>"
        "本通道测的是<b>实时油耗</b>，用燃料守恒法推出每台车（每台发动机）的碳排放，"
        "与发票油量、VSP 估算构成三条独立证据链。<br>"
        "接入方式：设备直传 <code>POST /api/upload_obd_gps</code>（令牌鉴权），"
        "或把 CSV 放到 <code>data/raw/obd_gps/obd_stream.csv</code>，"
        "然后执行 <code>python -m src.obd_pipeline</code> 生成结果表。"
        "想先看效果可执行 <code>python -m src.obd_pipeline --demo</code>。"
        "</div>", unsafe_allow_html=True)


def render_obd_panel() -> None:
    """OBD + GPS 通道面板（挂在云边端硬件监控页的第二个 tab）。"""
    st.markdown(CSS, unsafe_allow_html=True)
    summary = services.obd_summary()
    vehicles = services.obd_vehicles()

    if vehicles.empty:
        _empty_state()
        return

    _status_strip(summary)
    if summary.get("is_demo"):
        st.warning("演示数据：当前结果由确定性演示序列生成（未接入真实 OBD 设备），"
                   "不代表现场读数。")
    _kpi_row(summary, vehicles)

    st.markdown(
        "<div class='ob-note'>"
        "<b>口径：</b>守恒法 CO₂(kg) = 油耗(L) × ρ × w<sub>C</sub> × (M<sub>CO₂</sub>/M<sub>C</sub>)，"
        "出账系数取自 <code>config/calc_factors.json</code>（唯一真源）；"
        "油耗优先用<b>累计油耗差分</b>，无累计值时用<b>瞬时油耗积分</b>。<br>"
        "<b>边界：</b>本通道是实测轨，<b>不写入 E₀ 主口径、不参与主闸门对拍</b>，"
        "主账口径与 28 条判据保持冻结。"
        "</div>", unsafe_allow_html=True)

    # ---- 车辆选择 ----
    options = vehicles["vehicle_id"].astype(str).tolist()
    label_map = {}
    for _, row in vehicles.iterrows():
        vid = str(row.get("vehicle_id"))
        plate = str(row.get("plate") or "")
        label_map[vid] = "%s%s" % (vid, ("（%s）" % plate) if plate else "")
    default_index = 0
    vehicle_id = st.selectbox(
        "选择车辆", options, index=default_index,
        format_func=lambda value: label_map.get(value, value),
        key="obd_vehicle_select")
    points = services.obd_points(vehicle_id)

    row = vehicles[vehicles["vehicle_id"].astype(str) == str(vehicle_id)]
    if not row.empty:
        one = row.iloc[0]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("本车碳排", "%s kg" % _f(one.get("kg_co2"), 3))
        c2.metric("本车里程", "%s km" % _f(one.get("dist_km"), 2))
        c3.metric("本车油耗", "%s L" % _f(one.get("fuel_l"), 2))
        c4.metric("怠速占比", "%s" % (
            ("%.1f%%" % (float(one.get("idle_ratio")) * 100.0))
            if one.get("idle_ratio") is not None and pd.notna(one.get("idle_ratio")) else "—"))

    # ---- 曲线 ----
    left, right = st.columns(2, gap="large")
    with left:
        st.plotly_chart(build_carbon_figure(points), width="stretch")
    with right:
        st.plotly_chart(build_duty_figure(points), width="stretch")

    left2, right2 = st.columns(2, gap="large")
    with left2:
        st.plotly_chart(build_intensity_figure(points), width="stretch")
        st.caption("碳强度按「每 1 km 距离窗口」计：怠速段不跑里程却持续排碳，"
                   "曲线会明显抬升 —— 这就是怠速浪费最直观的证据。")
    with right2:
        _render_trajectory(points, vehicle_id)

    # ---- 车级聚合表 ----
    theme.section("每车碳排聚合", "守恒法实测 · 一台车（一个动力总成）一行")
    st.dataframe(build_vehicle_table(vehicles), width="stretch", hide_index=True)

    with st.expander("行程级明细", expanded=False):
        trips = services.obd_trips()
        if trips.empty:
            st.caption("没有行程数据。")
        else:
            subset = trips[trips["vehicle_id"].astype(str) == str(vehicle_id)]
            st.dataframe(subset, width="stretch", hide_index=True)

    with st.expander("发动机参数统计", expanded=False):
        stats = _engine_stats(points)
        if stats.empty:
            st.caption("没有可统计的发动机参数。")
        else:
            st.dataframe(stats, width="stretch", hide_index=True)

    with st.expander("数据质量与质控", expanded=False):
        _render_quality(summary, vehicles)

    with st.expander("接入方式与接口契约", expanded=False):
        _render_contract()


def _render_trajectory(points: pd.DataFrame, vehicle_id: str) -> None:
    """GPS 轨迹：有高德 JS Key 走真地图，否则退回经纬度散点图。"""
    if points.empty or "lat" not in points.columns:
        st.caption("本车没有 GPS 位置数据。")
        return
    if live_services.amap_js_available():
        key, security = live_services.amap_js_credentials()
        try:
            html_doc = build_trajectory_map_html(
                points, key, security, badge="GPS TRAJECTORY · %s" % vehicle_id)
            components.html(html_doc, height=MAP_HEIGHT, scrolling=False)
            return
        except ValueError as exc:
            st.caption(str(exc))
    st.plotly_chart(build_trajectory_figure(points), width="stretch")


def _render_quality(summary: Dict[str, Any], vehicles: pd.DataFrame) -> None:
    """数据质量面板：质控标记、不可用原因、因子对拍、有效率。"""
    flags = summary.get("qc_flags") or {}
    reasons = summary.get("reject_reasons") or {}
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**质控标记**")
        if flags:
            st.dataframe(pd.DataFrame(
                [{"标记": key, "次数": int(value)} for key, value in
                 sorted(flags.items(), key=lambda kv: -kv[1])]),
                width="stretch", hide_index=True)
        else:
            st.caption("没有质控标记。")
    with col_b:
        st.markdown("**不可用记录原因**")
        if reasons:
            st.dataframe(pd.DataFrame(
                [{"原因": key, "次数": int(value)} for key, value in reasons.items()]),
                width="stretch", hide_index=True)
        else:
            st.caption("没有不可用记录。")

    factor_source = str(summary.get("factor_source") or "")
    st.markdown("**因子与对拍**")
    st.caption(factor_source or "—")
    if not vehicles.empty:
        consistency = pd.to_numeric(vehicles.get("factor_consistency_pct"), errors="coerce").dropna()
        if not consistency.empty:
            worst = float(consistency.abs().max())
            st.caption("守恒推导值与权威因子最大偏差 %.3f%%（容差 3%%）—— 偏差来自两者参考条件"
                       "不同：推导用燃料元素组成与常温密度，权威因子用标准燃料。" % worst)


def _render_contract() -> None:
    """接口契约与接入说明（前端只展示，不做计算）。"""
    st.markdown(
        "**设备上报端点**：`POST /api/upload_obd_gps`（与传感器通道同一套令牌鉴权、"
        "限流与审计；默认 fail-closed）\n\n"
        "**请求体**：单条记录对象，或 `{\"records\": [ ... ]}` 批量；"
        "字段名用契约的规范名或任一别名（如 `rpm` / `engine_rpm`、`fuel_rate` / "
        "`instant_fuel_lph`）均可。\n\n"
        "**文件回传**：CSV 放到 `data/raw/obd_gps/obd_stream.csv`，"
        "再执行 `python -m src.obd_pipeline`。\n\n"
        "**硬要求**：没有油耗字段的记录直接判不可用 —— 没有油耗就没有碳，"
        "本通道不会用 0 冒充缺失。\n\n"
        "**落地位置**：`config/obd_gps_contract.json`（契约）、"
        "`config/obd_carbon_factors.json`（守恒物理量）、"
        "`src/obd_gps.py`（校验与算碳）、`src/obd_pipeline.py`（结果表）。",
        unsafe_allow_html=True)
    with st.expander("看契约原文"):
        try:
            st.json(json.loads(
                (live_services.ROOT / "config" / "obd_gps_contract.json")
                .read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001
            st.caption("读取契约失败：%s" % exc)


__all__ = ["render_obd_panel", "build_carbon_figure", "build_duty_figure",
           "build_intensity_figure", "build_trajectory_figure",
           "build_trajectory_map_html", "build_vehicle_table"]
