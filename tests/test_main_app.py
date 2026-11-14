# -*- coding: utf-8 -*-
"""六页主应用：共享状态与跨模块联动测试。"""
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

    monkeypatch.setattr(main_views.live_services, "extract_invoice", lambda *args: {
        "fuel_liters": 65.5, "mock": False})
    monkeypatch.setattr(main_views.live_services, "invoice_summary", lambda **kwargs: {
        "distance_km": 233.9})
    monkeypatch.setattr(main_views.live_services, "calc_carbon", lambda *args: 210.5)

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


def test_failed_invoice_does_not_create_carbon(monkeypatch):
    import main_views

    monkeypatch.setattr(main_views.live_services, "extract_invoice", lambda *args: {
        "status": "failed", "note": "未识别出发票内容", "fuel_liters": None})

    class Uploaded:
        name = "broken.jpg"

        @staticmethod
        def getvalue():
            return b"image"

    def script(uploaded_cls):
        from demo_state import init_state
        from main_views import _run_invoice
        init_state()
        _run_invoice(uploaded_cls())

    at = _run(script, Uploaded)
    assert at.session_state["current_invoice_result"] is None
    assert at.session_state["current_invoice_carbon"] == 0.0
    assert at.session_state["today_invoice_count"] == 0
    assert "未识别出发票内容" in at.session_state["invoice_error"]


def test_shared_snapshot_updates_gap_and_financial_risk():
    def script():
        import streamlit as st
        from demo_state import carbon_snapshot, init_state
        init_state()
        st.session_state["current_invoice_carbon"] = 2000.0  # 2t
        st.session_state["carbon_price"] = 100.0
        snap = carbon_snapshot()
        st.session_state["test_snapshot"] = snap

    at = _run(script)
    snap = at.session_state["test_snapshot"]
    assert snap["base_source"] == "result"
    # CO₂ 传感器通道已删除：快照只累加发票新增，不再有硬件突发成本。
    assert snap["total_t"] == 4504.04
    assert snap["gap_t"] == 2.0
    assert snap["balance_t"] == -2.0
    assert snap["financial_risk_yuan"] == 200.0


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
    assert source.count("st.Page(") == 6
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


def test_only_six_visible_pages_remain():
    """旧 pages 目录已删除，顶部导航只保留当前六个业务页面（含数据链总览）。"""
    from components.main_nav import NAV_ITEMS

    assert not (APP_DIR / "pages").exists(), "app/pages 会触发旧页面自动注册"
    assert not (APP_DIR / "legacy_pages").exists(), "旧页面归档也应完成清理"
    assert [item["id"] for item in NAV_ITEMS] == [
        "dashboard", "chain", "invoice", "sensor", "routes", "ledger"]
    config = (APP_DIR.parent / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert "showSidebarNavigation = false" in config


def test_replaced_ledger_has_no_old_price_or_quota_controls():
    def script():
        from demo_state import init_state
        from main_views import render_ledger
        init_state()
        render_ledger()

    at = _run(script)
    assert not at.number_input
    assert not at.tabs
    assert len(at.get("iframe")) == 1


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


def test_page_cache_is_cleared_when_a_new_run_lands(monkeypatch):
    """pipeline 发新 run 后页面必须换版：读取层是永久缓存，只能靠 run 指纹清一次。"""
    import types
    import services

    cleared = []
    monkeypatch.setattr(services.st, "cache_data",
                        types.SimpleNamespace(clear=lambda: cleared.append(1)))
    monkeypatch.setattr(services, "_RUN_TOKEN", [0.0])
    monkeypatch.setattr(services, "mtime_token",
                        lambda name: 100.0 if name == "current_run.json" else 200.0)
    assert services.refresh_cache_on_new_run() is False   # 进程刚起：只记指纹
    assert cleared == []

    monkeypatch.setattr(services, "mtime_token",
                        lambda name: 100.0 if name == "current_run.json" else 900.0)
    assert services.refresh_cache_on_new_run() is True    # 新 run：清一次
    assert cleared == [1]
