# -*- coding: utf-8 -*-
"""串口文本帧桥：COM 口 -> 解析 -> POST /api/upload_obd_gps。

设备侧只吐文本行，不联网；由本桥把行转换成契约 JSON 后走**现有接收端点**，
因此鉴权、限流、审计、落盘全部复用 `server.py`，不新开一条绕过校验的口子。

两种来源：

    python -m tools.obd_serial_bridge --port COM5 --baud 115200 --vehicle-id TRK-GZ-001
    python -m tools.obd_serial_bridge --replay data/raw/obd_gps/sample_frames.txt --vehicle-id TRK-GZ-001

`--replay` 用于无硬件时跑通链路（也用于 CI）。

令牌：`--token` 或环境变量 ``OBD_DEVICE_TOKEN``；服务端 ``SENSOR_AUTH=0`` 时可省略。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import obd_line  # noqa: E402

CST = timezone(timedelta(hours=8))
DEFAULT_ENDPOINT = "http://127.0.0.1:5000"


# ---------------------------------------------------------------------------
# 来源
# ---------------------------------------------------------------------------
def iter_serial_lines(port: str, baud: int) -> Iterator[str]:
    """从串口持续读行。缺 pyserial 时给出明确指引而不是 ImportError 堆栈。"""
    try:
        import serial  # noqa: WPS433 - 延迟导入，无硬件环境不需要装
    except ImportError:
        raise SystemExit(
            "缺少 pyserial：pip install pyserial\n"
            "（只想验证链路可用 --replay 回放文本文件，不需要装 pyserial）"
        )

    with serial.Serial(port=port, baudrate=baud, timeout=1.0) as handle:
        print("[OK] 已打开 %s @ %d" % (port, baud), file=sys.stderr)
        while True:
            raw = handle.readline()
            if not raw:
                continue
            yield raw.decode("utf-8", errors="replace").rstrip("\r\n")


def iter_file_lines(path: Path) -> Iterator[str]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            yield line.rstrip("\r\n")


# ---------------------------------------------------------------------------
# 上报
# ---------------------------------------------------------------------------
def post_batch(endpoint: str, token: Optional[str], records: List[dict],
               timeout: float = 10.0) -> dict:
    """把一批记录 POST 给接收端。返回服务端响应体。"""
    url = endpoint.rstrip("/") + "/api/upload_obd_gps"
    body = json.dumps({"records": records}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        request.add_header("X-Device-Token", token)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        return {"status": "error", "http_status": exc.code, "message": detail}
    except urllib.error.URLError as exc:
        return {"status": "error", "message": "连不上接收端：%s" % exc.reason}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run(lines: Iterable[str], *, endpoint: str, token: Optional[str], vehicle_id: str,
        fuel_type: Optional[str], plate: Optional[str], batch: int,
        flush_seconds: float, dry_run: bool, has_gps_source: bool) -> int:
    clock = obd_line.UptimeClock(CST)
    buffer: List[dict] = []
    last_flush = time.monotonic()
    totals = {"sent": 0, "accepted": 0, "rejected": 0, "failed": 0}

    def flush() -> None:
        nonlocal buffer, last_flush
        last_flush = time.monotonic()
        if not buffer:
            return
        if dry_run:
            print("[dry-run] 将上报 %d 条，首条：%s" % (
                len(buffer), json.dumps(buffer[0], ensure_ascii=False)))
            totals["sent"] += len(buffer)
            buffer = []
            return
        response = post_batch(endpoint, token, buffer)
        if response.get("status") == "success":
            totals["accepted"] += int(response.get("accepted", 0))
            totals["rejected"] += int(response.get("rejected", 0))
        else:
            totals["failed"] += len(buffer)
            print("[!] 上报失败：%s" % json.dumps(response, ensure_ascii=False),
                  file=sys.stderr)
        totals["sent"] += len(buffer)
        buffer = []

    for line in lines:
        records, _rejects, _stats = obd_line.parse_stream(
            [line], clock=clock, vehicle_id=vehicle_id, fuel_type=fuel_type,
            plate=plate, has_gps_source=has_gps_source,
        )
        buffer.extend(records)
        if len(buffer) >= batch or (time.monotonic() - last_flush) >= flush_seconds:
            flush()

    flush()
    print(json.dumps(totals, ensure_ascii=False))
    return 0 if totals["failed"] == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OBD 文本帧串口桥")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--port", help="串口名，如 COM5 或 /dev/ttyUSB0")
    source.add_argument("--replay", help="回放文本帧文件（无硬件时用）")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--vehicle-id", required=True, help="契约必填；帧里没有车辆标识")
    parser.add_argument("--fuel-type", default="diesel",
                        choices=["diesel", "gasoline", "lpg", "cng", "electric", "hybrid"])
    parser.add_argument("--plate", default=None)
    parser.add_argument("--endpoint", default=os.environ.get("OBD_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--token", default=os.environ.get("OBD_DEVICE_TOKEN"))
    parser.add_argument("--batch", type=int, default=20)
    parser.add_argument("--flush-seconds", type=float, default=5.0)
    parser.add_argument("--dry-run", action="store_true", help="只解析不发送")
    parser.add_argument("--has-gps-source", action="store_true",
                        help="若另有 GPS 通道在补 lat/lng，加此开关以取消 no_gps_source 标记")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    lines = iter_serial_lines(args.port, args.baud) if args.port \
        else iter_file_lines(Path(args.replay))

    if args.port and not args.token and os.environ.get("SENSOR_AUTH", "1") != "0":
        print("[!] 未提供令牌：服务端默认 fail-closed，整批会被 401 拒收", file=sys.stderr)

    return run(
        lines, endpoint=args.endpoint, token=args.token, vehicle_id=args.vehicle_id,
        fuel_type=args.fuel_type, plate=args.plate, batch=args.batch,
        flush_seconds=args.flush_seconds, dry_run=args.dry_run,
        has_gps_source=args.has_gps_source,
    )


if __name__ == "__main__":
    raise SystemExit(main())
