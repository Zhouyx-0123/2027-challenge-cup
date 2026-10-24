# -*- coding: utf-8 -*-
"""单入口导航：页面由 st.navigation 统一管理，不再手工维护多页 URL。"""
import sys
from pathlib import Path

import streamlit as st

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

PAGES = [
    st.Page("pages/0_概览.py", title="概览", url_path="概览", default=True),
    st.Page("pages/1_基准核算.py", title="核算", url_path="基准核算"),
    st.Page("pages/2_高排放诊断.py", title="诊断", url_path="高排放诊断"),
    st.Page("pages/3_替代方案.py", title="方案", url_path="替代方案"),
    st.Page("pages/4_成本效益.py", title="成本", url_path="成本效益"),
    st.Page("pages/5_碳会计台账.py", title="台账", url_path="碳会计台账"),
    st.Page("pages/6_路线实验室.py", title="路线", url_path="路线实验室"),
    st.Page("pages/7_电动化测算.py", title="测算", url_path="电动化测算"),
    st.Page("pages/8_车队TCO沙盘.py", title="TCO", url_path="车队TCO沙盘"),
]

pg = st.navigation(PAGES, position="hidden")
pg.run()
