# -*- coding: utf-8 -*-
"""设备令牌管理：登记 / 轮换 / 吊销 / 查看（企业级 P0 · 传感器接入鉴权）。

用法
----
::

    python -m tools.device_tokens list
    python -m tools.device_tokens add    --device-id PDM-GZ-001 --note "1 号冷链车"
    python -m tools.device_tokens rotate --device-id PDM-GZ-001
    python -m tools.device_tokens remove --device-id PDM-GZ-001
    python -m tools.device_tokens batch  --prefix PDM-GZ --count 5 --out outputs/device_tokens.csv

明文令牌**只在 add / rotate 时打印一次**——注册表里只存 sha256 摘要。
丢了就 ``rotate``（旧令牌立即失效）。

设备端怎么带这个令牌（ESP32 / MicroPython）::

    import requests
    requests.post("http://<主机>:5000/api/upload_sensor",
                  json={"device_id": "PDM-GZ-001", "co2": 812.3, "temp": 24.1},
                  headers={"X-Device-Token": "<你打印出来的那串明文>"})

纪律
----
* 注册表默认落在 ``config/devices.json``（**只存摘要**，但本机运行状态不入库，
  随仓库交付的是空的 ``config/devices.example.json`` 模板）；
* 明文令牌不要写进任何文件——用环境变量或设备端安全存储；
* 部署到企业环境前必须保持 ``SENSOR_AUTH`` 开启（默认就是开的）。

``batch`` 例外
--------------
``batch`` 是**唯一**会把明文令牌写进文件的子命令（因为现场要一次性把几十台
设备配好）。导出的 CSV **绝不入库**——``.gitignore`` 里的 ``*.csv`` 规则已经
挡住了默认路径 ``outputs/device_tokens.csv``；用 ``--out`` 改了路径的话，
要自己确认那个路径也被忽略。
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: batch 默认导出路径（在 outputs/ 下，被 .gitignore 的 *.csv 规则挡住）
DEFAULT_BATCH_OUT = ROOT / "outputs" / "device_tokens.csv"

# Windows 控制台默认 GBK，装不下「¥」这类符号，强制 UTF-8 输出。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - 老解释器
        pass

from src import device_auth  # noqa: E402


def _print_devices(path: Optional[Path]) -> int:
    devices = device_auth.list_devices(path=path)
    target = device_auth.registry_path(path)
    print("注册表：%s" % target)
    if not devices:
        print("（空）还没有登记任何设备")
        print("新增：python -m tools.device_tokens add --device-id <设备号>")
        return 0
    print("%-18s %-8s %-12s %s" % ("DEVICE_ID", "状态", "摘要前缀", "备注"))
    for item in devices:
        digest = item["token_sha256"]
        print("%-18s %-8s %-12s %s" % (
            item["device_id"],
            "启用" if item["enabled"] else "停用",
            (digest[:10] + "…") if digest else "（缺）",
            item["note"] or "",
        ))
    return 0


def _add(args: argparse.Namespace) -> int:
    token = args.token or device_auth.generate_token()
    if len(token) < device_auth.MIN_TOKEN_LENGTH:
        print("[FAIL] 令牌至少 %d 个字符" % device_auth.MIN_TOKEN_LENGTH)
        return 1
    device_auth.upsert_device(args.device_id, device_auth.token_digest(token),
                               note=args.note, enabled=not args.disabled,
                               path=args.file)
    print("[OK] 已登记设备 %s（%s）"
          % (args.device_id, "停用" if args.disabled else "启用"))
    print("-" * 66)
    print("明文令牌（只显示这一次，请立刻抄到设备端）：")
    print("    %s" % token)
    print("-" * 66)
    print("设备端请求头：%s: %s" % (device_auth.TOKEN_HEADER, token))
    return 0


def _rotate(args: argparse.Namespace) -> int:
    registry = device_auth.load_registry(args.file)
    if device_auth.find_device(args.device_id, registry) is None:
        print("[FAIL] 未找到设备 %s" % args.device_id)
        return 1
    token = args.token or device_auth.generate_token()
    device_auth.upsert_device(args.device_id, device_auth.token_digest(token),
                               note=args.note or "", enabled=not args.disabled,
                               path=args.file)
    print("[OK] 已轮换设备 %s 的令牌（旧令牌立即失效）" % args.device_id)
    print("-" * 66)
    print("明文令牌（只显示这一次）：")
    print("    %s" % token)
    print("-" * 66)
    return 0


def _remove(args: argparse.Namespace) -> int:
    if device_auth.remove_device(args.device_id, args.file):
        print("[OK] 已吊销设备 %s" % args.device_id)
        return 0
    print("[FAIL] 未找到设备 %s" % args.device_id)
    return 1


def _batch(args: argparse.Namespace) -> int:
    """批量登记 ``<prefix>-001``… 并导出「设备号 + 明文令牌」CSV。

    注册表里**只有摘要**；明文只出现在导出的 CSV 里（现场下发用）。
    """
    prefix = (args.prefix or "").strip()
    if not prefix:
        print("[FAIL] --prefix 不能为空")
        return 1
    count = int(args.count)
    if count < 1:
        print("[FAIL] --count 至少为 1")
        return 1
    width = max(3, len(str(count)))
    note = args.note or "批量登记"

    out_path = Path(args.out) if args.out else DEFAULT_BATCH_OUT
    rows: List[Tuple[str, str]] = []
    for index in range(1, count + 1):
        device_id = "%s-%0*d" % (prefix, width, index)
        token = device_auth.generate_token()
        device_auth.upsert_device(device_id, device_auth.token_digest(token),
                                   note=note, enabled=True, path=args.file)
        rows.append((device_id, token))

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["device_id", "token", "note", "created_at"])
            stamp = datetime.now().isoformat(timespec="seconds")
            for device_id, token in rows:
                writer.writerow([device_id, token, note, stamp])
    except OSError as exc:
        print("[FAIL] 写不了 %s：%s" % (out_path, exc))
        return 1

    print("=" * 66)
    print("[OK] 批量登记完成：%d 个设备（前缀 %s）" % (count, prefix))
    print("     注册表：%s（**只存 sha256 摘要**）"
          % device_auth.registry_path(args.file))
    print("     设备清单：%s … %s"
          % (rows[0][0], rows[-1][0]))
    print("-" * 66)
    print("[!] 警告：CSV 里是**明文令牌**，等于所有设备的钥匙")
    print("    路径：%s" % out_path)
    print("    请确认这个路径被 .gitignore 挡住，**绝不要提交进版本库**")
    print("    用完后请立刻分发到设备端，并删除这份 CSV")
    print("-" * 66)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="传感器设备令牌管理（注册表 + 摘要）")
    parser.add_argument("--file", default=None,
                        help="注册表路径（默认 config/devices.json）")
    sub = parser.add_subparsers(dest="action")

    sub.add_parser("list", help="列出已登记设备")

    add = sub.add_parser("add", help="登记新设备并生成令牌")
    add.add_argument("--device-id", required=True, help="设备唯一号，如 PDM-GZ-001")
    add.add_argument("--note", default="", help="备注（车号 / 位置等）")
    add.add_argument("--token", default=None, help="自定义令牌（不给就随机生成）")
    add.add_argument("--disabled", action="store_true", help="登记为停用状态")

    rotate = sub.add_parser("rotate", help="轮换令牌（旧令牌立即失效）")
    rotate.add_argument("--device-id", required=True)
    rotate.add_argument("--note", default="", help="同时更新备注")
    rotate.add_argument("--token", default=None)
    rotate.add_argument("--disabled", action="store_true", help="轮换后置为停用")

    remove = sub.add_parser("remove", help="吊销设备")
    remove.add_argument("--device-id", required=True)

    batch = sub.add_parser("batch", help="批量登记设备并导出明文令牌 CSV（**CSV 不入库**）")
    batch.add_argument("--prefix", required=True,
                       help="设备号前缀，生成 <prefix>-001…<prefix>-NNN")
    batch.add_argument("--count", type=int, default=5, help="生成几个（默认 5）")
    batch.add_argument("--out", default=None,
                       help="CSV 输出路径（默认 outputs/device_tokens.csv）")
    batch.add_argument("--note", default="", help="登记时的备注")

    args = parser.parse_args(argv)
    action = args.action or "list"
    if action == "list":
        return _print_devices(args.file)
    if action == "add":
        return _add(args)
    if action == "rotate":
        return _rotate(args)
    if action == "remove":
        return _remove(args)
    if action == "batch":
        return _batch(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
