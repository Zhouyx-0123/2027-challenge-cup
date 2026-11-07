# -*- coding: utf-8 -*-
"""K-means 3 簇碳规则接入 Q 成本模型的回归测试。"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from src.rl.historical import (ODOption, RouteCandidate, build_profile)
from src.rl.kmeans_rules import (CLUSTER_LABELS_3, MIN_CELL_N, build_cluster_rules,
                                 load_cluster_rules)
from src.rl.real_policy import fit_real_policy
from src.rl.real_world import solve_real_route


LOAD_SPECS = ((5.0, 0), (15.0, 1), (25.0, 2), (40.0, 3))
CLUSTER_SPECS = ((0, 20.0, 10.0), (1, 50.0, 100.0), (2, 70.0, 800.0))
# 行 = 簇，列 = 载重带 0-10 / 10-20 / 20-30 / 30-60
EF_TABLE = ((400.0, 600.0, 800.0, 1000.0),
            (500.0, 700.0, 900.0, 1100.0),
            (600.0, 800.0, 1000.0, 1200.0))


def _cluster_master() -> pd.DataFrame:
    rows = []
    for cluster_id, speed, distance in CLUSTER_SPECS:
        for load, load_index in LOAD_SPECS:
            # 簇 2 的轻载格故意留空，用来验证“无样本回退 5 档”。
            if cluster_id == 2 and load_index == 0:
                continue
            for index in range(MIN_CELL_N):
                rows.append({
                    "trip_id": "T%d-%d-%d" % (cluster_id, load_index, index),
                    "passed": True,
                    "cluster_id": cluster_id,
                    "speed_kmh": speed,
                    "distance_km": distance,
                    "load_t": load,
                    "carbon_kg": EF_TABLE[cluster_id][load_index] * distance / 1000.0,
                    "duration_h": distance / speed,
                })
    return pd.DataFrame(rows)


def _audit() -> dict:
    raw = np.asarray([[20.0, 10.0, 5.0], [50.0, 100.0, 15.0], [70.0, 800.0, 40.0]])
    center = np.asarray([45.0, 3.0, 15.0])
    scale = np.asarray([20.0, 1.5, 10.0])
    features = raw.copy()
    features[:, 1] = np.log1p(features[:, 1])
    scaled = (features - center) / scale
    return {
        "feature_set": "proxy_v1",
        "features": ["speed_mean_kmh", "log1p_distance_km", "load_t"],
        "k_selected": 3,
        "scaling": {"center": center.tolist(), "scale": scale.tolist()},
        "centers_raw": raw.tolist(),
        "centers_scaled": scaled.tolist(),
    }


def _option() -> ODOption:
    points = []
    for i in range(12):
        points.append("%.5f,%.5f" % (121.0 + i * 0.01, 31.0 + (i % 3) * 0.01))
    polyline = ";".join(points)
    candidates = (
        RouteCandidate(0, "综合", 12.0, 20.0, 5.0, 3),
        RouteCandidate(1, "避开收费", 15.0, 26.0, 0.0, 8),
        RouteCandidate(4, "躲避拥堵", 13.0, 18.0, 5.0, 2),
        RouteCandidate(6, "不走高速", 16.0, 30.0, 0.0, 12),
    )
    return ODOption(
        od_key="OD1", od_site="S1", od_pair="A|B", origin="A", dest="B",
        o_lng=121.0, o_lat=31.0, d_lng=121.11, d_lat=31.02,
        polyline=polyline, trips=10, total_km=120.0, total_carbon_kg=84.0,
        load_t=15.0, speed_kmh=30.0, duration_min=20.0,
        candidates=candidates, departure_minute=480.0)


def test_cluster_labels_and_classification():
    rules = build_cluster_rules(_cluster_master(), _audit())
    assert rules.n_clusters == 3
    assert rules.labels == CLUSTER_LABELS_3
    assert rules.ef_by_cluster == pytest.approx([700.0, 800.0, 1000.0])
    hit = rules.classify(45.0, 12.0, 15.0)
    assert hit is not None
    assert hit["cluster_id"] == 1
    assert hit["cluster_label"] == "中距中载"


def test_cluster_cell_requires_enough_samples_and_profile_falls_back():
    master = _cluster_master()
    rules = build_cluster_rules(master, _audit())
    cell = rules.cell(0, 25.0, 25.0)
    assert cell is not None
    assert cell["rule_cell"] == "C0/20-30"
    assert cell["ef_g_km"] == pytest.approx(800.0)
    assert cell["source"] == "kmeans_cluster_load"

    # 簇 2 / 0-10 t 没有样本：规则层返回 None，画像层回退 5 档。
    assert rules.cell(2, 5.0, 5.0) is None
    profile = build_profile(master, cluster_rules=rules)
    hit = profile.cell(45.0, 15.0, cluster_id=1)
    assert hit["source"] == "kmeans_cluster_load"
    assert hit["rule_cell"] == "C1/10-20"
    assert hit["ef_g_km"] == pytest.approx(700.0)
    fallback = profile.cell(45.0, 5.0, cluster_id=2)
    assert fallback["cluster_fallback"] is True
    assert fallback["rule_cell"] is None


def test_solve_uses_route_clusters_and_keeps_legacy_path():
    master = _cluster_master()
    profile = build_profile(master, cluster_rules=build_cluster_rules(master, _audit()))
    result = solve_real_route(_option(), profile, use_q=False)
    assert result["carbon_rule"] == "kmeans_cluster_load"
    assert result["route_clusters"]
    assert result["selected"]["cluster_id"] is not None
    assert result["selected"]["rule_cell"] is not None
    assert result["profile_cell"]["source"] == "kmeans_cluster_load"
    assert result["selected"]["carbon_kg"] == pytest.approx(
        result["selected"]["distance_km"] * result["profile_cell"]["ef_g_km"] / 1000.0,
        rel=0.02)

    legacy = solve_real_route(_option(), build_profile(master), use_q=False)
    assert legacy["carbon_rule"] == "speed_load"
    assert legacy["route_clusters"] == {}
    assert legacy["selected"]["cluster_id"] is None


def test_real_policy_audits_cluster_rule():
    master = _cluster_master()
    profile = build_profile(master, cluster_rules=build_cluster_rules(master, _audit()))
    options = [replace(_option(), od_key="OD%d" % i, departure_minute=480.0 + 30.0 * i)
               for i in range(8)]
    _policy, audit = fit_real_policy(options, profile, epochs=2, seed=7)
    assert audit["carbon_rule"] == "kmeans_cluster_load"
    assert audit["n_od_with_cluster"] == 8
    assert audit["cluster_labels"] == list(CLUSTER_LABELS_3)
    assert audit["cluster_ef_g_km"] == pytest.approx([700.0, 800.0, 1000.0])


def test_load_cluster_rules_returns_none_without_audit(tmp_path):
    assert load_cluster_rules(_cluster_master(), tmp_path) is None
