# -*- coding: utf-8 -*-
"""主应用顶部导航：悬浮玻璃药丸条（ScrollTide 式）。

设计要点（逆向来源见 docs/47_前端重设计v2_暗色电影感.md）：
  · 形态   —— scrolltide 的 sticky 毛玻璃 + 药丸导航（hover 才出底）；
  · 无边界 —— 药丸本身不描边，边界由 backdrop-filter 与扩散阴影给出；
  · 事件源 —— 只用原生 st.page_link，不自绘 <a>、不挂 JS 桥接，
              避免历史上出现过的「一次点击打开两个页面」；
  · 活动态 —— 当前页用 tide 蓝玻璃药丸 + 亮字，其余为纯文字弱化。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import streamlit as st

NAV_ITEMS = [
    {"id": "dashboard", "label": "首页"},
    {"id": "invoice", "label": "发票感知"},
    {"id": "sensor", "label": "硬件监控"},
    {"id": "routes", "label": "路线沙盘"},
    {"id": "ledger", "label": "碳台账"},
]

# 页面注册表：app.py 建好 st.Page 后登记进来。
# st.navigation 的页面只认 Page 对象，不接受 "/invoice" 这种裸路径，
# 首页的 KPI 卡与步骤流要做真实跳转，就必须拿到同一个 Page 实例。
_PAGE_REGISTRY: Dict[str, Any] = {}


def register_pages(pages: List[Any]) -> None:
    """登记 st.navigation 的页面对象，供首页等处做真实跳转。"""
    _PAGE_REGISTRY.clear()
    for page in pages:
        url_path = getattr(page, "url_path", None)
        if url_path:
            _PAGE_REGISTRY[url_path] = page


def page_by_path(url_path: str) -> Optional[Any]:
    """按 url_path 取回页面对象；未登记时返回 None（调用方负责降级）。"""
    return _PAGE_REGISTRY.get(url_path)

NAV_CSS = """
<style>
/* ---------- 外层：吸顶层，自己不带任何材质 ---------- */
div[class*="st-key-cm_nav_bar"], .st-key-cm_nav_bar{
  position:sticky; top:.5rem; z-index:900;
  background:transparent !important;
  border-radius:0 !important; box-shadow:none !important;
  padding:.15rem 0 .7rem !important;
}

/* ---------- 内层：悬浮玻璃药丸 ---------- */
div[class*="st-key-cm_nav_pill"], .st-key-cm_nav_pill{
  max-width:1340px; margin:0 auto;
  border-radius:var(--ct-r-pill) !important;
  background:rgba(9,12,18,.58) !important;
  -webkit-backdrop-filter:blur(24px) saturate(170%);
  backdrop-filter:blur(24px) saturate(170%);
  box-shadow:0 16px 48px rgba(0,0,0,.58), 0 0 0 1px rgba(255,255,255,.10) !important;
  padding:.34rem .42rem .34rem .58rem !important;
}
/* 药丸内的三列：品牌 | 导航 | 状态 */
div[class*="st-key-cm_nav_pill"] [data-testid="stHorizontalBlock"],
.st-key-cm_nav_pill [data-testid="stHorizontalBlock"]{ align-items:center; gap:.5rem; }
div[class*="st-key-cm_nav_pill"] [data-testid="stColumn"],
.st-key-cm_nav_pill [data-testid="stColumn"]{ min-width:0 !important; }
div[class*="st-key-cm_nav_pill"] [data-testid="stColumn"] [data-testid="stMarkdownContainer"],
.st-key-cm_nav_pill [data-testid="stColumn"] [data-testid="stMarkdownContainer"]{
  width:100% !important; min-width:0 !important;
}

/* ---------- 品牌 ---------- */
.cm-brand{ display:flex; align-items:center; gap:.6rem; overflow:hidden; }
.cm-brand .mark{
  display:inline-flex; align-items:center; justify-content:center; flex:none;
  width:34px; height:34px; border-radius:11px; overflow:hidden;
  background:linear-gradient(145deg,var(--ct-blue-500),var(--ct-blue-deep));
  box-shadow:0 6px 20px rgba(70,183,255,.32), 0 0 0 1px rgba(255,255,255,.12);
}
.cm-brand .mark img{ width:100%; height:100%; display:block; object-fit:cover; }
.cm-brand .word{
  display:block; font-family:var(--ct-font-display); font-weight:700;
  font-size:15.5px; color:var(--ct-ink); line-height:1.14; letter-spacing:-.02em;
  white-space:nowrap;
}
.cm-brand .word em{ font-style:normal; color:var(--ct-blue); }
.cm-brand .sub{
  display:block; font-family:var(--ct-font-mono); font-size:7px;
  letter-spacing:.12em; color:var(--ct-ink-4); text-transform:uppercase; margin-top:2px;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.cm-brand-text{ min-width:0; }

/* ---------- 导航：药丸化 ---------- */
div[class*="st-key-main_nav_"] [data-testid="stPageLink-NavLink"]{
  display:flex !important; align-items:center; justify-content:center;
  min-height:36px; padding:.40rem .82rem !important;
  border-radius:var(--ct-r-pill) !important;
  color:var(--ct-ink-3) !important; text-decoration:none !important;
  transition:background-color .22s var(--ct-ease), color .22s var(--ct-ease),
             box-shadow .22s var(--ct-ease);
}
div[class*="st-key-main_nav_"] [data-testid="stPageLink-NavLink"]:hover{
  background:rgba(255,255,255,.09) !important; color:var(--ct-ink) !important;
}
div[class*="st-key-main_nav_"] [data-testid="stPageLink-NavLink"][aria-disabled="true"]{
  background:rgba(70,183,255,.18) !important; color:#EAF6FF !important;
  box-shadow:0 0 0 1px rgba(70,183,255,.34), 0 6px 18px rgba(70,183,255,.16) !important;
  cursor:default;
}
div[class*="st-key-main_nav_"] [data-testid="stPageLink-NavLink"] p,
div[class*="st-key-main_nav_"] [data-testid="stPageLink-NavLink"] p *{
  font-family:var(--ct-font-display) !important;
  font-size:13px !important; font-weight:600 !important; white-space:nowrap;
}

/* ---------- 右侧状态：纯文字（scrolltide 的处理方式） ---------- */
.cm-head-status{ display:flex; align-items:center; gap:.55rem; flex-wrap:nowrap; min-width:0; }
.cm-head-pill{
  display:inline-flex; align-items:center; gap:.32rem;
  font-family:var(--ct-font-mono); font-size:9.5px; font-weight:600;
  letter-spacing:.1em; color:var(--ct-ink-4); white-space:nowrap;
}
.cm-head-pill.live{ color:var(--ct-mint); }
.cm-head-pill.live .cm-dot{
  width:6px; height:6px; border-radius:50%; background:var(--ct-mint);
  box-shadow:0 0 0 3px rgba(139,243,230,.18);
  animation:cm-pulse 2.4s var(--ct-ease) infinite;
}
.cm-head-pill .cm-dot{ width:6px; height:6px; border-radius:50%; background:var(--ct-ink-4); }
.cm-head-sep{ color:var(--ct-ink-4); opacity:.45; font-family:var(--ct-font-mono); }

@media (max-width:1080px){
  .cm-brand .sub{ display:none; }
  div[class*="st-key-main_nav_"] [data-testid="stPageLink-NavLink"]{
    padding:.36rem .6rem !important;
  }
  div[class*="st-key-main_nav_"] [data-testid="stPageLink-NavLink"] p{
    font-size:12px !important;
  }
}
@media (max-width:820px){
  .cm-brand .word{ font-size:14px; }
  .cm-head-pill span:not(.cm-dot){ display:none; }
  .cm-head-status{ gap:.35rem; }
}
@media (prefers-reduced-motion:reduce){
  div[class*="st-key-main_nav_"] [data-testid="stPageLink-NavLink"]{ transition:none; }
}
</style>
"""


def render_main_nav(current: str, pages: List[Any]) -> None:
    st.markdown(NAV_CSS, unsafe_allow_html=True)
    with st.container(key="cm_nav_bar"):
        with st.container(key="cm_nav_pill"):
            brand_col, links_col, status_col = st.columns(
                [0.20, 0.62, 0.18], gap="small", vertical_alignment="center")
            with brand_col:
                st.markdown(
                    "<div class='cm-brand'>"
                    "<span class='mark'><img src='/app/static/brand_mark.png' alt=''></span>"
                    "<span class='cm-brand-text'><span class='word'>碳感知<em>智算</em></span>"
                    "<span class='sub'>LOGISTICS CARBON OS</span></span></div>",
                    unsafe_allow_html=True)
            with links_col:
                nav_columns = st.columns(len(NAV_ITEMS), gap="small")
                for item, page, column in zip(NAV_ITEMS, pages, nav_columns):
                    with column:
                        with st.container(key="main_nav_" + item["id"]):
                            # 唯一事件源：原生 page_link。当前页 disabled，只做视觉标识。
                            st.page_link(page, label=item["label"],
                                         disabled=(item["id"] == current))
            with status_col:
                st.markdown(
                    "<div class='cm-head-status'>"
                    "<span class='cm-head-pill live'><span class='cm-dot'></span>LIVE</span>"
                    "<span class='cm-head-sep'>·</span>"
                    "<span class='cm-head-pill'><span class='cm-dot'></span>SESSION SYNC</span>"
                    "</div>",
                    unsafe_allow_html=True)


__all__ = ["NAV_ITEMS", "render_main_nav", "register_pages", "page_by_path"]
