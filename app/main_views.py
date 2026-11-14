# -*- coding: utf-8 -*-
"""六页主应用视图：所有跨页数字均通过 demo_state 共享。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import live_services
import services
from components import (carbon_disclosure_panel, dashboard_home, flags, obd_panel,
                        qlearning_panel, route_sandbox, theme)
from demo_state import carbon_snapshot


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
    from finance_panel import render
    render()
    with st.expander('运输成本证据（票面原始记录，15 列证据表）'):
        st.caption(
            '本表只登记票面原始记录（15 列证据表），供运输成本核验与导出；'
            '财务主表负责人工确认、抵扣判断与跨趟分摊。两者用途不同，'
            '请勿把证据表当作财务主表使用。')
        _render_transport_records()


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


def _obd_raw_caption() -> None:
    """硬件页顶部一行实流事实：只读 data/raw，不进 E₀。"""
    raw = services.latest_raw_stream()
    if not raw:
        st.caption("设备实流：data/raw/obd_gps/obd_stream.csv 尚无数据 —— 板子上电后 POST /api/upload_obd_gps。")
        return
    window = int(raw.get("window_s") or 0)
    st.caption(
        "设备实流：%s 行 × %d 列，UTC %s → %s（窗口 %dh%02dm），%d 台车；"
        "文件 %s，%.1f KB。图表读 data/processed 的核算结果，实流本身不进 E₀。"
        % (raw.get("rows"), raw.get("columns"), raw.get("first_ts"), raw.get("last_ts"),
           window // 3600, window % 3600 // 60, raw.get("n_vehicles"),
           raw.get("mtime"), (raw.get("bytes") or 0) / 1024.0))


def render_sensor() -> None:
    theme.hero_compact(
        "云边端硬件监控",
        "ESP32-C3 → WiFi → Flask → obd_stream.csv → Streamlit；守恒法算碳，实测轨不进 E₀。",
        kicker="02 / EDGE MONITOR",
        badges=["OBD+GPS 实测", "守恒法算碳", "实测轨不进 E₀"])
    st.caption(
        "本页只保留 OBD+GPS 一条硬件链路：CO₂ 环境浓度传感器通道已于 2026-11-08 删除，"
        "运输油耗与碳排由车载 OBD 守恒法计算，不用环境 CO₂ 浓度折算。")
    _obd_raw_caption()
    obd_panel.render_obd_panel()


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


def render_ledger() -> None:
    """主导航碳台账的唯一页面：排放接收、范围归集与披露依据。"""
    theme.hero_compact(
        "碳台账",
        "接收排放结果，归集范围一、二、三；每一笔排放都能追溯来源、方法和披露依据。",
        kicker="04 / CARBON LEDGER",
        badges=["CSV 自动归集", "范围一二三", "披露依据", "逐条追溯"])
    carbon_disclosure_panel.render_carbon_disclosure_panel()


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
        scope = result['run'].get('scope', {})
        if scope.get('excluded_invoice_count'):
            st.caption(
                f"核算口径：{scope.get('invoice_count', 0)} 张能源票进入油耗/碳账；"
                f"{scope['excluded_invoice_count']} 张维修/保养等非能源票只保留证据，已排除。"
            )
        for label, key in [('匹配结果','matches'), ('周期分配','allocations'), ('路线指标','routes'), ('检查执行状态','checks'), ('异常','anomalies'), ('数据缺口','gaps'), ('已排除非能源票','excluded')]:
            if key == 'excluded' and not result.get('excluded'):
                continue
            st.write(label)
            st.dataframe(result.get(key, []))
        result['run']['stale'] = stale
        st.download_button('下载核验结果', json.dumps(result, ensure_ascii=False, indent=2), file_name='transport_result_'+batch+'.json')
        from src.fuel_layer import build_fuel_layer, write_fuel_layer
        layer = build_fuel_layer(result)
        layer_liters = float(layer['allocated_liters'].fillna(0).sum()) if len(layer) else 0.0
        layer_km = float(layer['trip_km'].fillna(0).sum()) if len(layer) else 0.0
        weighted = 100.0 * layer_liters / layer_km if layer_km else 0.0
        st.caption(
            f"油耗中间层：{len(layer)} 条趟级分摊，{layer_km:,.1f} km，"
            f"{layer_liters:,.2f} L，加权 {weighted:.2f} L/100km。"
            "只汇总运输证据，不自动写财务主表分摊，不改碳账。"
        )
        layer_path = directory / ('fuel_layer_' + mode + '.xlsx')
        if st.button('生成本批次油耗中间层', key='transport_fuel_layer_' + batch):
            try:
                report = write_fuel_layer(result, directory)
                st.success(f"已生成：{Path(report['xlsx']).name}（{report['rows']} 行）")
                if report.get('json'):
                    st.caption('同时生成可审计 JSON：' + Path(report['json']).name)
            except (ValueError, OSError) as exc:
                st.error(str(exc))
        if layer_path.is_file():
            st.download_button('下载油耗中间层（xlsx）', layer_path.read_bytes(),
                               file_name=layer_path.name, key='transport_fuel_download_' + batch)
        if st.button('使用该批次原始快照重新复算', key='transport_replay_'+batch):
            try:
                replayed = replay_run(batch, mode, directory)
                st.success('已基于历史批次快照生成复算批次：'+replayed['run']['batch_id'])
            except (ValueError, OSError) as exc:
                st.error(str(exc))
