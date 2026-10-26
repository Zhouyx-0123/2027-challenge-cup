# -*- coding: utf-8 -*-
"""DeepSeek 官方 Vision / Text 请求契约测试。"""
from __future__ import annotations

from src import disclosure, invoice


class _Response:
    status_code = 200
    text = ""

    def __init__(self, content):
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}

    def raise_for_status(self):
        return None


def test_deepseek_vision_official_request(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured.update({"url": url, "payload": json})
        return _Response('{"fuel_liters":65.5}')

    monkeypatch.setattr(invoice.requests, "post", fake_post)
    result = invoice.call_deepseek_vision("data:image/jpeg;base64,abc", "test-key")
    assert result == '{"fuel_liters":65.5}'
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["payload"]["model"] == "deepseek-flash"
    assert captured["payload"]["max_tokens"] == 800
    assert "thinking" not in captured["payload"]
    image = captured["payload"]["messages"][0]["content"][1]
    assert image["image_url"]["url"] == "data:image/jpeg;base64,abc"


def test_deepseek_disclosure_request(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured.update({"url": url, "payload": json})
        return _Response("本期披露附注")

    monkeypatch.setattr(disclosure.requests, "post", fake_post)
    assert disclosure.generate_disclosure_note({"total_t": 1.0}, "test-key") == "本期披露附注"
    assert captured["payload"]["model"] == "deepseek-flash"
    assert captured["payload"]["max_tokens"] == 500
    assert "thinking" not in captured["payload"]
