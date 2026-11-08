# -*- coding: utf-8 -*-
"""多维 K-means 聚类并列轨测试（SEG-CLUSTER-v1）。

覆盖：三维簇恢复、确定性、缺失不插补、车牌加盐哈希、OBD 六特征、
schema 注册、attach 不改变行数、样本不足跳过。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src import schema, segment_cluster as sc


def _proxy_frame(seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    specs = [
        ("苏US3126", 20.0, 30.0, 5.0, "轻型代表组（4.5t代理）"),
        ("沪A12345", 50.0, 120.0, 18.0, "中型代表组（18t代理）"),
        ("京B88888", 80.0, 500.0, 40.0, "重型代表组（40t代理）"),
    ]
    rows = []
    trip = 1
    for plate, speed, dist, load, group in specs:
        for _ in range(40):
            rows.append({
                "trip_id": trip,
                "plate": plate,
                "speed_kmh": speed + rng.normal(0, 1.5),
                "trip_km": max(1.0, dist + rng.normal(0, 4.0)),
                "load_t": load + rng.normal(0, 0.3),
                "vehicle_group": group,
                "carbon_kg": dist,
            })
            trip += 1
    return pd.DataFrame(rows)


def _obd_frame(seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    specs = [
        (20.0, 900.0, 20.0, 4.0, 0.20),
        (50.0, 1400.0, 45.0, 12.0, 0.08),
        (80.0, 1800.0, 70.0, 22.0, 0.03),
    ]
    rows = []
    sid = 1
    for speed, rpm, torque, fuel, idle in specs:
        for _ in range(20):
            rows.append({
                "segment_id": f"seg-{sid:03d}",
                "trip_id": "trip-A",
                "speed_mean_kmh": speed + rng.normal(0, 1.0),
                "rpm_mean": rpm + rng.normal(0, 30.0),
                "torque_actual_mean_pct": torque + rng.normal(0, 2.0),
                "fuel_rate_mean_lph": max(0.5, fuel + rng.normal(0, 0.5)),
                "idle_ratio": max(0.0, idle + rng.normal(0, 0.01)),
                "distance_km": 10.0 + rng.random() * 5.0,
            })
            sid += 1
    return pd.DataFrame(rows)


def test_recovers_three_proxy_clusters():
    out = sc.build_cluster_table(_proxy_frame(), grain="trip",
                                 feature_set="proxy_v1", n_init=5)
    audit = out["audit"]
    assert audit["status"] == "ok"
    assert audit["n_valid"] == 120
    assert audit["n_input"] == 120
    assert audit["cluster_sizes"] == [40, 40, 40]
    speeds = sorted(row[0] for row in audit["centers_raw"])
    assert speeds == pytest.approx([20.0, 50.0, 80.0], abs=5.0)
    assert out["profiles"]["n"].sum() == 120
    assert set(out["assignments"]["cluster_id"].dropna().unique()) == {0, 1, 2}
    assert 0.0 < audit["silhouette"] <= 1.0
    assert 0.8 <= audit["stability_ari"] <= 1.0
    assert all(0.0 <= row["silhouette"] <= 1.0 for row in audit["k_scan"])
    assert not set(audit["features"]) & {"carbon_kg", "kg_co2", "kg_co2_phys", "ef_g_km"}


def test_deterministic_same_seed():
    df = _proxy_frame()
    a = sc.build_cluster_table(df, grain="trip", n_init=5, seed=123)
    b = sc.build_cluster_table(df, grain="trip", n_init=5, seed=123)
    assert a["assignments"]["cluster_id"].tolist() == b["assignments"]["cluster_id"].tolist()
    assert a["audit"]["centers_raw"] == b["audit"]["centers_raw"]
    assert a["audit"]["inertia"] == b["audit"]["inertia"]


def test_missing_feature_not_imputed():
    df = _proxy_frame()
    df.loc[0, "load_t"] = np.nan
    out = sc.build_cluster_table(df, grain="trip", n_init=3)
    row = out["assignments"].iloc[0]
    assert pd.isna(row["cluster_id"])
    assert int(row["n_missing_features"]) == 1
    assert out["audit"]["n_valid"] == 119
    assert out["audit"]["missing_by_feature"]["load_t"] == 1


def test_speed_outlier_excluded_not_clustered():
    df = _proxy_frame()
    df.loc[0, "speed_kmh"] = 3906.62
    out = sc.build_cluster_table(df, grain="trip", n_init=3)
    assert pd.isna(out["assignments"].iloc[0]["cluster_id"])
    assert out["audit"]["n_invalid"] == 1
    assert out["audit"]["exclusion_by_reason"]["speed_out_of_range"] == 1
    assert out["audit"]["n_valid"] == 119


def test_plate_hash_normalises_and_hides_raw():
    assert sc.plate_id(" 苏US3126 ") == sc.plate_id("苏us3126")
    assert sc.plate_id("苏US3126") != sc.plate_id("沪A12345")
    assert sc.plate_id(None) is None
    assert sc.plate_id("   ") is None
    out = sc.build_cluster_table(_proxy_frame(), grain="trip", n_init=2)
    pm = out["plate_map"]
    assert "plate" in pm.columns and "plate_id" in pm.columns
    assert set(pm["plate"]) == {"苏US3126", "沪A12345", "京B88888"}
    assert "plate" not in out["assignments"].columns


def test_obd_feature_set_uses_six_features_and_segment_grain():
    out = sc.build_cluster_table(_obd_frame(), feature_set="obd_v1", n_init=5)
    audit = out["audit"]
    assert audit["grain"] == "segment"
    assert audit["features"] == [
        "speed_mean_kmh", "rpm_mean", "torque_actual_mean_pct",
        "fuel_rate_mean_lph", "idle_ratio", "log1p_distance_km",
    ]
    assert "load_t" not in audit["features"]
    assert audit["cluster_sizes"] == [20, 20, 20]
    assert out["assignments"]["grain"].eq("segment").all()


def test_schema_registers_cluster_outputs():
    values = set(schema.RESULT_FILES.values())
    for name in (sc.RESULT_CLUSTERS, sc.RESULT_PROFILES,
                 sc.RESULT_PLATE_MAP, sc.RESULT_AUDIT):
        assert name in values


def test_attach_cluster_ids_preserves_rows_and_maps():
    df = _proxy_frame().head(6)
    assignments = pd.DataFrame({
        "grain": "trip",
        "unit_id": [1, 2, 3, 4, 5, 6],
        "cluster_id": pd.array([0, 0, 1, 1, 2, 2], dtype="Int64"),
    })
    seg = pd.DataFrame({
        "trip_id": [1, 1, 2, 2, 3, 3, 4, 4],
        "segment_id": range(8),
        "distance_km": 1.0,
    })
    out = sc.attach_cluster_ids(seg, assignments, key="trip_id")
    assert len(out) == len(seg)
    assert out["trip_id"].tolist() == seg["trip_id"].tolist()
    assert out.loc[out["trip_id"] == 3, "cluster_id"].tolist() == [1, 1]
    assert out.loc[out["trip_id"] == 4, "cluster_id"].tolist() == [1, 1]


def test_small_sample_skips_without_imputation():
    df = _proxy_frame().head(5)
    out = sc.build_cluster_table(df, grain="trip", n_init=3, min_valid_rows=10)
    assert out["audit"]["status"] == "skipped"
    assert out["audit"]["cluster_sizes"] == []
    assert out["assignments"]["cluster_id"].isna().all()
    assert out["profiles"].empty
