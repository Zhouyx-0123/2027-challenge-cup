# -*- coding: utf-8 -*-
"""一键启动：环境自检 → 一键复算 → 打开五页。

给两类人用：
  - **团队自己**：日常开发，一条命令看全项目健康度；
  - **答辩现场**：陌生电脑、可能断网、可能没有高德 key——也要能开。

设计原则（工程师建议 #1/#9：静态兜底、不许红屏）：
  - 任何一步失败都**不阻断启动**，只打印警告并继续；
  - 高德 key 缺失 → 自动走离线降级（五页照常演）；
  - 路线表缺失 → 跳过路线段，主口径照常；
  - 永远不出现"启动脚本报错、演示机打不开"。

用法：
    python -m tools.launch              # 自检 + 复算 + 启动 Streamlit
    python -m tools.launch --check      # 只自检，不启动
    python -m tools.launch --no-recalc  # 不复算，直接启动（改前端时用）
    python -m tools.launch --port 8502  # 换端口
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
CONFIG = ROOT / "config"

OK = "[OK]"
WARN = "!"
BAD = "[X]"

# 前端只读铁律：这些必须成立
# 闸门当前 26 项（8 目标 + 18 结构）；pipeline 的 passed 标志才是权威，
# 这里只作参考值，不对具体 PASS 数做硬编码断言（结构项还会增长）。
GATE_EXPECT_PASS = 26


def _print_header(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def check_python() -> Tuple[bool, str]:
    if sys.version_info < (3, 8):
        return False, "Python %s 过低，需要 3.8+" % ".".join(map(str, sys.version_info[:3]))
    return True, "Python %s" % ".".join(map(str, sys.version_info[:3]))


def check_deps() -> Tuple[bool, str]:
    missing: List[str] = []
    versions: List[str] = []
    for mod, name in (("streamlit", "streamlit"), ("pandas", "pandas"),
                      ("numpy", "numpy"), ("pyarrow", "pyarrow"),
                      ("plotly", "plotly")):
        try:
            m = __import__(mod)
            versions.append("%s %s" % (name, getattr(m, "__version__", "?")))
        except ImportError:
            missing.append(name)
    if missing:
        return False, "缺依赖：%s（pip install -r requirements.txt）" % ", ".join(missing)
    return True, " · ".join(versions)


def check_data() -> Tuple[bool, str]:
    required = ["wide_data.parquet", "baseline_carbon.parquet", "final_report.parquet",
                "od_band.parquet", "ledger.parquet"]
    missing = [f for f in required if not (PROCESSED / f).exists()]
    if missing:
        return False, "缺结果表 %d 个：%s（跑 python -m src.pipeline）" % (
            len(missing), ", ".join(missing))
    return True, "%d 个核心结果表就绪" % len(required)


def check_gate() -> Tuple[bool, str]:
    """闸门：passed 为真且 FAIL=0 才算健康（当前 26 项：8 目标 + 18 结构）。"""
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
    from src.routing import amap_js
    path = CONFIG / "amap_key.txt"
    web_ready = False
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        web_ready = bool(text and not text.startswith("请至高德"))
    js_ready = amap_js.js_available()
    if web_ready and js_ready:
        return True, "REST Key + JS API 凭据齐备 → 路线规划与 3D 地图可用"
    if web_ready:
        return True, "REST Key 已配置；缺 JS API Key/安全密钥 → 精细地图待接入"
    return True, "未配置高德凭据 → 路线沙盘会显示配置指引，不影响其他页面"


def check_routes() -> Tuple[bool, str]:
    path = PROCESSED / "route_od.parquet"
    if not path.exists():
        return True, "路线基线表未生成 → 不影响五页主应用与实时路线沙盘"
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


def start_streamlit(port: int, open_browser: bool = False) -> int:
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(ROOT / "app" / "app.py"),
        "--server.port", str(port),
        "--server.headless", "true" if not open_browser else "false",
        "--browser.gatherUsageStats", "false",
    ]
    print("  启动命令：%s" % " ".join(cmd))
    print("  浏览器打开：http://localhost:%d" % port)
    print("  停止：Ctrl+C")
    print("-" * 78)
    try:
        return subprocess.call(cmd, cwd=ROOT)
    except KeyboardInterrupt:
        print("\n  已停止")
        return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="一键启动：自检 + 复算 + 五页")
    parser.add_argument("--check", action="store_true", help="只自检，不复算不启动")
    parser.add_argument("--no-recalc", action="store_true", help="不复算，直接启动")
    parser.add_argument("--no-routes", action="store_true", help="复算时不带路线段")
    parser.add_argument("--force-recalc", action="store_true",
                        help="data/raw 为空时也强制复算（会回退演示数据，慎用）")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--open", action="store_true", help="自动打开浏览器")
    args = parser.parse_args(argv)

    _print_header("环境自检（任何一项不通过都不会阻断启动）")
    checks: List[Tuple[str, Any]] = [
        ("Python 版本", check_python()),
        ("依赖包", check_deps()),
        ("核心数据表", check_data()),
        ("对拍闸门", check_gate()),
        ("高德 key", check_amap()),
        ("路线表", check_routes()),
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
        print("  但演示优先——**继续启动**，缺的部分页面会提示，不白屏。")

    if args.check:
        print()
        print("  自检完成。")
        return 0

    if not args.no_recalc:
        _print_header("一键复算（python -m src.pipeline）")
        t0 = time.perf_counter()
        ok, summary = run_recalc(with_routes=not args.no_routes, force=args.force_recalc)
        print("  %s 耗时 %.1fs｜%s" % (OK if ok else WARN, time.perf_counter() - t0, summary))
        if not ok:
            print("  [!] 复算未通过，但仍然启动——演示用上一轮结果表")

    _print_header("启动应用")
    return start_streamlit(args.port, open_browser=args.open)


if __name__ == "__main__":
    sys.exit(main())
