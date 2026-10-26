# -*- coding: utf-8 -*-
"""实时监测接收端（tools/）：设备上报 → 契约校验 → 落盘。

定位：**只做「校验 → 落盘 → 记账」，不做业务计算**（同 tools/route_fetch.py 的 I/O 原则）。
    核心逻辑在 src/telemetry.py；本文件只负责收数据、调度、退出码。

三种接入模式（与 config/telemetry_contract.json 的 delivery 对应）：
    1. 文件轮询  python -m tools.ingest_pm --watch --dir data/raw/实时
    2. HTTP 接收  python -m tools.ingest_pm --serve --port 8760
    3. MQTT       预留（--mqtt），设备端选型确定后再启用

退出码：0 成功 / 1 系统错误（依赖缺失、目录不可写、HTTP 端口占用）/ 2 输入错误（参数非法）。

用法：
    python -m tools.ingest_pm --input data/raw/实时/pm_20261025.csv --dry-run
    python -m tools.ingest_pm --watch --dir data/raw/实时 --interval 30
    python -m tools.ingest_pm --serve --port 8760 --token-file config/ingest_token.txt
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import telemetry  # noqa: E402

DEFAULT_WATCH_DIR = ROOT / "data" / "raw" / "实时"
DEFAULT_LEDGER = ROOT / "outputs" / "pm_readings.csv"
DEFAULT_REPORT = ROOT / "outputs" / "pm_ingest_report.json"
DEFAULT_TOKEN_FILE = ROOT / "config" / "ingest_token.txt"


# ---------------------------------------------------------------------------
# 共用
# ---------------------------------------------------------------------------

def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")


def ingest_records(records: Sequence[Dict[str, Any]],
                   ledger: Path) -> Dict[str, Any]:
    """校验一批读数 → 落盘 → 汇总。返回报告片段。"""
    validated = telemetry.validate_batch(records)
    written = telemetry.write_ledger(ledger, validated["ok"]) if validated["ok"] else 0
    summary = telemetry.qc_summary(validated)
    return {
        "counts": dict(validated["counts"], written=written),
        "qc": summary,
        "rejected": [
            {"index": r["index"], "errors": r["errors"]} for r in validated["rejected"][:20]
        ],
        "rejected_total": len(validated["rejected"]),
        "ledger": str(ledger),
    }


def _read_token(path: Path) -> str:
    """读设备令牌。文件不存在 → 空串（此时不校验令牌，仅本机演示用）。"""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 模式 1：文件
# ---------------------------------------------------------------------------

def run_file_mode(input_path: Path, ledger: Path, report_path: Path,
                  dry_run: bool = False) -> int:
    """读一个 CSV → 校验 → 落盘（dry-run 则只出报告不落盘）。"""
    if not input_path.exists():
        print("[FAIL] 输入文件不存在：%s" % input_path)
        return 2
    records = telemetry.read_csv(input_path)
    if not records:
        print("[FAIL] 输入文件没有可用数据行：%s" % input_path)
        return 2

    validated = telemetry.validate_batch(records)
    summary = telemetry.qc_summary(validated)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": "file",
        "source": str(input_path),
        "contract": telemetry.contract_version(),
        "counts": validated["counts"],
        "qc": summary,
        "rejected": [
            {"index": r["index"], "errors": r["errors"]} for r in validated["rejected"][:20]
        ],
        "rejected_total": len(validated["rejected"]),
        "dry_run": dry_run,
    }
    if not dry_run:
        payload["ledger"] = str(ledger)
        payload["counts"]["written"] = telemetry.write_ledger(ledger, validated["ok"])
    _write_json(report_path, payload)

    print("=" * 66)
    print("实时监测接收（file%s）" % (" · dry-run" if dry_run else ""))
    print("=" * 66)
    print("  读入 %d 条｜可用 %d｜拒绝 %d｜设备 %d"
          % (validated["counts"]["input"], validated["counts"]["ok"],
             validated["counts"]["rejected"], validated["counts"]["devices"]))
    if not dry_run:
        print("  落盘 %d 条 → %s" % (payload["counts"]["written"], ledger))
    print("  采样窗：可用 %s / 共 %s｜质控标记 %s"
          % (summary["windows_usable"], summary["windows_total"],
             summary["flag_counts"] or "无"))
    for item in payload["rejected"][:5]:
        print("  [REJECT] 第 %s 条：%s" % (item["index"], "；".join(item["errors"])))
    if payload["rejected_total"] > 5:
        print("  ……另有 %d 条被拒，详见报告" % (payload["rejected_total"] - 5))
    print("  报告：%s" % report_path)
    return 0


def run_watch_mode(watch_dir: Path, ledger: Path, report_path: Path,
                   interval: int = 30) -> int:
    """轮询目录里的 CSV，收到新文件就收一批。

    已处理过的文件靠「台账里已有该 source_image」去重？—— 这里用文件级记忆：
    记录已处理文件的 (大小, 修改时间)，变了就再收一次（设备可能在追加）。
    """
    if not watch_dir.exists():
        print("[FAIL] 监听目录不存在：%s" % watch_dir)
        return 2
    print("监听 %s（每 %ds 扫一次，Ctrl+C 停）" % (watch_dir, interval))
    seen: Dict[str, Any] = {}
    try:
        while True:
            for path in sorted(watch_dir.glob("*.csv")):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                sig = (stat.st_size, int(stat.st_mtime))
                if seen.get(str(path)) == sig:
                    continue
                seen[str(path)] = sig
                print("→ 收到 %s" % path.name)
                run_file_mode(path, ledger, report_path)
            time.sleep(max(5, int(interval)))
    except KeyboardInterrupt:
        print("\n已停止。")
    return 0


# ---------------------------------------------------------------------------
# 模式 2：HTTP
# ---------------------------------------------------------------------------

def make_handler(ledger: Path, report_path: Path, token: str):
    """构造 POST /ingest/pm 的请求处理器。"""

    class Handler(BaseHTTPRequestHandler):
        server_version = "CarbonIngest/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:  # 静音默认访问日志
            return

        def _reply(self, code: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") in ("/health", "/ingest/health"):
                self._reply(200, {"ok": True, "contract": telemetry.contract_version()})
            else:
                self._reply(404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path.rstrip("/") != "/ingest/pm":
                self._reply(404, {"ok": False, "error": "not found"})
                return
            if token:
                supplied = self.headers.get("X-Device-Token", "").strip()
                if supplied != token:
                    self._reply(401, {"ok": False, "error": "bad token"})
                    return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                payload = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                self._reply(400, {"ok": False, "error": "bad json: %s" % exc})
                return

            records = payload.get("readings") if isinstance(payload, dict) else None
            if records is None:
                records = [payload] if isinstance(payload, dict) else []
            result = ingest_records(records, ledger)
            _write_json(report_path, dict(result, mode="http",
                                         generated_at=time.strftime("%Y-%m-%dT%H:%M:%S")))
            self._reply(200, {"ok": True, "counts": result["counts"]})

    return Handler


def run_serve_mode(port: int, ledger: Path, report_path: Path,
                    token_file: Path) -> int:
    """起一个最小 HTTP 接收端（stdlib，不引新依赖）。"""
    token = _read_token(token_file)
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port),
                                    make_handler(ledger, report_path, token))
    except OSError as exc:
        print("[FAIL] 端口 %d 起不来：%s" % (port, exc))
        return 1
    print("=" * 66)
    print("实时监测接收端已启动")
    print("=" * 66)
    print("  监听 0.0.0.0:%d" % port)
    print("  接口  POST /ingest/pm   （单条对象或 {\"readings\":[...]}）")
    print("  健康  GET  /health")
    print("  令牌  %s" % ("已启用（X-Device-Token）" if token
                             else "未配置 —— 仅本机演示，勿暴露到公网"))
    print("  台账  %s" % ledger)
    print("  契约  %s" % telemetry.contract_version())
    print("  Ctrl+C 停")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="实时监测接收端：设备上报 → 契约校验 → 落盘（不算账）")
    parser.add_argument("--input", default="", help="单个 CSV 文件")
    parser.add_argument("--watch", action="store_true", help="轮询目录收新文件")
    parser.add_argument("--dir", default=str(DEFAULT_WATCH_DIR), help="监听目录")
    parser.add_argument("--serve", action="store_true", help="起 HTTP 接收端")
    parser.add_argument("--port", type=int, default=8760, help="HTTP 端口")
    parser.add_argument("--token-file", default=str(DEFAULT_TOKEN_FILE),
                        help="设备令牌文件（内容即 X-Device-Token）")
    parser.add_argument("--interval", type=int, default=30, help="轮询间隔（秒）")
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER), help="落盘 CSV")
    parser.add_argument("--report", default=str(DEFAULT_REPORT), help="报告 JSON")
    parser.add_argument("--dry-run", action="store_true", help="只出报告不落盘")
    args = parser.parse_args(argv)

    ledger = Path(args.ledger)
    report = Path(args.report)

    try:
        if args.serve:
            return run_serve_mode(args.port, ledger, report, Path(args.token_file))
        if args.watch:
            return run_watch_mode(Path(args.dir), ledger, report, args.interval)
        if args.input:
            return run_file_mode(Path(args.input), ledger, report, args.dry_run)
    except Exception as exc:
        print("[FAIL] %s: %s" % (type(exc).__name__, exc))
        return 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
