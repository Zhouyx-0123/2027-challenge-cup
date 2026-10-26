# -*- coding: utf-8 -*-
"""通用财务发票碳核算：活动数据优先、支出法兜底、去重与汇总测试。

所有支出因子均为明确标注的合成测试值，不是行业因子，不进入生产配置。
"""
from __future__ import annotations

import json

import pytest

from src import invoice
from tools import invoice_ocr


def spend_factor(value=1.25, basis="including_tax", year=2026):
    return {
        "value": value,
        "unit": "kgCO2e/CNY",
        "source": "SYNTHETIC TEST FACTOR - NOT FOR PRODUCTION",
        "price_year": year,
        "price_basis": basis,
        "scope": "scope_3",
        "gas_coverage": "CO2e (synthetic test)",
    }


def diesel_record(**kwargs):
    row = {
        "invoice_code": "DEMO-CODE",
        "invoice_no": "DEMO-FUEL-001",
        "seller_name": "DEMO-SELLER",
        "invoice_date": "2026-10-25",
        "invoice_amount": 476.84,
        "item_name": "0#柴油",
        "quantity": 65.5,
        "unit": "L",
        "fuel_liters": 65.5,
    }
    row.update(kwargs)
    return row


def test_diesel_activity_data_and_scope_breakdown():
    out = invoice.calculate_invoice_carbon(diesel_record())
    assert out["status"] == "calculated"
    assert out["method"] == "activity_data"
    assert out["activity_quantity"] == 65.5
    assert out["activity_unit"] == "L"
    assert out["emissions_kg_co2e"] == pytest.approx(206.98, abs=0.001)
    assert out["scope_breakdown_kg"]["scope_1"] == pytest.approx(172.265)
    assert out["scope_breakdown_kg"]["scope_3"] == pytest.approx(34.715)
    assert sum(out["scope_breakdown_kg"].values()) == pytest.approx(out["emissions_kg_co2e"])
    assert len(out["factors"]) == 2
    assert out["assumptions"]


def test_legacy_diesel_record_without_item_name_still_works():
    out = invoice.calculate_invoice_carbon({"invoice_no": "DEMO", "fuel_liters": 65.5})
    assert out["status"] == "calculated"
    assert out["emission_category"] == "diesel"
    assert out["emissions_kg_co2e"] == pytest.approx(206.98, abs=0.001)


def test_electricity_invoice_uses_kwh_and_scope_2():
    out = invoice.calculate_invoice_carbon({
        "invoice_no": "DEMO-ELEC-001", "item_name": "电费",
        "quantity": "1,000", "unit": "千瓦时", "invoice_amount": 800,
    })
    assert out["status"] == "calculated"
    assert out["method"] == "activity_data"
    assert out["activity_unit"] == "kWh"
    assert out["emissions_kg_co2e"] == pytest.approx(556.8)
    assert out["scope_breakdown_kg"] == {"scope_2": pytest.approx(556.8)}


def test_cng_invoice_uses_cubic_metres():
    out = invoice.calculate_invoice_carbon({
        "item_name": "压缩天然气", "quantity": 100, "unit": "立方米",
    })
    assert out["status"] == "calculated"
    assert out["activity_unit"] == "m3"
    assert out["emissions_kg_co2e"] == pytest.approx(274.0)


def test_activity_data_has_priority_over_spend_factor():
    out = invoice.calculate_invoice_carbon(
        diesel_record(), spend_factors={"diesel": spend_factor(value=999)})
    assert out["method"] == "activity_data"
    assert out["emissions_kg_co2e"] == pytest.approx(206.98, abs=0.001)


def test_conflicting_fuel_quantities_require_review():
    out = invoice.calculate_invoice_carbon(diesel_record(quantity=60))
    assert out["status"] == "needs_review"
    assert out["reason_code"] == "conflicting_activity_data"
    assert out["emissions_kg_co2e"] is None


def test_spend_based_invoice_uses_configured_category_factor():
    out = invoice.calculate_invoice_carbon({
        "invoice_no": "DEMO-FREIGHT-001", "invoice_date": "2026-01-01",
        "emission_category": "road_freight", "invoice_amount": 100,
    }, spend_factors={"road_freight": spend_factor()})
    assert out["status"] == "calculated"
    assert out["method"] == "spend_based"
    assert out["emissions_kg_co2e"] == pytest.approx(125.0)
    assert out["activity_unit"] == "CNY"
    assert out["scope"] == "scope_3"
    assert out["data_quality"] == "spend_estimated"


def test_spend_factor_tax_basis_selects_matching_amount():
    record = {
        "emission_category": "road_freight", "invoice_amount": 113,
        "amount_excluding_tax": 100,
    }
    out = invoice.calculate_invoice_carbon(
        record, spend_factors={"road_freight": spend_factor(basis="excluding_tax")})
    assert out["activity_quantity"] == 100
    assert out["emissions_kg_co2e"] == pytest.approx(125.0)


def test_spend_factor_does_not_mix_gross_amount_with_net_factor():
    out = invoice.calculate_invoice_carbon(
        {"emission_category": "road_freight", "invoice_amount": 113},
        spend_factors={"road_freight": spend_factor(basis="excluding_tax")})
    assert out["status"] == "needs_review"
    assert out["reason_code"] == "missing_amount_basis"


def test_amount_only_invoice_without_factor_is_not_invented():
    out = invoice.calculate_invoice_carbon({
        "item_name": "公路运输费", "invoice_amount": 10000,
    })
    assert out["status"] == "needs_review"
    assert out["reason_code"] == "missing_spend_factor"
    assert out["emissions_kg_co2e"] is None


def test_unknown_category_requires_explicit_mapping():
    out = invoice.calculate_invoice_carbon({"item_name": "办公用品", "invoice_amount": 100})
    assert out["status"] == "needs_review"
    assert out["reason_code"] == "unknown_category"


def test_spend_factor_requires_auditable_metadata():
    bad = spend_factor()
    bad["source"] = ""
    out = invoice.calculate_invoice_carbon(
        {"emission_category": "road_freight", "invoice_amount": 100},
        spend_factors={"road_freight": bad})
    assert out["reason_code"] == "invalid_spend_factor_metadata"
    assert out["emissions_kg_co2e"] is None


def test_price_year_mismatch_is_disclosed_not_silently_adjusted():
    out = invoice.calculate_invoice_carbon(
        {"emission_category": "road_freight", "invoice_amount": 100,
         "invoice_date": "2026-10-25"},
        spend_factors={"road_freight": spend_factor(year=2023)})
    assert out["status"] == "calculated"
    assert any("未自动进行通胀" in text for text in out["warnings"])


def test_parse_invoice_keeps_generic_financial_fields():
    row = invoice.parse_invoice({
        "invoice_code": "0110", "invoice_no": "DEMO-ELEC",
        "seller_name": "DEMO-POWER", "invoice_amount": "¥1,130.00",
        "amount_excluding_tax": "1000", "tax_amount": "130",
        "item_name": "电费", "quantity": "1,000", "unit": "kWh",
        "invoice_date": "2026-10-25",
    })
    assert row["status"] == "ok"
    assert row["quantity"] == 1000
    assert row["amount_excluding_tax"] == 1000
    assert row["seller_name"] == "DEMO-POWER"


def test_exact_duplicate_is_counted_once():
    rows = [diesel_record(), diesel_record()]
    out = invoice.calculate_invoice_batch(rows)
    assert out["summary"]["calculated_count"] == 1
    assert out["summary"]["excluded_duplicate_count"] == 1
    assert out["summary"]["total_emissions_kg_co2e"] == pytest.approx(206.98, abs=0.001)
    assert out["results"][1]["status"] == "excluded_duplicate"
    assert out["results"][1]["emissions_kg_co2e"] is None


def test_conflicting_duplicate_group_is_fully_excluded():
    rows = [diesel_record(), diesel_record(invoice_amount=999, fuel_liters=60, quantity=60)]
    out = invoice.calculate_invoice_batch(rows)
    assert out["summary"]["duplicate_conflict_groups"] == 1
    assert out["summary"]["calculated_count"] == 0
    assert out["summary"]["needs_review_count"] == 2
    assert out["summary"]["total_emissions_kg_co2e"] is None
    assert all(row["reason_code"] == "duplicate_conflict" for row in out["results"])


def test_missing_invoice_number_is_not_guessed_as_duplicate():
    first = diesel_record(invoice_no="")
    second = diesel_record(invoice_no="")
    out = invoice.calculate_invoice_batch([first, second])
    assert out["summary"]["calculated_count"] == 2
    assert out["summary"]["excluded_duplicate_count"] == 0


def test_batch_summary_reconciles_method_scope_and_total():
    electricity = {
        "invoice_no": "DEMO-ELEC-002", "seller_name": "DEMO-POWER",
        "item_name": "购电", "quantity": 1000, "unit": "kWh",
    }
    out = invoice.calculate_invoice_batch([diesel_record(), electricity])
    summary = out["summary"]
    assert out["status"] == "complete"
    assert summary["total_complete"] is True
    assert summary["coverage_pct"] == 100
    assert summary["total_emissions_kg_co2e"] == pytest.approx(763.78, abs=0.001)
    assert sum(summary["by_method_kg"].values()) == pytest.approx(summary["total_emissions_kg_co2e"])
    assert sum(summary["by_scope_kg"].values()) == pytest.approx(summary["total_emissions_kg_co2e"])
    assert set(summary["by_scope_kg"]) == {"scope_1", "scope_2", "scope_3"}


def test_cli_writes_carbon_detail_and_privacy_safe_summary(monkeypatch, tmp_path):
    monkeypatch.setattr(invoice_ocr.kernel, "extract_invoice_fields", lambda *a, **k: {
        "status": "ok", "invoice_code": "", "invoice_no": "DEMO-ELEC-SECRET",
        "seller_name": "DEMO-POWER", "invoice_amount": 800,
        "amount_excluding_tax": 800, "tax_amount": 0,
        "item_name": "电费", "quantity": 1000, "unit": "kWh",
        "fuel_liters": None, "plate": "", "invoice_date": "2026-10-25", "note": "",
    })
    monkeypatch.setattr(invoice_ocr, "load_api_key", lambda: "sk-test-fake")
    source = tmp_path / "invoices"
    source.mkdir()
    (source / "electricity.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 32)
    ledger = tmp_path / "ledger.csv"
    report_path = tmp_path / "report.json"
    report = invoice_ocr.run(source, ledger, report_path)
    assert report["carbon_summary"]["total_emissions_kg_co2e"] == pytest.approx(556.8)
    assert report["carbon_summary"]["calculated_count"] == 1
    rows = invoice_ocr.read_ledger(ledger)
    assert rows["electricity.jpg"]["carbon_method"] == "activity_data"
    assert rows["electricity.jpg"]["co2e_kg"] == "556.8"
    assert rows["electricity.jpg"]["included_in_total"] == "1"
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert "carbon_summary" in saved
    assert "DEMO-ELEC-SECRET" not in report_path.read_text(encoding="utf-8")
