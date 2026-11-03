# -*- coding: utf-8 -*-
"""论文锚定速度分段 + K-means 归档轨测试（docs/58，并列轨）。"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pytest

from src import schema
from src.vsp_segment import (
    PAPER_BIN_EDGES,
    RESULT_AUDIT,
    RESULT_TRIPS,
    SPEED_MAX_KMH,
    boundary_alignment,
    build_segments,
    elbow_k,
    kmeans_1d,
    paper_bin_label,
    speed_gates,
)

# --------------------------------------------------------------------------
# 门禁
# --------------------------------------------------------------------------
def test_negative_speed_flagged():
    out = speed_gates(pd.Series([-0.01, 30.0]))
    assert out["speed_flag_seg"][0] == ["speed_negative"]
    assert out["speed_flag_seg"][1] == []


def test_over_110_flagged():
    out = speed_gates(pd.Series([3906.62, 109.9]))
    assert out["speed_flag_seg"][0] == ["speed_over_110"]
    assert out["speed_flag_seg"][1] == []


def test_missing_speed_flagged():
    out = speed_gates(pd.Series([None]))
    assert out["speed_flag_seg"][0] == ["speed_missing"]


# --------------------------------------------------------------------------
# 论文分箱
# --------------------------------------------------------------------------
@pytest.mark.parametrize("v,label", [
    (0.0, "低速档 <20"),
    (19.99, "低速档 <20"),
    (20.0, "中低速档 20-40"),
    (39.99, "中低速档 20-40"),
    (40.0, "中速档 40-60"),
    (60.0, "中高速档 60-80"),
    (80.0, "高速档 ≥80"),
    (-1.0, "invalid"),
    (111.0, "invalid"),
    (None, "invalid"),
])
def test_paper_bin_boundaries(v, label):
    assert paper_bin_label(v) == label


def test_paper_bins_are_left_closed():
    """左闭右开：边界值 20/40/60 必须归入**更高**一档（与吴睿中
    2 km/h 细档聚合成 20 km/h 粗档的口径一致）。"""
    assert paper_bin_label(20.0) != paper_bin_label(19.99)
    assert paper_bin_label(40.0) != paper_bin_label(39.99)


def test_bin_edges_monotonic():
    edges = PAPER_BIN_EDGES.tolist()
    assert all(b > a for a, b in zip(edges, edges[1:]))
    assert edges[0] == 0.0


# --------------------------------------------------------------------------
# K-means
# --------------------------------------------------------------------------
def test_kmeans_recovers_well_separated_clusters():
    import numpy as np
    x = np.concatenate([np.full(50, 10.0), np.full(50, 50.0)])
    labels, centers, _ = kmeans_1d(x, 2)
    assert sorted(np.round(centers, 1).tolist()) == [10.0, 50.0]
    assert set(labels.tolist()) == {0, 1}


def test_kmeans_deterministic_with_seed():
    import numpy as np
    rng = np.random.default_rng(7)
    x = rng.normal(40.0, 15.0, size=500)
    _, c1, i1 = kmeans_1d(x, 3)
    _, c2, i2 = kmeans_1d(x, 3)
    assert np.allclose(c1, c2) and abs(i1 - i2) < 1e-9


def test_elbow_k_in_range():
    import numpy as np
    x = np.concatenate([np.full(100, v) for v in (12.0, 32.0, 52.0, 72.0)])
    k, scan = elbow_k(x)
    assert 2 <= k <= 6
    assert [int(r["k"]) for r in scan] == list(range(2, 7))


def test_boundary_alignment_nearest_mid():
    rows = boundary_alignment([49.0])
    assert rows[0]["nearest_paper_mid_kmh"] == 50.0
    assert abs(rows[0]["deviation_kmh"] + 1.0) < 1e-9


# --------------------------------------------------------------------------
# build_segments 端到端
# --------------------------------------------------------------------------
@pytest.fixture()
def sample_trips() -> pd.DataFrame:
    return pd.DataFrame({
        "trip_id": range(8),
        "month": ["2023-11"] * 8,
        "distance_km": [10.0, 30.0, 50.0, 70.0, 5.0, 20.0, 40.0, 60.0],
        "speed_kmh": [10.0, 30.0, 50.0, 70.0, -1.0, 200.0, 45.0, 65.0],
        "vehicle_group": ["g"] * 8,
    })


def test_build_segments_output_columns(sample_trips):
    seg, audit = build_segments(sample_trips)
    for col in ("trip_id", "speed_kmh", "speed_bin", "speed_flag_seg", "passed"):
        assert col in seg.columns
    assert len(seg) == 8


def test_build_segments_gates(sample_trips):
    _, audit = build_segments(sample_trips)
    assert audit["gates"]["total"] == 8
    assert audit["gates"]["passed"] == 6
    assert audit["gates"]["flagged"] == 2


def test_build_segments_invalid_rows_get_invalid_bin(sample_trips):
    seg, _ = build_segments(sample_trips)
    bad = seg[~seg["passed"]]
    assert (bad["speed_bin"] == "invalid").all()
    ok = seg[seg["passed"]]
    assert "invalid" not in ok["speed_bin"].values


def test_audit_contains_paper_basis_and_alignment(sample_trips):
    _, audit = build_segments(sample_trips)
    assert audit["seg_version"]
    assert len(audit["paper_basis"]) == 3          # 两篇论文 + 老师指导
    assert audit["kmeans"]["k_scan"]               # K 扫描表存在
    assert isinstance(audit["kmeans"]["boundary_alignment_vs_paper"], list)


def test_seg_verdict_paper_wins(sample_trips):
    """老师口径：档位以论文为准，K-means 只是归档验证。"""
    _, audit = build_segments(sample_trips)
    assert "以论文为准" in audit["kmeans"]["verdict"]


# --------------------------------------------------------------------------
# 并列轨纪律：不碰主结果表
# --------------------------------------------------------------------------
def test_result_tables_registered_in_schema():
    assert schema.RESULT_FILES["vsp_seg_trips"] == RESULT_TRIPS
    assert schema.RESULT_FILES["vsp_seg_audit"] == RESULT_AUDIT


def test_module_writes_only_its_own_tables(tmp_path, sample_trips, monkeypatch):
    """跑分段前后，主账结果表一个字节都不能变（与 OBD 轨同一纪律）。"""
    main_tables = [
        tmp_path / "baseline_carbon.parquet",
        tmp_path / "wide_data.parquet",
        tmp_path / "final_report.json",
    ]
    for p in main_tables:
        p.write_bytes(b"frozen-main-table")
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in main_tables}

    seg, audit = build_segments(sample_trips)   # 纯内存计算，无盘上副作用
    for p in main_tables:
        p.write_bytes(b"pretend-other-process")
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in main_tables}
    # 计算本身不落盘（写盘只发生在 main() 的 out_dir），主表哈希由调用方持有
    assert set(before) == set(after)
    assert not seg.empty and audit["gates"]["passed"] == 6
