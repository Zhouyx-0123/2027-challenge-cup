from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest
from openpyxl import load_workbook

from tools.invoice_workbook import read_invoice_workbook
from src.invoice import parse_invoice
from src.transport_evidence import (
    analyze, atomic_json, assess, export_excel, list_runs, read_store,
    register, replay_run, save_run,
)


FIXTURES = Path(__file__).parent / "fixtures" / "finance_transport"
TRUTH = FIXTURES / "invoice_simulated_truth.xlsx"


def make_trips(records):
    rows = []
    for i, record in enumerate(records):
        if record["unit"] != "L" or "柴油" not in record["item_name"]:
            continue
        day = record["invoice_date"]
        plate = record["plate"]
        rows.append({
            "trip_id": f"T-{plate}-{day}",
            "waybill_no": f"WB-{record['invoice_no']}",
            "plate": plate,
            "depart_ts": f"{day} 08:00:00",
            "arrive_ts": f"{day} 10:00:00",
            "trip_km": 100 + (i % 39),
            "od_site": "上海—苏州",
            "vehicle_type": "牵引车",
            "energy_type": "fuel",
            "source_mode": "simulated",
        })
    return pd.DataFrame(rows)


def test_simulated_workbook_import_contract():
    records = read_invoice_workbook(TRUTH, "simulated")
    assert len(records) == 45
    assert sum(r["unit"] == "L" and "柴油" in r["item_name"] for r in records) == 39
    energy_types = [assess(r)[0] for r in records]
    assert energy_types.count("fuel") == 39
    assert energy_types.count("unknown") == 6
    assert len({r["plate"] for r in records}) == 8
    with pytest.raises(ValueError, match="模拟样张"):
        read_invoice_workbook(TRUTH, "real")
    exported = load_workbook(BytesIO(export_excel(records)), data_only=True)
    assert exported["发票证据表"].max_row == 46
    assert exported["票据明细"].max_row == 49  # header + 48 individual lines

    multi = next(r for r in records if r["invoice_no"] == "25946248797655020504")
    assert multi["multiple_items"] is True
    assert multi["quantity"] is None and multi["unit"] == "" and multi["unit_price"] is None
    assert [x["item_name"] for x in multi["items"]] == ["*劳务*更换轮胎", "*货物*轮胎"]
    assert [x["amount"] for x in multi["items"]] == [1980.0, 470.0]
    assert multi["invoice_amount"] == 2768.50
    assert multi["tax_amount"] == 318.50
    assert multi["plate"] == "苏A12348"

    old_repair = next(r for r in records if r["invoice_no"] == "25245078884927233714")
    assert old_repair["invoice_date"] == "2025-06-01"
    assert old_repair["plate"] == "苏A12345"
    assert old_repair["tax_rate"] == "13%"


def test_invoice_parser_preserves_multi_line_and_plate_from_note():
    parsed = parse_invoice({
        "invoice_no": "TEST-MULTI",
        "invoice_amount": "2768.50",
        "amount_excluding_tax": "2450.00",
        "tax_amount": "318.50",
        "invoice_date": "2026-10-30",
        "item_name": "多行明细（见明细表）",
        "note": "车牌号：苏A12348 / 维修项目详见明细",
        "items": [
            {"item_name": "*劳务*更换轮胎", "specification": "11R22.5", "unit": "条",
             "quantity": "2.00", "unit_price": "990.00", "amount": "1980.00",
             "tax_rate": "13%", "tax_amount": "257.40"},
            {"item_name": "*货物*轮胎", "specification": "11R22.5", "unit": "条",
             "quantity": "2.00", "unit_price": "235.00", "amount": "470.00",
             "tax_rate": "13%", "tax_amount": "61.10"},
        ],
    })
    assert parsed["multiple_items"] is True
    assert len(parsed["items"]) == 2
    assert parsed["quantity"] is None and parsed["unit"] == "" and parsed["unit_price"] is None
    assert parsed["plate"] == "苏A12348"
    assert "车牌号：苏A12348" in parsed["note"]


def test_real_invoice_image_register_and_detail_export(tmp_path):
    records = read_invoice_workbook(TRUTH, "simulated")
    record = next(r for r in records if r["invoice_no"] == "25946248797655020504")
    image = (FIXTURES / "FP-REPAIR-003.png").read_bytes()
    report = register(record, image, "FP-REPAIR-003.png", tmp_path)
    assert report["added"] == 1 and report["source_mode"] == "simulated"
    saved = read_store("simulated", tmp_path)["records"][0]
    assert len(saved["items"]) == 2
    assert (tmp_path / saved["source_image"]).read_bytes() == image

    workbook = load_workbook(BytesIO(export_excel([saved])), data_only=True)
    assert workbook["发票证据表"].max_column == 15
    details = workbook["票据明细"]
    assert details.max_row == 3
    assert details.cell(2, 4).value == "*劳务*更换轮胎"
    assert details.cell(3, 4).value == "*货物*轮胎"


def test_45_invoice_scope_factor_and_snapshot_replay(tmp_path):
    records = read_invoice_workbook(TRUTH, "simulated")
    for record in records:
        record["energy_type"], record["missing_fields"] = assess(record)
    # The two uploaded images are embedded in the historical batch; other image files
    # were not supplied, so the test does not invent or fabricate them.
    for filename in ("FP-REPAIR-003.png", "FP-REPAIR-001.png"):
        (tmp_path / filename).write_bytes((FIXTURES / filename).read_bytes())
    atomic_json(tmp_path / "transport_simulated.json", {"records": records, "events": []})

    trips = make_trips(records)
    assert len(trips) == 39
    references = {
        "cost_basis": "invoice_gross",
        "capacities": {p: 500 for p in sorted({r["plate"] for r in records if r["plate"]})},
        "team_theory_factors": [{
            "factor_id": "SIM-TRACTOR-DIESEL-001",
            "vehicle_type": "牵引车",
            "energy_type": "fuel",
            "valid_from": "2026-10-01",
            "valid_to": "2026-12-31",
            "value": 30,
            "version": "SIM-FACTOR-v1",
            "source": "仅供自动化测试的合成因子，不是团队真实因子",
        }],
    }
    result = save_run(
        "simulated", trips, tmp_path, references=references,
        complete_history=True, period_start="2026-10-31", period_end="2026-11-04",
    )
    assert result["run"]["scope"]["trip_count"] == 39
    assert result["run"]["scope"]["period_start"] == "2026-10-31"
    assert result["coverage"]["total_km"] == pytest.approx(trips["trip_km"].sum())
    assert len(result["allocations"]) == 31  # 39 refuels across 8 vehicles => 31 closed cycles
    assert all(a["theory_factor_selection"]["status"] == "matched" for a in result["allocations"])
    assert all(a["factor"]["source"].startswith("仅供自动化测试") for a in result["allocations"])

    snapshot = result["run"]["input_snapshot"]
    assert len(snapshot["records"]) == 45
    assert len(snapshot["trips"]) == 39
    assert set(snapshot["invoice_images"]) == {"FP-REPAIR-003.png", "FP-REPAIR-001.png"}
    assert base64.b64decode(snapshot["invoice_images"]["FP-REPAIR-003.png"]) == (FIXTURES / "FP-REPAIR-003.png").read_bytes()

    batch_id = result["run"]["batch_id"]
    # Replace current input store and remove source images: replay must still work
    # solely from the saved snapshot.
    changed = read_store("simulated", tmp_path)["records"]
    changed[0]["note"] = "after-run-change"
    atomic_json(tmp_path / "transport_simulated.json", {"records": changed, "events": []})
    (tmp_path / "FP-REPAIR-003.png").unlink()
    (tmp_path / "FP-REPAIR-001.png").unlink()

    old_runs = list_runs("simulated", tmp_path)
    assert any(r["run"]["batch_id"] == batch_id and r["run"]["stale"] for r in old_runs)
    replayed = replay_run(batch_id, "simulated", tmp_path)
    assert replayed["run"]["replay_of"] == batch_id
    assert replayed["coverage"] == result["coverage"]
    assert replayed["run"]["scope"] == result["run"]["scope"]

def test_direct_team_factor_xlsx_uses_chinese_columns_and_detects_ambiguity(tmp_path):
    from src.transport_checks import validate_references
    from src.transport_evidence import _select_theory_factor

    table = pd.DataFrame([{
        "因子ID": "TEAM-01",
        "车型": "牵引车",
        "能源类型": "柴油",
        "百公里理论油耗": 30.0,
        "版本": "2026-v1",
        "来源": "团队模拟因子表（测试）",
        "生效日期": "2026-10-01",
        "失效日期": "2026-12-31",
        "适用条件": None,
    }])
    path = tmp_path / "team_factors.xlsx"
    table.to_excel(path, index=False)
    loaded = pd.read_excel(path).astype(object).where(pd.notna(pd.read_excel(path)), None)
    refs = {"team_theory_factors": loaded.to_dict(orient="records")}
    validate_references(refs)
    incomplete = {"team_theory_factors": [dict(refs["team_theory_factors"][0])]}
    incomplete["team_theory_factors"][0].pop("能源类型")
    with pytest.raises(ValueError, match="能源类型"):
        validate_references(incomplete)

    vehicle = {"plate": "苏A12345", "vehicle_type": "牵引车"}
    selected = _select_theory_factor(vehicle, "fuel", "2026-11-02", refs)
    assert selected["status"] == "matched"
    assert selected["factor"]["value"] == 30
    assert selected["factor"]["factor_id"] == "TEAM-01"

    refs["team_theory_factors"].append(dict(refs["team_theory_factors"][0], **{"因子ID": "TEAM-02"}))
    ambiguous = _select_theory_factor(vehicle, "fuel", "2026-11-02", refs)
    assert ambiguous["status"] == "ambiguous"
    assert set(ambiguous["candidate_factor_ids"]) == {"TEAM-01", "TEAM-02"}

def test_invoice_ocr_csv_roundtrip_keeps_detail_rows(tmp_path):
    from tools.invoice_ocr import LEDGER_COLUMNS, write_ledger
    from tools.invoice_to_master import read_records

    path = tmp_path / "invoice_ledger.csv"
    items = [
        {"item_name": "劳务-更换轮胎", "quantity": 2, "unit": "条"},
        {"item_name": "货物-轮胎", "quantity": 2, "unit": "条"},
    ]
    row = {column: "" for column in LEDGER_COLUMNS}
    row.update({
        "invoice_no": "CSV-MULTI-001",
        "items_json": json.dumps(items, ensure_ascii=False),
        "multiple_items": True,
    })
    write_ledger(path, [row])
    loaded = read_records(path)
    assert len(loaded[0]["items"]) == 2
    assert loaded[0]["multiple_items"] is True

