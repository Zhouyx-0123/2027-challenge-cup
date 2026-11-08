from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.fuel_layer import (
    ALLOCATION_COLUMNS, EXCLUDED_SHEET, META_SHEET, PLATE_SHEET,
    build_by_plate, build_fuel_layer, build_meta, build_uncovered,
    load_fuel_layer, write_fuel_layer,
)


def make_result():
    trips = [
        {"trip_id": "T1", "waybill_no": "W1", "plate": "苏A1", "depart_ts": "2026-11-01 08:00:00",
         "arrive_ts": "2026-11-01 10:00:00", "trip_km": 120.0, "od_site": "上海—苏州",
         "vehicle_type": "牵引车"},
        {"trip_id": "T2", "waybill_no": "W2", "plate": "苏A1", "depart_ts": "2026-11-02 08:00:00",
         "arrive_ts": "2026-11-02 11:00:00", "trip_km": 180.0, "od_site": "苏州—上海",
         "vehicle_type": "牵引车"},
        {"trip_id": "T3", "waybill_no": "W3", "plate": "苏A2", "depart_ts": "2026-11-03 08:00:00",
         "arrive_ts": "2026-11-03 09:00:00", "trip_km": 100.0, "od_site": "上海—嘉兴",
         "vehicle_type": "厢式"},
    ]
    records = [
        {"record_id": "R1", "invoice_no": "F1", "invoice_date": "2026-11-01", "plate": "苏A1"},
        {"record_id": "R2", "invoice_no": "F2", "invoice_date": "2026-11-03", "plate": "苏A1"},
        {"record_id": "R3", "invoice_no": "F3", "invoice_date": "2026-11-03", "plate": "苏A2"},
    ]
    return {
        "run": {
            "batch_id": "B1", "source_mode": "simulated", "input_sha256": "abc",
            "scope": {"period_start": "2026-11-01", "period_end": "2026-11-30",
                      "trip_count": 3, "invoice_count": 2, "evidence_invoice_count": 3,
                      "excluded_invoice_count": 1},
            "references_snapshot": {"cost_basis": "invoice_gross"},
            "input_snapshot": {"records": records, "trips": trips},
        },
        "allocations": [
            {"trip_id": "T1", "waybills": ["W1"], "plate": "苏A1", "od_site": "上海—苏州",
             "cycle_id": "C1", "record_id": "R1", "end_record_id": "R2",
             "source_image": "F1.png", "rule": "docs45-section5", "share": 0.4,
             "trip_km": 120.0, "allocated_liters": 40.0, "l_per_100km": 33.333333,
             "allocated_cost": 300.0, "yuan_per_km": 2.5, "cost_basis": "invoice_gross",
             "measurement": "rule_allocated", "theory_liters": 36.0, "deviation": 0.111111,
             "factor": {"factor_id": "TF-1"}},
            {"trip_id": "T2", "waybills": ["W2"], "plate": "苏A1", "od_site": "苏州—上海",
             "cycle_id": "C1", "record_id": "R1", "end_record_id": "R2",
             "source_image": "F1.png", "rule": "docs45-section5", "share": 0.6,
             "trip_km": 180.0, "allocated_liters": 60.0, "l_per_100km": 33.333333,
             "allocated_cost": 450.0, "yuan_per_km": 2.5, "cost_basis": "invoice_gross",
             "measurement": "rule_allocated", "theory_liters": 54.0, "deviation": 0.111111,
             "factor": {"factor_id": "TF-1"}},
        ],
        "coverage": {"total_km": 400.0, "association_km": 300.0, "allocation_km": 300.0,
                     "association_rate": 0.75, "allocation_rate": 0.75},
        "gaps": [{"trip_id": "T3", "reason": "no_valid_cycle_cover"}],
        "excluded": [{"record_id": "R3", "invoice_no": "F3", "plate": "苏A2",
                      "invoice_date": "2026-11-03", "item_name": "更换轮胎",
                      "invoice_amount": 1000.0, "energy_type": "unknown",
                      "reason": "non_energy_invoice"}],
    }


def test_layer_is_one_row_per_allocation_and_weighted_average():
    layer = build_fuel_layer(make_result())
    assert list(layer.columns) == ALLOCATION_COLUMNS
    assert len(layer) == 2
    assert layer["allocated_liters"].sum() == 100
    assert layer["trip_km"].sum() == 300
    assert not {"carbon_kg", "kg_co2", "ef_g_km"} & set(layer.columns)
    assert layer.loc[layer["trip_id"] == "T1", "invoice_no"].item() == "F1"
    assert layer.loc[layer["trip_id"] == "T1", "next_invoice_no"].item() == "F2"
    assert layer.loc[layer["trip_id"] == "T1", "theory_factor_id"].item() == "TF-1"

    by_plate = build_by_plate(layer)
    assert len(by_plate) == 1
    row = by_plate.iloc[0]
    assert row["plate"] == "苏A1"
    assert row["allocated_trips"] == 2
    assert row["distance_km"] == 300
    assert row["allocated_liters"] == 100
    assert row["l_per_100km"] == 100 * 100 / 300
    assert row["allocated_cost"] == 750
    assert row["invoice_count"] == 1


def test_uncovered_and_meta_carry_the_reason_and_boundaries():
    result = make_result()
    layer = build_fuel_layer(result)
    uncovered = build_uncovered(result)
    assert len(uncovered) == 1
    assert uncovered.iloc[0]["trip_id"] == "T3"
    assert uncovered.iloc[0]["reason"] == "no_valid_cycle_cover"
    meta = build_meta(result, layer, uncovered, pd.DataFrame())
    assert meta["allocated_trips"] == 2
    assert meta["uncovered_trips"] == 1
    assert meta["allocated_liters"] == 100
    assert meta["allocation_rate"] == 0.75
    assert "不写碳列" in meta["carbon_boundary"]
    assert "不自动写财务主表" in meta["posting_boundary"]


def test_write_layer_workbook_and_json(tmp_path: Path):
    result = make_result()
    report = write_fuel_layer(result, tmp_path)
    xlsx = Path(report["xlsx"])
    assert xlsx.is_file()
    assert Path(report["json"]).is_file()
    sheets = load_fuel_layer(xlsx)
    assert META_SHEET in sheets and PLATE_SHEET in sheets and EXCLUDED_SHEET in sheets
    assert len(sheets) == 7
    assert len(sheets["01_趟级油耗"]) == 2
    assert len(sheets[PLATE_SHEET]) == 1
    meta = {str(row["项目"]): row["值"] for _, row in sheets[META_SHEET].iterrows()}
    assert meta["allocated_liters"] == 100
    assert meta["uncovered_trips"] == 1
    assert "carbon" not in " ".join(sheets["01_趟级油耗"].columns).lower()

    payload = json.loads(Path(report["json"]).read_text(encoding="utf-8"))
    assert payload["meta"]["allocated_trips"] == 2
    assert len(payload["allocations"]) == 2
    assert payload["excluded"][0]["reason"] == "non_energy_invoice"


def test_load_layer_keeps_long_invoice_numbers_as_text(tmp_path: Path):
    result = make_result()
    result["run"]["input_snapshot"]["records"][0]["invoice_no"] = "25940000000000000001"
    report = write_fuel_layer(result, tmp_path)
    loaded = load_fuel_layer(Path(report["xlsx"]))["01_趟级油耗"]
    assert isinstance(loaded.loc[0, "invoice_no"], str)
    assert loaded.loc[0, "invoice_no"] == "25940000000000000001"


def test_write_layer_is_idempotent(tmp_path: Path):
    first = write_fuel_layer(make_result(), tmp_path)
    second = write_fuel_layer(make_result(), tmp_path)
    assert first["xlsx"] == second["xlsx"]
    assert len(load_fuel_layer(Path(second["xlsx"]))["01_趟级油耗"]) == 2
