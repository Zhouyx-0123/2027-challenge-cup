# -*- coding: utf-8 -*-
"""配置与密钥的分层读取（企业部署口径，docs/50 §1）。

为什么要有这一层
----------------
``.gitignore`` 只能挡住"进不进 git"，**挡不住"进不进镜像层""进不进日志""进不进备份包"**。
企业部署时密钥必须能被*注入*，而不是躺在仓库里。本模块把"一个键从哪儿取"收敛到一处，
优先级固定且可解释：

===  ==========================================  ==========================
序   来源                                        适用
===  ==========================================  ==========================
1    环境变量 ``<KEY>``                          compose / CI / 容器注入
2    环境变量 ``<KEY>_FILE`` 指向的文件           Docker secret、K8s Secret
3    目录 ``SECRET_DIR``（默认 ``/run/secrets``）  标准挂载约定
4    仓库 ``config/<file>``                      本机开发（.gitignore 已挡）
5    默认值                                       没有就是没有，不编造
===  ==========================================  ==========================

**本模块只返回"值"和"来源"，不打印、不写日志、不落盘**——密钥不外泄。
需要看"配了没有"时用 :func:`known_secrets`，它只暴露布尔值与来源，不暴露值本身。

与既有代码的关系
----------------
改造前散落在 5 处的读取逻辑（``amap_client`` / ``amap_js`` / ``live_services`` /
``invoice_ocr`` / ``route_fetch``）都退化成调用本模块，**行为保持向后兼容**：
环境变量仍然最优先，本地 ``config/*.txt`` 仍然可用，占位符仍然不算密钥。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

#: 仓库根（src/config.py → parents[1] = 仓库根）
ROOT = Path(__file__).resolve().parents[1]

#: 挂载密钥的标准目录（Docker/K8s 约定）；可用环境变量 ``SECRET_DIR`` 覆盖
DEFAULT_SECRET_DIR = "/run/secrets"

#: 占位符前缀：这些内容是"占位文本"，不是密钥，一律当作未配置
PLACEHOLDER_PREFIXES: Tuple[str, ...] = ("请至高德", "PLACEHOLDER", "<", "你的")

#: 已知密钥登记表：(环境变量名, 仓库内文件名, 中文标签)
#: 诊断与巡检用；**不含任何值**。
SECRET_SPECS: Tuple[Tuple[str, Tuple[str, ...], str], ...] = (
    ("AMAP_WEB_SERVICE_KEY", ("amap_key.txt",), "高德 Web 服务 Key"),
    ("AMAP_JS_API_KEY", ("amap_js_key.txt",), "高德 JS API Key"),
    ("AMAP_JS_SECURITY_CODE", ("amap_js_security_code.txt",), "高德 JS 安全密钥"),
    ("DEEPSEEK_API_KEY", ("deepseek_key.txt",), "DeepSeek 发票识别 Key"),
)


def secret_dir() -> Path:
    """返回挂载密钥目录（不存在则返回该路径本身，由调用方判断存在性）。"""
    raw = (os.environ.get("SECRET_DIR") or "").strip()
    return Path(raw) if raw else Path(DEFAULT_SECRET_DIR)


def _is_placeholder(value: str) -> bool:
    """占位符文本不算密钥。"""
    return any(value.startswith(prefix) for prefix in PLACEHOLDER_PREFIXES)


def strip_assignment_prefix(value: str, env_name: str = "") -> str:
    """去掉 ``KEY=`` 赋值前缀。

    有人会把密钥文件写成 ``DEEPSEEK_API_KEY=sk-xxxx``（整行赋值）而不是只写值，
    改造前各调用点各自处理一遍；这里统一收口。**只在前缀完全匹配时剥离**，
    不会误伤本身带 ``=`` 结尾的 base64 密钥。
    """
    text = (value or "").strip()
    prefix = (env_name or "").strip()
    if prefix and text.upper().startswith(prefix.upper() + "="):
        text = text.split("=", 1)[1].strip()
    return text


def _read_text(path: Path) -> str:
    """读一个文本文件；任何异常都当作"没有"（缺文件、权限不足、乱码都不该炸服务）。"""
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return ""


def secret_with_source(env_name: str, files: Sequence[str] = (),
                        default: str = "") -> Tuple[str, str]:
    """按分层优先级取一个密钥，返回 ``(值, 来源)``。

    来源取值：``env`` / ``env_file`` / ``secret_dir`` / ``config`` / ``default`` / ``missing``。

    参数
    ----
    env_name:
        环境变量名，例如 ``AMAP_WEB_SERVICE_KEY``。
    files:
        仓库 ``config/`` 下的候选文件名（按顺序取第一个非空的）；
        同样的文件名也会去 ``SECRET_DIR`` 下找。
    default:
        全部没有时的返回值（默认空串）。
    """
    env_value = (os.environ.get(env_name) or "").strip()
    if env_value and not _is_placeholder(env_value):
        return strip_assignment_prefix(env_value, env_name), "env"

    file_pointer = (os.environ.get("%s_FILE" % env_name) or "").strip()
    if file_pointer:
        value = _read_text(Path(file_pointer))
        if value and not _is_placeholder(value):
            return strip_assignment_prefix(value, env_name), "env_file"

    mounted = secret_dir()
    for name in files:
        value = _read_text(mounted / name)
        if value and not _is_placeholder(value):
            return strip_assignment_prefix(value, env_name), "secret_dir"

    for name in files:
        value = _read_text(ROOT / "config" / name)
        if value and not _is_placeholder(value):
            return strip_assignment_prefix(value, env_name), "config"

    if default:
        return default, "default"
    return "", "missing"


def secret(env_name: str, files: Sequence[str] = (),
           default: str = "") -> str:
    """只取值的版本（大多数调用方只需要这个）。"""
    return secret_with_source(env_name, files=files, default=default)[0]


# ---------------------------------------------------------------------------
# 普通配置（非密钥）：环境变量优先，其次仓库 config 里的同名 json/txt
# ---------------------------------------------------------------------------

def setting(env_name: str, default: str = "") -> str:
    """读一个普通配置项（不是密钥）：环境变量 → 默认值。"""
    value = (os.environ.get(env_name) or "").strip()
    return value if value else default


def int_setting(env_name: str, default: int) -> int:
    """读整数配置；非法值退回默认（不抛异常，服务不能因为一个环境变量挂掉）。"""
    raw = setting(env_name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def bool_setting(env_name: str, default: bool) -> bool:
    """读布尔配置。

    真值：``1 / true / yes / on``；假值：``0 / false / no / off``；其余按默认。
    """
    raw = setting(env_name).lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def csv_setting(env_name: str, default: Sequence[str] = ()) -> List[str]:
    """读逗号分隔列表（用于 CORS 白名单这类多值配置）。"""
    raw = setting(env_name)
    if not raw:
        return list(default)
    return [part.strip() for part in raw.split(",") if part.strip()]


# ---------------------------------------------------------------------------
# 巡检：只说"配了没有、从哪儿来的"，永远不说值
# ---------------------------------------------------------------------------

def known_secrets() -> List[Dict[str, object]]:
    """巡检视图：每个已知密钥的 {env, label, configured, source}。

    **不含值**——用于部署自检页、CI 断言与 ``tools/verify_p0.py``。
    """
    out: List[Dict[str, object]] = []
    for env_name, files, label in SECRET_SPECS:
        _, source = secret_with_source(env_name, files=files)
        out.append({
            "env": env_name,
            "label": label,
            "configured": source != "missing",
            "source": source,
        })
    return out


def secrets_configured() -> bool:
    """所有已知密钥是否都就绪（缺一个就返回 False）。"""
    return all(bool(item["configured"]) for item in known_secrets())


def describe() -> Dict[str, object]:
    """配置面概览（给运维看的第一手信息，不含任何密钥值）。"""
    return {
        "secret_dir": str(secret_dir()),
        "secret_dir_exists": secret_dir().is_dir(),
        "secrets": known_secrets(),
    }


__all__ = [
    "ROOT",
    "SECRET_SPECS",
    "secret",
    "secret_with_source",
    "secret_dir",
    "setting",
    "int_setting",
    "bool_setting",
    "csv_setting",
    "known_secrets",
    "secrets_configured",
    "describe",
]
