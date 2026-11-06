# -*- coding: utf-8 -*-
"""OBD 稀疏校准的契约测试。"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from src.obd_calibration import (
    CALIBRATION_VERSION,
    allocate_samples,
    apply_calibration,
    build_prior_table,
    calibrate,
    load_samples,
    required_sample_size,
)


def _master() -> pd.DataFrame:
    return pd.DataFrame([
        {"trip_id": "T1", "passed": True, "speed_kmh": 50.0, "load_t": 10.0,
         "distance_km": 100.0, "carbon_kg": 10.0},
        {"trip_id": "T2", "passed": True, "speed_kmh": 55.0, "load_t": 12.0,
         "distance_km": 100.0, "carbon_kg": 30.0},
        {"trip_id": "T3", "passed": False, "speed_kmh": 50.0, "load_t": 10.0,
         "distance_km": 100.0, "carbon_kg": 99.0},
    ])


def _prior() -> pd.DataFrame:
    return pd.DataFrame([{
        "speed_bin": "40-60", "load_group": "light",
        "prior_trips": 10, "prior_km": 10000.0,
        "prior_carbon_kg": 10000.0, "prior_ef_g_km": 1000.0,
    }])


def _samples(n: int = 10, *, demo: bool = False) -> pd.DataFrame:
    return pd.DataFrame([
        {"speed_kmh": 50.0, "load_t": 10.0, "distance_km": 100.0,
         "carbon_kg": 200.0, "is_demo": demo}
        for _ in range(n)
    ])


def test_build_prior_table_is_distance_weighted() -> None:
    prior = build_prior_table(_master())
    row = prior[(prior["speed_bin"] == "40-60") & (prior["load_group"] == "light")].iloc[0]
    assert int(row["prior_trips"]) == 2
    assert row["prior_km"] == pytest.approx(200.0)
    assert row["prior_carbon_kg"] == pytest.approx(40.0)
    assert row["prior_ef_g_km"] == pytest.approx(200.0)
    assert set(prior["speed_bin"]) == {"<20", "20-40", "40-60", "60-80", ">=80"}
    assert set(prior["load_group"]) == {"light", "heavy"}


def test_build_prior_table_requires_columns() -> None:
    with pytest.raises(ValueError, match="缺列"):
        build_prior_table(pd.DataFrame([{"trip_id": "T1"}]))


def test_prior_only_keeps_every_factor_at_one() -> None:
    table, audit = calibrate(_prior(), None)
    assert len(table) == 1
    assert table.iloc[0]["status"] == "prior_only"
    assert table.iloc[0]["factor"] == pytest.approx(1.0)
    assert audit["version"] == CALIBRATION_VERSION
    assert audit["does_not_change_e0"] is True
    assert audit["n_prior_only_cells"] == 1
    assert audit["n_calibrated_cells"] == 0
    assert audit["weighted_factor"] == pytest.approx(1.0)


def test_calibration_shrinks_observation_toward_prior() -> None:
    table, audit = calibrate(_prior(), _samples(10))
    row = table.iloc[0]
    assert row["weight"] == pytest.approx(10.0 / 40.0)
    assert row["post_ef_g_km"] == pytest.approx(1250.0)
    assert row["factor"] == pytest.approx(1.25)
    assert row["ci_low_g_km"] < row["post_ef_g_km"] < row["ci_high_g_km"]
    assert row["status"] == "calibrated"
    assert audit["n_samples_used"] == 10
    assert audit["n_calibrated_cells"] == 1


def test_small_sample_is_provisional() -> None:
    table, _ = calibrate(_prior(), _samples(4))
    assert table.iloc[0]["status"] == "provisional"
    assert table.iloc[0]["weight"] == pytest.approx(4.0 / 34.0)


def test_demo_is_excluded_by_default_and_can_be_opted_in() -> None:
    table, audit = calibrate(_prior(), _samples(5, demo=True))
    assert table.iloc[0]["status"] == "prior_only"
    assert table.iloc[0]["factor"] == pytest.approx(1.0)
    assert audit["demo_only"] is True
    assert audit["n_samples_demo_excluded"] == 5
    assert audit["n_samples_used"] == 0

    table2, audit2 = calibrate(_prior(), _samples(5, demo=True), include_demo=True)
    assert table2.iloc[0]["status"] == "demo_fixture"
    assert table2.iloc[0]["factor"] > 1.0
    assert audit2["include_demo"] is True
    assert audit2["demo_only"] is True


def test_string_false_demo_flag_is_not_treated_as_true() -> None:
    samples = pd.DataFrame([{
        "speed_kmh": 50.0, "load_t": 10.0, "distance_km": 100.0,
        "carbon_kg": 200.0, "is_demo": "False",
    }])
    table, audit = calibrate(_prior(), samples)
    assert audit["n_samples_demo_excluded"] == 0
    assert audit["n_samples_used"] == 1
    assert table.iloc[0]["status"] == "provisional"


def test_required_sample_size_and_allocation() -> None:
    assert required_sample_size() == 25
    prior = build_prior_table(_master())

    plan60, audit60 = allocate_samples(prior, total=60)
    assert audit60["target_per_cell"] == 25
    assert audit60["allocation_mode"] == "balanced"
    assert plan60["allocated_n"].sum() == 60
    assert (plan60["allocated_n"] == 6).all()
    assert plan60["carbon_share"].sum() == pytest.approx(1.0)

    plan250, audit250 = allocate_samples(prior, total=250)
    assert plan250["allocated_n"].sum() == 250
    assert (plan250["allocated_n"] == 25).all()
    assert audit250["allocated"] == 250

    plan_carbon, audit_carbon = allocate_samples(prior, total=250, mode="carbon")
    assert audit_carbon["allocation_mode"] == "carbon"
    assert plan_carbon["allocated_n"].sum() == 250
    assert (plan_carbon["allocated_n"] >= 2).all()
    assert plan_carbon["allocated_n"].max() > 25


def test_load_samples_converts_fuel_litres() -> None:
    samples = load_samples(pd.DataFrame([{
        "speed_kmh": 50.0, "load_t": 10.0,
        "distance_km": 100.0, "fuel_l": 38.0228,
    }]))
    assert samples.iloc[0]["carbon_kg"] == pytest.approx(38.0228 * 2.63)


def test_apply_calibration_never_overwrites_e0() -> None:
    master = pd.DataFrame([
        {"trip_id": "T1", "passed": True, "speed_kmh": 50.0, "load_t": 10.0,
         "carbon_kg": 100.0},
        {"trip_id": "T2", "passed": False, "speed_kmh": 50.0, "load_t": 10.0,
         "carbon_kg": 50.0},
    ])
    table = pd.DataFrame([{
        "speed_bin": "40-60", "load_group": "light",
        "factor": 1.25, "status": "calibrated",
    }])
    out, audit = apply_calibration(master, table)
    valid = out[out["trip_id"] == "T1"].iloc[0]
    invalid = out[out["trip_id"] == "T2"].iloc[0]
    assert valid["carbon_kg"] == pytest.approx(100.0)
    assert valid["carbon_kg_calibrated"] == pytest.approx(125.0)
    assert valid["calibration_factor"] == pytest.approx(1.25)
    assert valid["calibration_status"] == "calibrated"
    assert invalid["calibration_status"] == "invalid"
    assert math.isnan(invalid["carbon_kg_calibrated"])
    assert audit["carbon_prior_kg"] == pytest.approx(100.0)
    assert audit["carbon_calibrated_kg"] == pytest.approx(125.0)
    assert audit["delta_kg"] == pytest.approx(25.0)
    assert audit["does_not_change_e0"] is True
