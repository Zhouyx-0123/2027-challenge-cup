# -*- coding: utf-8 -*-
"""trip_proxy 主表测试（SEG-MASTER-v1）：一行一趟、碳口径、审计与聚合合同。"""
from __future__ import annotations

import pandas as pd
import pytest

from src import segment_master as sm
from src import schema


def _frames():
    seg = pd.DataFrame({
        "trip_id": [1, 2, 3],
        "month": ["2023-11", "2023-11", "2023-11"],
        "speed_kmh": [10.0, 50.0, 120.0],
        "distance_km": [10.0, 50.0, 5.0],
        "speed_bin": ["低速档 <20", "中速档 40-60", "invalid"],
        "speed_flag_seg": [[], [], ["speed_over_110"]],
        "passed": [True, True, False],
        "cluster_id": pd.Series([0, 1, pd.NA], dtype="Int64"),
        "vehicle_group": ["轻型", "中型", "重型"],
    })
    assignments = pd.DataFrame({
        "unit_id": [1, 2, 3],
        "plate_id": ["p1", "p2", "p3"],
        "load_t": [4.5, 18.0, 4.5],
        "feature_set": ["proxy_v1"] * 3,
    })
    ledger = pd.DataFrame({
        "trip_id": [1, 1, 2, 3],
        "waybill_no": ["w1", "w2", "w3", "w4"],
        "depart_ts": ["2023-11-01 08:00", "2023-11-01 12:00",
                      "2023-11-02 08:00", "2023-11-03 08:00"],
        "arrive_ts": ["2023-11-01 10:00", "2023-11-01 14:00",
                      "2023-11-02 10:00", "2023-11-03 09:00"],
        "origin": ["A", "B", "A", "C"],
        "dest": ["B", "C", "C", "A"],
        "duration_h": [2.0, 2.0, 2.0, 1.0],
        "trip_km": [10.0, 10.0, 50.0, 5.0],
        "kg_co2": [1.0, 2.0, 5.0, 7.0],
        "kg_co2_phys": [1.5, 2.5, 6.0, 8.0],
        "vehicle_type": ["9.6米厢式货车"] * 4,
    })
    return seg, assignments, ledger


def test_master_is_one_row_per_trip_and_keeps_carbon_basis():
    seg, assignments, ledger = _frames()
    master, audit = sm.build_master(seg, assignments, ledger, kpi_total_kg=15.0)

    assert len(master) == 3
    assert master["trip_id"].tolist() == [1, 2, 3]
    assert master["segment_id"].tolist() == ["TRIP-000001", "TRIP-000002", "TRIP-000003"]
    assert master["grain"].unique().tolist() == ["trip_proxy"]
    assert master["source"].unique().tolist() == ["JD_WAYBILL"]
    assert master["segment_scope"].unique().tolist() == ["whole_trip"]
    assert master["has_subtrip_trajectory"].tolist() == [False, False, False]

    row1 = master[master["trip_id"] == 1].iloc[0]
    assert row1["carbon_kg"] == pytest.approx(3.0)
    assert row1["carbon_kg_phys"] == pytest.approx(4.0)
    assert row1["n_waybills"] == 2
    assert row1["n_origins"] == 2
    assert row1["n_destinations"] == 2
    assert row1["ef_g_km"] == pytest.approx(300.0)

    # 主账口径 = sum(kg_co2)，不是物理轨、不是 VSP 轨
    assert audit["carbon_kg"] == pytest.approx(15.0)
    assert audit["carbon_kg_phys"] == pytest.approx(18.0)
    assert audit["reconciliation"]["matches"] is True


def test_master_marks_invalid_and_unclustered_without_writing_zero():
    seg, assignments, ledger = _frames()
    master, audit = sm.build_master(seg, assignments, ledger, kpi_total_kg=15.0)

    bad = master[master["trip_id"] == 3].iloc[0]
    assert bool(bad["passed"]) is False
    assert bad["qc_flags"] == "speed_over_110"
    assert pd.isna(bad["cluster_id"])
    assert bad["paper_bin"] == "invalid"
    assert bad["carbon_kg"] == pytest.approx(7.0)  # 保留但不进有效聚合

    assert audit["n_passed"] == 2
    assert audit["n_invalid"] == 1
    assert audit["n_unclustered"] == 1
    assert audit["carbon_kg_valid"] == pytest.approx(8.0)
    assert audit["carbon_kg_invalid"] == pytest.approx(7.0)
    assert audit["n_plates"] == 3
    assert audit["n_waybills"] == 4


def test_audit_by_bin_and_cluster_use_valid_rows_only():
    seg, assignments, ledger = _frames()
    _, audit = sm.build_master(seg, assignments, ledger, kpi_total_kg=15.0)

    assert [r["paper_bin"] for r in audit["by_paper_bin"]] == [
        "低速档 <20", "中速档 40-60"]
    bins = {r["paper_bin"]: r for r in audit["by_paper_bin"]}
    assert bins["低速档 <20"]["trips"] == 1
    assert bins["低速档 <20"]["carbon_kg"] == pytest.approx(3.0)
    assert bins["中速档 40-60"]["carbon_kg"] == pytest.approx(5.0)
    assert bins["低速档 <20"]["ef_g_km"] == pytest.approx(300.0)
    assert bins["低速档 <20"]["avg_speed_trip_kmh"] == pytest.approx(10.0)
    assert bins["低速档 <20"]["avg_speed_kmh"] == pytest.approx(5.0)  # 时间加权
    assert bins["中速档 40-60"]["avg_speed_kmh"] == pytest.approx(25.0)

    clusters = {r["cluster_id"]: r for r in audit["by_cluster"]}
    assert set(clusters) == {0, 1}
    assert clusters[0]["trips"] == 1
    assert clusters[1]["carbon_kg"] == pytest.approx(5.0)


def test_aggregation_contract_forbids_averaging_ratios():
    seg, assignments, ledger = _frames()
    _, audit = sm.build_master(seg, assignments, ledger, kpi_total_kg=15.0)
    contract = audit["aggregation_contract"]
    assert "carbon_kg" in contract["additive"]
    assert contract["non_additive"]["ef_g_km"].startswith("1000 * sum(carbon_kg)")
    assert "直接求平均" in contract["do_not"]


def test_missing_ledger_columns_raise():
    seg, assignments, ledger = _frames()
    with pytest.raises(ValueError):
        sm.build_master(seg, assignments, ledger.drop(columns=["kg_co2"]),
                        kpi_total_kg=15.0)


def test_kpi_mismatch_is_reported_not_hidden():
    seg, assignments, ledger = _frames()
    _, audit = sm.build_master(seg, assignments, ledger, kpi_total_kg=14.0)
    assert audit["reconciliation"]["status"] == "mismatch"
    assert audit["reconciliation"]["delta_kg"] == pytest.approx(1.0)
    assert audit["reconciliation"]["matches"] is False


def test_schema_registration():
    assert schema.RESULT_FILES["jd_segment_master"] == sm.RESULT_MASTER
    assert schema.RESULT_FILES["jd_segment_master_audit"] == sm.RESULT_AUDIT
