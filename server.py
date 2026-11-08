# -*- coding: utf-8 -*-
"""ESP32-C3 / OBD 边缘数据接收服务：POST /api/upload_obd_gps -> obd_stream.csv。

CO₂ 环境浓度传感器通道已于 2026-11-08 删除：运输油耗与碳排只走
OBD+GPS 实测（守恒法）这一条硬件链路，环境 CO₂ 浓度不属于运输排放。

企业级 P0 改造（docs/50 §3）：把"任何人可写"的接口收紧成"只有登记过的设备能写"。

新增四层防护
------------
1. **设备身份**：请求头 ``X-Device-Token`` + 设备注册表 ``config/devices.json``。
   默认 **fail-closed**——没登记的设备一律 401；注册表为空也一律 401。
   令牌用 ``tools/device_tokens.py`` 生成（明文只打印一次，注册表只存 sha256）。
2. **CORS 收敛**：从通配符 ``*`` 改成白名单 ``SENSOR_ALLOWED_ORIGINS``（默认空 = 不发 CORS 头）。
3. **限流**：每设备每分钟 ``SENSOR_RATE_LIMIT_PER_MIN`` 次（默认 60），超限 429。
4. **审计**：每次调用落一行 JSONL（device_id / 时间 / 结果 / 来源 IP / 数值），
   **绝不记令牌**。

退路（仅限本机演示）
--------------------
``SENSOR_AUTH=0`` 关闭鉴权。关闭时启动会打醒目告警、``/api/health`` 也会标出来——
**部署到企业环境前必须打开**。

OBD 通道的校验口径由 ``src/obd_gps.normalize_records`` 决定（契约驱动）：
``vehicle_id``、带时区的 ``ts``、以及至少一个油耗字段必填；缺油耗即拒收。
"""
from __future__ import annotations

import csv
import json
import math
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from flask import Flask, jsonify, request

try:  # flask-cors 只在"浏览器跨域访问"时需要；缺它服务照常起（只是不发跨域头）
    from flask_cors import CORS
except ImportError:  # pragma: no cover - 本机/CI 未装 flask-cors 时的退化路径
    CORS = None

import sys

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import device_auth  # noqa: E402
from src import obd_gps  # noqa: E402

#: OBD+GPS 实测碳排通道（docs/52）：规范字段列序取自 src/obd_gps.py
OBD_FIELDNAMES = list(obd_gps.CANONICAL_FIELDS) + ["qc_flags"]
#: 单次请求最多接受多少条 OBD 记录（防止一条巨型 payload 打爆磁盘）
OBD_MAX_BATCH = 5000
_file_lock = threading.Lock()

#: 限流窗口（秒）
RATE_WINDOW_SECONDS = 60.0
#: 每设备每分钟默认上限
DEFAULT_RATE_LIMIT = 60

#: 审计日志：每设备请求留痕
AUDIT_PATH = ROOT / "outputs" / "sensor_audit.jsonl"

#: 限流状态：device_id -> 最近 N 次请求时间戳
_rate_state: Dict[str, List[float]] = {}
_rate_lock = threading.Lock()

app = Flask(__name__)

# ---- CORS：白名单（默认空 = 不跨域；本机演示也不需要跨域）----
_allowed_origins = [origin.strip() for origin
                    in (os.environ.get("SENSOR_ALLOWED_ORIGINS") or "").split(",")
                    if origin.strip()]
if _allowed_origins and CORS is not None:
    CORS(app, resources={r"/api/*": {"origins": _allowed_origins}})
elif _allowed_origins and CORS is None:
    print("[!] 配了 SENSOR_ALLOWED_ORIGINS 但本机没装 flask-cors —— "
          "跨域头不会发出（设备直连不受影响）")


def _audit_path() -> Path:
    """审计日志路径（可用 ``SENSOR_AUDIT_FILE`` 覆盖）。"""
    raw = (os.environ.get("SENSOR_AUDIT_FILE") or "").strip()
    return Path(raw) if raw else AUDIT_PATH


def _rate_limit() -> int:
    """每设备每分钟请求上限（``SENSOR_RATE_LIMIT_PER_MIN``）。"""
    try:
        return max(1, int(os.environ.get("SENSOR_RATE_LIMIT_PER_MIN",
                                        DEFAULT_RATE_LIMIT)))
    except (TypeError, ValueError):
        return DEFAULT_RATE_LIMIT


def _audit(event: str, device_id: str = "", reason: str = "",
           remote: str = "") -> None:
    """写一行审计（不含令牌）。写失败不影响主流程。"""
    record: Dict[str, Any] = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "event": event,
        "device_id": device_id,
        "reason": reason,
        "remote": remote,
    }
    path = _audit_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _rate_lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _rate_ok(device_id: str) -> Tuple[bool, int]:
    """滑动窗口限流：返回 ``(是否放行, 建议重试秒数)``。"""
    limit = _rate_limit()
    now = time.time()
    with _rate_lock:
        hits = [t for t in _rate_state.get(device_id, []) if now - t < RATE_WINDOW_SECONDS]
        if len(hits) >= limit:
            _rate_state[device_id] = hits
            oldest = min(hits)
            retry = max(1, int(RATE_WINDOW_SECONDS - (now - oldest)) + 1)
            return False, retry
        hits.append(now)
        _rate_state[device_id] = hits
    return True, 0


@app.get("/api/health")
def health():
    """健康检查：顺带报出鉴权模式与注册设备数（**不含任何令牌**）。"""
    enabled = device_auth.auth_enabled()
    registry = device_auth.load_registry()
    return jsonify({
        "status": "success",
        "auth": {
            "enabled": enabled,
            "header": device_auth.TOKEN_HEADER,
            "devices": len(device_auth.list_devices(registry)),
            "registry": str(device_auth.registry_path()),
        },
        "rate_limit_per_min": _rate_limit(),
        "allowed_origins": _allowed_origins,
        "channels": {
            "obd_gps": str(obd_gps.stream_path()),
        },
    })


# ---------------------------------------------------------------------------
# OBD + GPS 实测碳排通道（docs/52）
# ---------------------------------------------------------------------------
#
# 与已删除的 CO₂ 传感器通道同构的四层防护（令牌 → 限流 → 校验 → 审计），
# 差别只在校验口径与落盘位置：
#   · 校验走 src/obd_gps.normalize_records（契约驱动的别名归一 + 必填判定）；
#   · **没有油耗的记录直接判不可用**（本通道没有油耗就没有碳）；
#   · 落盘到 data/raw/obd_gps/obd_stream.csv（规范字段名）；
#   · 不可用记录另存 obd_rejects.csv，带原因，便于设备端自查。
#
# 接收端**只做校验与落盘**，不算碳 —— 算碳在 src/obd_pipeline.py。


def _obd_stream_file() -> Path:
    return obd_gps.stream_path()


def _obd_rejects_file() -> Path:
    return obd_gps.stream_dir() / "obd_rejects.csv"


def _csv_value(value: Any) -> Any:
    """落盘值：None / NaN 一律写空串（契约禁止用 0 或 nan 冒充缺失）。

    不引入 pandas 做缺失判断：``value != value`` 就是 NaN 的自反判据。
    """
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    if not isinstance(value, (str, bytes)):
        try:
            if value != value:  # NaN
                return ""
        except Exception:  # pragma: no cover - 极端类型兜底
            pass
    return value


def _append_rows(path: Path, fieldnames: List[str], rows: List[Dict[str, Any]]) -> None:
    """追加写 CSV（带表头判断）。失败不抛业务异常，交给调用方记审计。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock:
        need_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            if need_header:
                writer.writeheader()
            for row in rows:
                writer.writerow({key: _csv_value(row.get(key)) for key in fieldnames})


@app.post("/api/upload_obd_gps")
def upload_obd_gps():
    """接收 OBD + GPS 工况流。

    请求体：单条记录对象，或 ``{"records": [ ... ]}`` 批量。
    响应：``{"status": "success", "accepted": n, "rejected": m, "reasons": {...}}``
    """
    payload = request.get_json(silent=True)
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        records = payload["records"]
    elif isinstance(payload, dict):
        records = [payload]
    elif isinstance(payload, list):
        records = payload
    else:
        _audit("reject", reason="bad_payload_obd")
        return jsonify({"status": "error", "message": "请求体必须是记录对象或 {\"records\": [...]}"
                                      }), 400

    if not records:
        _audit("reject", reason="empty_batch")
        return jsonify({"status": "error", "message": "records 为空"}), 400
    if len(records) > OBD_MAX_BATCH:
        _audit("reject", reason="batch_too_large")
        return jsonify({"status": "error",
                        "message": "单批最多 %d 条" % OBD_MAX_BATCH}), 413
    if not all(isinstance(item, dict) for item in records):
        _audit("reject", reason="not_all_records")
        return jsonify({"status": "error", "message": "records 里出现了非对象元素"}), 400

    # ---- 1) 设备身份（fail-closed）：网关只此一套注册表与令牌 ----
    device_ids = {str(item.get("vehicle_id") or item.get("device_id") or "").strip()
                  for item in records}
    device_ids.discard("")
    if device_auth.auth_enabled():
        token = request.headers.get(device_auth.TOKEN_HEADER, "")
        # 整批都没有 vehicle_id / device_id 时，下面两个循环都会「空转」——
        # 鉴权与限流全被绕过，rejects 照样落盘。必须先堵这条 fail-open。
        if not device_ids:
            _audit("reject", device_id="", reason="missing_device_id",
                   remote=request.remote_addr or "")
            return jsonify({
                "status": "error",
                "message": "缺少 vehicle_id / device_id，无法鉴权（SENSOR_AUTH=1 时必填）",
            }), 401
        # 一批里可能有多台车；逐台校验，任一台未授权则整批拒收（不部分落盘）
        unauthorized = []
        for device_id in sorted(device_ids):
            ok, reason = device_auth.verify(device_id, token)
            if not ok:
                unauthorized.append({"device_id": device_id, "reason": reason})
        if unauthorized:
            _audit("reject", device_id=",".join(sorted(device_ids))[:120],
                   reason="unauthorized_obd", remote=request.remote_addr or "")
            return jsonify({
                "status": "error",
                "message": "设备未授权：令牌缺失或不匹配（请用 tools/device_tokens 登记设备）",
                "unauthorized": unauthorized,
            }), 401

    # ---- 2) 限流（按设备） ----
    throttled = []
    for device_id in sorted(device_ids):
        allowed, retry = _rate_ok(device_id or "unknown")
        if not allowed:
            throttled.append(device_id)
    if throttled:
        _audit("throttle", device_id=",".join(throttled)[:120], reason="rate_limit",
               remote=request.remote_addr or "")
        response = jsonify({"status": "error", "message": "请求过于频繁，请稍后再试"})
        response.headers["Retry-After"] = "60"
        return response, 429

    # ---- 3) 契约校验与归一（别名映射 + 必填判定） ----
    normalized, rejects = obd_gps.normalize_records(records)
    if normalized.empty and rejects.empty:
        _audit("reject", reason="no_records_parsed")
        return jsonify({"status": "error", "message": "没有可解析的记录"}), 400

    accepted_rows: List[Dict[str, Any]] = []
    for row in normalized.to_dict(orient="records"):
        flags = row.get("qc_flags")
        item = {key: row.get(key) for key in obd_gps.CANONICAL_FIELDS}
        item["ts"] = pd_ts_to_text(row.get("ts"))
        item["qc_flags"] = ";".join(str(v) for v in (flags or []))
        accepted_rows.append(item)

    # ---- 4) 落盘（只落盘，不算碳） ----
    try:
        if accepted_rows:
            _append_rows(_obd_stream_file(), OBD_FIELDNAMES, accepted_rows)
        if not rejects.empty:
            reject_rows = []
            for row in rejects.to_dict(orient="records"):
                item = {key: row.get(key) for key in
                        ("ts", "vehicle_id", "reason", "detail")}
                reject_rows.append(item)
            _append_rows(_obd_rejects_file(), ["ts", "vehicle_id", "reason", "detail"],
                         reject_rows)
    except OSError as exc:
        _audit("error", device_id=",".join(sorted(device_ids))[:120], reason="write_failed",
               remote=request.remote_addr or "")
        return jsonify({"status": "error", "message": "写入失败：%s" % exc}), 500

    reasons: Dict[str, int] = {}
    if not rejects.empty:
        reasons = {str(k): int(v) for k, v in rejects["reason"].value_counts().items()}
    if accepted_rows:
        _audit("accept", device_id=",".join(sorted(device_ids))[:120],
               reason="obd_accepted", remote=request.remote_addr or "")
    else:
        # 一条都没收下却记 accept，会让审计日志与真实结果相反（现场排查会被带偏）。
        _audit("reject", device_id=",".join(sorted(device_ids))[:120],
               reason="all_rejected", remote=request.remote_addr or "")
    return jsonify({
        "status": "success",
        "accepted": len(accepted_rows),
        "rejected": int(len(rejects)),
        "reasons": reasons,
        "stream_file": str(_obd_stream_file()),
    })


def pd_ts_to_text(value: Any) -> str:
    """时间戳统一存 ISO8601 带时区。

    归一阶段已把 ts 转成 tz-aware 的 UTC 时间戳，这里统一用 isoformat() 落盘，
    保证与契约要求的「带时区偏移的 ISO8601」一致。
    """
    if value is None:
        return ""
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            return str(isoformat())
        except Exception:  # pragma: no cover - 极端类型兜底
            pass
    return str(value)


if __name__ == "__main__":
    if not device_auth.auth_enabled():
        print("[!] 网关鉴权已关闭（SENSOR_AUTH=0）——仅限本机演示，不要暴露到公网")
    else:
        _count = len(device_auth.list_devices())
        print("[OK] 网关鉴权已启用（%s）｜已登记设备 %d 个"
              % (device_auth.TOKEN_HEADER, _count))
    # ESP32 与电脑须在同一局域网；host=0.0.0.0 允许局域网设备访问。
    app.run(host="0.0.0.0", port=5000, debug=False)
