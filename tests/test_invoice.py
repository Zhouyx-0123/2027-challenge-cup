# -*- coding: utf-8 -*-
"""发票内核（src/invoice.py）的口径与逻辑测试。

守四条：
  1. **JSON 抽取鲁棒性** —— 模型输出的各种脏形态都要能抠出 JSON；
  2. **不编造数据** —— 识别不出就留空，越界判失败，绝不填假数；
  3. **口径不漂移** —— 因子表查表必须与主账 src/carbon_model._factor 逐格一致；
     柴油因子必须来自 config/calc_factors.json，src/ 里不得硬编码；
  4. **纪律** —— src.invoice 不 import preprocess（不读宽表、不写结果表）；
     输出必带 assumptions 列表（页面据此打黄框）。

全部离线：不需要 API key、不联网。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

from src import carbon_model, invoice

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 1. JSON 抽取
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    '{"invoice_no": "123", "fuel_liters": 65.5}',
    '```json\n{"invoice_no": "123", "fuel_liters": 65.5}\n```',
    '```\n{"invoice_no": "123", "fuel_liters": 65.5}\n```',
    '好的，结果如下：\n```json\n{"invoice_no": "123", "fuel_liters": 65.5}\n```\n以上。',
    '前缀噪声 {"invoice_no": "123", "fuel_liters": 65.5} 后缀噪声',
    '{"invoice_no": "123", "fuel_liters": 65.5, "note": "含 } 杂字符"}',
    '{"invoice_no": "123", "fuel_liters": 65.5}\n\n注：以上为识别结果。',
])
def test_extract_json_handles_dirty_model_output(raw):
    """模型输出各种脏形态都要能抠出 JSON（含字符串里的花括号）。"""
    obj = invoice.extract_json_object(raw)
    assert obj["fuel_liters"] == 65.5
    assert obj["invoice_no"] == "123"


def test_extract_json_raises_on_garbage():
    with pytest.raises(ValueError):
        invoice.extract_json_object("完全不是 JSON")


# ---------------------------------------------------------------------------
# 2. 字段归一与范围校验（不编造数据）
# ---------------------------------------------------------------------------

def test_parse_invoice_normalizes_full_row():
    obj = {"invoice_no": "24317000000123456789", "invoice_amount": 476.84,
           "fuel_liters": 65.5, "plate": "苏US3126",
           "invoice_date": "2026-10-25"}
    row = invoice.parse_invoice(obj)
    assert row["status"] == "ok"
    assert row["fuel_liters"] == 65.5
    assert row["invoice_amount"] == 476.84
    assert row["plate"] == "苏US3126"


def test_parse_invoice_accepts_amount_with_currency_symbol():
    """票面金额常带 ¥ 和千分位，油量常带「升」，都要能剥掉。"""
    row = invoice.parse_invoice({"invoice_amount": "¥1,234.50",
                                 "fuel_liters": "65.5 升"})
    assert row["invoice_amount"] == 1234.50
    assert row["fuel_liters"] == 65.5


@pytest.mark.parametrize("bad", [65000, 0, -5])
def test_parse_invoice_rejects_out_of_range_fuel(bad):
    """OCR 常把 65.5 读成 655 或 6.5 —— 越界判失败，不修不补。"""
    row = invoice.parse_invoice({"fuel_liters": bad, "invoice_amount": 100.0})
    assert row["fuel_liters"] is None
    assert row["status"] in ("partial", "failed")


def test_parse_invoice_null_fuel_is_not_failure_when_amount_present():
    """只有金额没有油量：判 partial（金额本身是有效信息）。"""
    row = invoice.parse_invoice({"fuel_liters": None, "invoice_amount": 476.84})
    assert row["status"] == "partial"
    assert row["fuel_liters"] is None
    assert row["invoice_amount"] == 476.84


def test_parse_invoice_not_an_invoice():
    row = invoice.parse_invoice({"error": "not_an_invoice"})
    assert row["status"] == "failed"


def test_parse_invoice_never_invents_amount():
    """没抽到就必须是 None —— docs/42 §3.1「不得为填满字段而编造发票」。"""
    row = invoice.parse_invoice({"fuel_liters": 65.5})
    assert row["invoice_amount"] is None
    assert row["invoice_no"] == ""


# ---------------------------------------------------------------------------
# 3. 图片编码
# ---------------------------------------------------------------------------

def test_image_to_data_url_rejects_empty():
    url, err = invoice.image_to_data_url(b"")
    assert url is None and err


def test_image_to_data_url_builds_data_url():
    url, err = invoice.image_to_data_url(b"\xff\xd8\xff\xe0" + b"0" * 32, "a.jpg")
    assert err == ""
    assert url.startswith("data:image/jpeg;base64,")


def test_image_to_data_url_rejects_oversize():
    url, err = invoice.image_to_data_url(b"0" * (invoice.MAX_IMAGE_BYTES + 1), "a.jpg")
    assert url is None
    assert "32 MB" in err


# ---------------------------------------------------------------------------
# 4. 口径：油量直算 / 里程因子表 / 反推
# ---------------------------------------------------------------------------

def test_diesel_factor_from_calc_factors_json():
    """柴油因子只从 config/calc_factors.json 读，src/ 里不得硬编码。"""
    fac = invoice.diesel_factors()
    assert fac["ttw_kg_per_l"] == pytest.approx(2.63)
    assert fac["wtt_kg_per_l"] == pytest.approx(0.53)
    source = (ROOT / "src" / "invoice.py").read_text(encoding="utf-8")
    assert "2.63" not in source, "柴油因子不得硬编码在 src/ 里"
    assert "0.53" not in source


def test_calc_fuel_carbon_65_5_liters():
    """65.5 L × (2.63 + 0.53) = 206.98 kg CO₂。"""
    out = invoice.calc_fuel_carbon(65.5)
    assert out["ok"] is True
    assert out["co2_kg"] == pytest.approx(206.98, abs=0.01)
    assert out["co2_t"] == pytest.approx(0.20698, abs=1e-5)


def test_calc_fuel_carbon_without_fuel_is_not_invented():
    out = invoice.calc_fuel_carbon(None)
    assert out["ok"] is False
    assert "co2_kg" not in out


def test_implied_distance():
    """65.5 L ÷ (28 L/100km ÷ 100) = 233.93 km。"""
    assert invoice.implied_distance(65.5, 28.0) == pytest.approx(233.9286, abs=1e-3)
    assert invoice.implied_distance(None, 28.0) is None
    assert invoice.implied_distance(65.5, 0) is None


def test_calc_distance_carbon_uses_factor_table():
    out = invoice.calc_distance_carbon(1000.0, 30.0, 3)
    assert out["ok"] is True
    assert out["factor_gkm"] == pytest.approx(1055.952)
    assert out["co2_kg"] == pytest.approx(1055.952, abs=0.01)


def test_factor_for_matches_main_ledger_model():
    """★ 口径不漂移守护：factor_for 必须与主账 src/carbon_model._factor 逐格一致。

    这是发票交叉校验轨的地基 —— 两轨若口径不同，差异就分不清是
    「车型错配」还是「代码写错了」。
    """
    grid = []
    for vclass in (1, 2, 3):
        for load in (1.0, 4.5, 8.0, 10.0, 12.0, 18.0, 25.0, 30.0, 32.0,
                     35.0, 38.0, 40.0, 45.0, 49.0):
            grid.append({"vclass": vclass, "load_t": load})
    frame = pd.DataFrame(grid)
    expected = carbon_model._factor(frame)
    for i, row in enumerate(frame.itertuples()):
        assert invoice.factor_for(int(row.vclass), float(row.load_t)) == \
            pytest.approx(float(expected[i])), \
            "vclass=%s load=%s 与主账不一致" % (row.vclass, row.load_t)


# ---------------------------------------------------------------------------
# 5. invoice_summary：交叉校验 + assumptions
# ---------------------------------------------------------------------------

def test_invoice_summary_returns_both_tracks_and_assumptions():
    """油量直算轨 + 里程因子表轨 + 交叉校验 + assumptions 列表，一个都不能少。"""
    out = invoice.invoice_summary(fuel_liters=65.5, distance_km=None,
                                  load_t=18.0, vclass=3, l_per_100km=28.0)
    assert out["fuel_track"]["ok"] is True
    assert out["distance_track"]["ok"] is True
    assert out["derived_distance_km"] == pytest.approx(233.9286, abs=1e-3)
    assert out["cross_check"]["ok"] is True
    assert out["cross_check"]["diff_pct"] is not None
    assert isinstance(out["assumptions"], list) and out["assumptions"]
    joined = " ".join(out["assumptions"])
    assert "假设值" in joined
    assert "情景推演" in joined and "非对拍口径" in joined


def test_invoice_summary_flags_manual_distance_mismatch():
    """手填里程与油量反推值不一致时，assumptions 必须点出来。"""
    out = invoice.invoice_summary(fuel_liters=65.5, distance_km=500.0,
                                  load_t=18.0, vclass=3, l_per_100km=28.0)
    joined = " ".join(out["assumptions"])
    assert "不一致" in joined


def test_cross_check_verdict_is_a_string():
    out = invoice.invoice_summary(fuel_liters=65.5, distance_km=None,
                                  load_t=18.0, vclass=3, l_per_100km=28.0)
    verdict = out["cross_check"]["verdict"]
    assert isinstance(verdict, str) and verdict


def test_invoice_summary_without_fuel_does_not_crash():
    out = invoice.invoice_summary(fuel_liters=None, distance_km=100.0,
                                  load_t=18.0, vclass=3)
    assert out["fuel_track"]["ok"] is False
    assert out["distance_track"]["ok"] is True


# ---------------------------------------------------------------------------
# 6. 纪律：src/invoice 不 import preprocess；提取入口可 mock
# ---------------------------------------------------------------------------

def test_invoice_does_not_import_preprocess():
    """src/invoice 不 import preprocess/evaluator —— 不读宽表、不写结果表。"""
    tree = ast.parse((ROOT / "src" / "invoice.py").read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(("." * node.level) + (node.module or ""))
    offenders = [n for n in imported
                 if n.endswith("preprocess") or n.endswith("evaluator")
                 or n.endswith("pipeline")]
    assert not offenders, "src/invoice 不得 import %s" % offenders


def test_extract_invoice_fields_is_mockable(monkeypatch):
    """识别入口是纯函数形态：mock 掉网络调用即可单测，不碰真实 API。"""
    monkeypatch.setattr(invoice, "call_deepseek_vision",
                        lambda *a, **k: '{"fuel_liters": 65.5, "invoice_amount": 476.84}')
    out = invoice.extract_invoice_fields(b"\xff\xd8\xff\xe0" + b"0" * 16, "sk-test", "a.jpg")
    assert out["status"] == "ok"
    assert out["fuel_liters"] == 65.5
    assert out["invoice_amount"] == 476.84


def test_extract_invoice_fields_handles_api_error(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("HTTP 401")
    monkeypatch.setattr(invoice, "call_deepseek_vision", _boom)
    out = invoice.extract_invoice_fields(b"\xff\xd8\xff\xe0" + b"0" * 16, "sk-test", "a.jpg")
    assert out["status"] == "failed"
    assert "401" in out["note"]
    assert out["fuel_liters"] is None


# ---------------------------------------------------------------------------
# 7. 自测函数
# ---------------------------------------------------------------------------

def test_self_test_runs():
    out = invoice.self_test()
    assert out["fuel_65_5_kg"] == pytest.approx(206.98, abs=0.01)
    assert out["implied_km_28"] == pytest.approx(233.929, abs=0.01)
    assert out["factor_class3_30t"] == pytest.approx(1055.952)
