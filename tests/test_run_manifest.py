# -*- coding: utf-8 -*-
"""run 版本化 / 生效指针 / 发布闸门 的契约测试（docs/50 §2）。

覆盖三组断言：

1. **模块级**（tmp_path 沙箱，不碰真实结果目录）：run_id 形态、指纹随输入变化、
   发布写指针与快照、失败不挪指针、回滚能把改坏的文件恢复回来、快照保留策略。
2. **流水线级**（子进程跑真流水线）：闸门通过 → 有生效指针 + 快照；
   闸门失败 → **不发布**（指针不动）+ 结果表回滚到上一生效版本。
3. **纪律级**：``run_log.json`` / ``gate_report.json`` 记录最近一次执行（失败也留痕），
   不参与回滚；``_mock.flag`` 必须参与回滚（否则会出现"真数据 + 演示标记"）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from src import run_manifest

ROOT = Path(__file__).resolve().parents[1]

# 流水线运行所需的最小子集：src 全部 + config 因子表。
_COPY_ITEMS = ("src", "config")


# ---------------------------------------------------------------------------
# 沙箱工具
# ---------------------------------------------------------------------------

@pytest.fixture()
def sandbox_dirs(tmp_path: Path):
    """一套空的"结果目录 + 快照目录"。"""
    processed = tmp_path / "data" / "processed"
    runs = processed / "_runs"
    processed.mkdir(parents=True)
    runs.mkdir(parents=True)
    return processed, runs


def _fake_report(passed: bool = True) -> dict:
    return {
        "passed": passed,
        "counts": {"pass": 28 if passed else 0, "fail": 0 if passed else 1, "skip": 0},
        "targets": [],
        "structural": [],
    }


def _fake_run_log() -> dict:
    return {
        "generated_at": "2026-10-27T10:00:00",
        "source": "real",
        "code_version": "abc1234",
        "input_fingerprint": "deadbeefcafe",
        "total_seconds": 14.9,
    }


# ---------------------------------------------------------------------------
# 模块级
# ---------------------------------------------------------------------------

def test_run_id_shape_and_fingerprint_changes_with_input(tmp_path: Path):
    """run_id 形如 <时间戳>-<指纹前 8>；输入变了指纹必须变。"""
    (tmp_path / "config").mkdir()
    factor = tmp_path / "config" / "calc_factors.json"
    factor.write_text('{"a": 1}', encoding="utf-8")

    fp1 = run_manifest.input_fingerprint(tmp_path)
    assert len(fp1) == 12
    run_id = run_manifest.make_run_id(fp1, root=tmp_path)
    assert run_id.endswith("-" + fp1[:8])
    assert len(run_id.split("-")[0]) == 8  # YYYYMMDD

    factor.write_text('{"a": 2}', encoding="utf-8")
    assert run_manifest.input_fingerprint(tmp_path) != fp1


def test_publish_writes_pointer_snapshot_and_index(sandbox_dirs):
    """发布要同时落下：生效指针 + 快照目录 + 索引行。"""
    processed, runs = sandbox_dirs
    (processed / "kpi.json").write_text('{"kpi": 1}', encoding="utf-8")

    pointer = run_manifest.publish("20261027-100000-abcdef12", _fake_report(),
                                   _fake_run_log(), processed=processed, runs_dir=runs)

    assert pointer["run_id"] == "20261027-100000-abcdef12"
    assert pointer["gate_passed"] is True
    assert pointer["gate_counts"]["pass"] == 28

    current = run_manifest.read_current(processed)
    assert current is not None and current["run_id"] == pointer["run_id"]

    snap = runs / pointer["run_id"]
    assert snap.is_dir()
    assert (snap / "kpi.json").is_file()

    index = run_manifest.read_index(runs / "index.jsonl")
    assert index and index[-1]["status"] == "published"


def test_failure_does_not_move_pointer(sandbox_dirs):
    """失败执行只留快照与索引，**不动生效指针**。"""
    processed, runs = sandbox_dirs
    run_manifest.publish("20261027-100000-abcdef12", _fake_report(), _fake_run_log(),
                         processed=processed, runs_dir=runs)
    before = run_manifest.read_current(processed)

    run_manifest.record_failure("20261027-110000-99999999", _fake_report(False),
                                 _fake_run_log(), reason="gate_failed",
                                 processed=processed, runs_dir=runs)

    after = run_manifest.read_current(processed)
    assert after == before
    assert run_manifest.read_index(runs / "index.jsonl")[-1]["status"] == "failed"


def test_rollback_restores_modified_tables(sandbox_dirs):
    """结果表被改坏后，回滚必须把它恢复成上一生效版本。"""
    processed, runs = sandbox_dirs
    (processed / "kpi.json").write_text('{"kpi": "GOOD"}', encoding="utf-8")
    (processed / "ledger.parquet").write_bytes(b"GOODBYTES")
    pointer = run_manifest.publish("20261027-100000-abcdef12", _fake_report(),
                                   _fake_run_log(), processed=processed, runs_dir=runs)

    # 模拟"算坏了"：结果表被写成坏内容
    (processed / "kpi.json").write_text('{"kpi": "BROKEN"}', encoding="utf-8")
    (processed / "ledger.parquet").write_bytes(b"BROKENBYTES")

    rollback = run_manifest.rollback_to_last_published(processed=processed, runs_dir=runs)
    assert rollback["ok"] is True
    assert rollback["run_id"] == pointer["run_id"]
    assert (processed / "kpi.json").read_text(encoding="utf-8") == '{"kpi": "GOOD"}'
    assert (processed / "ledger.parquet").read_bytes() == b"GOODBYTES"


def test_rollback_keeps_recent_execution_log_but_restores_mock_flag(sandbox_dirs):
    """run_log/gate_report 留"最近一次执行"的痕迹；_mock.flag 必须跟着回滚。"""
    processed, runs = sandbox_dirs
    (processed / "run_log.json").write_text('{"run": "old"}', encoding="utf-8")
    (processed / "gate_report.json").write_text('{"passed": true}', encoding="utf-8")
    (processed / "_mock.flag").write_text("demo", encoding="utf-8")
    run_manifest.publish("20261027-100000-abcdef12", _fake_report(), _fake_run_log(),
                         processed=processed, runs_dir=runs)

    # 一次失败执行：run_log 变成"新的"，演示标记也变了
    (processed / "run_log.json").write_text('{"run": "failed-now"}', encoding="utf-8")
    (processed / "_mock.flag").unlink()

    run_manifest.rollback_to_last_published(processed=processed, runs_dir=runs)

    assert (processed / "run_log.json").read_text(encoding="utf-8") == '{"run": "failed-now"}'
    assert (processed / "_mock.flag").read_text(encoding="utf-8") == "demo"


def test_rollback_without_published_run_is_not_fatal(sandbox_dirs):
    """首次发布前失败：没有可回滚的版本，要如实报告而不是炸掉。"""
    processed, runs = sandbox_dirs
    result = run_manifest.rollback_to_last_published(processed=processed, runs_dir=runs)
    assert result["ok"] is False
    assert result["restored"] == []
    assert result["note"]


def test_prune_keeps_latest_runs(sandbox_dirs):
    """快照保留策略：只留最近 N 个，且只删 _runs 下的 run 目录。"""
    processed, runs = sandbox_dirs
    for index in range(5):
        (runs / ("2026102%d-100000-abcdef12" % index)).mkdir(parents=True)
    (processed / "keep_me.txt").write_text("x", encoding="utf-8")

    removed = run_manifest.prune(keep=2, runs_dir=runs)
    assert len(removed) == 3
    assert len(run_manifest.list_runs(runs)) == 2
    assert (processed / "keep_me.txt").exists()


def test_describe_current_without_pointer(sandbox_dirs):
    """没有指针时返回 published=False（页面据此显示"未发布"）。"""
    processed, runs = sandbox_dirs
    info = run_manifest.describe_current(processed)
    assert info["published"] is False
    assert info["run_id"] == ""


# ---------------------------------------------------------------------------
# 流水线级（真跑一遍）
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def pipeline_sandbox():
    with tempfile.TemporaryDirectory(prefix="wb_run_manifest_") as tmp:
        work = Path(tmp)
        for item in _COPY_ITEMS:
            src_dir = ROOT / item
            if src_dir.exists():
                shutil.copytree(src_dir, work / item,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        yield work


def _run_pipeline(sandbox: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "src.pipeline", *args],
        cwd=sandbox, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900, env=env,
    )


def test_pipeline_publishes_run_and_pointer(pipeline_sandbox: Path):
    """闸门通过 → 有生效指针 + 快照 + 索引，且指针里的闸门计数为 0 FAIL。"""
    result = _run_pipeline(pipeline_sandbox)
    assert result.returncode == 0, result.stdout + result.stderr

    processed = pipeline_sandbox / "data" / "processed"
    pointer = run_manifest.read_current(processed)
    assert pointer is not None, "没有生效指针"
    assert pointer["gate_passed"] is True
    assert pointer["gate_counts"].get("fail") == 0
    assert (processed / pointer["snapshot"]).is_dir()

    run_log = json.loads((processed / "run_log.json").read_text(encoding="utf-8"))
    assert run_log.get("published") is True
    assert run_log.get("run_id") == pointer["run_id"]


def test_pipeline_gate_failure_does_not_publish_and_rolls_back(pipeline_sandbox: Path):
    """闸门变红 → 不发布（指针不动）+ 结果表回滚到上一生效版本。"""
    processed = pipeline_sandbox / "data" / "processed"
    before = run_manifest.read_current(processed)
    assert before is not None, "前置条件：先有一次成功发布"

    # --strict：演示数据也强制对拍 → 闸门必红
    result = _run_pipeline(pipeline_sandbox, "--strict")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "未发布" in result.stdout

    after = run_manifest.read_current(processed)
    assert after == before, "闸门 FAIL 时生效指针不能动"

    run_log = json.loads((processed / "run_log.json").read_text(encoding="utf-8"))
    assert run_log.get("published") is False
    assert run_log.get("rollback", {}).get("ok") is True
