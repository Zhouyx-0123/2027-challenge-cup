# -*- coding: utf-8 -*-
"""五页主应用：共享状态与跨模块联动测试。"""
from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd
from streamlit.testing.v1 import AppTest

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def _run(script, *args) -> AppTest:
    at = AppTest.from_function(script, args=args, default_timeout=40)
    at.run()
    assert not at.exception, "; ".join(str(e.value) for e in at.exception)
    return at


def test_invoice_writes_shared_carbon_state(monkeypatch):
    import main_views

    monkeypatch.setattr(main_views.services, "extract_invoice", lambda *args: {
        "fuel_liters": 65.5, "mock": False})
    monkeypatch.setattr(main_views.services, "invoice_summary", lambda **kwargs: {
        "distance_km": 233.9})
    monkeypatch.setattr(main_views.services, "calc_carbon", lambda *args: 210.5)

    class Uploaded:
        name = "invoice.jpg"

        @staticmethod
        def getvalue():
            return b"image"

    def script(uploaded_cls):
        from demo_state import init_state
        from main_views import _run_invoice
        init_state()
        _run_invoice(uploaded_cls())

    at = _run(script, Uploaded)
    assert at.session_state["current_invoice_carbon"] == 210.5
    assert at.session_state["current_invoice_fuel"] == 65.5
    assert at.session_state["today_invoice_count"] == 1
    assert len(at.session_state["invoice_history"]) == 1


def test_sensor_warning_writes_penalty_state(monkeypatch):
    import main_views

    frame = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-10-26 16:00:00"]),
        "device_id": ["env_01"], "co2": [1200.0], "temp": [26.5],
    })
    monkeypatch.setattr(main_views.services, "sensor_data", lambda: frame)

    def script():
        from demo_state import init_state
        from main_views import render_sensor
        init_state()
        render_sensor()

    at = _run(script)
    assert at.session_state["sensor_is_warning"] is True
    assert at.session_state["sensor_penalty"] > 0
    assert at.session_state["sensor_excess_carbon_t"] > 0


def test_shared_snapshot_updates_gap_and_financial_risk():
    def script():
        import streamlit as st
        from demo_state import carbon_snapshot, init_state
        init_state()
        st.session_state["current_invoice_carbon"] = 2000.0  # 2t
        st.session_state["sensor_excess_carbon_t"] = 1.0
        st.session_state["sensor_penalty"] = 80.0
        st.session_state["carbon_price"] = 100.0
        snap = carbon_snapshot()
        st.session_state["test_snapshot"] = snap

    at = _run(script)
    snap = at.session_state["test_snapshot"]
    assert snap["total_t"] == 4505.04
    assert snap["gap_t"] == 3.0
    assert snap["balance_t"] == -3.0
    assert snap["financial_risk_yuan"] == 280.0


def test_all_five_views_render_without_exceptions():
    from main_views import (
        render_dashboard, render_invoice, render_ledger, render_routes, render_sensor)

    for view in (render_dashboard, render_invoice, render_routes, render_ledger):
        def script(page_view):
            import streamlit as st
            from demo_state import init_state
            from components import theme
            init_state()
            st.markdown(theme.CSS, unsafe_allow_html=True)
            page_view()

        _run(script, view)


def test_main_entry_uses_custom_top_navigation():
    source = (APP_DIR / "app.py").read_text(encoding="utf-8")
    nav_source = (APP_DIR / "components" / "main_nav.py").read_text(encoding="utf-8")
    assert source.count("st.Page(") == 5
    assert 'position="hidden"' in source
    assert "render_main_nav(page.url_path, PAGES)" in source
    assert "cm_nav_source" not in nav_source
    assert "st.page_link(page" in nav_source
    assert "<a class='cm-nav-link" not in nav_source
    assert "全局参数" not in nav_source
    # 关键样式先于重型业务模块，降低首屏裸界面的可见时间。
    assert source.index("st.set_page_config") < source.index("st.markdown(theme.CSS")
    assert source.index("st.markdown(theme.CSS") < source.index("from main_views")
    assert "theme.inject()" not in source


def test_only_five_visible_pages_remain():
    """旧 pages 目录已删除，顶部导航只保留当前五个业务页面。"""
    from components.main_nav import NAV_ITEMS

    assert not (APP_DIR / "pages").exists(), "app/pages 会触发旧页面自动注册"
    assert not (APP_DIR / "legacy_pages").exists(), "旧页面归档也应完成清理"
    assert [item["id"] for item in NAV_ITEMS] == [
        "dashboard", "invoice", "sensor", "routes", "ledger"]
    config = (APP_DIR.parent / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert "showSidebarNavigation = false" in config


def test_ledger_parameters_sync_to_shared_state():
    def script():
        from demo_state import init_state
        from main_views import render_ledger
        init_state()
        render_ledger()

    at = _run(script)
    price = next(n for n in at.number_input if n.label == "碳价（元/tCO2）")
    price.set_value(180.0).run()
    assert not at.exception
    assert at.session_state["carbon_price"] == 180.0
    quota = next(n for n in at.number_input if n.label == "当期碳配额（tCO2）")
    quota.set_value(4600.0).run()
    assert at.session_state["carbon_quota_t"] == 4600.0


def test_dashboard_uses_light_narrative_home():
    def script():
        from demo_state import init_state
        from main_views import render_dashboard
        init_state()
        render_dashboard()

    at = _run(script)
    content = "\n".join(item.value for item in at.markdown)
    assert "dh-hero" in content
    assert "让每一吨碳" in content
    assert "REAL-TIME CARBON PULSE" in content
    assert "prefers-reduced-motion" in content
