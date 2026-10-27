# -*- coding: utf-8 -*-
"""设备注册表与令牌校验（企业级 P0 · 传感器接入鉴权）。

为什么需要它
------------
改造前 ``server.py`` 的 ``POST /api/upload_sensor`` **任何人可写**：没有身份校验，
CORS 还是通配符 ``*``。局域网里任何设备都能伪造读数进数据管道——而读数会进
硬件监控页、影响运营判断。

本模块提供三件东西（纯标准库，不引新依赖）：

* **注册表**：``config/devices.json``，登记 ``device_id → 令牌摘要``；
* **令牌摘要**：只存 ``sha256(token)``，**明文令牌不落盘、不进 git**；
* **校验**：常量时间比较（``hmac.compare_digest``），避免逐字节试错。

令牌约定沿用 ``docs/46`` 已有的一套：请求头 ``X-Device-Token``。
生成与轮换用 ``python -m tools.device_tokens``（明文只在终端打印一次）。

口令与纪律
----------
* 注册表里**只有摘要**，明文只在生成时打印一次，丢了就 rotate；
* 令牌校验失败一律 401，**不透露"设备不存在"还是"令牌不对"**（防枚举）；
* 审计日志只记 ``device_id / 时间 / 结果 / 来源 IP``，**绝不记令牌**。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]

#: 默认注册表路径（可用环境变量 ``SENSOR_DEVICES_FILE`` 覆盖）
DEFAULT_REGISTRY_PATH = ROOT / "config" / "devices.json"

#: 令牌请求头（与 docs/46 的 PM 采集通道一致）
TOKEN_HEADER = "X-Device-Token"

#: 令牌最小长度（生成时保证；注册表里比它短的摘要一律视为无效配置）
MIN_TOKEN_LENGTH = 16

#: 校验结果原因码
REASON_OK = "ok"
REASON_NO_TOKEN = "no_token"
REASON_UNKNOWN_DEVICE = "unknown_device"
REASON_DISABLED = "disabled"
REASON_BAD_TOKEN = "bad_token"
REASON_NO_REGISTRY = "no_registry"


def token_digest(token: str) -> str:
    """令牌 → sha256 十六进制摘要（注册表只存这个）。"""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def generate_token() -> str:
    """生成一个足够强的设备令牌（URL 安全随机串）。"""
    return secrets.token_urlsafe(24)


def registry_path(path: Optional[Path] = None) -> Path:
    """注册表路径：显式参数 > 环境变量 > 默认 ``config/devices.json``。"""
    if path is not None:
        return Path(path)
    import os

    raw = (os.environ.get("SENSOR_DEVICES_FILE") or "").strip()
    return Path(raw) if raw else DEFAULT_REGISTRY_PATH


def load_registry(path: Optional[Path] = None) -> Dict[str, Any]:
    """读注册表；文件不存在或损坏都返回空注册表（**不抛异常**）。"""
    target = registry_path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "devices": []}
    if not isinstance(payload, dict):
        return {"version": 1, "devices": []}
    devices = payload.get("devices")
    if not isinstance(devices, list):
        payload["devices"] = []
    return payload


def save_registry(registry: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """写注册表（自动建目录）。只应存摘要，不应存明文。"""
    target = registry_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(registry, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    return target


def list_devices(registry: Optional[Dict[str, Any]] = None,
                 path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """列出设备条目（不含明文令牌）。"""
    data = registry if registry is not None else load_registry(path)
    out: List[Dict[str, Any]] = []
    for item in data.get("devices", []):
        if not isinstance(item, dict):
            continue
        out.append({
            "device_id": str(item.get("device_id") or ""),
            "enabled": bool(item.get("enabled", True)),
            "note": str(item.get("note") or ""),
            "token_sha256": str(item.get("token_sha256") or ""),
        })
    return out


def find_device(device_id: str, registry: Optional[Dict[str, Any]] = None,
                path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """按 device_id 找设备条目；找不到返回 ``None``。"""
    if not device_id:
        return None
    for item in list_devices(registry, path):
        if item["device_id"] == device_id:
            return item
    return None


def upsert_device(device_id: str, token_sha256: str, note: str = "",
                  enabled: bool = True,
                  path: Optional[Path] = None) -> Path:
    """登记或更新一个设备（只存摘要）。"""
    device_id = (device_id or "").strip()
    if not device_id:
        raise ValueError("device_id 不能为空")
    if not token_sha256:
        raise ValueError("token_sha256 不能为空")
    data = load_registry(path)
    devices = [d for d in data.get("devices", [])
               if isinstance(d, dict) and d.get("device_id") != device_id]
    devices.append({
        "device_id": device_id,
        "token_sha256": token_sha256,
        "enabled": bool(enabled),
        "note": note,
    })
    devices.sort(key=lambda d: str(d.get("device_id") or ""))
    data["devices"] = devices
    data.setdefault("version", 1)
    return save_registry(data, path)


def remove_device(device_id: str, path: Optional[Path] = None) -> bool:
    """移除一个设备；返回是否真的移除了。"""
    data = load_registry(path)
    before = len(data.get("devices", []))
    data["devices"] = [d for d in data.get("devices", [])
                       if not (isinstance(d, dict)
                               and d.get("device_id") == device_id)]
    if len(data["devices"]) == before:
        return False
    save_registry(data, path)
    return True


def verify(device_id: str, token: str,
           registry: Optional[Dict[str, Any]] = None,
           path: Optional[Path] = None) -> Tuple[bool, str]:
    """校验一个请求：返回 ``(是否通过, 原因码)``。

    原因码见模块常量。**对外只暴露"通过/不通过"**，原因码仅用于审计与排查。
    """
    data = registry if registry is not None else load_registry(path)
    if not data.get("devices"):
        # 注册表为空 = 没登记过任何设备：**不放行**（fail-closed）
        return False, REASON_NO_REGISTRY
    if not (token or "").strip():
        return False, REASON_NO_TOKEN
    device = find_device(device_id, data)
    if device is None:
        return False, REASON_UNKNOWN_DEVICE
    if not device.get("enabled", True):
        return False, REASON_DISABLED
    expected = str(device.get("token_sha256") or "")
    if not expected:
        return False, REASON_BAD_TOKEN
    if hmac.compare_digest(token_digest(token), expected):
        return True, REASON_OK
    return False, REASON_BAD_TOKEN


def auth_enabled() -> bool:
    """是否启用鉴权。

    默认**启用**（企业口径：默认拒绝）。仅当显式设置
    ``SENSOR_AUTH=0``（或 false/no/off）才关闭——那是给本机演示留的退路，
    关闭时服务启动会打醒目告警。
    """
    import os

    raw = (os.environ.get("SENSOR_AUTH") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


__all__ = [
    "DEFAULT_REGISTRY_PATH", "TOKEN_HEADER", "MIN_TOKEN_LENGTH",
    "REASON_OK", "REASON_NO_TOKEN", "REASON_UNKNOWN_DEVICE",
    "REASON_DISABLED", "REASON_BAD_TOKEN", "REASON_NO_REGISTRY",
    "token_digest", "generate_token", "registry_path", "load_registry",
    "save_registry", "list_devices", "find_device", "upsert_device",
    "remove_device", "verify", "auth_enabled",
]
