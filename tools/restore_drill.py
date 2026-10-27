# -*- coding: utf-8 -*-
"""恢复演练：验证一份备份「真的能还原」，且内容没被篡改。

定位（企业级 P0 交付形态 · 备份与恢复演练）：
    · **默认只演练**：解到临时目录校验，不动生产目录；
    · **逐文件指纹核对**：按 manifest 的 sha256 统计 匹配 / 缺失 / 损坏 / 多余；
    · **路径穿越防护**：包内出现绝对路径、盘符或 ``..`` 一律拒绝解包（防解压炸弹 / 路径穿越）；
    · **真恢复要显式开**：``--restore-to DIR`` 才落盘，且默认拒绝覆盖已存在文件。

用法：
    python -m tools.restore_drill backups/20261027-150400-demo
    python -m tools.restore_drill backups/20261027-150400-demo/backup.tar.gz
    python -m tools.restore_drill backups/20261027-150400-demo --restore-to restored/
    python -m tools.restore_drill backups/20261027-150400-demo --restore-to restored/ --force

退出码：0 校验通过 / 1 校验失败（损坏、缺失、路径穿越、解包异常）。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows 控制台默认 GBK，强制 UTF-8 输出（与 tools/demo_evolution.py 一致）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - 老解释器
        pass

from tools import backup as backup_tool  # noqa: E402  （sha256 等共用逻辑）

# Windows 盘符绝对路径（C:\... 或 C:/...）
_WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:")

PACKAGE_META = set(backup_tool.PACKAGE_META)


# ---------------------------------------------------------------------------
# 定位与安全检查
# ---------------------------------------------------------------------------

def locate_backup(target: Path) -> Tuple[Path, Optional[Path]]:
    """定位备份包：参数可以是备份目录（内含 backup.tar.gz）或直接给 .tar.gz。

    返回 (归档路径, 同级 manifest.json 路径或 None)。
    """
    target = Path(target)
    if target.is_dir():
        archive = target / "backup.tar.gz"
        if not archive.is_file():
            raise FileNotFoundError("备份目录里没有 backup.tar.gz：%s" % target)
        return archive, (target / "manifest.json")
    if target.is_file():
        return target, None
    raise FileNotFoundError("备份不存在：%s" % target)


def unsafe_member_reason(name: str) -> Optional[str]:
    """判断归档成员名是否不安全；安全返回 None，不安全返回原因。

    拦截三类：绝对路径（/… 或 C:\\…）、``..`` 路径穿越、空名。
    """
    if not name or not name.strip():
        return "空成员名"
    norm = name.replace("\\", "/")
    if norm.startswith("/"):
        return "绝对路径：%s" % name
    if _WIN_DRIVE_RE.match(norm):
        return "盘符绝对路径：%s" % name
    if ".." in PurePosixPath(norm).parts:
        return "路径穿越（..）：%s" % name
    return None


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def verify_tree(workdir: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    """按 manifest 的 sha256 逐文件核对解包结果。

    返回 {"matched": [...], "missing": [...], "corrupt": [...], "extra": [...]}。
    """
    files = manifest.get("files") or []
    declared = {item["path"] for item in files}
    meta = set(manifest.get("package_meta") or []) | PACKAGE_META

    matched: List[str] = []
    missing: List[str] = []
    corrupt: List[str] = []
    for item in files:
        rel = item["path"]
        expected = item.get("sha256")
        target = Path(workdir) / Path(rel)
        if not target.is_file():
            missing.append(rel)
            continue
        if not expected or backup_tool.sha256_file(target) != expected:
            corrupt.append(rel)
            continue
        matched.append(rel)

    present = set()
    for path in Path(workdir).rglob("*"):
        if path.is_file():
            present.add(path.relative_to(workdir).as_posix())
    extra = sorted(present - declared - meta)

    return {"matched": matched, "missing": missing,
            "corrupt": corrupt, "extra": extra}


def _extract(archive: Path, workdir: Path) -> List[str]:
    """解包到临时目录，返回不安全成员的原因列表（空列表 = 全部安全）。"""
    reasons: List[str] = []
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            reason = unsafe_member_reason(member.name)
            if reason:
                reasons.append(reason)
        if reasons:
            return reasons
        try:
            tar.extractall(workdir, filter="data")
        except TypeError:  # pragma: no cover - 老解释器没有 filter 参数
            tar.extractall(workdir)
    return reasons


# ---------------------------------------------------------------------------
# 真恢复
# ---------------------------------------------------------------------------

def restore_to_dir(workdir: Path, restore_dir: Path, files: Sequence[str],
                   *, force: bool = False) -> Tuple[int, List[str]]:
    """把校验通过的文件复制到目标目录。

    默认拒绝覆盖已存在的同名文件（除非 force）；返回 (实际写入数, 冲突清单)。
    """
    restore_dir = Path(restore_dir)
    conflicts: List[str] = []
    for rel in files:
        if (restore_dir / Path(rel)).exists():
            conflicts.append(rel)
    if conflicts and not force:
        return 0, conflicts

    restore_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for rel in files:
        source = Path(workdir) / Path(rel)
        target = restore_dir / Path(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source), str(target))
        written += 1
    # 连 manifest 一起留一份，恢复现场可追溯
    manifest_src = Path(workdir) / "manifest.json"
    if manifest_src.is_file():
        shutil.copy2(str(manifest_src), str(restore_dir / "manifest.json"))
    return written, conflicts


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run_drill(target: Path, restore_to: Optional[Path] = None, *,
              force: bool = False, keep_temp: bool = False) -> int:
    """跑一次恢复演练。返回退出码（0 通过 / 1 失败）。"""
    archive, _sidecar = locate_backup(Path(target))
    workdir = Path(tempfile.mkdtemp(prefix="restore_drill_"))
    try:
        reasons = _extract(archive, workdir)
        if reasons:
            print("[FAIL] 归档成员不安全，拒绝解包：")
            for reason in reasons[:10]:
                print("  · %s" % reason)
            return 1

        manifest_path = workdir / "manifest.json"
        if not manifest_path.is_file():
            print("[FAIL] 归档内缺少 manifest.json，无法校验")
            return 1
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            print("[FAIL] manifest.json 解析失败：%s" % exc)
            return 1

        result = verify_tree(workdir, manifest)
        matched, missing = result["matched"], result["missing"]
        corrupt, extra = result["corrupt"], result["extra"]

        print("=" * 66)
        print("恢复演练：%s" % archive)
        print("=" * 66)
        print("  匹配 %d｜缺失 %d｜损坏 %d｜多余 %d"
              % (len(matched), len(missing), len(corrupt), len(extra)))
        for rel in missing[:10]:
            print("  [MISSING] %s" % rel)
        for rel in corrupt[:10]:
            print("  [CORRUPT] %s" % rel)
        for rel in extra[:10]:
            print("  [EXTRA]   %s" % rel)

        if missing or corrupt:
            print("[FAIL] 校验未通过：备份不可用（缺失 %d / 损坏 %d）"
                  % (len(missing), len(corrupt)))
            return 1

        if restore_to is not None:
            written, conflicts = restore_to_dir(workdir, Path(restore_to), matched,
                                                 force=force)
            if conflicts and not force:
                print("[FAIL] 目标目录已存在同名文件，需 --force 才会覆盖：")
                for rel in conflicts[:10]:
                    print("  · %s" % rel)
                return 1
            print("  恢复  %d 个文件 → %s" % (written, restore_to))

        print("[OK] 恢复演练通过：备份内容完整、指纹一致")
        return 0
    finally:
        if keep_temp:
            print("  临时目录（已保留）：%s" % workdir)
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(
        description="恢复演练：解包到临时目录并按 manifest 校验内容完整性")
    parser.add_argument("target", help="备份目录（内含 backup.tar.gz）或 backup.tar.gz 路径")
    parser.add_argument("--restore-to", default="",
                        help="把校验通过的内容真正恢复到该目录（默认只演练）")
    parser.add_argument("--force", action="store_true",
                        help="允许覆盖恢复目标里已存在的同名文件")
    parser.add_argument("--keep-temp", action="store_true",
                        help="保留临时解包目录并打印路径（便于人工查看）")
    args = parser.parse_args(argv)

    try:
        return run_drill(
            Path(args.target),
            Path(args.restore_to) if args.restore_to else None,
            force=args.force,
            keep_temp=args.keep_temp,
        )
    except Exception as exc:
        print("[FAIL] 恢复演练失败：%s: %s" % (type(exc).__name__, exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
