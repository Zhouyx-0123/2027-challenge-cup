# -*- coding: utf-8 -*-
"""传感器接入鉴权的契约测试（企业级 P0 · docs/50 §3）。

要守住的行为：

1. **fail-closed**：注册表为空 / 设备未登记 / 令牌不对 → 一律 401，不写盘；
2. **放行有据**：登记过 + 令牌正确 → 200，且读数落盘格式与改造前一致；
3. **限流**：同一设备超配额 → 429 + Retry-After；
4. **CORS 收敛**：默认不发跨域头，白名单内才发；
5. **审计不泄密**：审计行里有 device_id / 结果，**没有令牌**；
6. **CLI 纪律**：注册表里只有 sha256 摘要，明文不落盘。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import device_auth  # noqa: E402


@pytest.fixture()
def registry(tmp_path: Path):
    """一份带一个设备的注册表。"""
    path = tmp_path / "devices.json"
    device_auth.upsert_device("PDM-TEST-001", device_auth.token_digest("tok-" + "x" * 24),
                               note="测试设备", path=path)
    return path


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    """Flask 测试客户端：读数写临时文件、审计写临时文件、注册表指向临时文件。"""
    monkeypatch.setenv("SENSOR_DATA_FILE", str(tmp_path / "sensor_data.csv"))
    monkeypatch.setenv("SENSOR_AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("SENSOR_AUTH", "1")
    monkeypatch.setenv("SENSOR_RATE_LIMIT_PER_MIN", "60")
    monkeypatch.delenv("SENSOR_ALLOWED_ORIGINS", raising=False)

    import importlib

    import server as server_module

    server_module = importlib.reload(server_module)
    server_module.app.config["TESTING"] = True
    with server_module.app.test_client() as test_client:
        yield test_client, server_module, tmp_path


def _post(client, device_id="PDM-TEST-001", token=None, co2=812.3, temp=24.1):
    headers = {}
    if token is not None:
        headers[device_auth.TOKEN_HEADER] = token
    return client.post("/api/upload_sensor",
                       json={"device_id": device_id, "co2": co2, "temp": temp},
                       headers=headers)


# ---------------------------------------------------------------------------
# 模块级
# ---------------------------------------------------------------------------

def test_verify_accepts_correct_token(registry):
    ok, reason = device_auth.verify("PDM-TEST-001", "tok-" + "x" * 24, path=registry)
    assert ok is True
    assert reason == device_auth.REASON_OK


def test_verify_rejects_wrong_and_unknown(registry):
    ok, reason = device_auth.verify("PDM-TEST-001", "wrong-token", path=registry)
    assert ok is False and reason == device_auth.REASON_BAD_TOKEN
    ok, reason = device_auth.verify("NO-SUCH-DEV", "tok-" + "x" * 24, path=registry)
    assert ok is False and reason == device_auth.REASON_UNKNOWN_DEVICE


def test_verify_fails_closed_on_empty_registry(tmp_path: Path):
    """注册表为空 = 没有登记过任何设备 → 拒绝（不放行）。"""
    path = tmp_path / "empty.json"
    ok, reason = device_auth.verify("PDM-TEST-001", "anything", path=path)
    assert ok is False
    assert reason == device_auth.REASON_NO_REGISTRY


def test_registry_stores_only_digest(tmp_path: Path):
    """注册表里只能有 sha256 摘要，不能有明文令牌。"""
    path = tmp_path / "devices.json"
    secret = "super-secret-token-value"
    device_auth.upsert_device("PDM-1", device_auth.token_digest(secret), path=path)
    raw = path.read_text(encoding="utf-8")
    assert secret not in raw
    assert device_auth.token_digest(secret) in raw


def test_auth_enabled_default_true_and_opt_out(monkeypatch):
    monkeypatch.delenv("SENSOR_AUTH", raising=False)
    assert device_auth.auth_enabled() is True
    monkeypatch.setenv("SENSOR_AUTH", "0")
    assert device_auth.auth_enabled() is False


# ---------------------------------------------------------------------------
# HTTP 层
# ---------------------------------------------------------------------------

def test_unregistered_device_is_rejected(client):
    """未登记设备 → 401，且不落盘。"""
    test_client, _module, tmp_path = client
    response = _post(test_client, device_id="PDM-NOT-REGISTERED",
                      token="tok-" + "x" * 24)
    assert response.status_code == 401
    assert not (tmp_path / "sensor_data.csv").exists()


def test_missing_token_is_rejected(client):
    test_client, _module, _tmp = client
    assert _post(test_client, token=None).status_code == 401


def test_registered_device_with_token_is_accepted(client, registry, monkeypatch):
    """登记过的设备 + 正确令牌 → 200，读数落盘。"""
    test_client, _module, tmp_path = client
    monkeypatch.setenv("SENSOR_DEVICES_FILE", str(registry))
    response = _post(test_client, token="tok-" + "x" * 24)
    assert response.status_code == 200
    text = (tmp_path / "sensor_data.csv").read_text(encoding="utf-8-sig")
    assert "PDM-TEST-001" in text
    assert text.splitlines()[0] == "timestamp,device_id,co2,temp"


def test_rate_limit_returns_429(client, registry, monkeypatch):
    test_client, _module, _tmp = client
    monkeypatch.setenv("SENSOR_DEVICES_FILE", str(registry))
    monkeypatch.setenv("SENSOR_RATE_LIMIT_PER_MIN", "2")
    token = "tok-" + "x" * 24
    assert _post(test_client, token=token).status_code == 200
    assert _post(test_client, token=token).status_code == 200
    response = _post(test_client, token=token)
    assert response.status_code == 429
    assert response.headers.get("Retry-After")


def test_cors_not_wildcard_by_default(client, registry, monkeypatch):
    """默认不发跨域头（原来是无条件 *）。"""
    test_client, _module, _tmp = client
    monkeypatch.setenv("SENSOR_DEVICES_FILE", str(registry))
    response = _post(test_client, token="tok-" + "x" * 24,
                     )
    assert "Access-Control-Allow-Origin" not in response.headers


def test_health_reports_auth_mode_without_secrets(client, registry, monkeypatch):
    test_client, _module, _tmp = client
    monkeypatch.setenv("SENSOR_DEVICES_FILE", str(registry))
    payload = test_client.get("/api/health").get_json()
    assert payload["status"] == "success"
    assert payload["auth"]["enabled"] is True
    assert payload["auth"]["devices"] == 1
    assert "token_sha256" not in json.dumps(payload)


def test_audit_log_has_no_token(client, registry, monkeypatch):
    test_client, _module, tmp_path = client
    monkeypatch.setenv("SENSOR_DEVICES_FILE", str(registry))
    token = "tok-" + "x" * 24
    _post(test_client, token=token)
    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "PDM-TEST-001" in audit
    assert token not in audit
    assert "tok-" not in audit


def test_invalid_values_still_rejected_after_auth(client, registry, monkeypatch):
    """数值校验口径不变：超范围仍然 400（且要过鉴权）。"""
    test_client, _module, _tmp = client
    monkeypatch.setenv("SENSOR_DEVICES_FILE", str(registry))
    token = "tok-" + "x" * 24
    assert _post(test_client, token=token, co2=999999).status_code == 400
    assert _post(test_client, token=token, temp=-80).status_code == 400
