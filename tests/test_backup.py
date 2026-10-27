# -*- coding: utf-8 -*-
"""备份与恢复演练的测试（pytest + tmp_path，不依赖仓库真实 data/raw）。

隔离原则：所有样本都在 ``tmp_path`` 里造，仓库工作区零副作用；
备份包只落在临时目录，不会污染 ``backups/`` 或任何 git 跟踪目录。

覆盖：
1. 备份产出 manifest.json + SHA256SUMS.txt，指纹与源文件一致，密钥默认不入包；
2. ``--include-secrets`` 时密钥入包且标志为 True；
3. 恢复演练能识别被篡改的归档（退出码非 0）；
4. 未篡改时恢复演练通过（退出码 0）；
5. 归档成员名含 ``..`` 路径穿越时被拒绝解包。
"""
from __future__ import annotations

import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import backup as backup_tool  # noqa: E402
from tools import restore_drill as drill_tool  # noqa: E402


@pytest.fixture()
def sample_root(tmp_path: Path) -> Path:
    """造一个最小的假仓库：原始证据 + 因子配置 + 密钥 + 代码 + 审计产物。"""
    root = tmp_path / "repo"
    (root / "data" / "raw").mkdir(parents=True)
    (root / "data" / "processed").mkdir(parents=True)
    (root / "config").mkdir(parents=True)
    (root / "src").mkdir(parents=True)
    (root / "outputs").mkdir(parents=True)

    (root / "data" / "raw" / "waybill.csv").write_text(
        "waybill_id,km\nWB-001,120.5\n", encoding="utf-8")
    (root / "data" / "raw" / "附件1.xlsx").write_bytes(b"fake-xlsx-bytes")
    (root / "config" / "calc_factors.json").write_text(
        '{"diesel_kg_per_l": 2.6}', encoding="utf-8")
    (root / "config" / "README.md").write_text("# 因子说明\n", encoding="utf-8")
    (root / "config" / "amap_key.txt").write_text("SUPER-SECRET", encoding="utf-8")
    (root / "data" / "processed" / "kpi.json").write_text('{"kpi": 1}', encoding="utf-8")
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "server.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "outputs" / "audit.json").write_text('{"ok": true}', encoding="utf-8")
    (root / "outputs" / "invoice_ledger.csv").write_text(
        "plate,amount\n沪A12345,100\n", encoding="utf-8")
    return root


def _members(archive: Path) -> set:
    """列出归档内的成员名。"""
    with tarfile.open(archive, "r:gz") as tar:
        return set(tar.getnames())


def _corrupt_archive(archive: Path, victim: str) -> None:
    """把归档里的某个成员内容改掉（模拟内容被篡改，指纹必然对不上）。"""
    tmp = archive.with_name(archive.name + ".tmp")
    with tarfile.open(archive, "r:gz") as src:
        with tarfile.open(tmp, "w:gz") as dst:
            for member in src.getmembers():
                if not member.isfile():
                    continue
                data = src.extractfile(member).read()
                if member.name == victim:
                    data = data + b"tampered"
                info = tarfile.TarInfo(member.name)
                info.size = len(data)
                info.mtime = 0
                info.mode = 0o644
                dst.addfile(info, io.BytesIO(data))
    tmp.replace(archive)


# ---------------------------------------------------------------------------
# 备份
# ---------------------------------------------------------------------------

def test_backup_creates_manifest_and_sumfile(sample_root: Path, tmp_path: Path):
    """备份产出 manifest + 校验和；指纹与源文件一致；密钥默认不入包。"""
    dest = tmp_path / "backups"
    result = backup_tool.create_backup(sample_root, dest, label="t")

    assert result.archive_path.is_file()
    assert result.manifest_path.is_file()
    assert result.sumfile_path.is_file()

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["secret_included"] is False
    assert manifest["include_processed"] is False
    assert manifest["file_count"] == len(manifest["files"])

    entries = {item["path"]: item for item in manifest["files"]}
    assert "data/raw/waybill.csv" in entries, "原始证据必须默认入包"
    assert "config/calc_factors.json" in entries
    assert "src/a.py" in entries
    assert entries["data/raw/waybill.csv"]["sha256"] == backup_tool.sha256_file(
        sample_root / "data" / "raw" / "waybill.csv")
    assert entries["data/raw/waybill.csv"]["bytes"] == (
        sample_root / "data" / "raw" / "waybill.csv").stat().st_size

    # 密钥默认不入包（manifest 与归档都不该出现）
    assert "config/amap_key.txt" not in entries
    names = _members(result.archive_path)
    assert "config/amap_key.txt" not in names
    assert "data/raw/waybill.csv" in names
    assert "manifest.json" in names
    assert "SHA256SUMS.txt" in names

    # 归档内不得出现绝对路径或盘符
    assert not any(name.startswith("/") or ":" in name for name in names)

    # SHA256SUMS.txt 是标准格式：<sha256>  <相对路径>
    lines = result.sumfile_path.read_text(encoding="utf-8").strip().splitlines()
    assert lines, "SHA256SUMS.txt 不应为空"
    for line in lines:
        digest, rel = line.split("  ", 1)
        assert len(digest) == 64
        assert rel and not rel.startswith("/")


def test_backup_include_secrets_flag(sample_root: Path, tmp_path: Path):
    """显式收密钥时：密钥入包且标志为 True，并留醒目告警。"""
    dest = tmp_path / "backups"
    result = backup_tool.create_backup(sample_root, dest, include_secrets=True,
                                       label="secret")
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["secret_included"] is True
    assert any("config/amap_key.txt" == item["path"] for item in manifest["files"])
    assert "config/amap_key.txt" in _members(result.archive_path)
    assert any("密钥" in warning for warning in manifest["warnings"])


def test_backup_excludes_processed_and_sensitive_outputs(sample_root: Path,
                                                         tmp_path: Path):
    """结果表默认不收；敏感审计产物（invoice_*）也不收。"""
    dest = tmp_path / "backups"
    result = backup_tool.create_backup(sample_root, dest, label="t")
    entries = {item["path"] for item in result.manifest["files"]}
    assert "data/processed/kpi.json" not in entries
    assert "outputs/invoice_ledger.csv" not in entries
    rules = result.manifest["excluded"]["by_rule"]
    assert rules.get("processed-default", 0) >= 1
    assert rules.get("outputs-sensitive", 0) >= 1


# ---------------------------------------------------------------------------
# 恢复演练
# ---------------------------------------------------------------------------

def test_restore_drill_success(sample_root: Path, tmp_path: Path):
    """未篡改的备份：逐文件指纹一致，演练通过（退出码 0）。"""
    dest = tmp_path / "backups"
    result = backup_tool.create_backup(sample_root, dest, label="ok")
    assert drill_tool.run_drill(result.backup_dir) == 0
    # 直接给 tar.gz 也要能定位
    assert drill_tool.run_drill(result.archive_path) == 0


def test_restore_drill_detects_corruption(sample_root: Path, tmp_path: Path):
    """归档内容被篡改后：校验必须失败（退出码非 0）。"""
    dest = tmp_path / "backups"
    result = backup_tool.create_backup(sample_root, dest, label="tamper")
    _corrupt_archive(result.archive_path, "data/raw/waybill.csv")
    assert drill_tool.run_drill(result.backup_dir) != 0


def test_restore_drill_rejects_path_traversal(tmp_path: Path):
    """归档成员名含 ``..``：拒绝解包，且不落地越界文件。"""
    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        payload = b"pwned"
        info = tarfile.TarInfo("../evil.txt")
        info.size = len(payload)
        info.mtime = 0
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(payload))
        manifest = json.dumps({"files": [], "package_meta": []}).encode("utf-8")
        info2 = tarfile.TarInfo("manifest.json")
        info2.size = len(manifest)
        info2.mtime = 0
        info2.mode = 0o644
        tar.addfile(info2, io.BytesIO(manifest))

    assert drill_tool.run_drill(archive) != 0
    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path.parent / "evil.txt").exists()


def test_restore_drill_restores_to_dir(sample_root: Path, tmp_path: Path):
    """--restore-to：校验通过的内容真的落盘；默认不覆盖已存在文件。"""
    dest = tmp_path / "backups"
    result = backup_tool.create_backup(sample_root, dest, label="restore")
    target = tmp_path / "restored"

    assert drill_tool.run_drill(result.backup_dir, target) == 0
    assert (target / "data" / "raw" / "waybill.csv").is_file()
    assert (target / "data" / "raw" / "waybill.csv").read_text(
        encoding="utf-8") == (sample_root / "data" / "raw" / "waybill.csv").read_text(
            encoding="utf-8")
    assert not (target / "config" / "amap_key.txt").exists()

    # 已存在同名文件 → 默认拒绝覆盖，退出码非 0；--force 才覆盖
    assert drill_tool.run_drill(result.backup_dir, target) != 0
    assert drill_tool.run_drill(result.backup_dir, target, force=True) == 0
