# -*- coding: utf-8 -*-
"""首页：暗色电影感叙事页（ScrollTide 结构 + Coolors 色板语言）。

与 docs/47 的关系：
  · 结构取自 scrolltide —— 左侧叙事 + 右侧实时卡片、栅格化 KPI、步骤流；
  · 色彩与「无边界玻璃」取自 coolors —— 大色块、发丝环、强 backdrop 模糊；
  · 底图由 AI 生成的极光与碳网络纹理提供，是玻璃要折射的「被折射物」。

联动纪律：
  · 首页的每一张 KPI 卡、每一个流程步骤都是真实的 st.page_link，
    点击进入对应页面；跨页状态由 demo_state 共享，不在首页重复计算。
  · 顶栏与首页的「当前页」标识一致，均以 url_path 为准。
"""
from __future__ import annotations

import html
from typing import Any, Dict

import streamlit as st

from components import main_nav

# 首页四张 KPI 卡与四个流程步骤的跳转目标（对应 app.py 的 st.Page url_path）
ROUTE_INVOICE = "invoice"
ROUTE_SENSOR = "sensor"
ROUTE_ROUTES = "routes"
ROUTE_LEDGER = "ledger"

CSS = """
<style>
/* 首页专用色板：不再另立一套令牌，只对全站令牌做别名映射。
   （早先把变量写在 .dh-wrap 里是个坑：Streamlit 每次 st.markdown 都是
     独立元素，那个 div 会被浏览器立刻闭合，变量传不到后面的内容。） */
:root{
  --dh-blue:var(--ct-blue); --dh-mint:var(--ct-mint); --dh-amber:var(--ct-amber-500);
  --dh-coral:var(--ct-coral); --dh-ink:var(--ct-ink); --dh-muted:var(--ct-ink-3);
}
.dh-wrap{
  font-family:var(--ct-font-body); color:var(--ct-ink);
}

/* ---------------- Hero：叙事 + 实时卡 ---------------- */
.dh-hero{
  position:relative; isolation:isolate; overflow:hidden;
  display:grid; grid-template-columns:1.06fr .94fr; gap:46px; align-items:center;
  min-height:520px; padding:56px 52px 40px;
  border-radius:var(--ct-r-xl);
  background:linear-gradient(150deg, rgba(255,255,255,.055), rgba(255,255,255,.015));
  -webkit-backdrop-filter:var(--ct-blur-lg); backdrop-filter:var(--ct-blur-lg);
  box-shadow:var(--ct-shadow-lg);
}
/* 极光 + 碳网络纹理：玻璃层折射的底 */
.dh-hero::before{
  content:''; position:absolute; inset:0; z-index:-2;
  background-image:url('/app/static/hero_carbon_mesh.jpg');
  background-size:cover; background-position:center 25%;
  opacity:.55; mix-blend-mode:screen;
  -webkit-mask-image:linear-gradient(120deg,rgba(0,0,0,.95),rgba(0,0,0,.28) 62%,rgba(0,0,0,.7));
  mask-image:linear-gradient(120deg,rgba(0,0,0,.95),rgba(0,0,0,.28) 62%,rgba(0,0,0,.7));
}
.dh-hero::after{
  content:''; position:absolute; inset:0; z-index:-1;
  background:
    radial-gradient(52% 46% at 14% 8%,  rgba(70,183,255,.28), transparent 68%),
    radial-gradient(46% 42% at 88% 92%, rgba(139,243,230,.16), transparent 70%),
    radial-gradient(40% 38% at 72% 6%,  rgba(124,147,255,.14), transparent 72%);
  filter:blur(28px);
}
.dh-eyebrow{
  display:flex; align-items:center; gap:12px;
  font-family:var(--ct-font-display); font-size:11px; font-weight:600;
  letter-spacing:.2em; color:var(--dh-mint); margin-bottom:24px;
}
.dh-eyebrow i{
  width:30px; height:2px; display:block; border-radius:2px;
  background:linear-gradient(90deg,var(--dh-mint),transparent);
}
.dh-copy h1{
  font-family:var(--ct-font-display); font-size:clamp(40px,4.6vw,72px);
  line-height:1.04; letter-spacing:-.055em; font-weight:700; margin:0 0 24px;
  max-width:700px; color:var(--dh-ink);
}
.dh-copy h1 span{ color:var(--dh-blue); position:relative; white-space:nowrap; }
.dh-copy h1 span::after{
  content:''; position:absolute; left:2%; right:0; bottom:-9px; height:7px;
  border-radius:50%; border-top:3px solid var(--dh-coral); transform:rotate(-1deg);
  opacity:.85;
}
.dh-copy>p{
  font-size:16px; line-height:1.9; color:var(--dh-muted); max-width:560px; margin:0 0 24px;
}
.dh-badges{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:30px; }
.dh-badges span{
  padding:6px 12px; border-radius:var(--ct-r-pill);
  background:rgba(255,255,255,.07); box-shadow:0 0 0 1px rgba(255,255,255,.12);
  font-size:12px; color:var(--dh-muted);
}
/* 信任信号条：这批数是哪一次算出来的（企业级 P0 · docs/50 §2） */
.dh-trust{
  display:flex; align-items:center; gap:18px; flex-wrap:wrap;
  padding:12px 18px; margin:16px 0 4px;
  border-radius:16px;
  background:rgba(255,255,255,.045);
  box-shadow:0 0 0 1px rgba(255,255,255,.09);
  font-size:12.5px; color:var(--dh-muted);
  animation:dh-rise .7s .05s both;
}
.dh-trust span{ white-space:nowrap; }
.dh-trust b{
  color:var(--dh-ink); font-weight:600; font-variant-numeric:tabular-nums;
}
.dh-trust .dh-trust-tag{
  padding:3px 10px; border-radius:var(--ct-r-pill);
  background:rgba(139,243,230,.12); box-shadow:0 0 0 1px rgba(139,243,230,.28);
  color:var(--dh-mint); font-size:11px; letter-spacing:.08em; font-weight:600;
}
.dh-trust .dh-trust-warn{
  background:rgba(255,138,91,.14); color:#FF8A5B;
  box-shadow:0 0 0 1px rgba(255,138,91,.30);
}
/* coolors 标志性的色板条 */
.dh-palette{
  display:grid; grid-template-columns:repeat(5,1fr); height:56px; overflow:hidden;
  border-radius:14px; box-shadow:0 10px 30px rgba(0,0,0,.5), 0 0 0 1px rgba(255,255,255,.10);
}
.dh-palette span{
  position:relative; display:flex; align-items:flex-end; padding:8px 10px;
  font-family:var(--ct-font-mono); font-size:8.5px; font-weight:700; letter-spacing:.08em;
  color:rgba(4,10,16,.62);
}
.dh-palette span:last-child{ color:rgba(255,255,255,.55); }

/* Hero 里的 CTA（真实 page_link） */
div[class*="st-key-dh_cta_"]{
  margin-top:26px; display:flex; gap:10px; flex-wrap:wrap;
}
div[class*="st-key-dh_cta_"] [data-testid="stPageLink-NavLink"]{
  display:inline-flex !important; align-items:center; gap:.45rem;
  padding:.66rem 1.25rem !important; border-radius:var(--ct-r-pill) !important;
  font-family:var(--ct-font-display) !important; font-size:13.5px !important;
  font-weight:700 !important; text-decoration:none !important;
  transition:transform .22s var(--ct-ease), box-shadow .22s var(--ct-ease),
             background-color .22s var(--ct-ease);
}
div[class*="st-key-dh_cta_"] [data-testid="stPageLink-NavLink"] p{
  font-family:var(--ct-font-display) !important; font-size:13.5px !important; font-weight:700 !important;
}
div[class*="st-key-dh_cta_"] [data-testid="stPageLink-NavLink"]:hover{ transform:translateY(-2px); }
div[class*="st-key-dh_cta_0"] [data-testid="stPageLink-NavLink"]{
  background:var(--dh-blue) !important; color:#06121F !important;
  box-shadow:0 10px 30px rgba(70,183,255,.30) !important;
}
div[class*="st-key-dh_cta_0"] [data-testid="stPageLink-NavLink"]:hover{
  background:#7CCBFF !important;
}
div[class*="st-key-dh_cta_1"] [data-testid="stPageLink-NavLink"]{
  background:rgba(255,255,255,.07) !important; color:var(--dh-ink) !important;
  box-shadow:0 0 0 1px rgba(255,255,255,.16) !important;
}
div[class*="st-key-dh_cta_1"] [data-testid="stPageLink-NavLink"]:hover{
  background:rgba(255,255,255,.13) !important;
}

/* Hero 右侧实时卡 */
.dh-visual{ position:relative; animation:dh-rise .7s var(--ct-ease-spring) both; }
.dh-live-card{
  position:relative; z-index:2; padding:26px 26px 58px;
  border-radius:26px; background:rgba(255,255,255,.07);
  -webkit-backdrop-filter:var(--ct-blur); backdrop-filter:var(--ct-blur);
  box-shadow:var(--ct-shadow-lg), 0 0 0 1px rgba(255,255,255,.12);
}
.dh-live-head{
  display:flex; align-items:center; justify-content:space-between; margin-bottom:20px;
  font-family:var(--ct-font-display); font-size:10px; font-weight:600;
  letter-spacing:.16em; color:var(--dh-muted);
}
.dh-live{ display:flex; align-items:center; gap:7px; color:var(--dh-mint); }
.dh-live::before{
  content:''; width:7px; height:7px; border-radius:50%; background:var(--dh-mint);
  box-shadow:0 0 0 4px rgba(139,243,230,.16);
}
.dh-total-label{ font-size:12px; color:var(--dh-muted); margin-bottom:4px; }
.dh-total{
  font-family:var(--ct-font-display); font-size:42px; font-weight:600;
  letter-spacing:-.05em; color:var(--dh-ink); font-variant-numeric:tabular-nums;
}
.dh-total small{ font-size:14px; font-weight:500; letter-spacing:0; color:var(--dh-muted); }
.dh-chart{ width:100%; height:118px; margin:10px 0 18px; display:block; }
.dh-mini-grid{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.dh-mini{
  padding:13px 14px; border-radius:14px; background:rgba(255,255,255,.05);
  box-shadow:0 0 0 1px rgba(255,255,255,.09);
}
.dh-mini label{ display:block; font-size:10px; color:var(--dh-muted); margin-bottom:5px; }
.dh-mini b{
  font-family:var(--ct-font-display); font-size:14px; font-weight:600; color:var(--dh-ink);
  font-variant-numeric:tabular-nums;
}
/* 悬浮徽章：只允许压住实时卡底部的留白，不能盖住卡片里的任何内容。
   （旧版 bottom:-26px 时徽章会压住「传感器」小卡，实测重叠 88×49px） */
.dh-float{
  position:absolute; z-index:3; right:-18px; bottom:-38px; padding:14px 18px;
  border-radius:18px; background:rgba(12,16,22,.86);
  -webkit-backdrop-filter:var(--ct-blur); backdrop-filter:var(--ct-blur);
  box-shadow:var(--ct-shadow-lg), 0 0 0 1px rgba(255,255,255,.12);
  animation:dh-rise .85s .12s both;
}
.dh-float label{
  display:block; font-size:9px; color:var(--dh-muted); margin-bottom:4px;
  letter-spacing:.12em; font-family:var(--ct-font-mono);
}
.dh-float b{
  font-family:var(--ct-font-display); font-size:18px; font-weight:600; color:var(--dh-mint);
  font-variant-numeric:tabular-nums;
}

/* ---------------- 区块标题 ---------------- */
.dh-section{ margin-top:46px; animation:dh-rise .65s .08s both; }
.dh-section-head{
  display:flex; align-items:flex-end; justify-content:space-between; gap:20px; margin-bottom:18px;
}
.dh-section-head h2{
  font-family:var(--ct-font-display); font-size:26px; letter-spacing:-.04em; margin:0; color:var(--dh-ink);
}
.dh-section-head span{
  font-family:var(--ct-font-mono); font-size:10px; letter-spacing:.16em; color:var(--dh-muted);
}

/* ---------------- KPI 玻璃卡（可点击跳转） ---------------- */
div[class*="st-key-dh_kpi_"]{
  position:relative; overflow:hidden;
  border-radius:var(--ct-r-lg); padding:1.15rem 1.2rem .9rem;
  background:var(--ct-glass);
  -webkit-backdrop-filter:var(--ct-blur); backdrop-filter:var(--ct-blur);
  box-shadow:var(--ct-shadow-sm);
  transition:transform .3s var(--ct-ease), box-shadow .3s var(--ct-ease);
  height:100%;
}
div[class*="st-key-dh_kpi_"]:hover{ transform:translateY(-6px); box-shadow:var(--ct-shadow-lg); }
.dh-kpi-bar{
  position:absolute; left:0; right:0; top:0; height:3px; background:var(--accent,#46B7FF);
}
.dh-kpi label{
  display:block; font-size:12px; color:var(--dh-muted); margin:0 0 10px;
}
.dh-kpi strong{
  display:block; font-family:var(--ct-font-display); font-size:26px; font-weight:600;
  letter-spacing:-.04em; margin-bottom:6px; color:var(--dh-ink); font-variant-numeric:tabular-nums;
}
.dh-kpi small{ display:block; font-size:11px; color:var(--dh-muted); margin-bottom:.7rem; }
div[class*="st-key-dh_kpi_"] [data-testid="stPageLink-NavLink"]{
  display:inline-flex !important; align-items:center; gap:.4rem;
  padding:.34rem .72rem !important; border-radius:var(--ct-r-pill) !important;
  background:rgba(255,255,255,.07) !important; color:var(--dh-muted) !important;
  box-shadow:0 0 0 1px rgba(255,255,255,.12) !important;
  text-decoration:none !important;
  transition:background-color .2s var(--ct-ease), color .2s var(--ct-ease);
}
div[class*="st-key-dh_kpi_"] [data-testid="stPageLink-NavLink"]:hover{
  background:rgba(70,183,255,.18) !important; color:#EAF6FF !important;
}
div[class*="st-key-dh_kpi_"] [data-testid="stPageLink-NavLink"] p{
  font-family:var(--ct-font-display) !important; font-size:11.5px !important; font-weight:600 !important;
}

/* ---------------- 决策区 ---------------- */
.dh-decision{
  display:grid; grid-template-columns:.72fr 1.28fr; gap:42px; padding:34px 36px;
  border-radius:var(--ct-r-xl); background:var(--ct-glass);
  -webkit-backdrop-filter:var(--ct-blur); backdrop-filter:var(--ct-blur);
  box-shadow:var(--ct-shadow-sm), 0 0 0 1px rgba(255,255,255,.08);
}
.dh-decision h2{
  font-family:var(--ct-font-display); font-size:30px; line-height:1.2; letter-spacing:-.045em;
  margin:0 0 14px; color:var(--dh-ink);
}
.dh-decision>div>p{ font-size:13px; line-height:1.85; color:var(--dh-muted); margin:0; }
.dh-row{
  display:grid; grid-template-columns:1fr 1.15fr auto; gap:18px; align-items:center;
  padding:15px 0; border-bottom:1px solid rgba(255,255,255,.08); font-size:13px;
}
.dh-row:last-child{ border-bottom:none; }
.dh-row b{ font-weight:600; color:var(--dh-ink); }
.dh-row span{ color:var(--dh-muted); font-variant-numeric:tabular-nums; }
.dh-status{
  font-size:11px; padding:5px 10px; border-radius:var(--ct-r-pill);
  background:rgba(255,255,255,.07); box-shadow:0 0 0 1px rgba(255,255,255,.10);
  white-space:nowrap; color:var(--dh-muted);
}

/* ---------------- 步骤流（同样可点击） ---------------- */
/* 决策面板与步骤卡之间必须留出间距：
   两者盒模型紧贴时，决策面板的大圆角底边 + 阴影会把步骤卡顶角「咬」掉一块，
   视觉上等同于重叠。 */
div[class*="st-key-dh_steps"]{ margin-top:34px; }
.dh-flow{
  display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin-top:20px;
}
div[class*="st-key-dh_step_"]{
  position:relative; overflow:hidden; padding:22px 20px 18px;
  border-radius:var(--ct-r-lg); background:var(--ct-glass);
  -webkit-backdrop-filter:var(--ct-blur-sm); backdrop-filter:var(--ct-blur-sm);
  box-shadow:0 0 0 1px rgba(255,255,255,.09);
  transition:transform .3s var(--ct-ease), box-shadow .3s var(--ct-ease);
  height:100%;
}
div[class*="st-key-dh_step_"]:hover{ transform:translateY(-5px); box-shadow:var(--ct-shadow); }
.dh-step i{
  font-family:var(--ct-font-mono); font-style:normal; font-size:11px;
  color:var(--dh-blue); letter-spacing:.1em;
}
.dh-step b{
  display:block; font-family:var(--ct-font-display); font-size:15px; margin:10px 0 7px;
  color:var(--dh-ink);
}
.dh-step p{ font-size:11.5px; line-height:1.75; color:var(--dh-muted); margin:0 0 .8rem; }
div[class*="st-key-dh_step_"] [data-testid="stPageLink-NavLink"]{
  display:inline-flex !important; align-items:center;
  padding:.3rem .66rem !important; border-radius:var(--ct-r-pill) !important;
  background:rgba(70,183,255,.14) !important; color:#BFE6FF !important;
  box-shadow:0 0 0 1px rgba(70,183,255,.28) !important; text-decoration:none !important;
}
div[class*="st-key-dh_step_"] [data-testid="stPageLink-NavLink"]:hover{
  background:rgba(70,183,255,.26) !important; color:#EAF6FF !important;
}
div[class*="st-key-dh_step_"] [data-testid="stPageLink-NavLink"] p{
  font-family:var(--ct-font-display) !important; font-size:11.5px !important; font-weight:600 !important;
}

@keyframes dh-rise{
  from{ opacity:0; transform:translateY(22px); }
  to{ opacity:1; transform:translateY(0); }
}
@media(max-width:1040px){
  .dh-hero{ grid-template-columns:1fr; padding:40px 32px; gap:34px; }
  .dh-flow{ grid-template-columns:1fr 1fr; }
  .dh-decision{ grid-template-columns:1fr; }
}
@media(max-width:640px){
  .dh-hero{ padding:30px 22px; min-height:0; }
  .dh-copy h1{ font-size:38px; }
  .dh-float{ right:6px; bottom:-18px; }
  .dh-decision{ padding:26px 22px; }
  .dh-row{ grid-template-columns:1fr auto; }
  .dh-row>span{ display:none; }
  .dh-flow{ grid-template-columns:1fr; }
}
@media(prefers-reduced-motion:reduce){
  .dh-visual,.dh-float,.dh-section{ animation:none; }
  div[class*="st-key-dh_kpi_"],div[class*="st-key-dh_step_"]{ transition:none; }
}
</style>
"""


def _e(value: Any) -> str:
    return html.escape(str(value))


def _page_link(url_path: str, label: str) -> None:
    """渲染真实跳转链接。

    st.navigation 的页面只认 st.Page 对象，裸路径会抛 StreamlitPageNotFoundError，
    所以统一从 main_nav 的注册表取；页面未登记时静默跳过（不打断页面渲染）。
    """
    page = main_nav.page_by_path(url_path)
    if page is None:
        return
    st.page_link(page, label=label)


def _trust_strip() -> None:
    """信任信号条：这批数是哪一次算出来的、闸门过没过（企业级 P0 · docs/50 §2）。

    只读 ``data/processed/current_run.json``——闸门失败时指针不动，所以这里
    永远是"对外生效的那一版"。没有指针（还没发布过）就明说"未发布"，不编。
    """
    import services

    info = services.current_run()
    if not info.get("published"):
        st.markdown(
            "<div class='dh-trust'><span class='dh-trust-tag dh-trust-warn'>未发布</span>"
            "<span>尚未产生生效版本——执行 <b>python -m src.pipeline</b> 复算并通过闸门后显示</span>"
            "</div>", unsafe_allow_html=True)
        return

    counts = info.get("gate_counts") or {}
    passed = int(counts.get("pass") or 0)
    failed = int(counts.get("fail") or 0)
    skipped = int(counts.get("skip") or 0)
    source = str(info.get("source") or "")
    source_text = "真实数据" if source == "real" else ("演示数据" if source else "未知来源")
    published_at = str(info.get("published_at") or "").replace("T", " ")

    gate_tag = ("dh-trust-tag" if not failed else "dh-trust-tag dh-trust-warn")
    st.markdown(
        "<div class='dh-trust'>"
        "<span class='%s'>生效版本</span>"
        "<span>run <b>%s</b></span>"
        "<span>发布于 <b>%s</b></span>"
        "<span>闸门 <b>%d PASS</b> / <b>%d FAIL</b> / <b>%d SKIP</b></span>"
        "<span>来源 <b>%s</b></span>"
        "<span>代码 <b>%s</b></span>"
        "</div>" % (gate_tag, _e(str(info.get("run_id") or "")),
                    _e(published_at), passed, failed, skipped,
                    _e(source_text), _e(str(info.get("code_version") or ""))),
        unsafe_allow_html=True)


def render_dashboard_home(snap: Dict[str, float]) -> None:
    co2 = st.session_state.get("sensor_co2")
    sensor_warning = bool(st.session_state.get("sensor_is_warning"))
    invoice_count = int(st.session_state.get("today_invoice_count", 0))
    has_invoice = invoice_count > 0
    invoice_status = ("估算待核" if st.session_state.get("current_invoice_estimated")
                      else ("已同步" if has_invoice else "等待识别"))
    sensor_text = "超标预警" if sensor_warning else ("运行正常" if co2 is not None else "等待数据")

    palette = [
        ("#46B7FF", "DATA"), ("#8BF3E6", "LIVE"), ("#FFC46B", "ESTIMATE"),
        ("#FF8A5B", "ALERT"), ("#151A22", "LEDGER"),
    ]
    st.markdown(CSS, unsafe_allow_html=True)

    hero_l, hero_r = st.columns([1.06, 0.94], gap="large", vertical_alignment="center")
    with hero_l:
        st.markdown(
            "<div class='dh-copy'>"
            "<div class='dh-eyebrow'><i></i>CARBON INTELLIGENCE · LIVE</div>"
            "<h1>让每一吨碳，<br><span>都有据可查。</span></h1>"
            "<p>发票、传感器、路线与碳账在同一会话中联动。不是静态报表，"
            "而是一套从物理感知到财务决策的实时碳操作系统。</p>"
            "<div class='dh-badges'><span>多模态感知</span><span>云边端协同</span>"
            "<span>四维路线进化</span><span>智能披露</span></div>"
            "<div class='dh-palette'>" + "".join(
                "<span style='background:%s'>%s</span>" % (color, label)
                for color, label in palette)
            + "</div></div>",
            unsafe_allow_html=True)
        cta_cols = st.columns(2, gap="small")
        with cta_cols[0]:
            with st.container(key="dh_cta_0"):
                _page_link(ROUTE_INVOICE, "感知一张发票 →")
        with cta_cols[1]:
            with st.container(key="dh_cta_1"):
                _page_link(ROUTE_LEDGER, "打开碳台账 →")

    with hero_r:
        st.markdown(
            "<div class='dh-visual'><div class='dh-live-card'>"
            "<div class='dh-live-head'><span>REAL-TIME CARBON PULSE</span>"
            "<span class='dh-live'>LIVE</span></div>"
            "<div class='dh-total-label'>当前总碳排放</div>"
            "<div class='dh-total'>%.4f <small>tCO2e</small></div>"
            "<svg class='dh-chart' viewBox='0 0 420 118' preserveAspectRatio='none'>"
            "<defs><linearGradient id='dhFill' x1='0' y1='0' x2='0' y2='1'>"
            "<stop offset='0' stop-color='#46B7FF' stop-opacity='.30'/>"
            "<stop offset='1' stop-color='#46B7FF' stop-opacity='0'/></linearGradient>"
            "<linearGradient id='dhStroke' x1='0' y1='0' x2='1' y2='0'>"
            "<stop offset='0' stop-color='#8BF3E6'/><stop offset='1' stop-color='#46B7FF'/>"
            "</linearGradient></defs>"
            "<path d='M0 102 C45 95 54 70 92 77 S146 93 177 66 S231 44 261 57 S313 77 340 42 "
            "S390 34 420 14 L420 118 L0 118Z' fill='url(#dhFill)'/>"
            "<path d='M0 102 C45 95 54 70 92 77 S146 93 177 66 S231 44 261 57 S313 77 340 42 "
            "S390 34 420 14' fill='none' stroke='url(#dhStroke)' stroke-width='3' "
            "stroke-linecap='round'/>"
            "<circle cx='420' cy='14' r='5' fill='#8BF3E6'/></svg>"
            "<div class='dh-mini-grid'>"
            "<div class='dh-mini'><label>今日发票</label><b>%d 张</b></div>"
            "<div class='dh-mini'><label>传感器</label><b>%s</b></div></div></div>"
            "<div class='dh-float'><label>碳配额余额</label><b>%.4f t</b></div></div>"
            % (snap["total_t"], invoice_count, _e(sensor_text), snap["balance_t"]),
            unsafe_allow_html=True)

    # ---------------------------------------------------- 信任信号（生效版本）
    _trust_strip()

    # ---------------------------------------------------------------- KPI 区
    st.markdown(
        "<section class='dh-section'><div class='dh-section-head'>"
        "<h2>此刻，系统在感知什么</h2><span>LIVE DECISION SNAPSHOT</span></div></section>",
        unsafe_allow_html=True)
    kpis = [
        ("当前总碳排放", "%.4f t" % snap["total_t"],
         "新增 %.4f t" % (snap["invoice_t"] + snap["sensor_t"]), "#46B7FF",
         ROUTE_LEDGER, "查看台账 →"),
        ("今日发票识别", "%d 张" % invoice_count,
         "当前 %.1f L" % float(st.session_state.get("current_invoice_fuel") or 0), "#8BF3E6",
         ROUTE_INVOICE, "上传发票 →"),
        ("传感器实时状态", sensor_text,
         ("%.0f ppm" % co2) if co2 is not None else "等待 ESP32 数据",
         "#FF8A5B" if sensor_warning else "#8BF3E6", ROUTE_SENSOR, "看监控 →"),
        ("碳配额余额", "%.4f t" % snap["balance_t"],
         ("缺口 %.4f t" % snap["gap_t"]) if snap["gap_t"] else "当前配额充足", "#FFC46B",
         ROUTE_LEDGER, "算缺口 →"),
    ]
    kpi_cols = st.columns(4, gap="medium")
    for idx, (col, (label, value, note, accent, target, cta)) in enumerate(
            zip(kpi_cols, kpis)):
        with col:
            with st.container(key="dh_kpi_%d" % idx):
                st.markdown(
                    "<div class='dh-kpi' style='--accent:%s'>"
                    "<span class='dh-kpi-bar' style='background:%s'></span>"
                    "<label>%s</label><strong>%s</strong><small>%s</small></div>"
                    % (accent, accent, _e(label), _e(value), _e(note)),
                    unsafe_allow_html=True)
                _page_link(target, cta)

    # ---------------------------------------------------------------- 决策区
    rows = [
        ("基准碳账", "%.4f tCO2e" % snap["base_t"],
         "结果表载入" if snap.get("base_source") == "result" else "静态演示快照"),
        ("发票数据感知", "新增 %.6f tCO2e" % snap["invoice_t"], invoice_status),
        ("云边端硬件监控", "突发成本 ¥%.4f" % snap["sensor_cost_yuan"], sensor_text),
        ("财务风险", "¥%.2f" % snap["financial_risk_yuan"],
         "需购买 %.4f t 配额" % snap["gap_t"] if snap["gap_t"] else "无配额缺口"),
    ]
    rows_html = "".join(
        "<div class='dh-row'><b>%s</b><span>%s</span><em class='dh-status'>%s</em></div>"
        % (_e(a), _e(b), _e(c)) for a, b, c in rows)
    st.markdown(
        "<section class='dh-section'><div class='dh-decision'><div>"
        "<h2>同一组状态，<br>驱动每一个决策。</h2>"
        "<p>首页只读共享快照，不在本页重复计算。每一次识别、预警和采纳都会同步到碳账。</p>"
        "</div><div>%s</div></div></section>" % rows_html, unsafe_allow_html=True)

    # ---------------------------------------------------------------- 步骤流
    steps = [
        ("01", "发票识别", "DeepSeek 提取油量，碳排同步入账", ROUTE_INVOICE, "去发票识别 →"),
        ("02", "硬件预警", "ESP32 超标事件联动突发成本", ROUTE_SENSOR, "去硬件监控 →"),
        ("03", "路线进化", "NSGA-II 与 Q-learning 协同寻优", ROUTE_ROUTES, "去路线沙盘 →"),
        ("04", "台账披露", "配额缺口与披露附注实时生成", ROUTE_LEDGER, "去碳台账 →"),
    ]
    # st.columns() 会就地渲染，必须在容器内部调用，否则列是容器的兄弟节点，
    # 容器的 margin-top 管不到它们。
    with st.container(key="dh_steps"):
        step_cols = st.columns(4, gap="medium")
        for col, (num, title, desc, target, cta) in zip(step_cols, steps):
            with col:
                with st.container(key="dh_step_" + num):
                    st.markdown(
                        "<div class='dh-step'><i>%s</i><b>%s</b><p>%s</p></div>"
                        % (num, _e(title), _e(desc)), unsafe_allow_html=True)
                    _page_link(target, cta)


__all__ = ["render_dashboard_home"]
