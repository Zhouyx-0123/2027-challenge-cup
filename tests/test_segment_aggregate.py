# -*- coding: utf-8 -*-
"""安全聚合入口测试：过滤、先加后除、双速口径、缺省键约束。"""
from __future__ import annotations

import pandas as pd
import pytest

from src import segment_aggregate as sa


def _master():
    return pd.DataFrame({
        "trip_id": [1, 2, 3],
        "plate_id": ["p1", "p2", "p3"],
        "month": ["2023-11", "2023-11", "2023-11"],
        "paper_bin": ["低速档 <20", "中速档 40-60", "invalid"],
        "cluster_id": pd.Series([0, 1, pd.NA], dtype="Int64"),
        "passed": [True, True, False],
        "distance_km": [10.0, 50.0, 5.0],
        "duration_h": [2.0, 2.0, 1.0],
        "carbon_kg": [3.0, 5.0, 7.0],
        "carbon_kg_phys": [4.0, 6.0, 8.0],
        "n_waybills": [2, 1, 1],
        "speed_kmh": [10.0, 50.0, 120.0],
        "load_t": [4.5, 18.0, 4.5],
    })


def test_groupby_bin_excludes_invalid_and_recomputes_ratios():
    out = sa.aggregate_master(_master(), "paper_bin")
    assert out["paper_bin"].tolist() == ["低速档 <20", "中速档 40-60"]
    low = out[out["paper_bin"] == "低速档 <20"].iloc[0]
    assert low["trips"] == 1
    assert low["carbon_kg"] == pytest.approx(3.0)
    assert low["ef_g_km"] == pytest.approx(300.0)
    assert low["avg_speed_trip_kmh"] == pytest.approx(10.0)
    assert low["avg_speed_kmh"] == pytest.approx(5.0)
    assert low["avg_load_trip_t"] == pytest.approx(4.5)
    assert low["carbon_share_pct"] == pytest.approx(37.5)


def test_weighted_speed_and_load_are_not_plain_averages():
    df = pd.DataFrame({
        "trip_id": [1, 2],
        "plate_id": ["p1", "p2"],
        "month": ["2023-11", "2023-11"],
        "paper_bin": ["低速档 <20", "低速档 <20"],
        "cluster_id": pd.Series([0, 0], dtype="Int64"),
        "passed": [True, True],
        "distance_km": [10.0, 30.0],
        "duration_h": [1.0, 1.0],
        "carbon_kg": [1.0, 3.0],
        "carbon_kg_phys": [1.0, 3.0],
        "n_waybills": [1, 1],
        "speed_kmh": [10.0, 30.0],
        "load_t": [4.0, 10.0],
    })
    row = sa.aggregate_master(df, "paper_bin").iloc[0]
    assert row["avg_speed_trip_kmh"] == pytest.approx(20.0)       # 趟等权
    assert row["avg_speed_kmh"] == pytest.approx(20.0)            # 40 km / 2 h
    assert row["avg_speed_dist_weighted_kmh"] == pytest.approx(25.0)  # (100+900)/40
    assert row["avg_load_trip_t"] == pytest.approx(7.0)           # (4+10)/2
    assert row["avg_load_t"] == pytest.approx(8.5)                # (40+300)/40
    assert row["ef_g_km"] == pytest.approx(100.0)                 # 4000 g / 40 km
    assert row["carbon_share_pct"] == pytest.approx(100.0)


def test_include_invalid_changes_denominator_but_is_explicit():
    valid = sa.aggregate_master(_master(), "paper_bin")
    all_rows = sa.aggregate_master(_master(), "paper_bin", include_invalid=True)
    assert valid["carbon_kg"].sum() == pytest.approx(8.0)
    assert all_rows["carbon_kg"].sum() == pytest.approx(15.0)
    assert all_rows["carbon_share_pct"].sum() == pytest.approx(100.0)


def test_null_cluster_is_kept_as_na_not_zero():
    df = _master()
    df["cluster_id"] = pd.Series([0, 1, pd.NA], dtype="Int64")
    out = sa.aggregate_master(df, "cluster_id", include_invalid=True)
    assert out["cluster_id"].isna().sum() == 1
    na_row = out[out["cluster_id"].isna()]
    assert len(na_row) == 1
    assert na_row["carbon_kg"].iloc[0] == pytest.approx(7.0)


def test_rejects_unknown_or_duplicate_keys():
    with pytest.raises(ValueError):
        sa.aggregate_master(_master(), "od_pair")
    with pytest.raises(ValueError):
        sa.aggregate_master(_master(), ["month", "month"])


def test_contract_columns_are_stable():
    out = sa.aggregate_master(_master(), ["month", "paper_bin"])
    assert out.columns.tolist() == ["month", "paper_bin"] + sa.AGGREGATE_COLUMNS
