# -*- coding: utf-8 -*-
"""引擎闸门测试（CI 自动跑，替代原手工命令）。

**隔离原则（2026-10-22 补）**：pipeline 会把整套结果表写进 ``data/processed/``——
直接在仓库根目录跑测试，本地 ``data/raw`` 为空时会用演示样本**覆盖团队共享的真实结果表**
（并留下 ``_mock.flag``）。所以本测试把仓库核心代码拷进临时目录，在副本里跑 pipeline，
仓库工作区零副作用；CI 环境同理（checkout 即副本，本来就没有 data/raw）。

覆盖 README §四 / docs/27 §2 的两条验收命令：
1. ``python -m src.pipeline`` → 退出码 0、闸门无 FAIL。
   沙箱没有 ``data/raw``（原始数据永不入库）→ 自动走 6,000 行演示样本，
   真实数据五项标 SKIP、因子两项 PASS；本机放了附件1/2 后同一命令切真实模式（PASS 24）。
2. ``python -m src.pipeline --simulate-fail 2`` → 退出码 1、打印"第 2 段失败"
   （证明闸门真的会红，不是装饰）。
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# pipeline 运行所需的最小子集：src 全部 + config 因子表（scenarios.py 读它）。
# data/ 由 pipeline 自建；raw 留空即演示模式。tools/、app/ 不参与引擎闸门。
_COPY_ITEMS = ("src", "config")


@pytest.fixture(scope="module")
def sandbox() -> Path:
    """临时工作区：仓库代码的副本，pipeline 在这里跑，结果表只落在副本里。"""
    with tempfile.TemporaryDirectory(prefix="wb_pipeline_") as tmp:
        work = Path(tmp)
        for item in _COPY_ITEMS:
            src_dir = ROOT / item
            if src_dir.exists():
                shutil.copytree(
                    src_dir, work / item,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
        yield work


def _run_pipeline(sandbox: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "src.pipeline", *args],
        cwd=sandbox, capture_output=True, text=True, timeout=900,
    )


def test_pipeline_exit_zero_and_gate_green(sandbox: Path):
    """一键复算必须跑通，且闸门零 FAIL（演示/真实两种模式都适用）。"""
    result = _run_pipeline(sandbox)
    assert result.returncode == 0, result.stdout + result.stderr
    gate = json.loads((sandbox / "data" / "processed" / "gate_report.json")
                      .read_text(encoding="utf-8"))
    assert gate["passed"] is True, "闸门未通过：%s" % gate.get("counts")
    assert gate["counts"]["fail"] == 0, "闸门有 FAIL 项：%s" % gate.get("targets")


def test_simulate_fail_reports_stage_and_exit_one(sandbox: Path):
    """人为触发第 2 段故障：必须报得出段名、退出码为 1。"""
    result = _run_pipeline(sandbox, "--simulate-fail", "2")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "第 2 段失败" in result.stdout
