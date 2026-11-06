# -*- coding: utf-8 -*-
"""前端数据接入与 P90 改动的影响面：改 P90 不碰学习，接数据只多一层胶水。"""
from __future__ import annotations

import json

import numpy as np
import pytest

from src.rl import settings
from src.rl.env import TripRequest
from src.rl.live import LiveTrip, apply_payload, states_from_payload
from src.rl.network import TrafficConfig, TrafficSimulator, build_demo_network


@pytest.fixture(scope="module")
def net():
    return build_demo_network()


@pytest.fixture(scope="module")
def policy(net):
    return settings.build_policy(net, settings.load_config())


DEFAULT_MESSAGE = {
    "minute": 480,
    "segments": [
        {"sid": "E_G00_G01", "speed_kmh": 12.0, "vc_ratio": 1.4},
        {"sid": "S_G00_G10", "speed_kmh": 30.0},
    ],
}


# ---------------------------------------------------------------- 数据接入
def test_payload_maps_to_segment_states(net):
    states, report = states_from_payload(net, DEFAULT_MESSAGE, minute=480)
    assert report["accepted"] == 2
    assert report["rejected"] == []
    assert states["E_G00_G01"].speed_kmh == pytest.approx(12.0)
    assert states["E_G00_G01"].vc_ratio == pytest.approx(1.4)
    assert states["E_G00_G01"].free_speed_kmh == pytest.approx(
        net.segment("E_G00_G01").free_speed_kmh)


def test_payload_accepts_dict_form(net):
    payload = {"segments": {"E_G00_G01": {"speed_kmh": 25.0}}}
    states, report = states_from_payload(net, payload, minute=0)
    assert report["accepted"] == 1 and "E_G00_G01" in states


def test_payload_reports_unknown_and_invalid(net):
    payload = {"segments": [
        {"sid": "NOT_A_SEGMENT", "speed_kmh": 30.0},
        {"sid": "E_G00_G01", "speed_kmh": 0},
        {"sid": "E_G00_G01"},
        {"speed_kmh": 40.0},
    ]}
    states, report = states_from_payload(net, payload, minute=0)
    assert report["accepted"] == 0
    assert report["ignored"] == ["NOT_A_SEGMENT"]
    assert {item["reason"] for item in report["rejected"]} == {
        "invalid_speed", "missing_speed", "missing_sid"}


def test_payload_rejects_bad_shape(net):
    with pytest.raises(ValueError):
        states_from_payload(net, {"segments": 3}, minute=0)


def test_sparse_push_is_queryable(net):
    timeline = TrafficSimulator(net, TrafficConfig(seed=3)).simulate(0, 60)
    states, _ = states_from_payload(net, DEFAULT_MESSAGE, minute=75)
    timeline.push(75, states)
    snapshot = timeline.at(75.4)          # 稀疏帧：退到 75 那一帧
    assert snapshot.state("E_G00_G01").speed_kmh == pytest.approx(12.0)
    # 没推的路段继承最近一帧，不能凭空消失
    assert snapshot.state("H_X1_X2").speed_kmh > 0
    assert len(snapshot.states) == len(net.segments)


def test_push_rejects_minute_before_start(net):
    timeline = TrafficSimulator(net, TrafficConfig(seed=3)).simulate(30, 60)
    with pytest.raises(ValueError):
        timeline.push(10, {})


# ------------------------------------------------------------ 实时会话闭环
def test_live_trip_decide_and_update(net, policy):
    trip = LiveTrip(net, policy, TripRequest("G00", "G33", load_t=18.0,
                                             departure_minute=480),
                    settings.load_config())
    trip.policy.start_episode()
    report = trip.traffic(DEFAULT_MESSAGE, minute=480)
    assert report["pushed"] == 2

    advice = trip.decide()
    assert advice["action"] in trip.env.candidates()
    assert advice["eta"]["eta_min"] > 0
    assert advice["segment_detail"]

    before = int(policy.td.visits.sum())
    result = trip.report(advice["action"])
    assert result["update"]["td_error"] is not None
    assert int(policy.td.visits.sum()) > before


def test_live_trip_congestion_changes_recommendation(net, policy):
    """把高架推成堵死，建议必须换到地面——证明前端数据真的进了决策。"""
    trip = LiveTrip(net, policy, TripRequest("G00", "G33", load_t=18.0,
                                             departure_minute=480),
                    settings.load_config())
    baseline = trip.decide(strategy="balanced")
    assert not baseline["plan"]["path"][0].startswith("RU_")   # 自由流下走地面

    jam = {"segments": [
        {"sid": sid, "speed_kmh": 5.0, "vc_ratio": 2.0, "incident": True}
        for sid in trip.network.segments if trip.network.segment(sid).road_class == 2
    ]}
    trip.traffic(jam, minute=480)
    trip.policy.start_episode()
    advice = trip.decide(strategy="balanced")
    assert advice["plan"]["path"][0].startswith("RU_")          # 地面堵死 → 改走高架


# ---------------------------------------------------------------- P90 影响面
def test_set_eta_interval_does_not_touch_learners(net, policy):
    td_before = policy.td.V.copy()
    q_before = policy.q.Q1.copy()
    correction_before = policy.corrector.time_ratio.copy()

    info = policy.set_eta_interval(quantile=0.8, min_residuals=50, window=120)

    assert info["quantile"] == pytest.approx(0.8)
    assert info["window"] == 120
    assert np.allclose(policy.td.V, td_before)
    assert np.allclose(policy.q.Q1, q_before)
    assert np.allclose(policy.corrector.time_ratio, correction_before)


def test_p90_responds_to_quantile_only(net, policy):
    policy.restore_residuals([1.0] * 80)
    policy.set_eta_interval(quantile=0.9, min_residuals=40, window=80)
    assert policy._conformal_p90(10.0, 0.1) == pytest.approx((11.0, "conformal"))
    policy.set_eta_interval(quantile=0.6)
    low, source = policy._conformal_p90(10.0, 0.1)
    assert source == "conformal" and low == pytest.approx(11.0)


def test_p90_quantile_rejects_out_of_range(policy):
    with pytest.raises(ValueError):
        policy.set_eta_interval(quantile=1.5)
    with pytest.raises(ValueError):
        policy.set_eta_interval(quantile=0.1)


def test_config_overrides_saved_p90(net, policy):
    from src.rl.train import load_policy, save_policy
    target = settings.ROOT / "outputs" / "rl" / "p90_policy_test.json"
    save_policy(policy, target)
    config = json.loads(json.dumps(settings.load_config()))
    config["eta"]["p90_quantile"] = 0.95
    config["eta"]["min_residuals"] = 99
    restored = load_policy(target, config)
    target.unlink(missing_ok=True)
    assert restored.eta_p90_quantile == pytest.approx(0.95)
    assert restored.min_residuals == 99
