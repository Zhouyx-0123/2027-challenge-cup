# -*- coding: utf-8 -*-
"""发票 CLI（tools/invoice_ocr.py）的调度与契约测试。

核心逻辑（识别 / JSON 抽取 / 字段归一 / 碳排口径）在 src/invoice.py，
本文件只测 CLI 层的调度行为：幂等、报告结构、退出码、离线不联网、台账契约。

全部离线：不需要 API key、不联网。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import invoice as kernel
from tools import invoice_ocr

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 1. 契约不漂移：台账列 vs preprocess 宽表发票列
# ---------------------------------------------------------------------------

def test_ledger_columns_align_with_preprocess_invoice_columns():
    """台账的 invoice_* 三列必须与 preprocess 宽表同名 —— 接进去才不用改列名。

    src/preprocess.py 的 WIDE_COLUMNS 里已有 invoice_no / invoice_ok / invoice_amount；
    本工具产出的台账必须用同一套名字，否则将来 merge 会静默错列。
    """
    from src import preprocess  # 只在测试里 import

    wide_cols = set(preprocess.WIDE_COLUMNS)
    for col in ("invoice_no", "invoice_ok", "invoice_amount"):
        assert col in wide_cols, "preprocess 宽表已无 %s，契约需重新对齐" % col
        assert col in invoice_ocr.LEDGER_COLUMNS, "台账缺少 %s 列" % col


def test_ledger_columns_are_unique_and_ordered():
    assert len(invoice_ocr.LEDGER_COLUMNS) == len(set(invoice_ocr.LEDGER_COLUMNS))
    assert invoice_ocr.LEDGER_COLUMNS[0] == "invoice_no"


def test_cli_reuses_kernel_instead_of_duplicating():
    """CLI 只做包装：核心函数必须来自 src/invoice，不能有第二份实现。

    口径只有一份是本项目的硬规矩（docs/20 §11.4「不维护第二套」）。
    """
    import inspect
    assert inspect.getsource(invoice_ocr.run).count("kernel.") > 0
    # CLI 不应自己再实现一份 JSON 抽取
    src_text = inspect.getsource(invoice_ocr)
    assert "def extract_json_object" not in src_text
    assert "def parse_invoice" not in src_text
    assert "def call_deepseek_vision" not in src_text
    assert kernel.extract_json_object is not None


# ---------------------------------------------------------------------------
# 2. 台账读写 + 幂等
# ---------------------------------------------------------------------------

def test_ledger_roundtrip_and_idempotent_skip(tmp_path):
    """第二次跑同一批图片 → 零 API 调用（幂等）。"""
    src = tmp_path / "invoices"
    src.mkdir()
    (src / "a.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 32)
    ledger = tmp_path / "ledger.csv"
    report = tmp_path / "report.json"

    first = invoice_ocr.run(src, ledger, report, offline=True)
    assert first["counts"]["images"] == 1
    assert first["counts"]["failed"] == 1          # 离线：判未识别
    assert ledger.exists()

    rows = invoice_ocr.read_ledger(ledger)
    assert "a.jpg" in rows
    assert rows["a.jpg"]["status"] == "offline"
    # 离线绝不填假数
    assert rows["a.jpg"]["fuel_liters"] == ""
    assert rows["a.jpg"]["invoice_amount"] == ""

    second = invoice_ocr.run(src, ledger, report, offline=True)
    assert second["counts"]["skipped"] == 1       # 幂等：跳过，不再处理
    assert second["counts"]["failed"] == 0


def test_report_shape_matches_docs42_section7(tmp_path):
    """报告结构按 docs/42 §7：status / errors[] / warnings[]。"""
    src = tmp_path / "invoices"
    src.mkdir()
    ledger = tmp_path / "ledger.csv"
    report = tmp_path / "report.json"
    invoice_ocr.run(src, ledger, report, offline=True)
    data = json.loads(report.read_text(encoding="utf-8"))
    for key in ("status", "generated_at", "counts", "errors", "warnings"):
        assert key in data
    assert isinstance(data["errors"], list)
    assert isinstance(data["warnings"], list)


def test_report_contains_no_business_identifiers(monkeypatch, tmp_path):
    """★ 隐私守护：报告里不得出现车牌号 / 发票号 / 金额等可识别业务明细。

    docs/42 §8「报告中不得包含不必要的完整车牌、客户或发票信息」。
    明细只允许留在本地台账。
    """
    monkeypatch.setattr(invoice_ocr.kernel, "extract_invoice_fields",
                        lambda *a, **k: {
                            "status": "ok", "invoice_no": "24317000000123456789",
                            "invoice_amount": 476.84, "fuel_liters": 65.5,
                            "plate": "苏US3126", "invoice_date": "2026-10-25",
                            "note": ""})
    # 没有 key 时 CLI 会走离线分支（不识别），这里给一个假 key 走在线分支
    monkeypatch.setattr(invoice_ocr, "load_api_key", lambda: "sk-test-fake")
    src = tmp_path / "invoices"
    src.mkdir()
    (src / "a.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 32)
    ledger = tmp_path / "ledger.csv"
    report = tmp_path / "report.json"
    invoice_ocr.run(src, ledger, report)

    text = report.read_text(encoding="utf-8")
    for secret in ("苏US3126", "24317000000123456789", "476.84"):
        assert secret not in text, "报告泄露了业务明细：%s" % secret

    # 但台账里必须有（明细的归处）
    rows = invoice_ocr.read_ledger(ledger)
    assert rows["a.jpg"]["plate"] == "苏US3126"
    assert rows["a.jpg"]["fuel_liters"] == "65.5"
    assert rows["a.jpg"]["invoice_ok"] == "1"


def test_offline_mode_makes_no_network_call(monkeypatch, tmp_path):
    """离线模式绝不能碰网络。"""
    def _boom(*args, **kwargs):
        raise AssertionError("离线模式不应发起网络请求")

    monkeypatch.setattr(kernel, "call_deepseek_vision", _boom)
    monkeypatch.setattr(invoice_ocr.kernel, "call_deepseek_vision", _boom)
    src = tmp_path / "invoices"
    src.mkdir()
    (src / "a.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 32)
    report = invoice_ocr.run(src, tmp_path / "l.csv", tmp_path / "r.json", offline=True)
    assert report["api"]["mode"] == "offline"
    assert report["counts"]["failed"] == 1
    assert report["status"] == "warn"      # 0/1 识别成功不能报 pass


# ---------------------------------------------------------------------------
# 3. CLI 退出码（docs/42 §7：0 通过 / 1 系统错误 / 2 输入错误）
# ---------------------------------------------------------------------------

def test_cli_exit_code_2_on_missing_input_dir(tmp_path):
    assert invoice_ocr.main(["--input", str(tmp_path / "nope")]) == 2


def test_cli_exit_code_0_on_offline_run(tmp_path):
    src = tmp_path / "invoices"
    src.mkdir()
    code = invoice_ocr.main(["--input", str(src),
                             "--out", str(tmp_path / "l.csv"),
                             "--report", str(tmp_path / "r.json"),
                             "--offline"])
    assert code == 0
