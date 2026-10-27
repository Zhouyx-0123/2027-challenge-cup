# -*- coding: utf-8 -*-
"""传感器链路探针：主机侧模拟设备 / 诊断云边端链路。

定位
----
**只做「发请求 + 看结果」，不碰业务数据**（与 tools/ingest_pm.py 的 I/O 原则一致）。
用来回答三个问题：

1. 服务端起来了吗？（``--expect-401``：不带令牌应该被拒）
2. 我这个设备登记过、令牌对吗？（``--register`` + 正常上报）
3. 读数真的落进 sensor_data.csv 了吗？（``--readback``）

只用标准库 ``urllib.request``（不引 requests）。

用法
----
::

    # 1) 自检：服务端在不在、鉴权是否 fail-closed
    python -m tools.sensor_probe --expect-401 --server http://127.0.0.1:5000

    # 2) 登记一个临时设备，拿到明文令牌（只打印一次）
    python -m tools.sensor_probe --register --server http://127.0.0.1:5000

    # 3) 带令牌发一条
    python -m tools.sensor_probe --server http://127.0.0.1:5000 \
        --device-id PDM-PROBE-01 --token <上一步打印的明文>

    # 4) 连发 N 次，测限流（服务端默认 60 次/分钟）
    python -m tools.sensor_probe --server http://127.0.0.1:5000 \
        --device-id PDM-PROBE-01 --token <令牌> --watch 70 --interval 1

    # 5) 校验读数确实落进 CSV
    python -m tools.sensor_probe --readback --device-id PDM-PROBE-01

退出码：0 全部通过 / 1 有失败项。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows 控制台默认 GBK，装不下「×」这类符号，强制 UTF-8 输出。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - 老解释器
        pass

from src import device_auth  # noqa: E402

DEFAULT_SERVER = "http://127.0.0.1:5000"
DEFAULT_DEVICE_ID = "PDM-PROBE-01"
UPLOAD_PATH = "/api/upload_sensor"
DEFAULT_TIMEOUT = 8.0


# ---------------------------------------------------------------------------
# HTTP 小工具
# ---------------------------------------------------------------------------
def _post(server: str, payload: Dict[str, Any], token: Optional[str],
          timeout: float = DEFAULT_TIMEOUT
          ) -> Tuple[Optional[int], Dict[str, str], str, str]:
    """发一条读数。

    返回 ``(状态码 or None, 响应头, 响应体, 错误说明)``。
    状态码为 ``None`` 表示**根本没连上**（服务没起来 / 网络不通）。
    """
    url = server.rstrip("/") + UPLOAD_PATH
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers[device_auth.TOKEN_HEADER] = token
    request = urllib.request.Request(url, data=body, headers=headers,
                                     method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            head = {k: v for k, v in response.headers.items()}
            return response.status, head, text, ""
    except urllib.error.HTTPError as exc:
        # 4xx / 5xx：这是"服务端有应答"，要如实报状态码
        text = ""
        try:
            text = exc.read().decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - 极端情况
            pass
        head = {k: v for k, v in (exc.headers or {}).items()}
        return exc.code, head, text, ""
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        return None, {}, "", str(reason)
    except OSError as exc:
        return None, {}, {}, str(exc)


def _health(server: str, timeout: float = 4.0) -> Tuple[Optional[int], str]:
    """GET /api/health（探活 + 看鉴权模式）。"""
    url = server.rstrip("/") + "/api/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception as exc:
        return None, str(exc)


def _offline_message(server: str, detail: str) -> str:
    return ("[FAIL] 连不上服务端 %s —— 服务可能没起来。"
            "先在项目根目录跑 `python server.py`，再试一次。（%s）"
            % (server, detail or "连接失败"))


# ---------------------------------------------------------------------------
# 各子命令
# ---------------------------------------------------------------------------
def cmd_expect_401(args: argparse.Namespace) -> int:
    """不带令牌发一次，期望 401（验证 fail-closed）。"""
    server = args.server
    code, _head, body, detail = _post(server, _payload(args, 1), None,
                                      timeout=args.timeout)
    if code is None:
        print(_offline_message(server, detail))
        return 1
    if code == 401:
        print("[OK] 未授权被拒（HTTP 401），fail-closed 生效")
        print("     服务端提示：%s" % _brief(body))
        return 0
    if code == 200:
        print("[FAIL] 未带令牌竟然被放行（HTTP 200）—— 服务端的 SENSOR_AUTH 是不是被关了？")
        return 1
    print("[FAIL] 期望 401，实际 HTTP %d：%s" % (code, _brief(body)))
    return 1


def cmd_register(args: argparse.Namespace) -> int:
    """在本机注册表登记一个探针设备，打印明文令牌。"""
    device_id = args.device_id
    token = args.token or device_auth.generate_token()
    if len(token) < device_auth.MIN_TOKEN_LENGTH:
        print("[FAIL] 令牌至少 %d 个字符" % device_auth.MIN_TOKEN_LENGTH)
        return 1
    path = device_auth.upsert_device(device_id, device_auth.token_digest(token),
                                     note=args.note or "链路探针（临时）",
                                     enabled=True, path=args.file)
    print("[OK] 已登记探针设备 %s" % device_id)
    print("     注册表：%s（**只存 sha256 摘要**）" % path)
    print("-" * 66)
    print("明文令牌（只显示这一次，请立刻抄到设备端）：")
    print("    %s" % token)
    print("-" * 66)
    print("设备端请求头：%s: %s" % (device_auth.TOKEN_HEADER, token))
    print("接着可以试：")
    print("    python -m tools.sensor_probe --server %s --device-id %s --token %s"
          % (args.server, device_id, token))
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    """带令牌发一条（或 --watch 连发 N 条）。"""
    server = args.server
    if not args.token:
        print("[FAIL] 缺少 --token。先跑 `--register` 拿明文令牌。")
        return 1

    count = args.watch if args.watch and args.watch > 0 else 1
    interval = max(0.0, float(args.interval))
    ok_count = 0
    throttled = 0
    for index in range(count):
        code, head, body, detail = _post(server, _payload(args, index + 1),
                                          args.token, timeout=args.timeout)
        if code is None:
            print(_offline_message(server, detail))
            return 1
        if code == 200:
            ok_count += 1
            print("[OK] 第 %d 次上报成功（HTTP 200）" % (index + 1))
        elif code == 401:
            print("[FAIL] 第 %d 次被拒（HTTP 401）—— 令牌不对或设备没登记，停止重试"
                  % (index + 1))
            print("     服务端提示：%s" % _brief(body))
            return 1
        elif code == 429:
            throttled += 1
            retry_after = head.get("Retry-After", "?")
            print("[WARN] 第 %d 次命中限流（HTTP 429），Retry-After=%s"
                  % (index + 1, retry_after))
        else:
            print("[FAIL] 第 %d 次失败（HTTP %d）：%s"
                  % (index + 1, code, _brief(body)))
        if index + 1 < count and interval > 0:
            time.sleep(interval)

    print("-" * 66)
    print("成功 %d 次｜限流 %d 次｜共 %d 次" % (ok_count, throttled, count))
    if throttled:
        print("[OK] 限流按预期生效（429 + Retry-After）")
    return 0 if ok_count > 0 else 1


def cmd_readback(args: argparse.Namespace) -> int:
    """读 CSV，确认探针那几行在。"""
    from src import sensor as sensor_kernel  # 延迟导入：只在回读时用

    path = sensor_kernel.sensor_data_path()
    if not path.exists():
        print("[FAIL] 读数文件不存在：%s" % path)
        print("     先发一条数据（不带 --readback），再回读。")
        return 1
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        print("[FAIL] 读不了 %s：%s" % (path, exc))
        return 1

    device_id = args.device_id
    matched = [row for row in rows
               if str(row.get("device_id", "")).strip() == device_id]
    print("读数文件：%s" % path)
    print("总行数 %d｜设备 %s 的行数 %d" % (len(rows), device_id, len(matched)))
    if not matched:
        print("[FAIL] 没找到设备 %s 的读数行" % device_id)
        return 1
    for row in matched[-5:]:
        print("     %s｜co2=%s｜temp=%s"
              % (row.get("timestamp", ""), row.get("co2", ""),
                 row.get("temp", "")))
    print("[OK] 设备 %s 的读数已落盘（共 %d 行）" % (device_id, len(matched)))
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    """探活 + 看服务端鉴权模式。"""
    code, text = _health(args.server, timeout=args.timeout)
    if code is None:
        print(_offline_message(args.server, text))
        return 1
    if code != 200:
        print("[FAIL] /api/health 返回 HTTP %d" % code)
        return 1
    try:
        payload = json.loads(text)
    except ValueError:
        print("[OK] /api/health 可达：%s" % _brief(text))
        return 0
    auth = payload.get("auth", {}) if isinstance(payload, dict) else {}
    print("[OK] /api/health 可达")
    print("     鉴权：%s｜请求头：%s｜已登记设备：%s 个｜限流：%s 次/分钟"
          % ("开启" if auth.get("enabled") else "**关闭（仅本机演示）**",
             auth.get("header", device_auth.TOKEN_HEADER),
             auth.get("devices", "?"),
             payload.get("rate_limit_per_min", "?")))
    print("     注册表：%s" % auth.get("registry", "?"))
    return 0


# ---------------------------------------------------------------------------
# 公共
# ---------------------------------------------------------------------------
def _payload(args: argparse.Namespace, index: int) -> Dict[str, Any]:
    """构造一条上报读数（带 ts / seq，服务端会忽略但留给 P1 时序库）。"""
    return {
        "device_id": args.device_id,
        "co2": args.co2,
        "temp": args.temp,
        "ts": int(time.time()),
        "seq": index,
    }


def _brief(text: str, limit: int = 160) -> str:
    """把响应体压成一行短文本。"""
    one_line = " ".join((text or "").split())
    if len(one_line) > limit:
        one_line = one_line[:limit] + "…"
    return one_line or "（空）"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="传感器链路探针：模拟设备上报 / 诊断鉴权与限流")
    parser.add_argument("--server", default=DEFAULT_SERVER,
                        help="接收端地址（默认 %s）" % DEFAULT_SERVER)
    parser.add_argument("--device-id", default=DEFAULT_DEVICE_ID,
                        help="设备号（默认 %s）" % DEFAULT_DEVICE_ID)
    parser.add_argument("--token", default="",
                        help="明文令牌（用 --register 拿）")
    parser.add_argument("--register", action="store_true",
                        help="在本机注册表登记探针设备并打印明文令牌")
    parser.add_argument("--expect-401", action="store_true",
                        help="不带令牌发一次，期望被 401 拒掉")
    parser.add_argument("--watch", type=int, default=0,
                        help="连发 N 次（0 = 只发一次）")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="--watch 的发送间隔（秒）")
    parser.add_argument("--readback", action="store_true",
                        help="回读 sensor_data.csv，确认读数落盘")
    parser.add_argument("--health", action="store_true",
                        help="探活并打印服务端鉴权模式")
    parser.add_argument("--co2", type=float, default=812.3,
                        help="模拟 CO2 读数（默认 812.3）")
    parser.add_argument("--temp", type=float, default=24.1,
                        help="模拟温度读数（默认 24.1）")
    parser.add_argument("--note", default="", help="登记时的备注")
    parser.add_argument("--file", default=None,
                        help="注册表路径（默认 config/devices.json）")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help="HTTP 超时（秒）")
    args = parser.parse_args(argv)

    if args.register:
        return cmd_register(args)
    if args.expect_401:
        return cmd_expect_401(args)
    if args.readback:
        return cmd_readback(args)
    if args.health:
        return cmd_health(args)
    return cmd_send(args)


if __name__ == "__main__":
    raise SystemExit(main())
