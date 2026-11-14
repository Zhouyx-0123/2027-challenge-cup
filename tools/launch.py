# -*- coding: utf-8 -*-
"""一键启动：环境自检 → 一键复算 → 打开六页。

给两类人用：
  - **团队自己**：日常开发，一条命令看全项目健康度；
  - **答辩现场**：陌生电脑、可能断网、可能没有高德 key——也要能开。

设计原则：
  - Python、依赖和披露页面资源必须就绪，失败时给出说明；
  - 数据、路线或外部服务未就绪时保留原来的降级展示；
  - 高德 key 缺失 → 自动走离线降级（六页照常演）；
  - 路线表缺失 → 跳过路线段，主口径照常；
  - 披露碳账由主系统页面加载，无需启动第二个服务。

用法：
    python -m tools.launch              # 自检 + 复算 + 启动 Streamlit
    python -m tools.launch --check      # 只自检，不启动
    python -m tools.launch --no-recalc  # 不复算，直接启动（改前端时用）
    python -m tools.launch --port 8502  # 换端口
"""

from __future__ import annotations

import argparse
import ast
import errno
from importlib import metadata
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.request import build_opener, ProxyHandler
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
CONFIG = ROOT / "config"

OK = "[OK]"
WARN = "!"
BAD = "[X]"

# 前端只读铁律：这些必须成立
# 闸门当前 28 项（10 目标 + 18 结构）；实测 2026-11-08 的 data/processed/gate_report.json：
# counts = {pass: 28, fail: 0, skip: 0}，targets 10 项 + structural 18 项（route_checks 3 项另计）。
# pipeline 的 passed 标志才是权威，这里只作参考值，不对具体 PASS 数做硬编码断言（结构项还会增长）。
GATE_EXPECT_PASS = 28


def _print_header(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def check_python() -> Tuple[bool, str]:
    if sys.version_info < (3, 10):
        return False, "Python %s 过低，需要 3.10+" % ".".join(map(str, sys.version_info[:3]))
    return True, "Python %s" % ".".join(map(str, sys.version_info[:3]))


def check_deps() -> Tuple[bool, str]:
    """Check every declared dependency and its minimum version without importing the UI."""
    missing: List[str] = []
    versions: List[str] = []
    requirements = ROOT / "requirements.txt"
    if not requirements.is_file():
        return False, "缺少 requirements.txt，请解压完整项目后再启动"
    for line in requirements.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        requirement = re.fullmatch(r"([A-Za-z0-9_.-]+)>=(\d+(?:\.\d+)*)", line)
        if not requirement:
            return False, "无法识别依赖声明：%s" % line
        name, minimum = requirement.groups()
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
            continue
        match = re.match(r"\d+(?:\.\d+)*", installed)
        current = tuple(map(int, match.group().split("."))) if match else ()
        required = tuple(map(int, minimum.split(".")))
        size = max(len(current), len(required))
        if not current or current + (0,) * (size - len(current)) < required + (0,) * (size - len(required)):
            missing.append("%s>=%s（当前%s）" % (name, minimum, installed))
        else:
            versions.append("%s %s" % (name, installed))
    if missing:
        return False, "需安装或升级：%s" % ", ".join(missing)
    return True, " · ".join(versions)


def ensure_dependencies() -> bool:
    ok, detail = check_deps()
    if ok:
        return True
    print("  %s" % detail, flush=True)
    command = [sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt"),
               "--disable-pip-version-check"]
    wheels = ROOT / "wheels"
    try:
        if wheels.is_dir() and any(wheels.glob("*.whl")):
            print("  先使用 wheels/ 离线依赖包。", flush=True)
            if subprocess.call(command + ["--no-index", "--find-links", str(wheels)], cwd=ROOT) == 0:
                return check_deps()[0]
        print("  正在安装主系统依赖，首次启动需要等待安装完成。", flush=True)
        if subprocess.call(command, cwd=ROOT) != 0:
            return False
    except OSError as exc:
        print("  依赖安装未完成：%s" % exc)
        return False
    return check_deps()[0]


def check_disclosure() -> Tuple[bool, str]:
    """Validate the embedded ledger assets; never start its standalone server."""
    required = ["carbon_disclosure/dist/index.html", "carbon_disclosure/dist/sample.csv",
                "carbon_disclosure/dist/methodology.pdf", "app/components/carbon_disclosure_panel.py",
                "app/main_views.py"]
    missing = [name for name in required if not (ROOT / name).is_file()]
    if missing:
        return False, "披露碳账文件不完整：%s；请使用完整项目包" % ", ".join(missing)
    if (ROOT / "app/components/carbon_ledger_panel.py").exists():
        return False, "检测到旧稿组件 app/components/carbon_ledger_panel.py，请移除旧页面后使用完整替换包"
    try:
        tree = ast.parse((ROOT / "app/main_views.py").read_text(encoding="utf-8-sig"))
        ledger = next((node for node in tree.body if isinstance(node, ast.FunctionDef)
                       and node.name == "render_ledger"), None)
        legacy = {"_render_ledger_e0", "_e0_disclosure_text", "_sync_ledger_carbon_params"}
        if any(isinstance(node, ast.FunctionDef) and node.name in legacy for node in tree.body):
            return False, "主前端仍保留旧碳台账代码，请使用直接替换版 app/main_views.py"
        connected = ledger is not None and any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "carbon_disclosure_panel"
            and node.func.attr == "render_carbon_disclosure_panel"
            for node in ast.walk(ledger))
        if not connected:
            return False, "主前端仍是旧版：app/main_views.py 未接入披露碳账；请完整更新 app/ 和 carbon_disclosure/ 目录"
    except (OSError, SyntaxError) as exc:
        return False, "主前端入口不可读：%s" % exc
    try:
        from carbon_disclosure import build_html
        build_html()
    except (ImportError, OSError, ValueError) as exc:
        return False, "披露碳账页面资源缺失或不可读：%s" % exc
    return True, "页面资源齐备；入口：主导航 → 碳台账（直接进入）"


def _safe_check(check) -> Tuple[bool, str]:
    try:
        return check()
    except Exception as exc:
        return False, "检查未完成：%s" % exc


def check_data() -> Tuple[bool, str]:
    required = ["wide_data.parquet", "baseline_carbon.parquet", "final_report.parquet",
                "od_band.parquet", "ledger.parquet"]
    missing = [f for f in required if not (PROCESSED / f).exists()]
    if missing:
        return False, "缺结果表 %d 个：%s（跑 python -m src.pipeline）" % (
            len(missing), ", ".join(missing))
    return True, "%d 个核心结果表就绪" % len(required)


def check_gate() -> Tuple[bool, str]:
    """闸门：passed 为真且 FAIL=0 才算健康（当前 28 项：10 目标 + 18 结构）。"""
    import json
    path = PROCESSED / "gate_report.json"
    if not path.exists():
        return False, "还没有 gate_report.json（跑 python -m src.pipeline）"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, "gate_report.json 读不了：%s" % exc
    counts = report.get("counts", {})
    passed = report.get("passed")
    detail = "PASS %s｜FAIL %s｜SKIP %s" % (
        counts.get("pass", "?"), counts.get("fail", "?"), counts.get("skip", "?"))
    if passed:
        return True, detail
    return False, detail + "（闸门未通过）"


def check_amap() -> Tuple[bool, str]:
    """分别检查后端 REST Key 与前端 JS API 地图凭据。"""
    from src import config as app_config
    from src.routing import amap_js

    key, source = app_config.secret_with_source(
        "AMAP_WEB_SERVICE_KEY", files=("amap_key.txt",))
    web_ready = bool(key and not key.startswith("请至高德"))
    js_ready = amap_js.js_available()
    if web_ready and js_ready:
        return True, "REST Key + JS API 凭据齐备 → 路线规划与 3D 地图可用"
    if web_ready:
        return True, "REST Key 已配置（来源 %s）；缺 JS API Key/安全密钥 → 精细地图待接入" % source
    return True, "未配置高德凭据 → 路线沙盘会显示配置指引，不影响其他页面"


def check_routes() -> Tuple[bool, str]:
    path = PROCESSED / "route_od.parquet"
    if not path.exists():
        return True, "路线基线表未生成 → 不影响六页主应用与实时路线沙盘"
    try:
        import pandas as pd
        df = pd.read_parquet(path)
        if "source" in df.columns:
            online = int((df["source"] == "amap_rest").sum())
            return True, "路线表 %d 条（高德真路线 %d / 离线兜底 %d）" % (
                len(df), online, len(df) - online)
        return True, "路线表 %d 条" % len(df)
    except Exception as exc:
        return True, "路线表读不了（不影响启动）：%s" % exc


def _raw_ready() -> bool:
    """data/raw 是否有可读的附件（xlsx/xls/csv）。"""
    raw = ROOT / "data" / "raw"
    if not raw.exists():
        return False
    return any(p.is_file() and p.suffix.lower() in (".xlsx", ".xls", ".csv")
               for p in raw.iterdir())


def run_recalc(with_routes: bool = True, force: bool = False) -> Tuple[bool, str]:
    """一键复算。失败返回 False，但**不阻断启动**。

    data/raw 为空且已有结果表时默认跳过：否则 preprocess 会回退合成演示样本，
    把真实的 data/processed 结果表覆盖掉。要强制复算（例如故意演练演示模式），
    用 --force-recalc 或直接运行 python -m src.pipeline。
    """
    if not force and not _raw_ready() and (PROCESSED / "baseline_carbon.parquet").exists():
        return True, "data/raw 为空：跳过复算，保留现有结果表（避免被演示样本覆盖）"
    cmd = [sys.executable, "-m", "src.pipeline"]
    if with_routes:
        cmd.append("--with-routes")
    # 编码要两端对齐：子进程 stdout 接管道后按系统 locale 写（中文 Windows = GBK），
    # 父进程再按 UTF-8 解就成乱码摘要。写端用 PYTHONIOENCODING=utf-8 钉死，
    # 读端显式 encoding="utf-8"，本地中文 Windows 与 CI（Ubuntu/UTF-8）行为一致。
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=900, env=env)
    tail = (proc.stdout or "").strip().splitlines()[-3:]
    summary = " / ".join(line.strip() for line in tail if line.strip())
    return proc.returncode == 0, summary or "退出码 %d" % proc.returncode


def _port_is_free(port: int) -> bool:
    """Check both local address families; never terminate an unknown listener."""
    for host in ("127.0.0.1", "::1"):
        try:
            with socket.create_connection((host, port), timeout=0.4):
                return False
        except OSError:
            pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("0.0.0.0", port))
            probe.listen(1)
    except OSError:
        return False
    if socket.has_ipv6:
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
                if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                probe.bind(("::", port))
                probe.listen(1)
        except OSError as exc:
            # Ignore an unavailable IPv6 stack, but respect an occupied port.
            if exc.errno not in (errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL, errno.EPROTONOSUPPORT):
                return False
    return True


def _port_owner_command(port: int) -> str:
    """Read the Windows listener's command line; unknown ownership stays unknown."""
    if os.name != "nt":
        return ""
    command = (
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new();"
        f"$listener=Get-NetTCPConnection -State Listen -LocalPort {int(port)} -ErrorAction SilentlyContinue | Select-Object -First 1;"
        "if($listener){$owner=Get-CimInstance Win32_Process -Filter ('ProcessId='+$listener.OwningProcess);"
        "@{command_line=$owner.CommandLine}|ConvertTo-Json -Compress}"
    )
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
        return str(json.loads(result.stdout or "{}").get("command_line") or "")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ""


def _same_project_is_running(port: int) -> bool:
    command = _port_owner_command(port)
    match = re.search(r'(?:^|\s)-m\s+streamlit\s+run\s+(?:"([^"]+)"|(\S+))', command)
    if not match:
        return False
    app = Path(match.group(1) or match.group(2))
    if not app.is_absolute() or os.path.normcase(str(app.resolve())) != os.path.normcase(str((ROOT / "app/app.py").resolve())):
        return False
    try:
        # Never send a localhost health check through a system web proxy.
        with build_opener(ProxyHandler({})).open(f"http://127.0.0.1:{port}/_stcore/health", timeout=2) as response:
            return response.status == 200 and response.read(16).strip() == b"ok"
    except OSError:
        return False


def start_streamlit(port: int, open_browser: bool = False) -> int:
    if not _port_is_free(port):
        if _same_project_is_running(port):
            url = f"http://localhost:{port}/ledger"
            print("  本项目已经在运行，直接使用现有服务，不重复启动。")
            print("  碳账地址：" + url)
            if open_browser:
                try:
                    webbrowser.open(url)
                except Exception:
                    print("  请将上面的地址复制到浏览器中打开。")
            return 0
        original = port
        port = next((candidate for candidate in range(original + 1, min(original + 21, 65536))
                     if _port_is_free(candidate)), 0)
        if not port:
            print("  [X] 附近端口均被占用，请使用 --port 指定其他端口。")
            return 1
        print(f"  端口 {original} 被其他程序占用，自动改用 {port}；未停止任何现有程序。")
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(ROOT / "app" / "app.py"),
        "--server.port", str(port),
        "--server.headless", "true" if not open_browser else "false",
        "--browser.gatherUsageStats", "false",
    ]
    print("  启动命令：%s" % " ".join(cmd))
    print("  浏览器打开：http://localhost:%d" % port)
    print("  碳账入口：主导航 → 碳台账（直接进入）")
    print("  停止：Ctrl+C")
    print("-" * 78)
    try:
        return subprocess.call(cmd, cwd=ROOT)
    except KeyboardInterrupt:
        print("\n  已停止")
        return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="一键启动：自检 + 复算 + 六页")
    parser.add_argument("--check", action="store_true", help="只自检，不复算不启动")
    parser.add_argument("--no-recalc", action="store_true", help="不复算，直接启动")
    parser.add_argument("--no-routes", action="store_true", help="复算时不带路线段")
    parser.add_argument("--force-recalc", action="store_true",
                        help="data/raw 为空时也强制复算（会回退演示数据，慎用）")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--open", action="store_true", help="自动打开浏览器")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--sensor", "-Sensor", action="store_true", help="同时启动原有设备接入服务")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在1到65535之间")

    if not args.check:
        for check in (check_python, check_disclosure):
            ok, detail = check()
            if not ok:
                print("  [X] %s" % detail)
                return 1
        if not ensure_dependencies():
            print("  [X] 主系统依赖未安装成功，请查看上面的安装提示后重新启动。")
            return 1

    _print_header("主系统统一启动检查（含披露碳账）")
    checks: List[Tuple[str, Any]] = [
        ("Python 版本", _safe_check(check_python)),
        ("依赖包", _safe_check(check_deps)),
        ("披露碳账", _safe_check(check_disclosure)),
        ("核心数据表", _safe_check(check_data)),
        ("对拍闸门", _safe_check(check_gate)),
        ("高德 key", _safe_check(check_amap)),
        ("路线表", _safe_check(check_routes)),
    ]
    hard_fail = 0
    for label, (ok, detail) in checks:
        icon = OK if ok else (WARN if label in ("高德 key", "路线表") else BAD)
        if not ok and label in ("高德 key", "路线表"):
            icon = WARN
        elif not ok:
            hard_fail += 1
        print("  %s %-14s %s" % (icon, label, detail))

    if hard_fail:
        print()
        print("  [!] 有 %d 项硬性检查未通过。启动前建议先修：" % hard_fail)
        for label, (ok, detail) in checks:
            if not ok and label not in ("高德 key", "路线表"):
                print("     · %s → %s" % (label, detail))
        print("  Python、依赖和碳账资源必须齐备；数据及路线问题由原页面提示。")

    required_failed = any(not ok for label, (ok, _) in checks
                          if label in ("Python 版本", "依赖包", "披露碳账"))

    if args.check:
        print()
        print("  自检完成。")
        return 1 if required_failed else 0

    if required_failed:
        return 1

    if not args.no_recalc:
        _print_header("一键复算（python -m src.pipeline）")
        t0 = time.perf_counter()
        ok, summary = run_recalc(with_routes=not args.no_routes, force=args.force_recalc)
        print("  %s 耗时 %.1fs｜%s" % (OK if ok else WARN, time.perf_counter() - t0, summary))
        if not ok:
            print("  [!] 复算未通过，但仍然启动——演示用上一轮结果表")

    _print_header("启动应用")
    if args.sensor:
        options: Dict[str, Any] = {}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.Popen([sys.executable, str(ROOT / "server.py")], cwd=ROOT, **options)
        print("  已发起设备接入服务：http://localhost:5000/api/upload_obd_gps")
    return start_streamlit(args.port, open_browser=args.open and not args.no_browser)


if __name__ == "__main__":
    sys.exit(main())
