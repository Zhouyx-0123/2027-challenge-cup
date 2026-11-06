# -*- coding: utf-8 -*-
"""k-means 的三个用途：状态抽象、载重分箱、k_ij 标定。"""
from __future__ import annotations

import numpy as np
import pytest

from src.rl import clustering


def _blobs(seed: int = 0):
    rng = np.random.default_rng(seed)
    centers = np.array([[0.0, 0.0], [5.0, 5.0], [10.0, 0.0]])
    rows = []
    labels = []
    for index, center in enumerate(centers):
        rows.append(center + rng.normal(0, 0.3, size=(40, 2)))
        labels.extend([index] * 40)
    return np.vstack(rows), np.asarray(labels)


def test_kmeans_separates_blobs_and_reports_inertia():
    X, truth = _blobs()
    result = clustering.kmeans(X, 3, seed=1)
    assert result["labels"].shape == (len(X),)
    assert result["inertia"] > 0
    # 每个簇只含一个真类（允许标签编号不同）
    for cluster in range(3):
        members = truth[result["labels"] == cluster]
        assert len(set(members.tolist())) == 1
    assert result["silhouette"] > 0.8


def test_kmeans_is_reproducible_with_same_seed():
    X, _ = _blobs()
    first = clustering.kmeans(X, 3, seed=42)
    second = clustering.kmeans(X, 3, seed=42)
    assert np.allclose(first["centroids"], second["centroids"])
    assert np.array_equal(first["labels"], second["labels"])


def test_kmeans_rejects_bad_k():
    X, _ = _blobs()
    with pytest.raises(ValueError):
        clustering.kmeans(X, 0)
    with pytest.raises(ValueError):
        clustering.kmeans(X, len(X) + 1)
    with pytest.raises(ValueError):
        clustering.kmeans(np.array([[np.nan, 1.0], [0.0, 1.0]]), 2)


def test_silhouette_bounds():
    X, labels = _blobs()
    assert -1.0 <= clustering.silhouette(X, labels) <= 1.0
    assert clustering.silhouette(X, np.zeros(len(X), dtype=int)) == 0.0


def test_load_bin_clusterer_orders_edges_and_assigns():
    loads = np.concatenate([
        np.random.default_rng(0).normal(4.0, 0.3, 50),
        np.random.default_rng(1).normal(16.0, 0.5, 50),
        np.random.default_rng(2).normal(30.0, 0.6, 50),
    ])
    clusterer = clustering.LoadBinClusterer(k=3, seed=0).fit(loads)
    edges = clusterer.edges
    assert len(edges) == 2 and edges[0] < edges[1]
    assert clusterer.assign(2.0) == 0
    assert clusterer.assign(16.0) == 1
    assert clusterer.assign(45.0) == 2
    # 边界属于上一箱（edges 属性做了四舍五入，用未取整的原值测）
    assert clusterer.assign(clusterer.edges_[0]) == 1


def test_load_bin_clusterer_needs_fit_and_enough_samples():
    with pytest.raises(ValueError):
        clustering.LoadBinClusterer(k=4).fit([1.0, 2.0])
    with pytest.raises(RuntimeError):
        clustering.LoadBinClusterer(k=2).assign(3.0)


def test_state_quantizer_roundtrip_and_dimension_guard():
    rng = np.random.default_rng(5)
    X = rng.uniform(0, 1, size=(120, 5))
    quantizer = clustering.StateQuantizer(k=6, seed=1, features=list("abcde")).fit(X)
    index, distance = quantizer.encode(X[0])
    assert 0 <= index < 6
    assert distance >= 0.0
    assert quantizer.encode_many(X).shape == (120,)
    with pytest.raises(ValueError):
        quantizer.encode([0.1, 0.2])
    payload = quantizer.to_dict()
    restored = clustering.StateQuantizer.from_dict(payload)
    assert restored.encode(X[0])[0] == index


def test_state_quantizer_same_state_maps_to_same_cluster():
    rng = np.random.default_rng(9)
    X = rng.uniform(0, 1, size=(200, 5))
    quantizer = clustering.StateQuantizer(k=5, seed=3).fit(X)
    point = X[7]
    assert quantizer.encode(point)[0] == quantizer.encode(point.copy())[0]


def test_calibrate_k_table_takes_median_ratio():
    samples = []
    for _ in range(5):
        samples.append({"road_class": 2, "load_t": 18.0,
                        "measured_kg": 1.20, "predicted_kg": 1.00})
    samples.append({"road_class": 2, "load_t": 18.0,
                    "measured_kg": 50.0, "predicted_kg": 1.00})  # 异常样本
    samples.append({"road_class": 3, "load_t": 4.5,
                    "measured_kg": 1.0, "predicted_kg": 1.0})
    result = clustering.calibrate_k_table(samples, [5.0, 15.0], min_group_samples=3)
    assert result["table"]["2"]["2"] == pytest.approx(1.2)
    # 只有 1 条样本的组回退 1.0 并被点名
    assert result["table"]["3"]["0"] == 1.0
    assert any(item["road_class"] == 3 for item in result["fallback_groups"])


def test_calibrate_k_table_clamps_extreme_ratios():
    samples = [{"road_class": 1, "load_t": 10.0, "measured_kg": 9.0,
                "predicted_kg": 1.0} for _ in range(4)]
    result = clustering.calibrate_k_table(samples, [5.0], min_group_samples=3,
                                          floor=0.5, ceiling=2.0)
    assert result["table"]["1"]["1"] == pytest.approx(2.0)
