# -*- coding: utf-8 -*-
"""高德 JS API 2.0 精细地图：配置、几何简化、HTML 契约与页面降级。"""
from __future__ import annotations

import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def _routes():
    return [
        {"route_id": 2, "polyline": [[121.0, 31.0], [121.5, 31.5], [122.0, 32.0]],
         "total_cost_yuan": 1500, "carbon_kg": 900, "duration_min": 120,
         "distance_km": 180, "tolls": 50, "avg_speed_kmh": 90, "congestion_factor": 1.1},
        {"route_id": 1, "polyline": [[121.0, 31.0], [121.2, 31.8], [122.0, 32.0]],
         "total_cost_yuan": 1200, "carbon_kg": 820, "duration_min": 110,
         "distance_km": 176, "tolls": 45, "avg_speed_kmh": 96, "congestion_factor": 1.0},
    ]


def test_simplify_polyline_keeps_endpoints_and_limit():
    from components.route_map import simplify_polyline
    points = [[121.0 + i * 0.0001, 31.0 + (i % 7) * 0.0001] for i in range(5000)]
    result = simplify_polyline(points, limit=300)
    assert 2 <= len(result) <= 300
    assert result[0] == [121.0, 31.0]
    assert result[-1] == points[-1]


def test_prepare_routes_orders_best_cost_and_assigns_colors():
    from components.route_map import prepare_routes
    routes = prepare_routes(_routes())
    assert [route["id"] for route in routes] == ["1", "2"]
    assert routes[0]["recommended"] is True
    assert routes[0]["color"] == "#FF6B6B"
    assert routes[1]["color"] == "#46B7FF"


def test_map_html_uses_webgl_darkblue_and_live_layers():
    from components.route_map import build_route_map_html
    html = build_route_map_html(
        _routes(), "121.0,31.0", "122.0,32.0", "js-key-test", "security-test",
        origin_label="上海虹桥站", destination_label="北京南站")
    assert "上海虹桥站" in html and "北京南站" in html
    assert "AMapLoader.load" in html
    assert "amap://styles/darkblue" in html
    assert "viewMode:'3D'" in html
    assert "showDir:true" in html
    assert "AMap.TileLayer.Traffic" in html
    assert "AMap.TileLayer.Satellite" in html
    assert "setFitView" in html
    assert "securityJsCode" in html
    assert "js-key-test" in html


def test_map_html_escapes_script_breakout():
    from components.route_map import build_route_map_html
    routes = _routes()
    routes[0]["route_id"] = "</script><script>alert(1)</script>"
    html = build_route_map_html(
        routes, "121.0,31.0", "122.0,32.0", "key", "security")
    assert "</script><script>alert(1)</script>" not in html
    assert "\\u003c/script\\u003e" in html


def test_missing_js_credentials_shows_setup(monkeypatch):
    from components import route_map
    monkeypatch.setattr(route_map.live_services, "amap_js_available", lambda: False)

    def script(routes):
        from components import route_map
        route_map.render_route_map(routes, "121.0,31.0", "122.0,32.0")

    at = AppTest.from_function(script, args=(_routes(),), default_timeout=40)
    at.run()
    assert not at.exception
    content = "\n".join(item.value for item in at.markdown)
    assert "Web 端（JS API）Key" in content
    assert "config/amap_js_key.txt" in content


def test_js_credentials_load_from_environment(monkeypatch, tmp_path):
    from src.routing import amap_js
    monkeypatch.setenv("AMAP_JS_API_KEY", "env-key")
    monkeypatch.setenv("AMAP_JS_SECURITY_CODE", "env-security")
    assert amap_js.load_js_credentials() == ("env-key", "env-security")
    monkeypatch.delenv("AMAP_JS_API_KEY")
    monkeypatch.delenv("AMAP_JS_SECURITY_CODE")
    monkeypatch.setattr(amap_js, "JS_KEY_PATH", tmp_path / "missing-key")
    monkeypatch.setattr(amap_js, "SECURITY_CODE_PATH", tmp_path / "missing-code")
    assert amap_js.load_js_credentials() == ("", "")
    assert amap_js.js_available() is False
