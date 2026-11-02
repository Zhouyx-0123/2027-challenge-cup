# -*- coding: utf-8 -*-
"""桥接端到端验收：文本帧 -> 解析 -> HTTP POST -> 接收端回执。

用标准库起一个桩服务模拟 `/api/upload_obd_gps`（真实服务是 Flask，
本脚本只验桥接端的请求构造、令牌头和批量语义，不验 Flask 逻辑）。
"""
from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "src"))

from src import obd_line  # noqa: E402
from tools import obd_serial_bridge as bridge  # noqa: E402

CAPTURED = {"batches": [], "tokens": []}


class StubHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - 标准库命名
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        CAPTURED["batches"].append(payload.get("records", []))
        CAPTURED["tokens"].append(self.headers.get("X-Device-Token"))
        body = json.dumps({
            "status": "success",
            "accepted": len(payload.get("records", [])),
            "rejected": 0,
            "reasons": {},
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静音
        return


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    sample = HERE.parent / "samples" / "frames_sample.txt"
    lines = list(bridge.iter_file_lines(sample))

    code = bridge.run(
        lines,
        endpoint="http://127.0.0.1:%d" % port,
        token="test-token-abc",
        vehicle_id="TRK-GZ-001",
        fuel_type="diesel",
        plate=None,
        batch=20,
        flush_seconds=999,
        dry_run=False,
        has_gps_source=False,
    )
    server.shutdown()

    sent = [r for batch in CAPTURED["batches"] for r in batch]
    failures = []
    if code != 0:
        failures.append("run() 退出码 %r" % code)
    if len(sent) != 5:
        failures.append("上报条数 期望 5 实际 %d" % len(sent))
    if set(CAPTURED["tokens"]) != {"test-token-abc"}:
        failures.append("令牌头未按契约发送: %r" % CAPTURED["tokens"])
    if not all(r.get("vehicle_id") == "TRK-GZ-001" for r in sent):
        failures.append("vehicle_id 未灌注到全部记录")
    if not all(isinstance(r.get("ts"), str) and "+08:00" in r["ts"] for r in sent):
        failures.append("ts 不是带偏移的 ISO8601")
    if not all(r.get("total_fuel_l") is not None for r in sent):
        failures.append("存在没有累计油耗的记录（应已在解析阶段剔除）")
    # 顺序与 uptime 单调一致
    if not all(sent[i]["ts"] <= sent[i + 1]["ts"] for i in range(len(sent) - 1)):
        failures.append("ts 未单调递增")

    print("PASSED %d / FAILED %d" % (7 - len(failures), len(failures)))
    for item in failures:
        print("  FAIL %s" % item)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
