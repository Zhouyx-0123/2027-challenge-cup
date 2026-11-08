#!/usr/bin/env python3
"""OBD 串口探针：列出串口，读取固件打印的 [ n s] RPM=... 文本。

用法：
    python tools/obd_serial_probe.py --list
    python tools/obd_serial_probe.py --port COM7 --seconds 15 --out outputs/obd_serial.log

不依赖项目其它模块，只依赖 pyserial。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

try:
    import serial
    from serial.tools import list_ports
except ImportError as exc:  # pragma: no cover - 运行环境提示
    raise SystemExit("缺少 pyserial，请先执行：python -m pip install pyserial") from exc


def _port_rows():
    rows = []
    for p in list_ports.comports():
        rows.append(
            {
                "device": p.device,
                "description": p.description or "",
                "hwid": p.hwid or "",
                "vid": p.vid,
                "pid": p.pid,
            }
        )
    return rows


def _pick_port(rows, explicit=None):
    if explicit:
        return explicit
    # 优先 CH340/CH341（VID 1A86），其次任何 USB 串口，最后才用第一个。
    for row in rows:
        if row["vid"] == 0x1A86:
            return row["device"]
    for row in rows:
        if "USB" in (row["hwid"] or "").upper() or "USB" in (row["description"] or "").upper():
            return row["device"]
    if rows:
        return rows[0]["device"]
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="读取 OBD 固件串口文本")
    ap.add_argument("--list", action="store_true", help="只列出串口")
    ap.add_argument("--port", help="例如 COM7；不填则自动选 CH340/USB 串口")
    ap.add_argument("--baud", type=int, default=115200, help="默认 115200（固件 LOG_BAUDRATE）")
    ap.add_argument("--seconds", type=float, default=10.0, help="读取时长，0 表示一直读到 Ctrl+C")
    ap.add_argument("--out", help="原始字节同时写入该文件")
    args = ap.parse_args(argv)

    rows = _port_rows()
    if args.list or not rows:
        print("串口列表：")
        for row in rows:
            print(
                f"  {row['device']:<8} {row['description']:<30} "
                f"VID:PID={row['vid']:04X}:{row['pid']:04X}  {row['hwid']}"
                if row["vid"] is not None and row["pid"] is not None
                else f"  {row['device']:<8} {row['description']:<30} {row['hwid']}"
            )
        if not rows:
            print("  （没有发现任何串口）")
        return 0 if rows else 2

    port = _pick_port(rows, args.port)
    if not port:
        print("没有可用串口。", file=sys.stderr)
        return 2

    out_path = Path(args.out) if args.out else None
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fh = out_path.open("wb")
    else:
        fh = None

    print(f"打开 {port} @ {args.baud} 8N1，读取 {args.seconds:g}s ...", flush=True)
    deadline = None if args.seconds <= 0 else time.monotonic() + args.seconds
    n_lines = 0
    try:
        with serial.Serial(port, args.baud, timeout=0.5) as ser:
            ser.reset_input_buffer()
            while deadline is None or time.monotonic() < deadline:
                raw = ser.readline()
                if not raw:
                    continue
                if fh:
                    fh.write(raw)
                    fh.flush()
                text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                print(text, flush=True)
                n_lines += 1
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，停止。", file=sys.stderr)
    except serial.SerialException as exc:
        print(f"串口错误：{exc}", file=sys.stderr)
        return 1
    finally:
        if fh:
            fh.close()
    print(f"共读取 {n_lines} 行。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
