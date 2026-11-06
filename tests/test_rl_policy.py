# -*- coding: utf-8 -*-
"""决策服务：路由、建议 payload、重规划迟滞、学习与持久化。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from src.rl import settings
from src.rl.agent import ObjectiveNormalizer, Strategy
from src.rl.env import SegmentCostModel, TripEnv, TripRequest
from src.rl.network import TrafficConfig, TrafficSimulator, build_demo_network
from src.rl.policy import RecommendationService, RoutePolicy, plan_route


@pytest.fixture(scope="module")
def config():
    return settings.load_config()


@pytest.fixture(scope="module")
def net():
    return build_demo_network()


@pytest.fixture(scope="module")
def timeline(net, config):
    return TrafficSimulator(net, TrafficConfig(seed=21)).simulate(0, 1440)


@pytest.fixture(scope="module")
def policy(net, config, timeline):
    samples = settings.sample_feature_space(400, seed=7)
    built = settings.build_policy(net, config, state_samples=samples, seed=7)
    balanced = next(s for s in built.strategies if s.sid == "balanced")
    request = TripRequest("G00", "G33", load_t=18.0, departure_minute=480)
    # 直接跑 train.run_episode 训练几单，避免测试里复制一遍训练循环
    from src.rl.train import run_episode
    for _ in range(8):
        run_episode(net, timeline, request, policy=built, config=config,
                    learn=True, explore=True)
    return built


def _env(net, timeline, minute=480, load=18.0):
    return TripEnv(net, timeline, TripRequest("G00", "G33", load_t=load,
                                              departure_minute=minute))


def test_plan_route_is_contiguous_and_positive(net, timeline, config, policy):
    env = _env(net, timeline)
    balanced = next(s for s in policy.strategies if s.sid == "balanced")
    normalizer = settings.reference_normalizer(env, balanced, config)
    plan = plan_route(net, env.snapshot(), "G00", "G33", balanced, normalizer,
                      env.cost_model)
    assert plan.path
    assert net.segment(plan.path[0]).u == "G00"
    assert net.segment(plan.path[-1]).v == "G33"
    assert plan.time_min > 0 and plan.carbon_kg > 0 and plan.cost_yuan >= 0
    assert len(plan.rows) == len(plan.path)
    assert all(row["ef_g_per_km"] > 0 for row in plan.rows)


def test_plan_route_respects_blocked(net, timeline, config, policy):
    env = _env(net, timeline)
    balanced = next(s for s in policy.strategies if s.sid == "balanced")
    normalizer = settings.reference_normalizer(env, balanced, config)
    highway_entry = "RU_G00_X1"
    plan = plan_route(net, env.snapshot(), "G00", "G33", balanced, normalizer,
                      env.cost_model, blocked={highway_entry})
    assert highway_entry not in plan.path
    assert plan.path and net.segment(plan.path[-1]).v == "G33"


def test_recommendation_payload_schema(net, timeline, config, policy):
    env = _env(net, timeline)
    balanced = next(s for s in policy.strategies if s.sid == "balanced")
    normalizer = settings.reference_normalizer(env, balanced, config)
    service = RecommendationService(policy, normalizer)
    payload = service.recommend(env)
    for key in ("action", "strategy", "plan", "eta", "segment_detail", "reasons", "state"):
        assert key in payload
    assert payload["action"] in env.candidates()
    assert payload["eta"]["eta_min"] > 0
    assert payload["eta"]["eta_p90_min"] >= payload["eta"]["eta_min"]
    assert 0.0 <= payload["eta"]["confidence"] <= 1.0
    assert isinstance(payload["reasons"], list) and payload["reasons"]
    assert len(payload["segment_detail"]) == len(payload["plan"]["path"])
    assert payload["strategy"]["sid"] in {s.sid for s in policy.strategies}


def test_replan_hysteresis_keeps_plan_when_unchanged(net, timeline, config, policy):
    env = _env(net, timeline)
    balanced = next(s for s in policy.strategies if s.sid == "balanced")
    normalizer = settings.reference_normalizer(env, balanced, config)
    policy.start_episode()
    first = policy.act(env, normalizer)
    second = policy.act(env, normalizer)
    assert first["next_segment"] == second["next_segment"]
    assert second["kept_plan"] is True


def test_observe_updates_td_and_q(policy, net, timeline, config):
    from src.rl.train import run_episode
    request = TripRequest("G00", "G33", load_t=30.0, departure_minute=1080)
    before_td = policy.td.visits.sum()
    before_q = policy.q.visits.sum()
    run_episode(net, timeline, request, policy=policy, config=config, learn=True)
    assert policy.td.visits.sum() > before_td
    assert policy.q.visits.sum() > before_q


def test_predictor_weights_follow_measured_error(policy):
    policy.model_abs_err = 0.05
    policy.td_abs_err = 1.00
    weights = policy.predictor_weights()
    assert weights["model"] > 0.9
    policy.model_abs_err = 1.00
    policy.td_abs_err = 0.05
    assert policy.predictor_weights()["td"] > 0.9
    policy.model_abs_err = None
    policy.td_abs_err = None
    assert policy.predictor_weights()["model"] == pytest.approx(0.5)


def test_policy_serialization_roundtrip(policy, net, timeline):
    from src.rl.train import load_policy, save_policy
    target = settings.ROOT / "outputs" / "rl_test" / "policy.json"
    path = save_policy(policy, target, meta={"test": True})
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == "rl-policy-state-v1"
    restored = load_policy(path)
    path.unlink(missing_ok=True)
    assert restored.encoder.n_states == policy.encoder.n_states
    assert np.allclose(restored.td.V, policy.td.V)
    assert np.allclose(restored.q.Q1, policy.q.Q1)


def test_cost_model_applies_k_table(net, timeline, config):
    table, bins = settings.k_table(config)
    request = TripRequest("G00", "G33", load_t=18.0, departure_minute=480)
    plain = SegmentCostModel(request)
    weighted = SegmentCostModel(request, k_table=table, load_bins=bins)
    env = TripEnv(net, timeline, request)
    sid = next(sid for sid in net.out_edges("G00") if sid.startswith("E_"))
    segment = net.segment(sid)
    assert weighted.evaluate(segment, env.snapshot())["k_factor"] > 1.0
    assert plain.evaluate(segment, env.snapshot())["k_factor"] == 1.0


def test_policy_set_episode_resets_state(policy, net, timeline):
    env = _env(net, timeline)
    balanced = next(s for s in policy.strategies if s.sid == "balanced")
    normalizer = settings.reference_normalizer(env, balanced, config)
    policy.start_episode()
    decision = policy.act(env, normalizer)
    assert decision["next_segment"] in env.candidates()
    policy.start_episode()
    assert policy._trip_action is None
    assert policy._pending == []
