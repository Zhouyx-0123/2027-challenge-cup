# -*- coding: utf-8 -*-
"""高德 Web 服务与 JS API 2.0 凭据配置检查。"""
from __future__ import annotations

import argparse
import getpass
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONFIG_DIR = ROOT / "config"
KEY_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
FILES = {
    "Web 服务 Key": CONFIG_DIR / "amap_key.txt",
    "JS API Key": CONFIG_DIR / "amap_js_key.txt",
    "JS 安全密钥": CONFIG_DIR / "amap_js_security_code.txt",
}


def read_value(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def state_of(path: Path) -> tuple[bool, str]:
    value = read_value(path)
    if not value:
        return False, "未配置"
    if value.startswith("请至高德"):
        return False, "仍为占位符"
    if not KEY_PATTERN.fullmatch(value):
        return False, "格式应为 32 位十六进制"
    return True, "已配置"


def verify_route_api() -> tuple[bool, str]:
    valid, message = state_of(FILES["Web 服务 Key"])
    if not valid:
        return False, message
    try:
        from src.routing.amap_client import fetch_driving_routes
        routes = fetch_driving_routes(
            "116.397128,39.916527", "121.473701,31.230416",
            api_key=read_value(FILES["Web 服务 Key"]), strategy=32)
        return True, "驾车路径规划调用成功，返回 %d 条备选路线" % len(routes)
    except Exception as exc:
        return False, "高德路线接口调用失败：%s" % exc


def configure() -> None:
    print("直接回车保留当前值；输入内容不会回显，也不会写入任何代码或日志。")
    for label, path in FILES.items():
        value = getpass.getpass("%s > " % label).strip()
        if value:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(value + "\n", encoding="utf-8")
            print("  已写入 %s" % path.relative_to(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="配置并验证高德地图凭据")
    parser.add_argument("--check", action="store_true", help="只检查配置，不修改文件")
    parser.add_argument("--online", action="store_true", help="额外调用一次驾车路线接口")
    args = parser.parse_args()

    if not args.check:
        configure()

    states = {label: state_of(path) for label, path in FILES.items()}
    for label, (ok, detail) in states.items():
        print("[%s] %s：%s" % ("OK" if ok else "MISS", label, detail))
    if args.online:
        ok, detail = verify_route_api()
        print("[%s] REST 路线验证：%s" % ("OK" if ok else "FAIL", detail))
    if all(ok for ok, _ in states.values()):
        print("下一步：streamlit run app/app.py，生成方案后查看精细路线地图。")
        return 0
    print("请补齐以上配置；JS 地图需要 Web 端（JS API）Key 与安全密钥。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
