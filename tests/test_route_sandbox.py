# -*- coding: utf-8 -*-
"""高德路线沙盘：REST 解析、拥堵碳排与成本口径测试。"""
from __future__ import annotations

import pytest

from src.invoice import calc_carbon
from src.routing import amap_client
from src.routing.route_carbon import calc_carbon_for_route, evaluate_routes


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_fetch_driving_v5_returns_all_paths(monkeypatch):
    payload = {
        "status": "1", "route": {"paths": [
            {"distance": "100000", "duration": "7200", "tolls": "35.5", "traffic_lights": "12"},
            {"distance": "90000", "duration": "6000", "tolls": "50", "traffic_lights": "8"},
        ]},
    }
    captured = {}

    def fake_get(url, params, timeout):
        captured.update({"url": url, "params": params, "timeout": timeout})
        return _Response(payload)

    monkeypatch.setattr(amap_client.requests, "get", fake_get)
    routes = amap_client.fetch_driving_routes(
        "121.471,31.233", (116.404, 39.915), api_key="test-key")
    assert len(routes) == 2
    assert routes[0] == {
        "route_id": 1, "distance": 100000.0, "duration": 7200.0,
        "tolls": 35.5, "traffic_lights": 12, "polyline": [],
    }
    assert captured["url"] == "https://restapi.amap.com/v5/direction/driving"
    assert captured["params"]["show_fields"] == "cost,polyline"
    assert captured["params"]["strategy"] == 32
    assert captured["params"]["origin"] == "121.471000,31.233000"


def test_fetch_driving_v5_reads_nested_cost(monkeypatch):
    payload = {"status": "1", "route": {"paths": [{
        "distance": "1225697",
        "cost": {"duration": "51405", "tolls": "605", "traffic_lights": "29"},
    }]}}
    monkeypatch.setattr(amap_client.requests, "get", lambda *args, **kwargs: _Response(payload))
    routes = amap_client.fetch_driving_routes(
        "121.471,31.233", "116.404,39.915", api_key="test-key")
    assert routes[0]["duration"] == 51405
    assert routes[0]["tolls"] == 605
    assert routes[0]["traffic_lights"] == 29


def test_fetch_driving_v5_merges_step_polylines(monkeypatch):
    payload = {"status": "1", "route": {"paths": [{
        "distance": "3000", "duration": "600", "steps": [
            {"polyline": "121.470000,31.230000;121.471000,31.231000"},
            {"polyline": "121.471000,31.231000;121.472000,31.232000"},
            {"polyline": "bad;121.473000,31.233000"},
        ],
    }]}}
    monkeypatch.setattr(amap_client.requests, "get", lambda *args, **kwargs: _Response(payload))
    route = amap_client.fetch_driving_routes(
        "121.470,31.230", "121.473,31.233", api_key="test-key")[0]
    assert route["polyline"] == [
        [121.47, 31.23], [121.471, 31.231], [121.472, 31.232], [121.473, 31.233]]


def test_fetch_driving_v5_surfaces_api_error(monkeypatch):
    monkeypatch.setattr(amap_client.requests, "get", lambda *args, **kwargs: _Response(
        {"status": "0", "info": "INVALID_USER_KEY", "infocode": "10001"}))
    with pytest.raises(RuntimeError, match="INVALID_USER_KEY"):
        amap_client.fetch_driving_routes(
            "121.471,31.233", "116.404,39.915", api_key="test-key")


def test_calc_carbon_for_route_uses_existing_model_and_congestion():
    route = {"route_id": 1, "distance": 100000, "duration": 7200,
             "tolls": 35.5, "traffic_lights": 12}
    result = calc_carbon_for_route(route, "重型", 18.0)
    base = calc_carbon(18.0, 100.0, truck_type="重型")
    assert result["avg_speed_kmh"] == 50.0
    assert result["congestion_factor"] == 1.2
    assert result["base_carbon_kg"] == base
    assert result["carbon_kg"] == round(base * 1.2, 3)


def test_evaluate_routes_total_cost_and_truck_type():
    routes = [{"route_id": 1, "distance": 100000, "duration": 3600,
               "tolls": 35.5, "traffic_lights": 12}]
    result = evaluate_routes(routes, "中型", 12.0, 100.0)[0]
    assert result["congestion_factor"] == 1.0
    assert result["base_carbon_kg"] == calc_carbon(12.0, 100.0, truck_type="中型")
    assert result["carbon_cost_yuan"] == round(result["carbon_kg"] / 1000 * 100, 2)
    assert result["total_cost_yuan"] == round(35.5 + result["carbon_cost_yuan"], 2)


def test_invalid_coordinate_and_route_are_rejected():
    with pytest.raises(ValueError, match="经度,纬度"):
        amap_client.fetch_driving_routes("bad", "116.4,39.9", api_key="test-key")
    with pytest.raises(ValueError, match="必须大于 0"):
        calc_carbon_for_route({"distance": 0, "duration": 60}, "重型", 18)
