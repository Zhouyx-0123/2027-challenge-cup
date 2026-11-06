# -*- coding: utf-8 -*-
"""配额（时变目标）与 15 分钟预报：单元行为 + 端到端可跑。"""
from __future__ import annotations

import numpy as np
import pytest

from src.rl import settings
from src.rl.agent import StateEncoder
from src.rl.env import TripRequest
from src.rl.forecast import TrafficForecaster, sample_points
from src.rl.network import TrafficConfig, TrafficSimulator, build_demo_network
from src.rl.policy import RoutePolicy
from src.rl.quota import FleetQuota, pressure_bucket, weights_for_pressure
from src.rl.train import evaluate_day, run_day


@pytest.fixture(scope="module")
def net():
    return build_demo_network()


@pytest.fixture(scope="module")
def timeline(net):
    return TrafficSimulator(net, TrafficConfig(seed=41)).simulate(0, 1440)


# ------------------------------------------------------------------ 配额
def test_pressure_starts_at_one_and_rises_when_overspending():
    quota = FleetQuota(budget_kg=30.0, trips_total=10)
    assert quota.pressure() == pytest.approx(1.0)
    quota.consume(6.0)          # 第一单就超平均配额(3.0)
    assert quota.pressure() > 1.0
    assert quota.bucket() >= 1


def test_pressure_is_clamped():
    quota = FleetQuota(budget_kg=10.0, trips_total=10)
    quota.consume(10.0)         # 预算耗尽
    assert quota.pressure() <= 3.0
    quota2 = FleetQuota(budget_kg=100.0, trips_total=10)
    quota2.consume(0.5)         # 花得极少
    assert quota2.pressure() >= 0.2


def test_weights_shift_toward_carbon_under_pressure():
    base = weights_for_pressure(1.0)
    tight = weights_for_pressure(2.5)
    assert sum(tight.values()) == pytest.approx(1.0)
    assert tight["carbon"] > base["carbon"]
    assert tight["time"] < base["time"]


def test_pressure_bucket_boundaries():
    assert pressure_bucket(0.5) == 0
    assert pressure_bucket(1.0) == 1
    assert pressure_bucket(1.2) == 2
    assert pressure_bucket(2.0) == 3


def test_quota_serialization_roundtrip():
    quota = FleetQuota(budget_kg=50.0, trips_total=8, consumed_kg=12.0, trips_done=2)
    restored = FleetQuota.from_dict(quota.to_dict())
    assert restored.pressure() == pytest.approx(quota.pressure())
    assert restored.remaining_kg == pytest.approx(quota.remaining_kg)


# ------------------------------------------------------------ 压力进状态
def test_pressure_extends_q_state_not_td_state(net):
    plain = settings.load_config()
    plain["quota"] = {"enabled": True, "pressure_buckets": 1}
    base = settings.build_policy(net, plain)
    config = settings.load_config()
    config["quota"] = {"enabled": True, "pressure_buckets": 4}
    with_quota = settings.build_policy(net, config)
    assert with_quota.encoder.n_states == base.encoder.n_states          # 预测层不变
    assert with_quota.encoder.n_q_states == base.encoder.n_q_states * 4  # 决策层×4
    assert with_quota.q.n_states == with_quota.encoder.n_q_states


def test_pressure_changes_q_state_index(net):
    config = settings.load_config()
    config["quota"] = {"enabled": True, "pressure_buckets": 4}
    policy = settings.build_policy(net, config)
    idx_a, sidx_a, meta_a = policy.encoder.encode([0.1, 0.2, 18.0, 0.0, 8.0], 0.0, 480, 0.8)
    idx_b, sidx_b, meta_b = policy.encoder.encode([0.1, 0.2, 18.0, 0.0, 8.0], 0.0, 480, 2.0)
    assert idx_a == idx_b                    # TD 状态不看配额
    assert sidx_a != sidx_b                  # Q 状态看配额
    assert meta_a["pressure_bucket"] == 0
    assert meta_b["pressure_bucket"] == 3


# ---------------------------------------------------------------- 预报
def test_forecast_returns_current_speed_at_zero_horizon(net, timeline):
    forecaster = TrafficForecaster(net)
    snapshot = timeline.at(480)
    sid = "E_G00_G01"
    segment = net.segment(sid)
    state = snapshot.state(sid)
    assert forecaster.speed_at(segment, state, 480, 0.0) == pytest.approx(state.speed_kmh)


def test_forecast_slows_down_when_demand_rises(net, timeline):
    """6:30 往 8:00 走是需求上行，预测速度应低于当前。"""
    forecaster = TrafficForecaster(net)
    snapshot = timeline.at(390)
    segment = net.segment("E_G00_G01")
    state = snapshot.state("E_G00_G01")
    predicted = forecaster.speed_at(segment, state, 390, 60)
    assert predicted <= state.speed_kmh + 1e-6


def test_forecast_beats_persistence(net, timeline):
    forecaster = TrafficForecaster(net)
    points = sample_points(net, range(420, 1140, 40))
    metrics = forecaster.speed_mae(timeline, points, horizon_min=15)
    assert metrics["forecast_mae_kmh"] < metrics["persistence_mae_kmh"] * 0.8


def test_forecast_travel_time_positive(net, timeline):
    forecaster = TrafficForecaster(net)
    snapshot = timeline.at(600)
    path = ["RU_G00_X1", "H_X1_X2", "H_X2_X3", "H_X3_X4", "RD_X4_G33"]
    value = forecaster.travel_time_min(path, snapshot, 600)
    assert 0 < value < 60


# ------------------------------------------------------------ 日级评估
def test_run_day_consumes_quota(net, timeline):
    requests = [TripRequest("G00", "G33", load_t=18.0, departure_minute=m)
                for m in (480, 780, 1140)]
    config = settings.load_config()
    policy = settings.build_policy(net, config)
    rows, quota = run_day(net, timeline, requests, config, budget_kg=12.0,
                          policy=policy)
    assert len(rows) == 3
    assert quota.consumed_kg == pytest.approx(sum(r["carbon_kg"] for r in rows), rel=1e-6)
    assert all("quota_before" in row for row in rows)


def test_evaluate_day_reports_three_policies(net, timeline):
    config = settings.load_config()
    config["quota"] = {"enabled": True, "budget_kg": 12.0, "pressure_buckets": 4}
    policy = settings.build_policy(net, config)
    requests = [TripRequest("G00", "G33", load_t=18.0, departure_minute=m)
                for m in (480, 780, 1140)]
    report = evaluate_day(net, timeline, requests, policy, config, budget_kg=12.0)
    for key in ("learned", "static_time", "static_carbon", "delta"):
        assert key in report
    assert report["learned"]["carbon_kg"] > 0
    assert 0 < report["learned"]["max_pressure"] <= 3.0
