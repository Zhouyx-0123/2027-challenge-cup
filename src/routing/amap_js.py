# -*- coding: utf-8 -*-
"""高德地图 JS API 2.0 凭据：只在渲染地图组件时从后端读取。

Web 服务 Key 不能加载 JS 地图。这里使用独立的「Web 端（JS API）」Key
与安全密钥，两者都必须配置；文件已加入 .gitignore。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
JS_KEY_PATH = CONFIG_DIR / "amap_js_key.txt"
SECURITY_CODE_PATH = CONFIG_DIR / "amap_js_security_code.txt"


def _read(path: Path, env_name: str) -> str:
    value = (os.environ.get(env_name) or "").strip()
    if value:
        return value
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if not value.startswith("请至高德"):
            return value
    return ""


def load_js_credentials() -> Tuple[str, str]:
    """返回 (JS API Key, 安全密钥)；任一缺失时为空字符串对。"""
    key = _read(JS_KEY_PATH, "AMAP_JS_API_KEY")
    security = _read(SECURITY_CODE_PATH, "AMAP_JS_SECURITY_CODE")
    return key, security


def js_available() -> bool:
    key, security = load_js_credentials()
    return bool(key and security)


__all__ = [
    "JS_KEY_PATH",
    "SECURITY_CODE_PATH",
    "load_js_credentials",
    "js_available",
]
