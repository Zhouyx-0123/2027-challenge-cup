# -*- coding: utf-8 -*-
"""五页主应用视图：所有跨页数字均通过 demo_state 共享。"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

import live_services
import services
from components import (carbon_ledger_panel, dashboard_home, flags, obd_panel,
                        qlearning_panel, route_sandbox, slider_kit, theme)
from demo_state import carbon_snapshot, fallback_disclosure


def render_dashboard() -> None:
    dashboard_home.render_dashboard_home(carbon_snapshot())


def _run_invoice(uploaded) -> None:
    st.session_state['finance_invoice_raw'] = None
    st.session_state['finance_invoice_image'] = None
    st.session_state['finance_invoice_notice'] = ''
    try:
        result = live_services.extract_invoice(uploaded.getvalue(), uploaded.name)
        if result.get("status") == "failed":
            st.session_state["invoice_error"] = result.get("note") or "发票识别失败，请换一张清晰图片"
            st.session_state["current_invoice_result"] = None
            st.session_state["disclosure_note"] = ""
            return
        # Keep the original OCR record before any carbon/fuel estimation.
        st.session_state['finance_invoice_raw'] = dict(result)
        st.session_state['finance_invoice_image'] = (uploaded.getvalue(), uploaded.name)
        # Non-diesel invoices must not inherit the legacy 50 L diesel fallback.
        name = str(result.get('item_name') or '')
        multiple_items = bool(result.get('multiple_items') or len(result.get('items') or []) > 1)
        if multiple_items:
            st.session_state['current_invoice_result'] = None
            st.session_state['current_invoice_carbon'] = 0.0
            st.session_state['current_invoice_fuel'] = None
            st.session_state['current_invoice_estimated'] = False
            st.session_state['invoice_error'] = ''
            st.session_state['finance_invoice_notice'] = '识别到多条发票明细，已保留逐行数据；请登记到运输成本证据表，暂不写入单行财务主表。'
            return
        if name and '柴油' not in name:
            st.session_state['current_invoice_result'] = None
            st.session_state['current_invoice_carbon'] = 0.0
            st.session_state['current_invoice_fuel'] = None
            st.session_state['current_invoice_estimated'] = False
            st.session_state['invoice_error'] = ''
            st.session_state['disclosure_note'] = ''
            st.session_state['finance_invoice_notice'] = '票面信息已识别，可登记到财务主表。本票未使用柴油估算；碳排需按对应类别核算。'
            return
        fuel, estimated = live_services.invoice_fuel_with_estimation(result.get("fuel_liters"))
        summary = live_services.invoice_summary(
            fuel_liters=fuel, distance_km=None, load_t=18.0,
            vclass=3, l_per_100km=28.0)
        carbon_kg = live_services.calc_carbon(18.0, float(summary["distance_km"]), "重型")
        summary["calc_carbon_kg"] = carbon_kg
        summary["is_estimated"] = estimated
        summary['mock'] = bool(result.get('mock'))
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


def _render_finance_register():
    _render_transport_records()
    from finance_panel import render
    render()
    raw = st.session_state.get('finance_invoice_raw')
    image = st.session_state.get('finance_invoice_image')
    if not raw or not image:
        return
    st.subheader('发票费用登记')
    if raw.get('mock'):
        st.warning('模拟识别数据，仅写入模拟主表。')
    st.write({'商品或服务': raw.get('item_name'), '不含税金额': raw.get('amount_excluding_tax'),
              '发票税额': raw.get('tax_amount'), '税率/征收率': raw.get('tax_rate'),
              '价税合计': raw.get('invoice_amount')})
    st.caption('按票面信息登记；税额、税率直接保存，抵扣资格另行判断。')
    multiple_items = bool(raw.get('multiple_items') or len(raw.get('items') or []) > 1)
    if multiple_items:
        st.info('本票包含多条明细，已保留逐行项目；暂不直接写入单行财务主表，请先登记到运输成本证据表。')
    if st.button('记录到财务主表', key='finance_register_save', disabled=multiple_items):
        try:
            report = live_services.register_finance_invoice(raw, image[0], image[1])
            if report['rejected']:
                st.error(report['rejected'][0]['reason'])
            elif report['duplicates']:
                st.info('这张发票已登记，本次未重复新增。')
            else:
                st.success('已记录到财务主表。')
            if not report['rejected']:
                st.session_state['finance_download_mode'] = report['source_mode']
        except (ValueError, OSError) as exc:
            st.error('登记未完成：%s' % exc)
    if st.button('记录到运输成本证据表', key='transport_register_save'):
        try:
            report = live_services.register_transport_invoice(raw, image[0], image[1])
            if report.get('rejected'):
                st.error(report['rejected'][0]['reason'])
            elif report.get('conflicts'):
                st.warning('同票号提交存在冲突，已保留版本并标记待核。')
            elif report.get('duplicates'):
                st.info('这张发票已登记到运输成本证据表，本次未重复新增。')
            else:
                st.success('已记录到运输成本证据表。')
            if not report.get('rejected'):
                st.session_state['transport_download_mode'] = report['source_mode']
        except (ValueError, OSError) as exc:
            st.error('运输成本证据登记未完成：%s' % exc)
    mode = st.session_state.get('finance_download_mode')
    if mode:
        try:
            st.download_button('下载财务主表', live_services.finance_download(mode),
                               file_name='finance_master_'+mode+'.xlsx',
                               mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                               key='finance_register_download')
        except (ValueError,OSError):
            pass


def render_invoice() -> None:
    theme.hero_compact(
        "发票数据感知",
        "发票识别、费用登记与柴油碳排演算。",
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
    if st.session_state.get('finance_invoice_notice'):
        st.info(st.session_state['finance_invoice_notice'])
    _render_finance_register()
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
    st.info(
        "**定位说明（避免误读）**：本页是**仓储 / 办公场所的环境 CO₂ 监测与碳资产管理**"
        "（浓度超标 → 超标碳排量 → 额外碳配额成本，阈值 1000 ppm，"
        "按输入的『监测空间体积 (m³)』折算）。"
        "**它不是运输排放测量**——项目已明确放弃车载 CO₂ 浓度传感器路线，"
        "运输排放由 **OBD+GPS 实测轨**与 **VSP 模型轨**承担（见 docs/57）。")
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
        kicker="03 / ROUTE EVOLUTION",
        badges=["四维 NSGA-II", "Q-learning 真实路网", "历史碳画像"])
    st.caption("实时演示通道：地点解析、路线规划与进化按需运行，不写主碳账；主账只读预计算宽表与结果表。")
    sandbox_tab, real_tab = st.tabs(["四维进化沙盘", "Q-learning 真实路网"])
    with sandbox_tab:
        route_sandbox.render(float(st.session_state["carbon_price"]))
    with real_tab:
        qlearning_panel.render(float(st.session_state["carbon_price"]))


def _sync_ledger_carbon_params() -> None:
    """控件键 page-scoped；规范状态键跨页面保留。"""
    st.session_state["carbon_price"] = float(st.session_state["ledger_carbon_price_input"])
    st.session_state["carbon_quota_t"] = float(st.session_state["ledger_carbon_quota_input"])
    st.session_state["disclosure_note"] = ""


def render_ledger() -> None:
    """碳台账页：两条并行的口径轨道。

    · Tab 1「工程口径 E₀」—— 主链路的实时成账（原有内容，一行未改）；
    · Tab 2「碳会计 A0–A6 审计轨」—— 碳会计交付物的**只读**判定视图。

    两者口径不同（E₀ = TTW 尾管燃烧；碳账 = WTW 全生命周期），**不合并、不相减**。
    """
    theme.hero_compact(
        "碳核算与台账",
        "工程口径 E₀ 实时成账，与碳会计 A0–A6 审计轨并列呈现；两套口径互不换算。",
        kicker="04 / CARBON LEDGER",
        badges=["统一快照", "配额缺口", "碳会计审计轨", "只读判定链"])
    tab_e0, tab_audit = st.tabs([
        "⚙️ 工程口径 E₀（主账）",
        "📋 碳会计口径 A0–A6（审计轨）",
    ])
    with tab_e0:
        _render_ledger_e0()
    with tab_audit:
        carbon_ledger_panel.render_carbon_ledger_panel()


def _render_ledger_e0() -> None:
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


def _render_transport_records():
    from src.transport_evidence import read_store, supplement, export_excel, save_run, list_runs, atomic_json, store_digest, replay_run
    from src.transport_checks import validate_references
    from src.finance_workflow import finance_dir
    import pandas as pd
    import json
    mode = st.selectbox('证据数据性质', ['simulated', 'real'], key='transport_mode')
    directory = finance_dir()
    records = read_store(mode, directory)['records']
    query = st.text_input('检索票号或车牌', key='transport_search')
    visible = [r for r in records if query in str(r.get('invoice_no', '')) or query in str(r.get('plate', ''))]
    st.dataframe([{k: r.get(k) for k in ('record_id', 'invoice_no', 'plate', 'invoice_date', 'missing_fields')} for r in visible])
    st.download_button('下载证据表及票据明细', export_excel(records), file_name='transport_evidence_'+mode+'.xlsx')
    if records:
        rid = st.selectbox('选择待补充记录', [r['record_id'] for r in records], key='transport_selected_'+mode)
        selected = next(r for r in records if r['record_id'] == rid)
        path = directory / selected['source_image']
        if path.is_file():
            try:
                st.image(path.read_bytes(), caption=selected.get('invoice_no'))
            except (ValueError, OSError):
                st.warning('原图无法预览，请下载原图核查')
            st.download_button('下载原图', path.read_bytes(), file_name=path.name)
        st.write('待补充或待核项', selected.get('missing_fields', []))
        fields = {'invoice_no':'发票号码', 'invoice_date':'开票日期', 'seller_name':'销售方',
                  'item_name':'商品名称', 'plate':'车牌', 'quantity':'数量', 'unit':'单位',
                  'unit_price':'票面单价', 'invoice_amount':'价税合计', 'tax_amount':'税额', 'note':'备注'}
        with st.form('transport_edit_'+rid):
            values = {key: st.text_input(label, value=str(selected.get(key) if selected.get(key) is not None else ''), key=rid+'_'+key) for key, label in fields.items()}
            submitted = st.form_submit_button('保存补充信息')
        if submitted:
            try:
                supplement(mode, rid, values, directory, expected_digest=store_digest([selected]))
                st.success('已保存，已有分析批次将标记为过期')
                records = read_store(mode, directory)['records']
            except (ValueError, OSError) as exc:
                st.error(str(exc))
        if selected.get('versions'):
            with st.expander('同票号历史提交与冲突版本'):
                st.json(selected['versions'])
    config_path = directory / ('transport_references_'+mode+'.json')
    refs = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
    with st.expander('参考数据与计算口径'):
        st.caption('上传团队参考配置，可包含车型容量、团队理论因子表、同口径油价、补能时间与车辆位置观测。理论因子支持 team_theory_factors 数组，按车型、能源类型、车牌及有效期匹配。')
        config_upload = st.file_uploader('导入参考配置 JSON', type=['json'], key='transport_references_'+mode)
        factor_upload = st.file_uploader('直接导入团队理论因子表（CSV / XLSX）', type=['csv','xlsx'], key='transport_factor_table_'+mode)
        cost = st.selectbox('燃料成本口径', ['', 'invoice_gross'], index=1 if refs.get('cost_basis')=='invoice_gross' else 0,
                            format_func=lambda v: '待确认' if not v else '票面含税总额按周期里程分配', key='transport_cost_'+mode)
        if st.button('保存参考配置', key='transport_save_refs_'+mode):
            try:
                refs = json.loads(config_upload.getvalue()) if config_upload else refs
                if factor_upload:
                    if factor_upload.name.lower().endswith('.csv'):
                        factor_frame = pd.read_csv(factor_upload)
                    else:
                        factor_frame = pd.read_excel(factor_upload)
                    factor_frame = factor_frame.astype(object).where(pd.notna(factor_frame), None)
                    refs['team_theory_factors'] = factor_frame.to_dict(orient='records')
                refs['cost_basis'] = cost or None
                validate_references(refs)
                atomic_json(config_path, refs)
                st.success('参考配置已保存')
            except (ValueError, OSError) as exc:
                st.error(str(exc))
                refs = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
        st.download_button('下载当前参考配置', json.dumps(refs,ensure_ascii=False,indent=2), file_name='transport_references.json')
    upload = st.file_uploader('上传行程 CSV 或 Parquet', type=['csv','parquet'], key='transport_trips')
    col1, col2 = st.columns(2)
    with col1:
        period_start = st.date_input('统计期间开始', value=None, key='transport_period_start_'+mode)
    with col2:
        period_end = st.date_input('统计期间结束', value=None, key='transport_period_end_'+mode)
    vehicle_options = sorted({str(r.get('plate')) for r in records if r.get('plate')})
    selected_vehicles = st.multiselect('统计车辆范围（不选表示全部车辆）', vehicle_options, key='transport_vehicles_'+mode)
    complete = st.checkbox('已核实上传数据覆盖完整油箱周期', key='transport_complete')
    if upload and st.button('执行匹配和周期分析'):
        try:
            trips = pd.read_parquet(upload) if upload.name.endswith('.parquet') else pd.read_csv(upload, dtype={'trip_id': str, 'waybill_no': str})
            save_run(mode, trips, directory, references=refs, complete_history=complete, period_start=period_start.isoformat() if period_start else None, period_end=period_end.isoformat() if period_end else None, vehicles=selected_vehicles or None)
            st.success('分析批次已保存')
        except (ValueError, OSError) as exc:
            st.error(str(exc))
    runs = list_runs(mode, directory)
    if runs:
        batch = st.selectbox('查看分析批次', [r['run']['batch_id'] for r in runs], key='transport_batch_'+mode)
        result = next(r for r in runs if r['run']['batch_id']==batch)
        stale = result['run']['stale'] or result['run'].get('references',{}) != refs
        if stale:
            st.warning('此批次依据的票据或参考配置已更新，请重新计算。下方保留历史结果。')
        st.write(result['coverage'])
        for label, key in [('匹配结果','matches'), ('周期分配','allocations'), ('路线指标','routes'), ('检查执行状态','checks'), ('异常','anomalies'), ('数据缺口','gaps')]:
            st.write(label)
            st.dataframe(result.get(key, []))
        result['run']['stale'] = stale
        st.download_button('下载核验结果', json.dumps(result, ensure_ascii=False, indent=2), file_name='transport_result_'+batch+'.json')
        if st.button('使用该批次原始快照重新复算', key='transport_replay_'+batch):
            try:
                replayed = replay_run(batch, mode, directory)
                st.success('已基于历史批次快照生成复算批次：'+replayed['run']['batch_id'])
            except (ValueError, OSError) as exc:
                st.error(str(exc))
