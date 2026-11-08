# -*- coding: utf-8 -*-
"""边缘网关鉴权的契约测试（企业级 P0 · docs/50 §3）。

CO₂ 传感器通道已于 2026-11-08 删除，网关只保留 OBD+GPS 一条链路，
但四层防护（令牌 → 限流 → 校验 → 审计）不变。要守住的行为：

1. **fail-closed**：注册表为空 / 设备未登记 / 令牌不对 → 一律 401，不写盘；
2. **身份必填**：整批都没有 vehicle_id / device_id → 401（不能静默落盘）；
3. **放行有据**：登记过 + 令牌正确 → 200，且 26 列 OBD 落盘格式不变；
4. **限流**：同一设备超配额 → 429 + Retry-After；
5. **CORS 收敛**：默认不发跨域头，白名单内才发；
6. **审计不泄密**：审计行里有 device_id / 结果，**没有令牌**；
7. **CLI 纪律**：注册表里只有 sha256 摘要，明文不落盘。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import device_auth, obd_gps  # noqa: E402

DEVICE = "TRK-TEST-001"
TOKEN = "tok-" + "x" * 24


def _record(vehicle_id: str = DEVICE, **overrides):
    record = {
        "vehicle_id": vehicle_id,
        "ts": "2026-11-08T10:00:00+08:00",
        "instant_fuel_lph": 20.0,
        "vehicle_speed_kmh": 50.0,
        "total_fuel_l": 1000.0,
        "odo_km": 12000.0,
    }
    record.update(overrides)
    return record


@pytest.fixture()
def registry(tmp_path: Path):
    """一份带一个车机的注册表。"""
    path = tmp_path / "devices.json"
    device_auth.upsert_device(DEVICE, device_auth.token_digest(TOKEN),
                               note="测试车机", path=path)
    return path


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    """Flask 测试客户端：实流写临时目录、审计写临时文件、注册表指向临时文件。"""
    monkeypatch.setenv("OBD_STREAM_DIR", str(tmp_path / "obd_gps"))
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


def _post(client, vehicle_id: str = DEVICE, token=None, record=None):
    headers = {}
    if token is not None:
        headers[device_auth.TOKEN_HEADER] = token
    payload = record if record is not None else _record(vehicle_id)
    return client.post("/api/upload_obd_gps", json=payload, headers=headers)


def _stream(tmp_path: Path) -> Path:
    return tmp_path / "obd_gps" / "obd_stream.csv"


# ---------------------------------------------------------------------------
# 模块级
# ---------------------------------------------------------------------------

def test_verify_accepts_correct_token(registry):
    ok, reason = device_auth.verify(DEVICE, TOKEN, path=registry)
    assert ok is True
    assert reason == device_auth.REASON_OK


def test_verify_rejects_wrong_and_unknown(registry):
    ok, reason = device_auth.verify(DEVICE, "wrong-token", path=registry)
    assert ok is False and reason == device_auth.REASON_BAD_TOKEN
    ok, reason = device_auth.verify("NO-SUCH-DEV", TOKEN, path=registry)
    assert ok is False and reason == device_auth.REASON_UNKNOWN_DEVICE


def test_verify_fails_closed_on_empty_registry(tmp_path: Path):
    """注册表为空 = 没有登记过任何设备 → 拒绝（不放行）。"""
    path = tmp_path / "empty.json"
    ok, reason = device_auth.verify(DEVICE, "anything", path=path)
    assert ok is False
    assert reason == device_auth.REASON_NO_REGISTRY


def test_registry_stores_only_digest(tmp_path: Path):
    """注册表里只能有 sha256 摘要，不能有明文令牌。"""
    path = tmp_path / "devices.json"
    secret = "super-secret-token-value"
    device_auth.upsert_device("TRK-1", device_auth.token_digest(secret), path=path)
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
    """未登记车机 → 401，且不落盘。"""
    test_client, _module, tmp_path = client
    response = _post(test_client, vehicle_id="TRK-NOT-REGISTERED", token=TOKEN)
    assert response.status_code == 401
    assert not _stream(tmp_path).exists()


def test_missing_token_is_rejected(client):
    test_client, _module, _tmp = client
    assert _post(test_client, token=None).status_code == 401


def test_batch_without_identity_is_rejected(client, registry, monkeypatch):
    """整批都没有 vehicle_id → 401，不能因为「没有设备要校验」而绕过。"""
    test_client, _module, tmp_path = client
    monkeypatch.setenv("SENSOR_DEVICES_FILE", str(registry))
    payload = {"records": [{"ts": "2026-11-08T10:00:00+08:00",
                            "instant_fuel_lph": 20.0}]}
    response = _post(test_client, token=TOKEN, record=payload)
    assert response.status_code == 401
    assert not _stream(tmp_path).exists()


def test_registered_device_with_token_is_accepted(client, registry, monkeypatch):
    """登记过的车机 + 正确令牌 → 200，26 列 OBD 实流落盘。"""
    test_client, _module, tmp_path = client
    monkeypatch.setenv("SENSOR_DEVICES_FILE", str(registry))
    response = _post(test_client, token=TOKEN)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["accepted"] == 1 and payload["rejected"] == 0
    text = _stream(tmp_path).read_text(encoding="utf-8-sig")
    assert DEVICE in text
    expected = ",".join(list(obd_gps.CANONICAL_FIELDS) + ["qc_flags"])
    assert text.splitlines()[0] == expected


def test_rate_limit_returns_429(client, registry, monkeypatch):
    test_client, _module, _tmp = client
    monkeypatch.setenv("SENSOR_DEVICES_FILE", str(registry))
    monkeypatch.setenv("SENSOR_RATE_LIMIT_PER_MIN", "2")
    assert _post(test_client, token=TOKEN).status_code == 200
    assert _post(test_client, token=TOKEN).status_code == 200
    response = _post(test_client, token=TOKEN)
    assert response.status_code == 429
    assert response.headers.get("Retry-After")


def test_cors_not_wildcard_by_default(client, registry, monkeypatch):
    """默认不发跨域头（原来是无条件 *）。"""
    test_client, _module, _tmp = client
    monkeypatch.setenv("SENSOR_DEVICES_FILE", str(registry))
    response = _post(test_client, token=TOKEN)
    assert "Access-Control-Allow-Origin" not in response.headers


def test_health_reports_auth_mode_without_secrets(client, registry, monkeypatch):
    test_client, _module, _tmp = client
    monkeypatch.setenv("SENSOR_DEVICES_FILE", str(registry))
    payload = test_client.get("/api/health").get_json()
    assert payload["status"] == "success"
    assert payload["auth"]["enabled"] is True
    assert payload["auth"]["devices"] == 1
    assert "obd_gps" in payload["channels"]
    assert "sensor" not in payload["channels"]
    assert "token_sha256" not in json.dumps(payload)


def test_audit_log_has_no_token(client, registry, monkeypatch):
    test_client, _module, tmp_path = client
    monkeypatch.setenv("SENSOR_DEVICES_FILE", str(registry))
    _post(test_client, token=TOKEN)
    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert DEVICE in audit
    assert TOKEN not in audit
    assert "tok-" not in audit


def test_record_without_fuel_is_rejected_but_kept_off_ledger(client, registry, monkeypatch):
    """没有油耗就没有碳：契约层拒收，不写 0 顶替。"""
    test_client, _module, tmp_path = client
    monkeypatch.setenv("SENSOR_DEVICES_FILE", str(registry))
    record = _record()
    record.pop("instant_fuel_lph")
    record.pop("total_fuel_l", None)
    response = _post(test_client, token=TOKEN, record=record)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["accepted"] == 0 and payload["rejected"] == 1
    assert "missing_fuel" in payload["reasons"]
    assert not _stream(tmp_path).exists()
