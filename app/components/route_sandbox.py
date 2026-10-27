# -*- coding: utf-8 -*-
"""四维进化路线沙盘：主应用 st.navigation 的减碳路线页面组件。"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import live_services
from components import route_map, slider_kit
from demo_state import carbon_snapshot

AXIS_OPTIONS = {
    "碳排放": ("carbon", "碳排放 (kgCO2)"),
    "综合成本": ("cost", "综合成本 (元)"),
    "时间": ("duration", "行驶时间 (min)"),
    "距离": ("distance", "行驶距离 (km)"),
}
STATE_ROUTES = "route_evolution_records"
STATE_LOGS = "route_decision_log"
STATE_ERROR = "route_evolution_error"
STATE_GEOMETRY = "route_real_geometry"
STATE_OD = "route_origin_destination"

STYLE = """
<style>
.rs-panel{background:var(--ct-glass);border:none;
  border-radius:24px;box-shadow:var(--ct-shadow-sm),0 0 0 1px var(--ct-ring);
  padding:22px 24px 10px;
  -webkit-backdrop-filter:blur(18px) saturate(140%);
  backdrop-filter:blur(18px) saturate(140%);margin-bottom:20px}
.rs-panel-title{font-family:var(--ct-font-display);font-size:18px;font-weight:750;
  letter-spacing:-.02em;color:var(--ct-ink);margin-bottom:4px}
.rs-panel-sub{font-size:13px;color:var(--ct-ink-3);margin-bottom:16px}
.rs-section{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;
  margin:26px 0 14px;padding-bottom:10px;border-bottom:1px solid var(--ct-ring)}
.rs-section h3{font-family:var(--ct-font-display);font-size:22px;letter-spacing:-.035em;
  color:var(--ct-ink);margin:0}
.rs-section span{font-size:12px;color:var(--ct-ink-3);letter-spacing:.04em}
.rs-empty{min-height:330px;display:flex;flex-direction:column;align-items:center;
  justify-content:center;text-align:center;background:rgba(255,255,255,.035);
  border:1px dashed var(--ct-ring-2);border-radius:24px;padding:36px}
.rs-empty-icon{width:64px;height:64px;border-radius:20px;background:var(--ct-blue-tint);
  display:flex;align-items:center;justify-content:center;margin-bottom:18px}
.rs-empty-title{font-size:20px;font-weight:700;color:var(--ct-ink);margin-bottom:8px}
.rs-empty-text{font-size:14px;color:var(--ct-ink-3);max-width:480px;line-height:1.8}
.rs-legend{display:flex;gap:18px;align-items:center;justify-content:center;
  color:var(--ct-ink-3);font-size:12px;margin:-4px 0 8px}
.rs-dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}
.st-key-route_map_stage{border-radius:22px;overflow:hidden;background:var(--ct-surface);
  box-shadow:0 22px 70px rgba(0,0,0,.48),0 0 0 1px var(--ct-ring-2)}
.st-key-route_map_stage iframe{display:block;width:100%!important;border:0;border-radius:22px}
div[data-testid="stFormSubmitButton"] button{border-radius:100px;min-height:48px;
  font-weight:700;background:var(--ct-blue);color:#06121F;border:none;
  box-shadow:var(--ct-shadow-blue);
  transition:transform .18s ease,box-shadow .18s ease}
div[data-testid="stFormSubmitButton"] button:hover{transform:translateY(-2px);
  background:var(--ct-blue-500);box-shadow:0 14px 30px rgba(70,183,255,.34)}
div[data-testid="stFormSubmitButton"] button p,
div[data-testid="stFormSubmitButton"] button span{color:#06121F !important;font-weight:700}
div[data-testid="stFormSubmitButton"] button:active{transform:scale(.985)}
@media(max-width:768px){.rs-panel{padding:16px 16px 8px}.rs-section{align-items:flex-start;
  flex-direction:column}.rs-empty{min-height:260px}}
@media(prefers-reduced-motion:reduce){div[data-testid="stFormSubmitButton"] button{
  transition:none}div[data-testid="stFormSubmitButton"] button:hover{transform:none}}
</style>
"""


def _groups(frame: pd.DataFrame) -> List[Tuple[str, pd.DataFrame]]:
    recommended = frame["is_recommended"].fillna(False).astype(bool)
    exploration = frame["is_exploration"].fillna(False).astype(bool) & ~recommended
    regular = ~recommended & ~exploration
    return [("常规路线", frame[regular]), ("探索路线", frame[exploration]),
            ("推荐路线", frame[recommended])]


def build_scatter_figure(frame: pd.DataFrame, x_axis: str, y_axis: str) -> go.Figure:
    x_field, x_label = AXIS_OPTIONS[x_axis]
    y_field, y_label = AXIS_OPTIONS[y_axis]
    styles = {
        "常规路线": dict(symbol="circle", size=10, color="#94A3B8", opacity=0.58),
        "探索路线": dict(symbol="star", size=15, color="#5FE3D2", opacity=0.95),
        "推荐路线": dict(symbol="circle", size=19, color="#FF6B6B", opacity=1.0),
    }
    figure = go.Figure()
    for name, group in _groups(frame):
        if group.empty:
            continue
        marker = styles[name]
        if name == "推荐路线":
            marker["line"] = dict(color="#8BF3E6", width=2)
        figure.add_trace(go.Scatter(
            x=group[x_field], y=group[y_field], mode="markers", name=name,
            marker=marker, text=group["route_id"].astype(str),
            customdata=group[["cost", "carbon", "duration", "distance"]].values,
            hovertemplate=(
                "<b>%{text}</b><br>" + x_label + ": %{x:.2f}<br>" + y_label
                + ": %{y:.2f}<br>成本: ¥%{customdata[0]:.2f}"
                + "<br>碳排: %{customdata[1]:.2f} kg<extra></extra>"),
        ))
    figure.update_layout(
        template="plotly_dark",
        height=440, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Manrope,Microsoft YaHei", color="#C7CDD6"),
        margin=dict(l=20, r=20, t=20, b=20), hovermode="closest",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        xaxis=dict(title=x_label, gridcolor="rgba(255,255,255,.10)", zeroline=False),
        yaxis=dict(title=y_label, gridcolor="rgba(255,255,255,.10)", zeroline=False),
    )
    return figure


def build_parallel_figure(frame: pd.DataFrame) -> go.Figure:
    figure = go.Figure()
    # Parcoords 不支持逐线 width；用独立轨迹和色彩权重突出推荐/探索。
    styles = {
        "常规路线": dict(color="rgba(148,163,184,0.35)"),
        "探索路线": dict(color="#5FE3D2"),
        "推荐路线": dict(color="#FF6B6B"),
    }
    labels = [("综合成本", "cost", "元"), ("碳排放", "carbon", "kg"),
              ("时间", "duration", "min"), ("距离", "distance", "km")]
    for name, group in _groups(frame):
        if group.empty:
            continue
        dimensions = [dict(label=label, values=group[field].astype(float))
                      for label, field, _unit in labels]
        figure.add_trace(go.Parcoords(
            dimensions=dimensions, line=styles[name], name=name,
            labelfont=dict(size=12, color="#C7CDD6"),
            tickfont=dict(size=10, color="#9AA2AD"),
            rangefont=dict(size=10, color="#9AA2AD"),
        ))
    figure.update_layout(
        template="plotly_dark",
        height=390, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=50, r=50, t=35, b=25),
        font=dict(family="Manrope,Microsoft YaHei", color="#C7CDD6"),
    )
    return figure


def _empty() -> None:
    st.markdown(
        "<div class='rs-empty'><div class='rs-empty-icon'>"
        "<svg width='34' height='34' viewBox='0 0 34 34' fill='none'>"
        "<path d='M7 25C10 18 13 22 17 15C21 8 24 13 28 6' stroke='#46B7FF' "
        "stroke-width='2.4' stroke-linecap='round'/><circle cx='7' cy='25' r='3' "
        "fill='#46B7FF'/><circle cx='28' cy='6' r='3' fill='#8BF3E6'/></svg></div>"
        "<div class='rs-empty-title'>请在左侧输入参数并生成方案</div>"
        "<div class='rs-empty-text'>高德真实路线、四维 NSGA-II 与 Q-learning 将在这里汇合。"
        "生成后可自由切换散点维度、比较平行坐标并采纳路线。</div></div>",
        unsafe_allow_html=True)


def _generate(origin: str, destination: str, truck_type: str, load: float,
              carbon_price: float, energy_rate: float, evolve_enabled: bool) -> None:
    logs: List[str] = []
    try:
        with st.spinner("正在解析地点并生成四维路线方案…"):
            start = live_services.resolve_route_place(origin)
            end = live_services.resolve_route_place(destination)
            logs.append("起点已定位：%s → %s" % (start["label"], start["location"]))
            logs.append("终点已定位：%s → %s" % (end["label"], end["location"]))
            raw = live_services.amap_driving_routes(start["location"], end["location"])
            evaluated = live_services.evaluate_route_records(
                raw, truck_type, load, carbon_price, energy_rate)
            if evolve_enabled:
                records = live_services.evolve_route_options(
                    evaluated, explore_prob=0.2, carbon_price=carbon_price,
                    energy_cost_per_km=energy_rate, n_gen=8, pop_size=16,
                    seed=42, decision_log=logs)
            else:
                records = live_services.baseline_route_choices(evaluated)
                logs.append("智能体进化未开启：采用高德备选路线的最低成本利用策略")
        if not records:
            raise RuntimeError("未获得可展示的路线，请调整起终点后重试")
        geometry = sorted(
            [item for item in evaluated if len(item.get("polyline") or []) >= 2],
            key=lambda item: float(item.get("total_cost_yuan", 0.0)))
        st.session_state[STATE_ROUTES] = records
        st.session_state[STATE_GEOMETRY] = geometry
        st.session_state[STATE_OD] = {
            "origin": start["location"], "destination": end["location"],
            "origin_name": start["label"], "destination_name": end["label"],
            "origin_address": start["formatted_address"],
            "destination_address": end["formatted_address"],
        }
        st.session_state[STATE_LOGS] = logs
        st.session_state[STATE_ERROR] = ""
        st.toast("✅ 四维调度方案已生成")
    except Exception as exc:
        st.session_state[STATE_ERROR] = str(exc)
        st.session_state[STATE_LOGS] = logs


def _save(route: Dict[str, Any]) -> None:
    try:
        live_services.save_route_memory(route)
        st.toast("✅ 数据已入库，系统已进化！")
    except Exception as exc:
        st.error("路线写入记忆库失败：%s" % exc)


def _route_map(geometry: List[Dict[str, Any]]) -> None:
    od = st.session_state.get(STATE_OD) or {}
    origin, destination = od.get("origin"), od.get("destination")
    if not geometry or not origin or not destination:
        return
    route_map.render_route_map(
        geometry, origin, destination,
        od.get("origin_name") or "起点", od.get("destination_name") or "终点")


def _table(frame: pd.DataFrame) -> None:
    display = frame.copy()
    display["路线"] = display.apply(
        lambda row: ("⭐ " if bool(row["is_exploration"]) else "")
        + ("⭐ " if bool(row["is_recommended"]) else "") + str(row["route_id"]), axis=1)
    display["探索"] = display["is_exploration"].map({True: "是", False: "否"})
    display["推荐"] = display["is_recommended"].map({True: "是", False: "否"})
    show = display[["路线", "cost", "carbon", "duration", "distance", "tolls",
                    "探索", "推荐", "exploration_strategy"]].rename(columns={
        "cost": "综合成本(元)", "carbon": "碳排放(kg)", "duration": "时间(min)",
        "distance": "距离(km)", "tolls": "过路费(元)",
        "exploration_strategy": "进化策略",
    })

    def highlight(row: pd.Series) -> List[str]:
        is_exploration = bool(frame.loc[row.name, "is_exploration"])
        style = "background-color:rgba(95,227,210,.14);color:#8BF3E6" if is_exploration else ""
        return [style] * len(row)

    st.dataframe(show.style.apply(highlight, axis=1).format({
        "综合成本(元)": "¥{:.2f}", "碳排放(kg)": "{:.2f}",
        "时间(min)": "{:.1f}", "距离(km)": "{:.1f}", "过路费(元)": "¥{:.2f}",
    }), width="stretch", hide_index=True)


def _carbon_price_chart(current: int) -> Dict[str, Any]:
    """碳价敏感度联动图。

    三条线全部由当前碳账快照真实算出（口径与 demo_state.carbon_snapshot 一致）：
      · 当期全额碳成本 = 当期总碳排(t) × 碳价   —— 情景口径：假设全部碳排都按碳价计价
      · 配额抵扣额度   = 当期配额(t)   × 碳价   —— 配额覆盖部分不计成本
      · 配额缺口采购成本 = 配额缺口(t) × 碳价   —— 需要自己掏钱的部分
    另外把当期财务风险（发票新增 + 硬件突发）写进副标题，避免与上面三条混淆。
    """
    snap = carbon_snapshot()
    total_t = float(snap["total_t"])
    quota_t = float(snap["quota_t"])
    gap_t = float(snap["gap_t"])
    invoice_t = float(snap["invoice_t"])
    sensor_cost = float(snap["sensor_cost_yuan"])
    prices = list(range(80, 201, 5))
    gross = [[p, total_t * p] for p in prices]
    covered = [[p, quota_t * p] for p in prices]
    gap_cost = [[p, gap_t * p] for p in prices]
    palette = slider_kit.PALETTE
    subtitle = ("总碳排 %.2f t · 配额 %.2f t · 缺口 %.2f t · 发票新增 %.4f t · 硬件成本 ¥%.2f"
                % (total_t, quota_t, gap_t, invoice_t, sensor_cost))
    return {
        "title": "碳价敏感度",
        "subtitle": subtitle,
        "x_name": "碳价 (元/tCO2)",
        "y_name": "元",
        "x_domain": [80, 200],
        "y_decimals": 1,
        "y_divisor": 10000.0,
        "y_suffix": "万",
        "hover_decimals": 0,
        "hover_suffix": "元",
        "series": [
            # 配额抵扣额度画成面积：它与全额碳成本几乎重合时，
            # 面积能直观看出「配额刚好盖住多少」，不会像两条重合的线那样看不见。
            {"name": "配额抵扣额度", "points": covered, "color": palette["foam"],
             "area": True, "width": 0},
            {"name": "当期全额碳成本（情景）", "points": gross, "color": palette["tide"],
             "width": 2.2},
            {"name": "配额缺口采购成本", "points": gap_cost, "color": palette["coral"],
             "width": 1.8},
        ],
        "cursor": {"follow": "value", "label": "当前碳价 {v} 元/t", "series": 1},
        "interact": "set_x",
        "legend": [
            {"name": "配额抵扣额度", "color": palette["foam"], "style": "opacity:.55"},
            {"name": "全额碳成本（情景）", "color": palette["tide"]},
            {"name": "缺口采购成本", "color": palette["coral"]},
        ],
    }


def _carbon_price_lab(default_carbon_price: float) -> int:
    """碳价实验室：高级滑条 + 敏感度联动图（放在表单外，改价即时生效）。"""
    value = int(min(200, max(80, default_carbon_price)))
    value = int(round(value / 5.0) * 5)
    # 用户在碳台账页改了全局碳价时，沙盘参数回到全局值，避免两边显示不一致
    seen = st.session_state.get("route_carbon_price_global_seen")
    if seen is not None and abs(float(seen) - float(default_carbon_price)) > 1e-9:
        st.session_state["route_carbon_price"] = float(value)
    st.session_state["route_carbon_price_global_seen"] = float(default_carbon_price)
    return int(slider_kit.render_lab(
        key="route_carbon_price",
        label="碳价 (元/tCO2) · 沙盘情景",
        min_value=80, max_value=200, step=5, value=value,
        unit="元/t",
        sub="拖动旋钮，右侧财务敏感度实时联动",
        hint="← → 方向键微调 5 元/tCO2，Home / End 直达端点；在图上点击可直接设定碳价。"
             "默认值取碳台账页的全局碳价（¥%d）；本参数只影响沙盘里的成本与碳排权衡，"
             "不修改主碳账。" % value,
        chart=_carbon_price_chart(value),
        height=330,
    ))


def render(default_carbon_price: float) -> None:
    st.markdown(STYLE, unsafe_allow_html=True)
    carbon_price = _carbon_price_lab(default_carbon_price)
    st.markdown(
        "<div class='rs-panel'><div class='rs-panel-title'>四维碳感知调度控制台</div>"
        "<div class='rs-panel-sub'>真实路线为基因 · 成本 / 碳排 / 时间 / 距离协同寻优</div>",
        unsafe_allow_html=True)
    with st.form("route_evolution_control"):
        left, right = st.columns(2, gap="large")
        with left:
            origin = st.text_input(
                "起点", "上海虹桥站",
                placeholder="地点、车站或地址，例如：上海虹桥站")
            destination = st.text_input(
                "终点", "北京南站",
                placeholder="地点、车站或地址，例如：北京南站")
            st.caption("输入地点名称即可，系统自动解析经纬度；也兼容直接输入坐标。")
            truck_type = st.selectbox("车型", ["重型", "中型", "轻型"])
            load = st.number_input("载重 (t)", 0.0, 60.0, 18.0, 0.5)
        with right:
            energy_rate = st.number_input(
                "能耗成本假设 (元/km)", 0.0, 10.0, 0.8, 0.1,
                help="仅用于沙盘综合成本，不修改主碳账")
            evolve_enabled = st.toggle("🌟 开启智能体进化探索", value=True)
            st.caption("NSGA-II · Q-learning 自适应变异 · Epsilon-Greedy 0.2")
            st.caption("碳价参数见上方「碳价实验室」，当前为 %d 元/tCO2。" % carbon_price)
        submitted = st.form_submit_button(
            "🚀 生成四维碳感知调度方案", width="stretch",
            disabled=not live_services.amap_key_available())
    st.markdown("</div>", unsafe_allow_html=True)

    if not live_services.amap_key_available():
        st.warning("未配置高德 Web 服务 Key，请设置 AMAP_WEB_SERVICE_KEY 或 config/amap_key.txt。")
    if submitted:
        _generate(origin, destination, truck_type, load, carbon_price,
                  energy_rate, evolve_enabled)

    error = st.session_state.get(STATE_ERROR)
    records = st.session_state.get(STATE_ROUTES) or []
    if error:
        st.error("本次求解失败：%s" % error)
        if records:
            st.caption("当前继续展示上一次成功结果")
    if not records:
        _empty()
        return

    frame = pd.DataFrame(records)
    required = {"route_id", "distance", "duration", "tolls", "carbon", "cost",
                "is_exploration", "is_recommended", "exploration_strategy"}
    if frame.empty or not required.issubset(frame.columns):
        st.warning("路线数据格式不完整，请重新生成方案")
        return
    recommended = frame[frame["is_recommended"].astype(bool)]
    best = recommended.iloc[0] if not recommended.empty else frame.iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("推荐综合成本", f"¥{best['cost']:.2f}")
    c2.metric("推荐碳排放", f"{best['carbon']:.2f} kg")
    c3.metric("推荐行驶时间", f"{best['duration']:.1f} min")
    c4.metric("候选 / 探索", f"{len(frame)} / {int(frame['is_exploration'].sum())}")
    od = st.session_state.get(STATE_OD) or {}
    if od.get("origin_name") and od.get("destination_name"):
        st.caption("本次行程：%s → %s　地点已自动解析为路线坐标。" % (
            od["origin_name"], od["destination_name"]))

    st.markdown(
        "<div class='rs-section'><h3>路线空间格局</h3><span>REAL ROUTE GEOMETRY</span></div>",
        unsafe_allow_html=True)
    _route_map(st.session_state.get(STATE_GEOMETRY) or [])

    st.markdown(
        "<div class='rs-section'><h3>四维权衡散点</h3><span>DYNAMIC OBJECTIVES</span></div>",
        unsafe_allow_html=True)
    axis_names = list(AXIS_OPTIONS)
    x_col, y_col = st.columns(2)
    with x_col:
        x_axis = st.selectbox("X 轴", axis_names, index=0, key="route_scatter_x")
    with y_col:
        y_axis = st.selectbox("Y 轴", axis_names, index=1, key="route_scatter_y")
    st.plotly_chart(build_scatter_figure(frame, x_axis, y_axis), width="stretch",
                    config={"displaylogo": False, "responsive": True})

    st.markdown(
        "<div class='rs-section'><h3>四维平行坐标</h3><span>PARETO TRADE-OFF</span></div>",
        unsafe_allow_html=True)
    st.plotly_chart(build_parallel_figure(frame), width="stretch",
                    config={"displaylogo": False, "responsive": True})
    st.markdown(
        "<div class='rs-legend'><span><i class='rs-dot' style='background:#94A3B8'></i>常规</span>"
        "<span><i class='rs-dot' style='background:#5FE3D2'></i>探索</span>"
        "<span><i class='rs-dot' style='background:#FF6B6B'></i>推荐</span></div>",
        unsafe_allow_html=True)

    st.markdown(
        "<div class='rs-section'><h3>路线决策与采纳</h3><span>LEARN BY ACCEPTANCE</span></div>",
        unsafe_allow_html=True)
    _table(frame)
    recommended_records = [r for r in records if bool(r.get("is_recommended"))]
    exploration_records = [r for r in records if bool(r.get("is_exploration"))]
    adopt_col, explore_col = st.columns(2)
    with adopt_col:
        if st.button("✅ 采纳推荐路线", width="stretch",
                     disabled=not recommended_records):
            _save(recommended_records[0])
    with explore_col:
        if exploration_records:
            preferred = next((r for r in exploration_records
                              if r.get("is_recommended")), exploration_records[0])
            if st.button("👣 采纳探索路线并记录进化", width="stretch"):
                _save(preferred)

    with st.expander("🧠 查看智能体决策日志"):
        logs = st.session_state.get(STATE_LOGS) or []
        if logs:
            for message in logs:
                st.markdown("- " + str(message))
        else:
            st.caption("暂无决策日志，生成方案后这里会展示 Q-learning 的动作与奖励。")
    with st.expander("📚 查看历史进化记忆库"):
        try:
            memory = live_services.route_memory()
            if memory.empty:
                st.caption("记忆库为空。采纳推荐或探索路线后，将在这里形成持久化学习记录。")
            else:
                st.metric("累计学习路线", f"{len(memory)} 条")
                st.dataframe(memory.iloc[::-1], width="stretch", hide_index=True)
        except Exception as exc:
            st.warning("记忆库暂时不可读：%s" % exc)
