# -*- coding: utf-8 -*-
"""本轮两项优化的回归测试：模型残差修正层 + 共形 P90。"""
from __future__ import annotations

import numpy as np
import pytest

from src.rl import settings
from src.rl.agent import ObjectiveNormalizer, ResidualCorrector
from src.rl.env import SegmentCostModel, TripEnv, TripRequest
from src.rl.network import TrafficConfig, TrafficSimulator, build_demo_network
from src.rl.policy import RoutePolicy


@pytest.fixture(scope="module")
def net():
    return build_demo_network()


@pytest.fixture(scope="module")
def timeline(net):
    return TrafficSimulator(net, TrafficConfig(seed=31)).simulate(0, 1440)


def test_corrector_learns_time_ratio():
    corrector = ResidualCorrector(n_buckets=8, alpha=0.3, min_samples=3)
    for _ in range(12):
        corrector.observe(road_class=1, minute=480, model_time=2.0,
                          actual_time=2.6, model_carbon=1.0, actual_carbon=1.0)
    assert corrector.factor_time(1, 480) == pytest.approx(1.3, abs=0.01)
    assert corrector.counts[1, 2] == 12


def test_corrector_requires_min_samples():
    corrector = ResidualCorrector(min_samples=5)
    corrector.observe(1, 480, 2.0, 3.0)
    assert corrector.factor_time(1, 480) == 1.0     # 样本不足 → 不干预
    for _ in range(5):
        corrector.observe(1, 480, 2.0, 3.0)
    assert corrector.factor_time(1, 480) > 1.4


def test_corrector_clips_extreme_ratios():
    corrector = ResidualCorrector(min_samples=1, floor=0.5, ceiling=2.5)
    for _ in range(20):
        corrector.observe(2, 600, 1.0, 50.0, model_carbon=1.0, actual_carbon=0.0)
    assert corrector.factor_time(2, 600) == pytest.approx(2.5)
    for _ in range(20):
        corrector.observe(3, 600, 1.0, 0.01)
    assert corrector.factor_time(3, 600) == pytest.approx(0.5)


def test_corrector_splits_by_road_class_and_bucket():
    corrector = ResidualCorrector(min_samples=1)
    for _ in range(5):
        corrector.observe(1, 480, 1.0, 1.5)     # 早高峰高速
        corrector.observe(2, 480, 1.0, 1.0)     # 早高峰主干道（准）
        corrector.observe(1, 780, 1.0, 0.9)     # 平峰高速（模型偏保守）
    assert corrector.factor_time(1, 480) == pytest.approx(1.5, abs=0.02)
    assert corrector.factor_time(2, 480) == pytest.approx(1.0, abs=0.02)
    assert corrector.factor_time(1, 780) == pytest.approx(0.9, abs=0.02)
    assert corrector.factor_time(1, 120) == 1.0  # 没样本的时段不猜


def test_corrector_oracle_matches_bias_table():
    table = {"time": {"1": [1.0] * 2 + [1.3, 1.3]}, "carbon": {"4": [1.0] * 8}}
    oracle = ResidualCorrector.from_bias_table(table, n_buckets=8)
    assert oracle.factor_time(1, 480) == pytest.approx(1.3)
    assert oracle.factor_time(1, 120) == pytest.approx(1.0)
    assert oracle.factor_carbon(4, 480) == pytest.approx(1.0)


def test_corrector_serialization_roundtrip():
    corrector = ResidualCorrector(min_samples=1)
    for _ in range(6):
        corrector.observe(3, 540, 1.0, 1.2, 2.0, 2.2)
    restored = ResidualCorrector.from_dict(corrector.to_dict())
    assert np.allclose(restored.time_ratio, corrector.time_ratio)
    assert np.allclose(restored.carbon_ratio, corrector.carbon_ratio)
    assert np.array_equal(restored.counts, corrector.counts)


def test_cost_model_applies_correction(net, timeline):
    request = TripRequest("G00", "G33", load_t=18.0, departure_minute=480)
    corrector = ResidualCorrector(min_samples=1)
    for _ in range(3):
        corrector.observe(2, 480, 1.0, 1.5)
    model = SegmentCostModel(request, corrector=corrector)
    env = TripEnv(net, timeline, request)
    sid = next(sid for sid in net.out_edges("G00") if sid.startswith("E_"))
    segment = net.segment(sid)
    plain = model.evaluate(segment, env.snapshot(), apply_correction=False)
    corrected = model.evaluate(segment, env.snapshot())
    assert corrected["time_min"] == pytest.approx(1.5 * plain["time_min"], rel=1e-6)
    assert corrected["carbon_kg"] == pytest.approx(plain["carbon_kg"], rel=1e-9)
    assert corrected["correction_time"] == pytest.approx(1.5)


def test_env_bias_table_scales_only_actual(net, timeline):
    request = TripRequest("G00", "G33", load_t=18.0, departure_minute=480)
    table = {"time": {"2": [1.0, 1.0, 2.0, 1.0, 1.0, 1.0, 1.0, 1.0]}}
    plain = TripEnv(net, timeline, request)
    biased = TripEnv(net, timeline, request, bias_table=table)
    sid = next(sid for sid in net.out_edges("G00") if sid.startswith("E_"))
    left = plain.step(sid)
    right = biased.step(sid)
    assert right.model_time_min == pytest.approx(left.model_time_min, rel=1e-9)
    assert right.time_min == pytest.approx(2.0 * left.time_min, rel=1e-6)
    assert right.carbon_kg == pytest.approx(left.carbon_kg, rel=1e-9)


def test_conformal_p90_uses_residual_quantile(net):
    policy = settings.build_policy(net, settings.load_config())
    for _ in range(60):
        policy._residuals.append(2.0)     # 残差恒为 +2 min
    p90, source = policy._conformal_p90(blended=10.0, spread=0.1)
    assert source == "conformal"
    assert p90 == pytest.approx(12.0, abs=1e-9)
    policy.restore_residuals([])
    p90, source = policy._conformal_p90(blended=10.0, spread=0.4)
    assert source == "buffer"
    assert p90 == pytest.approx(11.0, abs=1e-9)


def test_predictor_weights_prefer_corrected_model(net):
    policy = settings.build_policy(net, settings.load_config())
    policy.model_abs_err = 0.06
    policy.td_abs_err = 0.28
    assert policy.predictor_weights()["model"] > 0.9
