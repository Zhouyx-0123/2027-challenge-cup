# -*- coding: utf-8 -*-
"""高级滑条 + 联动图组件（components/slider_kit）契约测试。

覆盖三件最容易坏的事：
  1. 载体滑条的值必须能安全读出、并且夹紧在合法区间（坏值不让页面崩）；
  2. 载体滑条必须被 CSS 隐藏但保留可测量的布局宽度（坐标→数值映射依赖它）；
  3. 两张联动图的数据必须由真实快照算出，不能造数。
"""
from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def _slider_kit():
    from components import slider_kit
    return slider_kit


def test_carrier_css_hides_widget_but_keeps_width():
    """隐藏靠 opacity + 绝对定位，绝不能用 display:none（会让轨道宽度归零）。"""
    kit = _slider_kit()
    css = kit.CARRIER_CSS.format(ckey="slc_demo")
    assert "opacity:0" in css
    assert "display:none" not in css
    assert "position:absolute" in css
    assert "left:0" in css and "right:0" in css
    # 容器必须建立定位上下文，否则绝对定位会跑回页面根
    assert "position:relative" in css
    assert "st-key-slc_demo" in css


def test_lab_css_is_valid_single_brace():
    """LAB_CSS 里按 .format() 写法留了双花括号，注入前必须还原成单花括号。"""
    kit = _slider_kit()
    css = kit.LAB_CSS.replace("{{", "{").replace("}}", "}")
    assert "{{" not in css and "}}" not in css
    # 关键视觉件：玻璃轨道、发光旋钮、跟随气泡
    for token in (".sl-rail", ".sl-fill", ".sl-knob", ".sl-bubble", ".ch-series"):
        assert token in css


def test_render_lab_returns_current_session_value(monkeypatch):
    """载体滑条的值就是 Python 侧拿到的值，并且被夹紧在 [min, max]。"""
    kit = _slider_kit()
    import streamlit as st

    captured = {}

    def fake_markdown(*args, **kwargs):
        captured["css"] = args[0] if args else ""

    def fake_container(*args, **kwargs):
        class _Ctx:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def slider(self_inner, *a, **kw):
                captured["slider_kwargs"] = kw
                st.session_state.setdefault(kw.get("key"), kw.get("min_value", 0))
                return None

        return _Ctx()

    monkeypatch.setattr(st, "markdown", fake_markdown)
    monkeypatch.setattr(st, "container", fake_container)
    # 嵌入入口随 Streamlit 版本变化（st.iframe / components.html），两个都打桩
    monkeypatch.setattr(kit, "_embed_html", lambda *a, **kw: None)

    st.session_state["demo_key"] = 360
    out = kit.render_lab(key="demo_key", label="演示", min_value=10, max_value=500,
                         step=10, value=100, unit="条")
    assert out == 360
    # 越界值必须夹紧，而不是把负数直接抛给业务代码
    st.session_state["demo_key"] = -100.0
    assert kit.render_lab(key="demo_key", label="演示", min_value=10, max_value=500,
                          step=10, value=100) == 10
    st.session_state["demo_key"] = 9999
    assert kit.render_lab(key="demo_key", label="演示", min_value=10, max_value=500,
                          step=10, value=100) == 500


def test_carbon_price_chart_matches_snapshot(monkeypatch):
    """碳价敏感度三条线必须与 carbon_snapshot 口径一致（总额 / 配额 / 缺口）。"""
    from components import route_sandbox

    snap = {
        "total_t": 100.0, "quota_t": 90.0, "gap_t": 10.0,
        "invoice_t": 2.0, "sensor_cost_yuan": 5.0, "base_t": 98.0,
    }
    monkeypatch.setattr(route_sandbox, "carbon_snapshot", lambda: snap)
    chart = route_sandbox._carbon_price_chart(100)
    names = [s["name"] for s in chart["series"]]
    assert len(names) == 3
    covered, gross, gap = chart["series"][0]["points"], chart["series"][1]["points"], chart["series"][2]["points"]
    assert covered[0] == [80, 90.0 * 80]
    assert gross[0] == [80, 100.0 * 80]
    assert gap[0] == [80, 10.0 * 80]
    assert gross[-1][0] == 200
    assert chart["cursor"]["series"] == 1
    assert chart["interact"] == "set_x"


def test_sensor_window_chart_shape(monkeypatch):
    """监测窗口图：条数、预警线与可点击定位都要齐。"""
    import pandas as pd
    import main_views

    frame = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01 10:00:00", periods=30, freq="1min"),
        "device_id": ["ESP32-01"] * 30,
        "co2": [400.0 + i for i in range(30)],
        "temp": [22.0] * 30,
    })
    monkeypatch.setattr(main_views.live_services, "sensor_data", lambda: frame)
    monkeypatch.setattr(main_views.live_services, "sensor_is_mock", lambda: False)
    chart = main_views._sensor_window_chart()
    assert len(chart["series"][0]["points"]) == 30
    assert chart["hlines"][0]["y"] == 1000
    assert chart["interact"] == "set_x"
    assert chart["interact_transform"] == "reverse_count"
    assert chart["count"] == 30
    assert len(chart["x_labels"]) == 30


def test_sensor_window_chart_empty_state(monkeypatch):
    """没有传感器数据时必须给空态，不能让图崩。"""
    import pandas as pd
    import main_views

    empty = pd.DataFrame(columns=["timestamp", "device_id", "co2", "temp"])
    monkeypatch.setattr(main_views.live_services, "sensor_data", lambda: empty)
    monkeypatch.setattr(main_views.live_services, "sensor_is_mock", lambda: False)
    chart = main_views._sensor_window_chart()
    assert chart["series"] == []
    assert chart["interact"] == "none"
    assert "等待" in chart["empty"]
