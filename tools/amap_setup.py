# -*- coding: utf-8 -*-
"""高德 key 配置助手：交互式检查 + 一键写入 + 验证。

用途：PM/队友第一次配高德 key 时跑这个脚本，照着提示走，**不会填错类型**。
它会：
  1. 检查三个文件当前状态（已填 / 占位符 / 缺失）；
  2. 告诉你该去控制台点哪里、复制哪一列（针对每个文件分别给话术）；
  3. 你把值粘进来（**只在终端输入，不进 git、不进本对话**），脚本写进 config/；
  4. 立刻做真实连通性验证（REST 真调一次 + JS key 格式检查）；
  5. 提示下一步命令。

用法：
    python -m tools.amap_setup            # 交互式配置
    python -m tools.amap_setup --check    # 只检查不修改
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
WEB_KEY_PATH = CONFIG_DIR / "amap_key.txt"
JS_KEY_PATH = CONFIG_DIR / "amap_js_key.txt"
JS_CODE_PATH = CONFIG_DIR / "amap_js_code.txt"
PLACEHOLDER = "请至高德开放平台申请自有 key 并替换此占位符"

# 高德 key 是 32 位十六进制字符串
KEY_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")

# 每个文件的填写指引
GUIDE: Dict[str, Dict[str, str]] = {
    "amap_key.txt": {
        "title": "① Web 服务 key（后端用）",
        "where": "控制台 → 应用管理 → 你的应用 →【添加 Key】→ 服务平台选【Web 服务】→ 提交",
        "what": "复制列表里那一行的 **Key**（32 位，如 4a2…c89dc）",
        "dont": "⚠️ 同一行右边那个『安全密钥』**不要填这里**——那是数字签名私钥，我们用不上",
        "note": "IP 白名单留空、数字签名不开，最省事",
    },
    "amap_js_key.txt": {
        "title": "② Web 端（JS API）key（前端地图用）",
        "where": "同一个应用 →【添加 Key】→ 服务平台选【Web 端（JS API）】→ 提交",
        "what": "复制列表里那一行的 **Key**（32 位）",
        "dont": "⚠️ 服务平台千万别选成【Web 服务】，否则页面报 USERKEY_PLAT_NOMATCH",
        "note": "域名白名单必填：localhost、127.0.0.1、localhost:8501、127.0.0.1:8501（+ 答辩现场 IP:端口）",
    },
    "amap_js_code.txt": {
        "title": "③ 安全密钥 securityJsCode（JS API 强制）",
        "where": "同 ② 那一行，**第二列**『安全密钥』（32 位，如 8fb2…9f31）",
        "what": "复制这一串——它在添加 JS API key 时一并生成，勾了『安全密钥』才有",
        "dont": "⚠️ 这不是 ② 的 key，两串长得像但完全不同，别复制错列",
        "note": "代码里必须写在加载 SDK 的 script 之前，顺序反了页面直接白屏",
    },
}


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _status(path: Path) -> Tuple[str, str]:
    """返回 (状态, 详情)。状态 ∈ {missing, placeholder, ok, bad}"""
    if not path.exists():
        return "missing", "文件不存在"
    text = _read(path)
    if not text:
        return "missing", "文件为空"
    if text.startswith(PLACEHOLDER):
        return "placeholder", "仍是占位符"
    if not KEY_PATTERN.match(text):
        return "bad", "格式不像高德 key（应 32 位十六进制）"
    return "ok", "已配置"


_STATUS_ICON = {"ok": "✓", "placeholder": "○", "missing": "×", "bad": "!"}


def show_status() -> Dict[str, Tuple[str, str]]:
    print("=" * 78)
    print("高德 key 配置状态")
    print("=" * 78)
    result: Dict[str, Tuple[str, str]] = {}
    for name in ("amap_key.txt", "amap_js_key.txt", "amap_js_code.txt"):
        path = CONFIG_DIR / name
        state, detail = _status(path)
        result[name] = (state, detail)
        guide = GUIDE[name]
        print("  %s %-22s %s" % (_STATUS_ICON[state], name, detail))
    print("-" * 78)
    return result


def guide_for(name: str) -> None:
    g = GUIDE[name]
    print("")
    print("  %s" % g["title"])
    print("    去哪找 ：%s" % g["where"])
    print("    复制什么：%s" % g["what"])
    print("    %s" % g["dont"])
    print("    附带设置：%s" % g["note"])


def _write(path: Path, value: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(value.strip() + "\n", encoding="utf-8")


def verify_web_key() -> Tuple[bool, str]:
    """真调一次高德 REST，验证 Web 服务 key 有效。"""
    key = _read(WEB_KEY_PATH)
    if not key or key.startswith(PLACEHOLDER):
        return False, "未配置"
    try:
        import requests
        resp = requests.get(
            "https://rest.amap.com/v3/geocode/geo",
            params={"key": key, "address": "北京市天安门", "output": "json"},
            timeout=10,
        )
        data = resp.json()
        if data.get("status") == "1":
            loc = (data.get("geocodes") or [{}])[0].get("location", "?")
            return True, "调用成功（天安门 → %s）" % loc
        return False, "调用失败：%s（%s）" % (data.get("info", "?"), data.get("infocode", "?"))
    except Exception as exc:
        return False, "网络异常：%s" % exc


def verify_js_key() -> Tuple[bool, str]:
    """JS key 只能做格式与顺序检查（真正渲染要开浏览器）。"""
    key = _read(JS_KEY_PATH)
    code = _read(JS_CODE_PATH)
    if not key or key.startswith(PLACEHOLDER):
        return False, "未配置"
    if not code or code.startswith(PLACEHOLDER):
        return False, "key 已填但缺安全密钥（JS API 强制，缺了会白屏）"
    if key == code:
        return False, "两个文件内容相同——多半复制错列了，安全密钥和 key 是两串不同的值"
    return True, "格式正确（key 与安全密钥已区分）"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="高德 key 配置助手")
    parser.add_argument("--check", action="store_true", help="只检查状态，不修改任何文件")
    args = parser.parse_args(argv)

    status = show_status()

    if args.check:
        ok = all(state == "ok" for state, _ in status.values())
        print()
        if ok:
            print("  三件套齐了。下一步：")
            print("    1) python -m tools.route_fetch            # 预取真路线")
            print("    2) python -m src.pipeline --with-routes  # 重算路线情景 + 闸门 S1–S3")
            print("    3) python -m tools.amap_smoke_test       # 浏览器里验在线地图")
        else:
            print("  还缺东西。按上面 §1.1 的指引到控制台申请。")
        return 0 if ok else 1

    print()
    print("交互式配置：到控制台申请后，把值粘进来（输入不会进 git、不进对话记录）")
    print("直接回车跳过当前项；填错可以重跑本脚本。")
    print("=" * 78)

    for name in ("amap_key.txt", "amap_js_key.txt", "amap_js_code.txt"):
        path = CONFIG_DIR / name
        state, _ = status[name]
        if state == "ok":
            print()
            print("  %s 已配置，跳过。" % name)
            continue
        guide_for(name)
        try:
            value = input("    粘贴值（回车跳过）> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  已中断。")
            return 1
        if not value:
            print("    跳过。")
            continue
        if value.startswith(PLACEHOLDER):
            print("    跳过（还是占位符）。")
            continue
        if not KEY_PATTERN.match(value):
            print("    ⚠ 格式不像 32 位十六进制 key，仍要写入吗？(y/N) > ", end="")
            try:
                confirm = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                confirm = "n"
            if confirm != "y":
                print("    跳过。")
                continue
        _write(path, value)
        print("    ✓ 已写入 %s" % path.name)

    print()
    print("=" * 78)
    print("验证")
    print("=" * 78)
    web_ok, web_msg = verify_web_key()
    print("  %s Web 服务 key ：%s" % ("✓" if web_ok else "✗", web_msg))
    js_ok, js_msg = verify_js_key()
    print("  %s JS API key    ：%s" % ("✓" if js_ok else "✗", js_msg))
    print()
    print("=" * 78)
    if web_ok and js_ok:
        print("全部就绪。下一步：")
        print("  1) python -m tools.route_fetch              # 预取 310 条真路线（约 2 分钟）")
        print("  2) python -m src.pipeline --with-routes    # 重算 + 闸门 S1/S3 应转绿")
        print("  3) python -m tools.amap_smoke_test         # 浏览器里看在线地图")
        print("  4) streamlit run app/app.py                 # 第 3 页动态路线区块")
        return 0
    print("还没配完。按 §1.1 指引补齐后重跑本脚本。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
