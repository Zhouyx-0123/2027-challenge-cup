from html import escape
from io import BytesIO
from pathlib import Path
import hashlib
import json
import pandas as pd
import streamlit as st
from . import core

ROOT = Path(__file__).parent

def html(value):
    st.html(value)

def metric(label, value, unit, foot, featured=False, tag=''):
    html(f'<div class="metric {"featured" if featured else ""}"><div class="label">{label}<span>{tag}</span></div><div class="value">{value}<span class="unit">{unit}</span></div><div class="foot">{foot}</div></div>')

def chart(result, config):
    frame = result['frame']
    maxval = max(frame[core.EMISSION].max(), 1) * 1.15
    bars = []
    for index, row in frame.iterrows():
        h = float(row[core.EMISSION]) / maxval * 165
        green = row['数据层级'] == '绿框'
        if len(frame)<=30:
            bars.append(f'<div class="chart-column" title="原始行 {row["原始行号"]} · {row[core.EMISSION]:.2f} kg"><div class="chart-bar" style="height:{h:.2f}px;background:{"#419269" if green else "#d6b065"}"></div><span>{escape(str(row["日期"]))}</span></div>')
    if len(frame)>30:
        return '<div class="notice">当前数据超过 30 条，请在原始数据页面检索完整记录。</div>'
    return '<div class="chart-bars" role="img" aria-label="逐条排放量柱状图">'+''.join(bars)+'</div>'

def scenario_panel(result):
    rows = ''
    maxallow = max(x['情景额度'] for x in result['scenarios']) or 1
    for i, item in enumerate(result['scenarios']):
        rows += f'<div class="scenario-row {"stress" if i==3 else ""}"><div>{item["情景"]}<small>{item["假设强度"]:.2f} kg/km</small></div><div class="track"><div class="fill" style="width:{item["情景额度"]/maxallow*100:.1f}%"></div></div><b>¥ {item["情景金额"]:.2f}</b></div>'
    return '<div class="panel"><div class="section-head"><h3>强度情景对比</h3><span>SCENARIOS</span></div><div class="panel-sub">条形为情景额度 · 右侧为缺口情景金额</div>' + rows + '</div>'

def overview(result, config):
    a,b,c,d = st.columns(4, gap='small')
    with a: metric('绿框排放量', f'{result["emission"]:,.2f}', 'kgCO₂e', f'{len(result["green"])} 条绿框记录 · 直接计量分组', True, '↗')
    with b: metric('模拟净缺口', f'{result["gap"]:,.2f}', 'kg', f'情景额度 {result["allowance"]:,.2f} kg', tag='◌')
    with c: metric('缺口情景金额', f'{result["cost"]:,.2f}', '元', f'假设碳价 {config["gap_carbon_price"]:g} 元/t', tag='¥')
    with d: metric('影子碳成本中枢', f'{result["shadow"]:,.2f}', '元', f'绿框 {result["shadow_green"]:.2f} / 黄框 {result["shadow_yellow"]:.2f}', tag='◇')
    left,right = st.columns([1.9,1], gap='medium')
    with left:
        html('<div class="panel"><div class="section-head"><h3>排放数据概览</h3><span>EMISSION PROFILE</span></div><div class="panel-sub">逐条记录排放 · kgCO₂e · 样例为十日快照，不年化</div>'+chart(result,config)+'<div class="legend"><span><i class="dot"></i>绿框 · 直接计量分组</span><span><i class="dot yellow"></i>黄框 · 估计分组</span></div></div>')
    with right: html(scenario_panel(result))
    left,right = st.columns([1.9,1], gap='medium')
    with left:
        html('<div class="section-head" style="margin:26px 0 15px"><h3>原始数据速览</h3><span>TRACEABLE DATA</span></div>')
        show_table(result['frame'].head(5))
        st.button('查看全部记录 →', key='all_records', on_click=lambda: st.session_state.update(disclosure_view='原始数据'))
    with right:
        percent = len(result['green'])/len(result['frame'])*100
        html(f'<div class="panel"><div class="section-head"><h3>数据质量分层</h3><span>QUALITY</span></div><div class="panel-sub">按数据质量标识判定，独立于范围归属</div><div class="quality"><div class="ring" style="background:conic-gradient(#3a8e65 0 {percent}%,#d7b064 {percent}% 100%)"><div class="ring-hole">{len(result["frame"])}<span>记录总数</span></div></div><div class="quality-legend"><div><i class="dot"></i>绿框 <b>{len(result["green"])} 条</b></div><div><i class="dot yellow"></i>黄框 <b>{len(result["yellow"])} 条</b></div></div></div><div class="quality-note">黄框不进入配额缺口轨道；电力记录仍需核实排放范围。</div></div>')

def show_table(frame):
    columns = ['原始行号','日期','车牌号','燃料类型',core.DISTANCE,core.EMISSION,'数据层级']
    st.dataframe(frame[columns], hide_index=True, width='stretch', column_config={core.EMISSION:st.column_config.NumberColumn('排放量 / kgCO₂e',format='%.2f'),core.DISTANCE:st.column_config.NumberColumn('里程 / km',format='%.0f')})

@st.dialog('导入车辆排放数据', width='large')
def import_dialog():
    st.caption('支持 .xlsx 或 UTF-8 CSV。上传仅保存在当前会话，不写入仓库。')
    st.download_button('下载 Excel 输入模板',core.input_bytes(core.demo()),'车辆排放输入模板.xlsx')
    upload = st.file_uploader('选择数据文件',type=['xlsx','csv'],max_upload_size=10)
    if upload:
        try:
            raw = pd.read_excel(upload) if upload.name.lower().endswith('.xlsx') else pd.read_csv(upload)
            checked,notes = core.validate(raw)
            st.success(f'校验通过：{len(checked)} 条记录')
            for note in notes: st.warning(note)
            show_table(checked.head(5))
            if st.button('确认导入',type='primary'):
                st.session_state['disclosure_data'] = raw[list(core.demo().columns)]
                st.session_state['disclosure_source'] = upload.name
                st.session_state.pop('disclosure_files',None)
                st.rerun()
        except Exception as exc: st.error(str(exc))

@st.dialog('生成披露底稿', width='large')
def export_dialog(data, config, key):
    st.caption('生成当前数据与参数对应的五张 Excel 工作表及 Word 披露底稿。')
    try:
        with st.spinner('正在调用底稿引擎并执行一致性校验…'):
            if st.session_state.get('disclosure_export_key') != key:
                st.session_state['disclosure_files'] = core.export(data,config)
                st.session_state['disclosure_export_key'] = key
        st.success('底稿已生成。Excel 与 Word 使用同一套披露文本。')
        for name,blob in st.session_state['disclosure_files'].items():
            if name.endswith(('.xlsx','.docx')):
                st.download_button('下载 '+('Excel 计算底稿' if name.endswith('.xlsx') else 'Word 披露底稿'),blob,name,key='dl_'+name,type='primary')
        st.info('用于情景演示与管理分析，不构成法定披露结论。')
    except Exception as exc: st.error(str(exc))

def parameters(config):
    with st.expander('情景参数设置',expanded=True):
        with st.form('disclosure_params'):
            a,b,c = st.columns(3)
            with a:
                intensity=st.number_input('假设历史强度 / kg/km',min_value=.0001,max_value=10.,value=float(config['baseline_intensity']),step=.01,format='%.4f')
                factor=st.number_input('控排系数',min_value=.0001,max_value=1.,value=float(config['scenario_control_factor']),step=.01,format='%.4f')
            with b:
                price=st.number_input('缺口假设碳价 / 元/t',min_value=.01,max_value=100000.,value=float(config['gap_carbon_price']),step=10.)
                shadow=st.number_input('内部影子碳价中枢 / 元/t',min_value=.01,max_value=100000.,value=float(config['shadow_carbon_price_central']),step=10.)
            with c:
                band=st.slider('黄框假设带宽 / %',0,100,round(config['uncertainty_range']*100),5)
                st.caption('假设带宽并非统计置信区间；影子价格未经市场校准。')
            if st.form_submit_button('应用参数并重算',type='primary'):
                st.session_state['disclosure_config'] = dict(config,baseline_intensity=intensity,scenario_control_factor=factor,gap_carbon_price=price,shadow_carbon_price_central=shadow,uncertainty_range=band/100)
                st.rerun()

def render():
    html('<style>'+(ROOT/'style.css').read_text(encoding='utf-8')+'</style>')
    if 'disclosure_data' not in st.session_state:
        st.session_state['disclosure_data']=core.demo()
        st.session_state['disclosure_source']='默认演示样例'
    if 'disclosure_config' not in st.session_state: st.session_state['disclosure_config']=core.defaults()
    config=st.session_state['disclosure_config']
    with st.sidebar:
        html('<div class="brand"><div class="brand-icon">∿</div><div><div class="brand-name">碳序<span style="color:#8bb898;font-weight:400"> / </span></div><div class="brand-sub">CARBON DISCLOSURE</div></div></div><div class="workspace">工作空间 / WORKSPACE</div>')
        view=st.radio('工作台导航',['分析总览','原始数据','情景分析','披露底稿'],key='disclosure_view',label_visibility='collapsed')
        st.divider()
        html('<div class="workspace">数据集 / DATASET</div>')
        st.caption(st.session_state['disclosure_source'])
        if st.button('＋ 导入数据',width='stretch'): import_dialog()
        if st.button('↻ 恢复演示样例',width='stretch'):
            st.session_state['disclosure_data']=core.demo()
            st.session_state['disclosure_source']='默认演示样例'
            st.session_state['disclosure_config']=core.defaults()
            st.rerun()
        html('<div class="sidebar-note"><strong>让碳数据走向可解释的披露</strong><br>分层核算 · 情景推演 · 底稿溯源<br><br>教学演示 / 独立分析轨<br>不写入工程主账</div>')
    data=st.session_state['disclosure_data']
    try: result=core.calculate(data,config)
    except ValueError as exc:
        st.error(str(exc)); return
    html(f'<div class="topline"><div>工作空间 <span style="margin:0 12px;color:#bdc9be">/</span><b>碳披露工作台</b><span style="margin:0 12px;color:#bdc9be">/</span>{view}</div><span class="status">● 情景演示</span></div>')
    title_col,action=st.columns([3,1.15],vertical_alignment='center')
    with title_col:
        html('<div class="eyebrow">CARBON INSIGHT / DISCLOSURE WORKBENCH</div>')
        st.title({'分析总览':'从排放数据，到披露洞察。','原始数据':'每一条数据，都有迹可循。','情景分析':'让不同假设，变得可比较。','披露底稿':'把分析，转化为披露底稿。'}[view])
    key=hashlib.sha256(data.to_json(force_ascii=False,date_format='iso').encode()+json.dumps(config,sort_keys=True).encode()).hexdigest()
    with action:
        if st.button('↓ 生成披露底稿',type='primary',width='stretch'): export_dialog(data,config,key)
    html(f'<div class="intro">车辆排放与财务情景分析 · 双轨呈现，清晰解释每一个数字的来源。<br><span class="period">参数报告期：{escape(config["report_period_start"])} — {escape(config["report_period_end"])}　 /　{len(data)} 条记录　 /　{escape(st.session_state["disclosure_source"])}</span></div>')
    if st.session_state['disclosure_source']!='默认演示样例':
        st.caption('自定义数据尚未核验完整报告期，日期按原参数展示，不代表上传数据覆盖该期间。')
    if view=='分析总览': overview(result,config)
    elif view=='原始数据':
        st.caption('编辑后点击保存生效；原始行号对应输入表行号，不支持人工修改质量分层结果。')
        edited=st.data_editor(data,hide_index=True,width='stretch',num_rows='dynamic',key='disclosure_editor')
        if st.button('保存并重新计算',type='primary'):
            try:
                core.validate(edited)
                st.session_state['disclosure_data']=edited
                st.session_state['disclosure_source']='当前会话编辑数据'
                st.rerun()
            except ValueError as exc: st.error(str(exc))
        a,b=st.columns([2,1])
        with a: search=st.text_input('按车牌号检索',placeholder='输入车牌号…')
        with b: tier=st.selectbox('数据层级',['全部','绿框','黄框'])
        filtered=result['frame']
        if search: filtered=filtered[filtered['车牌号'].astype(str).str.contains(search,regex=False)]
        if tier!='全部': filtered=filtered[filtered['数据层级']==tier]
        show_table(filtered)
        st.download_button('下载当前输入 Excel',core.input_bytes(data),'当前输入.xlsx')
        for note in result['notes']: st.warning(note)
    elif view=='情景分析':
        parameters(config)
        a,b=st.columns([1.3,1])
        with a: html(scenario_panel(result))
        with b:
            html(f'<div class="panel"><div class="section-head"><h3>两条分析轨道</h3><span>INDEPENDENT TRACKS</span></div><div class="panel-sub">两轨金额不能相加</div><h3>配额缺口情景　¥ {result["cost"]:.2f}</h3><p class="intro">仅绿框 · 先汇总净差额，再取正<br>绿框里程 {result["distance"]:.0f} km × 假设强度 × 控排系数</p><hr style="border:0;border-top:1px solid #e3eae6"><h3>影子碳成本中枢　¥ {result["shadow"]:.2f}</h3><p class="intro">绿框 ¥ {result["shadow_green"]:.2f} / 黄框 ¥ {result["shadow_yellow"]:.2f}<br>黄框假设带宽 ¥ {result["low"]:.2f} — {result["high"]:.2f}</p></div>')
        st.dataframe(pd.DataFrame(result['scenarios']),hide_index=True,width='stretch')
        st.caption(f'主情景有符号净差额：{result["delta"]:.2f} kg；有效强度阈值：{config["baseline_intensity"]*config["scenario_control_factor"]:.4f} kg/km。')
    else:
        html('<div class="flow"><b>01 参数与口径</b><span>→</span><b>02 原始数据</b><span>→</span><b>03 双轨分析</b><span>→</span><b>04 披露文本</b><span>→</span><b>05 底稿留存</b></div>')
        st.subheader('五张工作表，一个可追溯的计算链')
        for name,desc in [('00_参数','记录假设、价格、报告期与编制信息'),('01_原始数据','保留输入记录、行号和质量分层'),('02_配额缺口模拟','仅绿框；汇总差额后计算模拟净缺口'),('03_影子碳成本','绿黄分列，展示黄框假设带宽'),('04_披露文本生成','四层披露文本，与 Word 同源')]:
            with st.expander(name): st.write(desc)
        st.info('顶部“生成披露底稿”调用原始引擎生成可下载的 .xlsx 与 .docx。修改数据或参数后会重新生成。')
        st.markdown((ROOT/'engine/POLICY_NOTE.md').read_text(encoding='utf-8'))
    html('<div class="notice"><span class="note-title">ⓘ 模型边界</span>假设强度与碳价用于管理情景。缺口轨道和影子轨道不可相加；情景金额不是实际购买义务、会计费用或负债。电力范围归属及 CO₂ / CO₂e 口径需另行核对。</div><div class="footer"><span>碳序 / CARBON DISCLOSURE WORKBENCH</span><span>可追溯 · 可解释 · 可复核　 /　DEMO V1.0</span></div>')
