# -*- coding: utf-8 -*-
"""备份工具：把「不可再生的原始证据 + 配置 + 结果元信息 + 代码版本」打成一份可校验、可异地保存的备份包。

定位（企业级 P0 交付形态 · 备份与恢复演练）：
    · **原始证据优先**：``data/raw/`` 是不可再生的原始运单附件，默认必收 —— 备份的核心价值就在这里；
    · **密钥默认不收**：``config/*_key.txt`` 等密钥文件默认排除，需显式 ``--include-secrets``；
    · **可校验**：每份备份带 ``manifest.json``（逐文件 sha256）与 ``SHA256SUMS.txt``（可 ``sha256sum -c``）；
    · **不写绝对路径**：归档内一律相对路径，元数据归零 → 同内容可复现比对；
    · **备份包不进 git**：默认写到 ``backups/``（该目录须由 .gitignore 挡住，勿上传公共仓库）。

采集清单（相对路径 → 分类）：
    raw       data/raw/**                     默认必收（不可再生的原始证据）
    config    config/*.json、config/README.md 默认必收
    secret    config/*_key.txt 等密钥文件      默认排除（--include-secrets 才收）
    processed data/processed/**              默认排除（可由流水线重算）
    outputs   outputs/**                     敏感模式与 *.csv 排除，非敏感审计产物收
    code      src/ app/ tools/ .streamlit/ server.py requirements.txt   默认必收（复现前提）

用法：
    python -m tools.backup                          # 默认：data/raw + config + 代码
    python -m tools.backup --label pre-demo         # 打标签，便于异地归档对应
    python -m tools.backup --include-processed      # 连结果表一起收
    python -m tools.backup --include-secrets        # 连密钥一起收（务必按机密材料管理）

配套校验：``python -m tools.restore_drill backups/<备份目录>``

退出码：0 成功 / 1 系统错误（目录不可写、归档失败等）。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows 控制台默认 GBK，装不下部分符号，强制 UTF-8 输出（与 tools/demo_evolution.py 一致）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - 老解释器
        pass

TOOL_VERSION = "1.0.0"

# ---- 分类常量 -------------------------------------------------------------
CATEGORY_RAW = "raw"
CATEGORY_CONFIG = "config"
CATEGORY_SECRET = "secret"
CATEGORY_PROCESSED = "processed"
CATEGORY_OUTPUTS = "outputs"
CATEGORY_CODE = "code"

CATEGORY_ORDER = (CATEGORY_RAW, CATEGORY_CONFIG, CATEGORY_SECRET,
                  CATEGORY_PROCESSED, CATEGORY_OUTPUTS, CATEGORY_CODE)

# ---- 敏感判定 -------------------------------------------------------------
# 密钥文件名后缀（config/ 下的高德 key、天地图 token 等；与 .gitignore 的口径一致）
SECRET_NAME_SUFFIXES = ("_key.txt", "_secret.txt", "_security_code.txt",
                        "_code.txt", "_token.txt")
# 密钥类精确文件名（.streamlit/secrets.toml 里是本机凭据）
SECRET_EXACT_NAMES = ("secrets.toml",)
# outputs/ 下含车牌 / 发票号 / 设备 ID 的敏感产物前缀
SENSITIVE_OUTPUT_PREFIXES = ("invoice_", "pm_", "fleet_", "telemetry_")
# outputs/ 下体积大、属派生产物的目录
HEAVY_OUTPUT_DIRS = ("figures", "tables", "route_baseline")
# 代码分类要收的目录与文件
CODE_DIRS = ("src", "app", "tools", ".streamlit")
CODE_FILES = ("server.py", "requirements.txt")
# 备份目录命名：<YYYYmmdd-HHMMSS>-<label>
BACKUP_DIR_RE = re.compile(r"^\d{8}-\d{6}-")

PACKAGE_META = ("manifest.json", "SHA256SUMS.txt")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class FileEntry:
    """归档内的一个文件条目（相对路径 + 分类 + 指纹）。"""

    path: str          # 相对路径（posix 分隔符）
    category: str      # 分类
    bytes: int         # 字节数
    sha256: str        # 内容指纹


@dataclass
class Exclusion:
    """被排除的文件与排除规则（写进 manifest 的「排除项统计」）。"""

    path: str
    rule: str
    reason: str


@dataclass
class BackupPlan:
    """采集结果：要收的文件 + 排除清单。"""

    entries: List[FileEntry] = field(default_factory=list)
    exclusions: List[Exclusion] = field(default_factory=list)


@dataclass
class BackupResult:
    """一次备份的产物位置与清单（供上层脚本与测试使用）。"""

    backup_dir: Path
    archive_path: Path
    manifest_path: Path
    sumfile_path: Path
    manifest: Dict[str, Any]
    pruned: List[Path] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    """算文件的 sha256（分块读，大文件不吃内存）。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rel_path(root: Path, path: Path) -> str:
    """求 path 相对 root 的 posix 路径（不 resolve，避免符号链接翻车）。"""
    return Path(os.path.relpath(str(path), str(root))).as_posix()


def _walk_files(base: Path) -> List[Path]:
    """列出目录下的全部文件（目录不存在 → 空列表）。"""
    base = Path(base)
    if base.is_file():
        return [base]
    if not base.is_dir():
        return []
    return sorted((p for p in base.rglob("*") if p.is_file()),
                  key=lambda p: p.as_posix())


def _is_cache_path(rel: PurePosixPath) -> bool:
    """判断是不是缓存文件（__pycache__ / *.pyc）——备份里没价值。"""
    if "__pycache__" in rel.parts:
        return True
    return rel.suffix.lower() in (".pyc", ".pyo")


def _is_placeholder(rel: PurePosixPath) -> bool:
    """判断是不是目录占位文件（.gitkeep）——没内容，不入包。"""
    return rel.name.lower() == ".gitkeep"


def is_secret_name(name: str) -> bool:
    """判断文件名是不是密钥/凭据（config/ 与 .streamlit/ 用）。"""
    low = name.lower()
    if low in SECRET_EXACT_NAMES:
        return True
    if low.endswith(".key"):
        return True
    return any(low.endswith(suffix) for suffix in SECRET_NAME_SUFFIXES)


def _safe_label(label: str) -> str:
    """把用户标签洗成能当目录名的形式（Windows 非法字符 + 空白 → '-'）。"""
    text = (label or "").strip()
    if not text:
        return "backup"
    text = re.sub(r"[\\/:*?\"<>|\s]+", "-", text)
    text = text.strip("-.")
    return text[:40] or "backup"


def _read_json(path: Path) -> Optional[Any]:
    """读 JSON；文件不存在或解析失败 → None（不因此让备份失败）。"""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _git_head(root: Path) -> Optional[str]:
    """取 git HEAD；读不到就返回 None（不是 git 仓库 / 没装 git 都不该让备份失败）。"""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root), capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    head = (proc.stdout or "").strip()
    return head or None


def _hostname() -> Optional[str]:
    """取主机名；失败返回 None。"""
    try:
        return socket.gethostname()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# 采集清单
# ---------------------------------------------------------------------------

def collect_plan(root: Path, *, include_secrets: bool = False,
                 include_processed: bool = False,
                 exclude_prefixes: Sequence[str] = ()) -> BackupPlan:
    """按分类规则扫描仓库，产出「要收什么 / 排除什么」。

    规则（与 README、.gitignore 口径一致）：
        · data/raw/** → raw，必收（不可再生的原始证据）；
        · config/*.json 与 config/README.md → config，必收；
        · 密钥文件 → secret，默认排除，``include_secrets=True`` 才收；
        · data/processed/** → processed，默认排除，``include_processed=True`` 才收；
        · outputs/** → outputs，排除敏感前缀与全部 *.csv，其余审计产物收；
        · src/ app/ tools/ .streamlit/ server.py requirements.txt → code，必收。

    ``exclude_prefixes``：额外的排除前缀（相对 posix 路径），用于把备份输出目录
    自身挡在采集范围之外，避免上一次的备份包被下一次收进去。
    """
    root = Path(root)
    plan = BackupPlan()
    add = plan.entries.append
    excl = plan.exclusions.append
    prefixes = tuple(p for p in exclude_prefixes if p)

    def _rel(path: Path) -> PurePosixPath:
        return PurePosixPath(_rel_path(root, path))

    def _excluded_prefix(rel: PurePosixPath) -> bool:
        text = str(rel)
        return any(text.startswith(prefix) for prefix in prefixes)

    def _record(path: Path, rel: PurePosixPath, category: str) -> None:
        add(FileEntry(str(rel), category, path.stat().st_size, sha256_file(path)))

    def _exclude(path: Path, rel: PurePosixPath, rule: str, reason: str) -> None:
        excl(Exclusion(str(rel), rule, reason))

    def _skip_common(path: Path, rel: PurePosixPath) -> bool:
        """缓存文件与目录占位文件统一排除；返回 True 表示已排除。"""
        if _is_cache_path(rel):
            _exclude(path, rel, "cache", "缓存文件，不备份")
            return True
        if _is_placeholder(rel):
            _exclude(path, rel, "placeholder", "目录占位文件，不备份")
            return True
        if _excluded_prefix(rel):
            _exclude(path, rel, "dest-excluded", "备份输出目录自身，不备份")
            return True
        return False

    # 1) 原始证据：默认必收
    for path in _walk_files(root / "data" / "raw"):
        rel = _rel(path)
        if _skip_common(path, rel):
            continue
        _record(path, rel, CATEGORY_RAW)

    # 2) 配置：因子 JSON 与说明；密钥单独处理
    for path in _walk_files(root / "config"):
        rel = _rel(path)
        if _skip_common(path, rel):
            continue
        if is_secret_name(path.name):
            if include_secrets:
                _record(path, rel, CATEGORY_SECRET)
            else:
                _exclude(path, rel, "secret-default",
                         "密钥/凭据默认不备份（--include-secrets 才收）")
            continue
        if path.suffix.lower() == ".json" or path.name == "README.md":
            _record(path, rel, CATEGORY_CONFIG)
        else:
            _exclude(path, rel, "config-unclassified",
                     "config/ 下未识别文件，默认不收")

    # 3) 结果表：可由流水线重算，默认不收
    for path in _walk_files(root / "data" / "processed"):
        rel = _rel(path)
        if _skip_common(path, rel):
            continue
        if include_processed:
            _record(path, rel, CATEGORY_PROCESSED)
        else:
            _exclude(path, rel, "processed-default",
                     "结果表可由流水线重算，默认不收（--include-processed 才收）")

    # 4) 审计产物：敏感模式与 CSV 排除，其余收
    for path in _walk_files(root / "outputs"):
        rel = _rel(path)
        if _skip_common(path, rel):
            continue
        name = path.name.lower()
        parts = rel.parts
        if name.startswith(SENSITIVE_OUTPUT_PREFIXES):
            _exclude(path, rel, "outputs-sensitive",
                     "含车牌/发票号/设备 ID 的敏感产物，永不备份")
        elif path.suffix.lower() == ".csv":
            _exclude(path, rel, "outputs-csv", "CSV 产物，默认不收")
        elif len(parts) > 2 and parts[1] in HEAVY_OUTPUT_DIRS:
            _exclude(path, rel, "outputs-heavy", "派生产物目录，默认不收")
        else:
            _record(path, rel, CATEGORY_OUTPUTS)

    # 5) 代码版本：复现的前提，必收
    for dirname in CODE_DIRS:
        for path in _walk_files(root / dirname):
            rel = _rel(path)
            if _skip_common(path, rel):
                continue
            if path.name.lower() in SECRET_EXACT_NAMES:
                if include_secrets:
                    _record(path, rel, CATEGORY_SECRET)
                else:
                    _exclude(path, rel, "secret-default",
                             "本机凭据文件默认不备份（--include-secrets 才收）")
                continue
            _record(path, rel, CATEGORY_CODE)
    for fname in CODE_FILES:
        path = root / fname
        if path.is_file():
            _record(path, _rel(path), CATEGORY_CODE)

    return plan


# ---------------------------------------------------------------------------
# 清单
# ---------------------------------------------------------------------------

def build_manifest(root: Path, plan: BackupPlan, backup_id: str, label: str,
                   *, include_secrets: bool, include_processed: bool) -> Dict[str, Any]:
    """组装 manifest.json 的内容（备份现场的自描述）。"""
    categories: Dict[str, Dict[str, int]] = {}
    for entry in plan.entries:
        bucket = categories.setdefault(entry.category, {"files": 0, "bytes": 0})
        bucket["files"] += 1
        bucket["bytes"] += entry.bytes

    by_rule: Dict[str, int] = {}
    samples: Dict[str, List[str]] = {}
    for item in plan.exclusions:
        by_rule[item.rule] = by_rule.get(item.rule, 0) + 1
        bucket = samples.setdefault(item.rule, [])
        if len(bucket) < 50:
            bucket.append(item.path)

    raw_count = categories.get(CATEGORY_RAW, {}).get("files", 0)
    git_head = _git_head(root)
    warnings: List[str] = []
    if include_secrets:
        warnings.append(
            "本次备份包含密钥/凭据文件，请按机密材料管理：勿上传公共仓库、勿走同步网盘。")
    if raw_count == 0:
        warnings.append(
            "未发现 data/raw/ 原始证据文件：备份缺少不可再生证据，请确认原始数据是否已就位。")
    if not include_processed:
        warnings.append("未包含 data/processed/ 结果表（可由流水线重算）。")
    if git_head is None:
        warnings.append("未能读取 git HEAD（非 git 仓库或环境无 git），代码版本未记录。")

    current_run = _read_json(Path(root) / "data" / "processed" / "current_run.json")
    gate = _read_json(Path(root) / "data" / "processed" / "gate_report.json")
    gate_summary: Optional[Dict[str, Any]] = None
    if isinstance(gate, dict):
        gate_summary = {"counts": gate.get("counts"), "passed": gate.get("passed")}

    return {
        "backup_id": backup_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "label": label,
        "tool": "tools/backup.py",
        "tool_version": TOOL_VERSION,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "hostname": _hostname(),
        "git_head": git_head,
        "current_run": current_run,
        "gate_report": gate_summary,
        "include_secrets": include_secrets,
        "include_processed": include_processed,
        "secret_included": include_secrets,
        "categories": categories,
        "category_order": list(CATEGORY_ORDER),
        "file_count": len(plan.entries),
        "total_bytes": sum(entry.bytes for entry in plan.entries),
        "package_meta": list(PACKAGE_META),
        "warnings": warnings,
        "files": [
            {"path": entry.path, "category": entry.category,
             "bytes": entry.bytes, "sha256": entry.sha256}
            for entry in sorted(plan.entries, key=lambda e: e.path)
        ],
        "excluded": {
            "total": len(plan.exclusions),
            "by_rule": by_rule,
            "samples": samples,
        },
    }


def render_sums(manifest: Dict[str, Any], manifest_text: str) -> str:
    """生成 SHA256SUMS.txt 内容（标准 ``<sha256>  <相对路径>`` 格式，可 sha256sum -c）。"""
    lines = ["%s  %s" % (item["sha256"], item["path"]) for item in manifest["files"]]
    manifest_hash = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
    lines.append("%s  manifest.json" % manifest_hash)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 归档
# ---------------------------------------------------------------------------

def _add_bytes(tar: tarfile.TarFile, arcname: str, data: bytes) -> None:
    """往包里加一个文件，归一化元数据（mtime/uid/gid 归零，mode 固定）。"""
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    info.mtime = 0
    info.mode = 0o644
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.type = tarfile.REGTYPE
    tar.addfile(info, io.BytesIO(data))


def write_archive(archive_path: Path, root: Path, plan: BackupPlan,
                  manifest_text: str, sums_text: str) -> None:
    """写 backup.tar.gz：相对路径归档 + 确定性元数据（同内容可复现比对）。"""
    payload = {
        "manifest.json": manifest_text.encode("utf-8"),
        "SHA256SUMS.txt": sums_text.encode("utf-8"),
    }
    with Path(archive_path).open("wb") as raw:
        # gzip 头里的 mtime 也归零，否则同内容打两次包字节不同
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
                for entry in sorted(plan.entries, key=lambda e: e.path):
                    data = (Path(root) / Path(entry.path)).read_bytes()
                    _add_bytes(tar, entry.path, data)
                _add_bytes(tar, "manifest.json", payload["manifest.json"])
                _add_bytes(tar, "SHA256SUMS.txt", payload["SHA256SUMS.txt"])


def prune_old_backups(dest: Path, keep: int) -> List[Path]:
    """只保留最近 keep 份备份；只删本工具命名前缀 ``<YYYYmmdd-HHMMSS>-*`` 的目录。"""
    dest = Path(dest)
    if keep <= 0 or not dest.is_dir():
        return []
    candidates = [d for d in dest.iterdir()
                  if d.is_dir() and BACKUP_DIR_RE.match(d.name)]
    candidates.sort(key=lambda d: d.name, reverse=True)  # 名称即时间戳
    removed: List[Path] = []
    for old in candidates[keep:]:
        try:
            shutil.rmtree(old)
        except OSError:
            continue
        removed.append(old)
    return removed


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _dest_prefixes(root: Path, dest: Path) -> List[str]:
    """备份输出目录若在仓库内，返回其相对前缀（用来把备份目录自身排除出采集）。"""
    try:
        rel = Path(os.path.relpath(str(Path(dest).resolve()), str(Path(root).resolve())))
    except (OSError, ValueError):
        return []
    rel_text = rel.as_posix()
    if rel_text.startswith("..") or Path(rel_text).is_absolute():
        return []
    return [rel_text.rstrip("/") + "/"]


def create_backup(root: Path, dest: Path, *, include_secrets: bool = False,
                  include_processed: bool = False, keep: int = 10,
                  label: str = "") -> BackupResult:
    """做一次备份：采集 → 写 manifest → 打包 → 落盘 → 清理旧备份。

    返回 BackupResult（产物路径 + manifest 字典）。失败抛异常，由 main 统一转成退出码。
    """
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError("仓库根目录不存在：%s" % root)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    safe_label = _safe_label(label)
    backup_id = "%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), safe_label)
    backup_dir = dest / backup_id
    suffix = 2
    while backup_dir.exists():  # 同秒同名撞车 → 加序号
        backup_dir = dest / ("%s-%d" % (backup_id, suffix))
        suffix += 1
    backup_dir.mkdir(parents=True, exist_ok=True)

    plan = collect_plan(root, include_secrets=include_secrets,
                        include_processed=include_processed,
                        exclude_prefixes=_dest_prefixes(root, dest))
    manifest = build_manifest(root, plan, backup_dir.name, safe_label,
                              include_secrets=include_secrets,
                              include_processed=include_processed)
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    sums_text = render_sums(manifest, manifest_text)

    archive_path = backup_dir / "backup.tar.gz"
    manifest_path = backup_dir / "manifest.json"
    sumfile_path = backup_dir / "SHA256SUMS.txt"
    write_archive(archive_path, root, plan, manifest_text, sums_text)
    manifest_path.write_text(manifest_text, encoding="utf-8")
    sumfile_path.write_text(sums_text, encoding="utf-8")

    removed = prune_old_backups(dest, keep)

    result = BackupResult(
        backup_dir=backup_dir,
        archive_path=archive_path,
        manifest_path=manifest_path,
        sumfile_path=sumfile_path,
        manifest=manifest,
        pruned=removed,
    )
    return result


def _print_summary(result: BackupResult, removed: List[Path]) -> None:
    """打印 [OK] 摘要（文件数、总字节、产物路径、分类明细、告警）。"""
    manifest = result.manifest
    print("[OK] 备份完成")
    print("  备份目录  %s" % result.backup_dir)
    print("  归档包    %s" % result.archive_path)
    print("  清单      %s" % result.manifest_path)
    print("  校验和    %s" % result.sumfile_path)
    print("  文件数    %d 个｜总大小 %s 字节"
          % (manifest["file_count"], manifest["total_bytes"]))
    cats = manifest["categories"]
    detail = "｜".join(
        "%s %d" % (cat, cats[cat]["files"])
        for cat in CATEGORY_ORDER if cat in cats)
    print("  分类      %s" % (detail or "无"))
    print("  排除      %d 个文件" % manifest["excluded"]["total"])
    if removed:
        print("  清理      旧备份 %d 份" % len(removed))
    for warning in manifest["warnings"]:
        print("  [WARN] %s" % warning)
    if manifest["secret_included"]:
        print("  [WARN] 归档内含密钥文件 —— 按机密材料管理，勿上传公共仓库")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(
        description="备份：原始证据 + 配置 + 结果元信息 + 代码版本 → 可校验备份包")
    parser.add_argument("--root", default=str(ROOT), help="仓库根目录（默认当前仓库）")
    parser.add_argument("--dest", default=str(ROOT / "backups"), help="备份输出目录")
    parser.add_argument("--include-secrets", action="store_true",
                        help="连密钥/凭据一起收（默认排除，慎用）")
    parser.add_argument("--include-processed", action="store_true",
                        help="连 data/processed/ 结果表一起收（默认排除）")
    parser.add_argument("--keep", type=int, default=10,
                        help="只保留最近 N 份备份（默认 10）")
    parser.add_argument("--label", default="", help="备份标签（拼进目录名）")
    args = parser.parse_args(argv)

    try:
        result = create_backup(
            Path(args.root), Path(args.dest),
            include_secrets=args.include_secrets,
            include_processed=args.include_processed,
            keep=args.keep,
            label=args.label,
        )
    except Exception as exc:
        print("[FAIL] 备份失败：%s: %s" % (type(exc).__name__, exc))
        return 1

    removed = [Path(p) for p in result.pruned]
    _print_summary(result, removed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
