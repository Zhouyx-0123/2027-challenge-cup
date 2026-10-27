# -*- coding: utf-8 -*-
"""地点名称 → GCJ-02 坐标解析测试。"""
from __future__ import annotations

import pytest

from src.routing import geocode


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_place_name_geocodes_to_coordinate(monkeypatch):
    captured = {}

    def fake_get(url, params, timeout):
        captured.update({"url": url, "params": params, "timeout": timeout})
        return _Response({"status": "1", "info": "OK", "infocode": "10000", "geocodes": [{
            "formatted_address": "上海市闵行区申贵路1500号",
            "province": "上海市", "city": "上海市", "district": "闵行区",
            "location": "121.320490,31.194920", "level": "兴趣点",
        }]})

    monkeypatch.setattr(geocode.requests, "get", fake_get)
    result = geocode.geocode_place("上海虹桥站", api_key="test-key")
    assert captured["url"] == geocode.GEOCODE_URL
    assert captured["params"]["address"] == "上海虹桥站"
    assert result["label"] == "上海虹桥站"
    assert result["location"] == "121.320490,31.194920"
    assert result["lng"] == pytest.approx(121.32049)
    assert result["lat"] == pytest.approx(31.19492)
    assert result["formatted_address"] == "上海市闵行区申贵路1500号"


def test_coordinate_input_skips_network(monkeypatch):
    monkeypatch.setattr(geocode.requests, "get", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("坐标输入不应请求地理编码")))
    result = geocode.geocode_place("121.47,31.23", api_key="test-key")
    assert result["source"] == "coordinate"
    assert result["label"] == "自定义坐标"
    assert result["location"] == "121.470000,31.230000"


def test_unknown_place_has_actionable_message(monkeypatch):
    monkeypatch.setattr(geocode.requests, "get", lambda *args, **kwargs: _Response(
        {"status": "1", "geocodes": []}))
    with pytest.raises(ValueError, match="补充城市"):
        geocode.geocode_place("不存在的地点XYZ", api_key="test-key")


def test_geocode_api_error_is_surfaced(monkeypatch):
    monkeypatch.setattr(geocode.requests, "get", lambda *args, **kwargs: _Response(
        {"status": "0", "info": "INVALID_USER_KEY", "infocode": "10001"}))
    with pytest.raises(RuntimeError, match="INVALID_USER_KEY"):
        geocode.geocode_place("上海虹桥站", api_key="test-key")
