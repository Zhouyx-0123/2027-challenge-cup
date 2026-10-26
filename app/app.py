# -*- coding: utf-8 -*-
"""物流企业碳感知智能决策系统：顶部定制导航与全局状态入口。"""
import sys
from pathlib import Path

import streamlit as st

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# 页面配置必须先于重型模块导入，避免用户在 pymoo/pandas 加载期间看到裸界面。
st.set_page_config(
    page_title="物流企业碳感知智能决策系统",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# theme 仅依赖 Streamlit；关键 CSS 先于算法、图表和业务视图注入。
from components import theme
st.markdown(theme.CSS, unsafe_allow_html=True)  # 只注入样式，不加载旧导航 JS / iframe。

from components.main_nav import register_pages, render_main_nav
from demo_state import init_state
from main_views import (
    render_dashboard,
    render_invoice,
    render_ledger,
    render_routes,
    render_sensor,
)

init_state()
PAGES = [
    st.Page(render_dashboard, title="🏠 首页概览", url_path="dashboard", default=True),
    st.Page(render_invoice, title="📄 发票数据感知", url_path="invoice"),
    st.Page(render_sensor, title="📡 云边端硬件监控", url_path="sensor"),
    st.Page(render_routes, title="🗺️ 减碳路线沙盘", url_path="routes"),
    st.Page(render_ledger, title="📊 碳核算与台账", url_path="ledger"),
]
page = st.navigation(PAGES, position="hidden")
register_pages(PAGES)
render_main_nav(page.url_path, PAGES)
page.run()
