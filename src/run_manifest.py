# -*- coding: utf-8 -*-
"""复算 run 的版本化、生效指针与发布闸门（docs/50 §2 · 企业级 P0）。

解决什么问题
------------
改造前，``python -m src.pipeline`` 每次都**原地覆盖** ``data/processed/`` 下的结果表。
于是有三个问题：

1. **看不出这批数是哪一次算出来的**——只有 ``run_log.json`` 里一个时间戳，
   对不上具体输入、具体代码版本；
2. **算坏了会污染线上**——某段异常或闸门 FAIL，结果表已经是坏的，
   但页面读的就是这些坏表，没有"上一版好的"可以退回去；
3. **历史不可回放**——想知道"上个月报出去的台账当时长什么样"已经做不到了。

本模块提供三件东西
------------------
* **run_id**：``<时间戳>-<输入指纹>``，把"哪一次算的"钉死；
* **快照**：每次执行的产物存一份到 ``data/processed/_runs/<run_id>/``；
* **生效指针**：``data/processed/current_run.json`` 指向**当前对外生效**的那一次。

发布语义（铁律）
----------------
``data/processed/`` 下的**数据表永远等于当前生效版本**：

* 闸门 PASS → 快照 → 更新生效指针 → 对外生效；
* 闸门 FAIL 或中途异常 → **回滚**到上一生效版本的快照 → 生效指针**不动**
  → 页面继续显示上一版好数据，运维在 run_log / 索引里看到失败记录。

例外：``run_log.json`` 与 ``gate_report.json`` 记录的是**最近一次执行**（失败也写），
它们不参与回滚——运维必须看得见失败。

口径纪律
--------
本模块只管**存哪、怎么发布**，**一个数字都不算**：不碰因子、不碰分摊、不碰闸门判据。
闸门本身仍由 ``src/pipeline.py`` 的 ``run_gate()`` 判定，本模块只消费它的结论。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src import schema

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"

#: run 快照目录（机器本地状态，不入 git）
RUNS_DIR = PROCESSED / "_runs"
#: run 索引（每行一条 JSON，追加写）
INDEX_PATH = RUNS_DIR / "index.jsonl"
#: 生效指针：当前对外生效的 run
CURRENT_PATH = PROCESSED / "current_run.json"

#: 演示数据标记（必须随结果一起快照与回滚，否则会出现"真数据 + 演示标记"）
MOCK_FLAG_NAME = "_mock.flag"

#: 参与回滚的文件：结果表 + 源标记 + 演示标记
#: （排除 run_log / gate_report —— 它们记录"最近一次执行"，不是"生效版本"）
ROLLBACK_NAMES: Tuple[str, ...] = tuple(sorted(
    (set(schema.RESULT_FILES.values())
     - {"run_log.json", "gate_report.json"})
    | {MOCK_FLAG_NAME}
))

#: 快照收录的文件：结果表全集 + 演示标记（存在的才收）
SNAPSHOT_NAMES: Tuple[str, ...] = tuple(sorted(
    set(schema.RESULT_FILES.values()) | {MOCK_FLAG_NAME}
))

#: 默认保留的快照数量
DEFAULT_KEEP = 10


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def code_version(root: Path = ROOT) -> str:
    """当前代码版本：git HEAD 短哈希；拿不到就返回 ``unknown``（**不因此失败**）。"""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root), capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    value = (out.stdout or "").strip()
    return value or "unknown"


def input_fingerprint(root: Path = ROOT) -> str:
    """输入指纹：原始附件清单 + 因子配置内容 + 代码版本。

    * 原始附件只取 ``名字/大小/mtime``（附件可能很大，全量哈希不划算）；
    * 因子配置取**内容**哈希（它们决定算出来的数）；
    * 代码版本参与（同样的输入 + 不同的代码 = 不同的结果）。
    """
    digest = hashlib.sha256()

    config_dir = root / "config"
    if config_dir.is_dir():
        for path in sorted(config_dir.glob("*.json")):
            digest.update(path.name.encode("utf-8"))
            digest.update(_sha256_file(path).encode("utf-8"))

    raw_dir = root / "data" / "raw"
    if raw_dir.is_dir():
        for path in sorted(raw_dir.rglob("*")):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            digest.update(str(path.relative_to(raw_dir)).encode("utf-8"))
            digest.update(str(stat.st_size).encode("utf-8"))
            digest.update(str(int(stat.st_mtime)).encode("utf-8"))

    digest.update(code_version(root).encode("utf-8"))
    return digest.hexdigest()[:12]


def make_run_id(fingerprint: Optional[str] = None,
                root: Path = ROOT) -> str:
    """生成 run_id：``YYYYmmdd-HHMMSS-<输入指纹前 8 位>``。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    fp = (fingerprint or input_fingerprint(root))[:8]
    return "%s-%s" % (stamp, fp)


# ---------------------------------------------------------------------------
# 快照
# ---------------------------------------------------------------------------

def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def snapshot(run_id: str, processed: Path = PROCESSED,
             runs_dir: Path = RUNS_DIR) -> Path:
    """把当前结果表存一份到 ``_runs/<run_id>/``；返回快照目录。

    只复制**存在的**文件；不做任何计算、不改内容。
    """
    target = runs_dir / run_id
    target.mkdir(parents=True, exist_ok=True)
    for name in SNAPSHOT_NAMES:
        src = processed / name
        if src.is_file():
            _copy(src, target / name)
    return target


def restore_from_snapshot(run_id: str, processed: Path = PROCESSED,
                          runs_dir: Path = RUNS_DIR) -> List[str]:
    """从快照恢复**参与回滚**的文件；返回实际恢复的文件名列表。

    ``run_log.json`` / ``gate_report.json`` 不在其中——它们要留下"本次失败"的痕迹。
    """
    source = runs_dir / run_id
    restored: List[str] = []
    if not source.is_dir():
        return restored
    for name in ROLLBACK_NAMES:
        src = source / name
        if src.is_file():
            _copy(src, processed / name)
            restored.append(name)
    return restored


# ---------------------------------------------------------------------------
# 生效指针与索引
# ---------------------------------------------------------------------------

def read_current(processed: Path = PROCESSED) -> Optional[Dict[str, Any]]:
    """读当前生效 run 的指针；没有或损坏返回 ``None``（页面据此显示"未发布"）。"""
    path = processed / "current_run.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not payload.get("run_id"):
        return None
    return payload


def _append_index(entry: Dict[str, Any], index_path: Path = INDEX_PATH) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def read_index(index_path: Path = INDEX_PATH, limit: int = 50) -> List[Dict[str, Any]]:
    """读 run 索引（最近 ``limit`` 条，新的在后）。"""
    out: List[Dict[str, Any]] = []
    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            out.append(payload)
    return out


def publish(run_id: str, report: Dict[str, Any], run_log: Dict[str, Any],
            processed: Path = PROCESSED, runs_dir: Path = RUNS_DIR) -> Dict[str, Any]:
    """**发布**一个 run：快照 → 写生效指针 → 记索引。

    只在闸门 PASS 后调用。返回写出的指针内容。
    """
    snap = snapshot(run_id, processed=processed, runs_dir=runs_dir)
    pointer: Dict[str, Any] = {
        "run_id": run_id,
        "published_at": _now(),
        "generated_at": run_log.get("generated_at", ""),
        "gate_passed": bool(report.get("passed")),
        "gate_counts": report.get("counts", {}),
        "source": run_log.get("source", "unknown"),
        "code_version": run_log.get("code_version", ""),
        "input_fingerprint": run_log.get("input_fingerprint", ""),
        "total_seconds": run_log.get("total_seconds", 0),
        "snapshot": str(snap.relative_to(processed)).replace("\\", "/"),
    }
    (processed / "current_run.json").write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2), encoding="utf-8")
    entry = dict(pointer)
    entry["status"] = "published"
    _append_index(entry, runs_dir / "index.jsonl")
    return pointer


def record_failure(run_id: str, report: Dict[str, Any], run_log: Dict[str, Any],
                   reason: str = "gate_failed",
                   processed: Path = PROCESSED,
                   runs_dir: Path = RUNS_DIR) -> Dict[str, Any]:
    """记一次**失败**的执行：留快照供排查 + 记索引；**不动生效指针**。"""
    try:
        snap = snapshot(run_id, processed=processed, runs_dir=runs_dir)
        snapshot_rel = str(snap.relative_to(processed)).replace("\\", "/")
    except OSError:
        snapshot_rel = ""
    entry = {
        "run_id": run_id,
        "status": "failed",
        "reason": reason,
        "finished_at": _now(),
        "generated_at": run_log.get("generated_at", ""),
        "gate_passed": False,
        "gate_counts": report.get("counts", {}) if report else {},
        "source": run_log.get("source", "unknown"),
        "code_version": run_log.get("code_version", ""),
        "input_fingerprint": run_log.get("input_fingerprint", ""),
        "total_seconds": run_log.get("total_seconds", 0),
        "snapshot": snapshot_rel,
    }
    _append_index(entry, runs_dir / "index.jsonl")
    return entry


def rollback_to_last_published(processed: Path = PROCESSED,
                               runs_dir: Path = RUNS_DIR) -> Dict[str, Any]:
    """回滚到**上一个生效版本**；没有可回滚的版本时返回 ``{"restored": []}``。

    返回 ``{"ok": bool, "run_id": str, "restored": [...], "note": str}``。
    """
    pointer = read_current(processed)
    if not pointer:
        return {"ok": False, "run_id": "", "restored": [],
                "note": "没有生效版本可回滚（首次发布前失败）——现场已保留在结果目录"}
    run_id = str(pointer.get("run_id") or "")
    restored = restore_from_snapshot(run_id, processed=processed, runs_dir=runs_dir)
    if not restored:
        return {"ok": False, "run_id": run_id, "restored": [],
                "note": "生效版本的快照缺失，无法回滚——请人工核对结果目录"}
    return {"ok": True, "run_id": run_id, "restored": restored,
            "note": "已回滚到生效版本 %s" % run_id}


# ---------------------------------------------------------------------------
# 保留策略
# ---------------------------------------------------------------------------

def list_runs(runs_dir: Path = RUNS_DIR) -> List[str]:
    """列出所有快照目录名（按 run_id 排序，时间戳前缀 → 字典序即时间序）。"""
    if not runs_dir.is_dir():
        return []
    return sorted(p.name for p in runs_dir.iterdir()
                   if p.is_dir() and p.name != "__pycache__")


def prune(keep: int = DEFAULT_KEEP, runs_dir: Path = RUNS_DIR) -> List[str]:
    """只保留最近 ``keep`` 个快照目录，多余的最旧删除；返回被删的目录名。

    **只删** ``_runs/`` 下形如 ``<时间戳>-<指纹>`` 的目录，绝不碰别的东西。
    """
    keep = max(1, int(keep))
    runs = list_runs(runs_dir)
    removed: List[str] = []
    for name in runs[:-keep] if len(runs) > keep else []:
        target = runs_dir / name
        try:
            shutil.rmtree(target)
            removed.append(name)
        except OSError:
            continue
    return removed


def adopt_current(processed: Path = PROCESSED, runs_dir: Path = RUNS_DIR) -> Dict[str, Any]:
    """把**当前结果目录**认领为生效版本（**不重算、不改一个数字**）。

    用途：企业化改造上线时，仓库里已有一批通过闸门的结果表，但还没有生效指针。
    这时用本函数"补一个出生证明"——快照现有结果 + 写指针 + 记索引，
    以后每次复算才走正常的"发布 / 回滚"流程。

    前提：目录里必须有 ``gate_report.json``（否则无从判断这批数据过没过闸门，
    本函数会直接报错，不编）。
    """
    gate_path = processed / "gate_report.json"
    try:
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("没有可读的 gate_report.json，无法认领当前结果：%s" % exc)
    if not isinstance(gate, dict):
        raise ValueError("gate_report.json 内容异常，无法认领当前结果")

    source = ""
    try:
        source = str(json.loads((processed / "_source.json")
                                .read_text(encoding="utf-8")).get("source", ""))
    except (OSError, ValueError):
        source = ""

    fingerprint = input_fingerprint()
    run_log = {
        "generated_at": _now(),
        "source": source or str(gate.get("source") or "unknown"),
        "code_version": code_version(),
        "input_fingerprint": fingerprint,
        "total_seconds": 0,
        "adopted": True,
    }
    run_id = make_run_id(fingerprint)
    return publish(run_id, gate, run_log, processed=processed, runs_dir=runs_dir)


def describe_current(processed: Path = PROCESSED) -> Dict[str, Any]:
    """给页面用的当前生效状态（**不含任何密钥或敏感数据**）。"""
    pointer = read_current(processed)
    if not pointer:
        return {"published": False, "run_id": "", "published_at": "",
                "gate_counts": {}, "source": ""}
    out = dict(pointer)
    out["published"] = True
    out.setdefault("gate_counts", {})
    return out


__all__ = [
    "ROOT", "PROCESSED", "RUNS_DIR", "INDEX_PATH", "CURRENT_PATH",
    "ROLLBACK_NAMES", "SNAPSHOT_NAMES", "DEFAULT_KEEP",
    "code_version", "input_fingerprint", "make_run_id",
    "snapshot", "restore_from_snapshot",
    "read_current", "read_index", "publish", "record_failure",
    "rollback_to_last_published", "list_runs", "prune", "describe_current",
]
