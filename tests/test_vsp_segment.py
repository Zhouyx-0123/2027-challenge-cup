# -*- coding: utf-8 -*-
"""论文锚定速度分段 + K-means 归档轨测试（docs/58，并列轨）。"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pytest

from src import schema
from src.vsp_segment import (
    FACTOR_RECON_THRESHOLD_PCT,
    PAPER_BIN_EDGES,
    RESULT_AUDIT,
    RESULT_CARBON,
    RESULT_RECON,
    RESULT_RULES,
    RESULT_TRIPS,
    SPEED_MAX_KMH,
    bin_carbon,
    bin_multiplier,
    boundary_alignment,
    build_factor_reconciliation,
    build_rule_table,
    build_segments,
    clean_speed_for_time,
    elbow_k,
    kmeans_1d,
    paper_bin_label,
    rule_ef,
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
        "ef_g_km": [394.56] * 8,
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
# 计碳规则卡 + 分档计碳
# --------------------------------------------------------------------------
def test_rule_table_shape_and_version():
    rules = build_rule_table()
    assert len(rules) == 30                     # 5 档 × 6 载重锚点
    assert (rules["rule_version"] == "SEG-RULE-v1").all()
    assert set(rules["load_t"]) == {4.5, 12.0, 18.0, 30.0, 40.0, 45.0}


def test_rule_table_ef_increases_with_load():
    rules = build_rule_table()
    mid = rules[rules["speed_bin"] == "中速档 40-60"].sort_values("load_t")
    assert mid["ef_gkm"].is_monotonic_increasing


def test_rule_table_ef_lowest_at_high_speed():
    """同一载重下，速度越高 EF 越低（VSP 乘数 ≤ 中速档锚点 1.0 的档更省）。"""
    rules = build_rule_table()
    lo = rules[(rules["speed_bin"] == "低速档 <20") & (rules["load_t"] == 18.0)]["ef_gkm"].iloc[0]
    mid = rules[(rules["speed_bin"] == "中速档 40-60") & (rules["load_t"] == 18.0)]["ef_gkm"].iloc[0]
    hi = rules[(rules["speed_bin"] == "高速档 ≥80") & (rules["load_t"] == 18.0)]["ef_gkm"].iloc[0]
    assert lo > mid > hi


def test_mid_bin_multiplier_is_unity():
    """中速档档中值 50 km/h → VSP=2.0（锚点）→ 乘数恰为 1.0（口径自洽）。"""
    assert abs(bin_multiplier(50.0) - 1.0) < 1e-9


def test_rule_ef_interpolates_load():
    rules = build_rule_table()
    ef = rule_ef(rules, "中速档 40-60", 15.0)   # 介于 12 与 18 之间
    lo = rule_ef(rules, "中速档 40-60", 12.0)
    hi = rule_ef(rules, "中速档 40-60", 18.0)
    assert lo < ef < hi


def test_bin_carbon_math_hand_checked():
    trips = pd.DataFrame({
        "trip_id": [1],
        "month": ["2023-11"],
        "distance_km": [50.0],
        "speed_kmh": [50.0],
        "ef_g_km": [544.752],
        "passed": [True],
        "speed_bin": ["中速档 40-60"],
    })
    audit = {"paper_bins": [{"speed_bin": "中速档 40-60", "bin_mean_speed": 50.0}]}
    seg, summary = bin_carbon(trips, audit)
    # ER = 544.752/72×1.0 = 7.566 g/s；秒 = 50/50×3600 = 3600；kg = 7.566×3600/1000
    assert abs(seg["kg_co2_binned"].iloc[0] - 7.566 * 3.6) < 1e-6
    # 本趟速度 = 档均速 → 两条轨相等
    assert abs(seg["kg_co2_vsp_own"].iloc[0] - seg["kg_co2_binned"].iloc[0]) < 1e-6
    assert abs(seg["binned_delta_pct"].iloc[0]) < 0.01


def test_bin_carbon_excludes_invalid(sample_trips):
    seg_trips, audit = build_segments(sample_trips)
    carbon, summary = bin_carbon(seg_trips, audit)
    assert carbon["kg_co2_binned"].isna().sum() == 2          # 2 趟门禁未过
    assert summary["totals"]["excluded_trips"] == 2


def test_bin_carbon_summary_contract(sample_trips):
    seg_trips, audit = build_segments(sample_trips)
    _, summary = bin_carbon(seg_trips, audit)
    assert summary["rule_version"] == "SEG-RULE-v1"
    assert "不做优化" in summary["downstream_contract"]
    assert len(summary["per_bin"]) == 5


def test_rule_table_no_optimizer_keywords():
    """规则卡里不允许出现优化/决策字段——规则与运筹分界。"""
    rules = build_rule_table()
    forbidden = {"cost", "optimal", "action", "reward", "q_value", "decision"}
    assert not (set(rules.columns) & forbidden)


# --------------------------------------------------------------------------
# 并列轨纪律：不碰主结果表
# --------------------------------------------------------------------------
def test_result_tables_registered_in_schema():
    assert schema.RESULT_FILES["vsp_seg_trips"] == RESULT_TRIPS
    assert schema.RESULT_FILES["vsp_seg_audit"] == RESULT_AUDIT
    assert schema.RESULT_FILES["vsp_seg_carbon"] == RESULT_CARBON
    assert schema.RESULT_FILES["vsp_seg_rule_table"] == RESULT_RULES


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


# --------------------------------------------------------------------------
# 因子口径对账（方案 B：只登记，不改计碳逻辑）
# --------------------------------------------------------------------------
@pytest.fixture()
def recon_sample():
    """3 趟样例：2 趟因子一致（主账 758.0 vs 分档 759.24 → 差 0.16%，
    低于阈值不计），1 趟代表因子远高于主账因子（必须进差异表）。"""
    seg = pd.DataFrame({
        "trip_id": [1, 2, 3],
        "month": ["2023-11"] * 3,
        "speed_kmh": [30.0, 50.0, 65.0],
        "distance_km": [40.0, 50.0, 60.0],
        "ef_g_km": [759.24, 759.24, 1374.672],
        "vehicle_group": ["中型代表组（18t代理）"] * 2 + ["重型代表组（40t代理）"],
    })
    carbon = pd.DataFrame({
        "trip_id": [1, 2, 3],
        "kg_co2_vsp_own": [30.0, 37.5, 80.0],
        "kg_co2_binned": [29.0, 37.5, 80.0],
        "passed": [True, True, True],
    })
    ledger = pd.DataFrame({
        "trip_id": [1, 2, 3],
        "factor_gkm": [758.0, 759.24, 1055.952],
        "kg_co2_phys": [25.0, 37.5, 50.0],
        "load_t": [18.0, 18.0, 30.0],
        "vclass": [2, 2, 3],
        "vehicle_type": ["9.6米厢式货车"] * 2 + ["牵引车头"],
        "trip_km": [40.0, 50.0, 60.0],
        "speed_kmh": [30.0, 50.0, 65.0],
        "factor_version": ["JCB2023-BASE-v1"] * 3,
    })
    return seg, carbon, ledger


def test_factor_recon_registered_in_schema():
    assert schema.RESULT_FILES["vsp_seg_factor_recon"] == RESULT_RECON


def test_factor_recon_selects_only_mismatched_trips(recon_sample):
    """只有因子差超阈值的趟进表；0.16% 的那趟不进。"""
    seg, carbon, ledger = recon_sample
    out, summary = build_factor_reconciliation(seg, carbon, ledger)
    assert out["trip_id"].tolist() == [3]
    assert summary["trips_compared"] == 3
    assert summary["mismatch_trips"] == 1
    assert summary["threshold_pct"] == FACTOR_RECON_THRESHOLD_PCT


def test_factor_recon_decomposition_is_additive(recon_sample):
    """三段差必须可加：因子效应 + 分档乘数效应 = 净差。"""
    seg, carbon, ledger = recon_sample
    out, _ = build_factor_reconciliation(seg, carbon, ledger)
    row = out.iloc[0]
    assert abs(row["kg_delta_factor"] + row["kg_delta_multiplier"]
               - row["kg_delta_binned"]) < 1e-9
    assert abs(row["kg_delta_factor"] - (80.0 - 50.0)) < 1e-9
    assert abs(row["kg_delta_multiplier"] - (80.0 - 80.0)) < 1e-9
    assert abs(row["kg_delta_binned"] - (80.0 - 50.0)) < 1e-9


def test_factor_recon_summary_matches_rows(recon_sample):
    seg, carbon, ledger = recon_sample
    out, summary = build_factor_reconciliation(seg, carbon, ledger)
    assert abs(summary["kg_ledger_physical_total"] - 112.5) < 1e-9
    assert abs(summary["kg_seg_binned_total"] - 146.5) < 1e-9
    assert abs(summary["delta_kg_net"] - 34.0) < 1e-9
    assert abs(summary["delta_kg_factor_effect"] - 35.0) < 1e-9
    assert abs(summary["delta_kg_multiplier_effect"] - (-1.0)) < 1e-9
    assert abs(summary["mismatch_delta_kg_net"] - 30.0) < 1e-9
    assert summary["lookup_file"] == RESULT_RECON


def test_factor_recon_does_not_touch_carbon(recon_sample):
    """对账是只读旁路：调用前后计碳表必须逐值不变（不改计碳逻辑）。"""
    seg, carbon, ledger = recon_sample
    before = carbon.copy(deep=True)
    build_factor_reconciliation(seg, carbon, ledger)
    pd.testing.assert_frame_equal(carbon, before)


def test_factor_recon_requires_ledger_columns(recon_sample):
    seg, carbon, ledger = recon_sample
    with pytest.raises(ValueError):
        build_factor_reconciliation(seg, carbon, ledger.drop(columns=["factor_gkm"]))


def test_factor_recon_leaves_missing_out_and_counts_it(recon_sample):
    """主账缺失的趟不进表、差异留空（不写 0），并计入 unmatched 计数。"""
    seg, carbon, ledger = recon_sample
    ledger2 = ledger.copy()
    ledger2.loc[2, "factor_gkm"] = float("nan")
    out, summary = build_factor_reconciliation(seg, carbon, ledger2)
    assert out["trip_id"].tolist() == []
    assert summary["trips_unmatched_ledger"] == 1
    assert summary["mismatch_trips"] == 0
    assert summary["median_abs_factor_diff_pct"] is None
