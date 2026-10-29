# -*- coding: utf-8 -*-
"""五页主应用视图：所有跨页数字均通过 demo_state 共享。"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

import live_services
import services
from components import dashboard_home, flags, obd_panel, route_sandbox, slider_kit, theme
from demo_state import carbon_snapshot, fallback_disclosure


def render_dashboard() -> None:
    dashboard_home.render_dashboard_home(carbon_snapshot())


def _run_invoice(uploaded) -> None:
    try:
        result = live_services.extract_invoice(uploaded.getvalue(), uploaded.name)
        if result.get("status") == "failed":
            st.session_state["invoice_error"] = result.get("note") or "发票识别失败，请换一张清晰图片"
            st.session_state["current_invoice_result"] = None
            st.session_state["disclosure_note"] = ""
            return
        fuel, estimated = live_services.invoice_fuel_with_estimation(result.get("fuel_liters"))
        summary = live_services.invoice_summary(
            fuel_liters=fuel, distance_km=None, load_t=18.0,
            vclass=3, l_per_100km=28.0)
        carbon_kg = live_services.calc_carbon(18.0, float(summary["distance_km"]), "重型")
        summary["calc_carbon_kg"] = carbon_kg
        summary["is_estimated"] = estimated
        st.session_state["current_invoice_carbon"] = float(carbon_kg)
        st.session_state["current_invoice_fuel"] = float(fuel)
        st.session_state["current_invoice_estimated"] = bool(estimated)
        st.session_state["current_invoice_result"] = summary
        st.session_state["today_invoice_count"] += 1
        st.session_state["invoice_history"].append({
            "时间": datetime.now().strftime("%H:%M:%S"), "文件": uploaded.name,
            "油量(L)": fuel, "碳排放(kgCO2)": round(carbon_kg, 3),
            "is_estimated": estimated,
        })
        st.session_state["invoice_error"] = ""
        st.session_state["disclosure_note"] = ""
    except Exception as exc:
        st.session_state["invoice_error"] = "识别或碳核算失败：%s" % exc


def render_invoice() -> None:
    theme.hero_compact(
        "发票数据感知",
        "DeepSeek 多模态识别 → 50L 缺失容错 → calc_carbon → 全局碳账。",
        kicker="01 / INVOICE PERCEPTION", badges=["多模态 OCR", "估算留痕", "状态共享"])
    uploaded = st.file_uploader(
        "上传发票图片", type=["jpg", "jpeg", "png", "webp", "gif"],
        key="main_invoice_upload")
    st.caption("实时演示通道：识别结果只同步本次会话，不回写 E₀、KPI 或闸门。")
    api_ready = bool(live_services.invoice_api_key()) or live_services.invoice_mock_mode()
    if not api_ready:
        st.warning("请配置 DEEPSEEK_API_KEY 或 config/deepseek_key.txt 后使用真实识别。")
    if uploaded:
        st.image(uploaded, caption=uploaded.name, width="stretch")
    if st.button("识别并同步到碳账", width="stretch",
                 disabled=uploaded is None or not api_ready):
        with st.spinner("正在识别发票并计算碳排放…"):
            _run_invoice(uploaded)
    if st.session_state.get("invoice_error"):
        st.error(st.session_state["invoice_error"])
    result = st.session_state.get("current_invoice_result")
    if result:
        if result.get("is_estimated"):
            flags.render_estimate_notice("估算值待人工复核", [
                "未识别出油量，已使用行业默认 50 L 估算。",
                "里程由 28 L/100km 假设油耗反推，属于情景推演，不是对拍口径。",
                "估算结果保留黄色标记，不覆盖主碳账，建议核对原图后再采纳。",
            ])
        if result.get("mock"):
            st.warning("演示识别数据，非真实发票结果")
        c1, c2, c3 = st.columns(3)
        c1.metric("识别油量", f"{st.session_state['current_invoice_fuel']:.1f} L")
        c2.metric("共享碳排放", f"{st.session_state['current_invoice_carbon']:.3f} kgCO2")
        c3.metric("全局状态", "已同步", delta="current_invoice_carbon")
        with st.expander("查看识别与核算明细"):
            st.json({k: v for k, v in result.items() if k != "raw"})
    history = st.session_state.get("invoice_history") or []
    if history:
        theme.section("本次会话识别记录", "跨页面切换后仍保留")
        st.dataframe(pd.DataFrame(history), width="stretch", hide_index=True)


def _sensor_window_chart() -> dict:
    """监测窗口联动图：CO2 序列 + 1000ppm 预警线 + 可点击定位窗口右端。

    图上的点全部来自真实传感器读数（sensor_data.csv），不造数。
    """
    frame = live_services.sensor_data()
    is_mock = live_services.sensor_is_mock()
    if frame.empty or "co2" not in frame.columns:
        return {
            "title": "监测窗口预览",
            "subtitle": "CO2 浓度（ppm）· 实时",
            "empty": "等待传感器数据：请启动 server.py 并发送测试读数。",
            "series": [],
            "interact": "none",
        }
    tail = frame.tail(500)
    points = [[i, float(v)] for i, v in enumerate(tail["co2"].tolist())]
    labels = []
    if "timestamp" in tail.columns:
        labels = [ts.strftime("%H:%M:%S") for ts in tail["timestamp"].tolist()]
    tide = slider_kit.PALETTE["tide"]
    coral = slider_kit.PALETTE["coral"]
    subtitle = "CO2 浓度（ppm）· 共 %d 条读数" % len(points)
    if is_mock:
        subtitle += " · 演示数据，非真实传感器读数"
    return {
        "title": "监测窗口预览",
        "subtitle": subtitle,
        "x_name": "时间",
        "y_name": "ppm",
        "x_labels": labels,
        "x_domain": [0, max(1, len(points) - 1)],
        "y_decimals": 0,
        "hover_decimals": 0,
        "hover_suffix": "ppm",
        "series": [{"name": "CO2", "points": points, "color": tide, "area": True}],
        "hlines": [{"y": 1000, "label": "CO2 预警 1000 ppm", "color": coral}],
        "window": {"from": 0, "to": len(points)},
        "count": len(points),
        "cursor": {"follow": "window_end", "label": "窗口右端 · {v} 条"},
        "interact": "set_x",
        "interact_transform": "reverse_count",
        "legend": [
            {"name": "CO2 浓度", "color": tide},
            {"name": "预警阈值 1000 ppm", "color": coral, "style": "opacity:.7"},
        ],
    }


def render_sensor() -> None:
    theme.hero_compact(
        "云边端硬件监控",
        "ESP32 → Flask → sensor_data.csv → Streamlit；超标事件同步财务状态。",
        kicker="02 / EDGE MONITOR", badges=["2 秒刷新", "CO2 > 1000 ppm", "黄框预警"])
    # 两条数据通道：① CO₂ 环境浓度（原有 ESP32 链路）② OBD+GPS 实测油耗（工科测量方向）。
    # 两者口径完全不同，所以分 tab 呈现，绝不混在一张图里。
    tab_co2, tab_obd = st.tabs(["🌫️ CO₂ 环境浓度", "🚘 OBD+GPS 实测碳排"])
    with tab_co2:
        _render_sensor_co2()
    with tab_obd:
        obd_panel.render_obd_panel()


def _render_sensor_co2() -> None:
    st.caption("实时演示通道：传感器读数与预警成本只驱动当前会话，不回写 E₀ 或结果表。")
    lab_col, side_col = st.columns([2.1, 1], gap="large")
    with lab_col:
        n = slider_kit.render_lab(
            key="sensor_window_n",
            label="折线图最近 N 条",
            min_value=10, max_value=500, step=10, value=100,
            unit="条",
            sub="拖动旋钮，右侧监测窗口实时联动",
            hint="← → 方向键微调 10 条，Home / End 直达端点；在图上点击可直接把窗口右端定位到该读数。",
            chart=_sensor_window_chart(),
            height=330,
        )
        # 防御：任何异常值都不允许把 frame.tail() 打成崩溃（项目纪律：不阻断启动）
        n = int(max(10, min(500, round(float(n)))))
    with side_col:
        volume = st.number_input("监测空间体积 (m³)", 1.0, 100000.0, 100.0, 10.0)
        st.caption("体积用于把 CO2 浓度换算成超标排放量（体积 × 浓度差）。")

    @st.fragment(run_every="2s")
    def live() -> None:
        frame = live_services.sensor_data()
        is_mock = live_services.sensor_is_mock()
        if frame.empty:
            st.info("等待传感器数据，请先启动 server.py 并发送测试读数。")
            return
        if is_mock:
            st.warning("演示数据：当前为确定性演示序列（未接入真实 ESP32），"
                       "不代表现场读数。启动 server.py 并发送测试读数后即切换为真实数据。")
        latest = frame.iloc[-1]
        co2, temp = float(latest["co2"]), float(latest["temp"])
        warning = co2 > 1000
        latest_ledger = live_services.sensor_warning_ledger(
            frame.tail(1), room_volume_m3=volume,
            carbon_price_yuan_t=float(st.session_state["carbon_price"]))
        st.session_state["sensor_co2"] = co2
        st.session_state["sensor_temp"] = temp
        st.session_state["sensor_device"] = str(latest["device_id"])
        st.session_state["sensor_updated_at"] = latest["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
        st.session_state["sensor_is_warning"] = warning
        st.session_state["disclosure_note"] = ""
        if warning and not latest_ledger.empty:
            row = latest_ledger.iloc[-1]
            st.session_state["sensor_penalty"] = float(row["额外碳配额成本(元)"])
            st.session_state["sensor_excess_carbon_t"] = float(row["超标碳排量"]) / 1000.0
        else:
            st.session_state["sensor_penalty"] = 0.0
            st.session_state["sensor_excess_carbon_t"] = 0.0

        if warning:
            st.error("⚠️ 警告：检测到局部碳排放浓度超标！")
        c1, c2, c3 = st.columns(3)
        c1.metric("CO2 浓度", f"{co2:.0f} ppm")
        c2.metric("温度", f"{temp:.1f} °C")
        c3.metric("同步突发成本", f"¥{st.session_state['sensor_penalty']:.6f}")
        st.line_chart(frame.tail(n).set_index("timestamp")[["co2"]],
                      x_label="时间", y_label="CO2 (ppm)", color="#ef4444")
        ledger = live_services.sensor_warning_ledger(
            frame.tail(100), room_volume_m3=volume,
            carbon_price_yuan_t=float(st.session_state["carbon_price"]))
        if not ledger.empty:
            styled = ledger.style.apply(live_services.highlight_sensor_warning, axis=1)
            st.dataframe(styled, width="stretch", hide_index=True)
            st.warning("系统已自动计提因物理环境超标产生的额外碳交易成本")
        st.caption("当前状态取最新一条读数；传感器历史事件不会因 2 秒刷新重复累加。")

    live()


def render_routes() -> None:
    theme.hero_compact(
        "减碳路线沙盘",
        "以高德路线为基因，让 NSGA-II 与 Q-learning 在成本、碳排、时间和距离之间寻找更优解。",
        kicker="03 / ROUTE EVOLUTION", badges=["四维 NSGA-II", "Q-learning 探索", "持久化记忆"])
    st.caption("实时演示通道：地点解析、路线规划与进化按需运行，不写主碳账；主账只读预计算宽表与结果表。")
    route_sandbox.render(float(st.session_state["carbon_price"]))


def _sync_ledger_carbon_params() -> None:
    """控件键 page-scoped；规范状态键跨页面保留。"""
    st.session_state["carbon_price"] = float(st.session_state["ledger_carbon_price_input"])
    st.session_state["carbon_quota_t"] = float(st.session_state["ledger_carbon_quota_input"])
    st.session_state["disclosure_note"] = ""


def render_ledger() -> None:
    with st.expander("碳账参数设置", expanded=True):
        param_a, param_b = st.columns(2)
        with param_a:
            st.number_input(
                "碳价（元/tCO2）", min_value=0.0, max_value=10000.0,
                value=float(st.session_state["carbon_price"]), step=10.0,
                key="ledger_carbon_price_input", on_change=_sync_ledger_carbon_params)
        with param_b:
            st.number_input(
                "当期碳配额（tCO2）", min_value=0.0, max_value=1000000.0,
                value=float(st.session_state["carbon_quota_t"]), step=10.0,
                key="ledger_carbon_quota_input", on_change=_sync_ledger_carbon_params)
    snap = carbon_snapshot()
    theme.hero_compact(
        "碳核算与台账",
        "基准碳账 + 当前发票 + 硬件超标事件，实时形成配额缺口、财务风险与披露附注。",
        kicker="04 / CARBON LEDGER", badges=["统一快照", "配额缺口", "DeepSeek 附注"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("当期总碳排", f"{snap['total_t']:.4f} t")
    c2.metric("碳配额缺口", f"{snap['gap_t']:.4f} t")
    c3.metric("财务罚单/风险", f"¥{snap['financial_risk_yuan']:.2f}")
    c4.metric("配额余额", f"{snap['balance_t']:.4f} t")
    if snap["gap_t"] > 0:
        st.error("综合刚才的发票和硬件超标，本期需购买 %.4f 吨碳配额。" % snap["gap_t"])
    else:
        st.success("当前配额充足，无需新增购买。")

    breakdown = pd.DataFrame([
        {"项目": "基准总碳排", "排放量(tCO2e)": snap["base_t"], "财务影响(元)": 0.0},
        {"项目": "当前发票新增", "排放量(tCO2e)": snap["invoice_t"],
         "财务影响(元)": snap["invoice_cost_yuan"]},
        {"项目": "硬件超标事件", "排放量(tCO2e)": snap["sensor_t"],
         "财务影响(元)": snap["sensor_cost_yuan"]},
        {"项目": "合计", "排放量(tCO2e)": snap["total_t"],
         "财务影响(元)": snap["financial_risk_yuan"]},
    ])
    st.dataframe(breakdown, width="stretch", hide_index=True)
    flags.render_estimate_audit()
    st.caption(
        "财务风险 = 发票新增排放 × 全局碳价 + 硬件突发成本；"
        "硬件超标碳成本不在配额购买成本中重复计提。基准总碳排只读 kpi 结果表，"
        "结果缺失时才显示静态演示快照。")

    if st.button("生成智能披露附注", width="stretch"):
        try:
            if live_services.invoice_api_key():
                with st.spinner("DeepSeek 正在生成披露附注…"):
                    st.session_state["disclosure_note"] = live_services.generate_disclosure_note(snap)
                st.session_state["disclosure_source"] = "deepseek-flash"
            else:
                st.session_state["disclosure_note"] = fallback_disclosure(snap)
                st.session_state["disclosure_source"] = "本地模板（未配置 DeepSeek Key）"
        except Exception as exc:
            st.session_state["disclosure_note"] = fallback_disclosure(snap)
            st.session_state["disclosure_source"] = "本地模板（DeepSeek 调用失败：%s）" % exc
    if st.session_state.get("disclosure_note"):
        st.info(st.session_state["disclosure_note"])
        st.caption("来源：" + st.session_state.get("disclosure_source", ""))
    st.download_button(
        "下载当期联动台账 CSV", data=breakdown.to_csv(index=False).encode("utf-8-sig"),
        file_name="current_carbon_ledger.csv", mime="text/csv", width="stretch")
    with st.expander("查看共享 State"):
        st.json({
            "current_invoice_carbon": st.session_state["current_invoice_carbon"],
            "sensor_penalty": st.session_state["sensor_penalty"],
            "sensor_excess_carbon_t": st.session_state["sensor_excess_carbon_t"],
            "snapshot": snap,
        })
