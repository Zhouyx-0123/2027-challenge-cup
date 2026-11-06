# -*- coding: utf-8 -*-
"""学习器：TD(λ) 值函数、Double Q、状态编码、归一化器。"""
from __future__ import annotations

import numpy as np
import pytest

from src.rl import agent
from src.rl.agent import (DEFAULT_STRATEGIES, MultiObjectiveTD, ObjectiveNormalizer,
                          StateEncoder, Strategy, StrategyQLearner)
from src.rl.clustering import StateQuantizer


def _fit_quantizer(seed: int = 0) -> StateQuantizer:
    rng = np.random.default_rng(seed)
    return StateQuantizer(k=4, seed=seed).fit(rng.uniform(0, 1, size=(100, 5)))


def test_normalizer_roundtrip_and_scalarize():
    normalizer = ObjectiveNormalizer(10.0, 5.0, 20.0)
    vector = np.array([5.0, 2.5, 10.0])
    assert np.allclose(normalizer.normalize(vector), [0.5, 0.5, 0.5])
    assert np.allclose(normalizer.denormalize(normalizer.normalize(vector)), vector)
    strategy = Strategy("t", "测试", {"time": 1.0, "carbon": 0.0, "cost": 0.0})
    assert strategy.scalarize([0.5, 0.5, 0.5]) == pytest.approx(0.5)
    restored = ObjectiveNormalizer.from_dict(normalizer.to_dict())
    assert np.allclose(restored.ref, normalizer.ref)


def test_normalizer_guards_zero_reference():
    normalizer = ObjectiveNormalizer(0.0, 0.0, 0.0)
    assert np.all(normalizer.ref > 0)


def test_td_learns_remaining_cost_on_chain():
    """三状态链：0→1→2→终点，真实剩余成本 = 6。γ=1 时必须收敛到 6。"""
    td = MultiObjectiveTD(n_states=3, gamma=1.0, alpha=0.25, lam=0.7)
    for _ in range(400):
        td.start_episode()
        td.update(0, [1.0, 0.1, 1.0], 1, terminal=False)
        td.update(1, [2.0, 0.2, 2.0], 2, terminal=False)
        td.update(2, [3.0, 0.3, 3.0], None, terminal=True)
    prediction = td.predict(0)
    assert prediction["remaining_time_min"] == pytest.approx(6.0, abs=0.2)
    assert prediction["remaining_carbon_kg"] == pytest.approx(0.6, abs=0.05)
    assert prediction["visits"] == pytest.approx(400, abs=1)


def test_td_zero_inflation_and_validation():
    td = MultiObjectiveTD(n_states=2)
    with pytest.raises(IndexError):
        td.update(5, [1.0, 0.1, 1.0])
    with pytest.raises(ValueError):
        td.update(0, [1.0, -0.1, 1.0])
    with pytest.raises(ValueError):
        td.update(0, [1.0, 0.1])


def test_td_interval_widens_with_noise():
    quiet = MultiObjectiveTD(n_states=1, gamma=1.0)
    noisy = MultiObjectiveTD(n_states=1, gamma=1.0)
    rng = np.random.default_rng(0)
    for _ in range(200):
        quiet.update(0, [5.0, 0.5, 5.0], None, terminal=True)
        noisy.update(0, [5.0 + rng.normal(0, 2.0), 0.5, 5.0], None, terminal=True)
    assert (noisy.predict(0)["time_p90_min"] - noisy.predict(0)["remaining_time_min"]
            > quiet.predict(0)["time_p90_min"] - quiet.predict(0)["remaining_time_min"])


def test_td_serialization_roundtrip():
    td = MultiObjectiveTD(n_states=3, gamma=1.0)
    for _ in range(20):
        td.update(0, [1.0, 0.1, 1.0], 1)
    restored = MultiObjectiveTD.from_dict(td.to_dict())
    assert np.allclose(restored.V, td.V)
    assert np.allclose(restored.abs_err, td.abs_err)
    assert restored.gamma == pytest.approx(1.0)


def test_q_learner_greedy_and_double_q_update():
    q = StrategyQLearner(n_states=2, n_actions=3, alpha=0.5, epsilon=0.0, double=True)
    q.Q1[0] = np.array([0.0, 2.0, 1.0])
    q.Q2[0] = np.array([0.0, 2.0, 1.0])
    assert q.select(0) == 1
    error = q.update(0, 1, -1.0, None, terminal=True)
    assert error == pytest.approx(-3.0)
    assert q.Q1[0, 1] < 2.0 or q.Q2[0, 1] < 2.0
    assert q.visits[0, 1] == 1


def test_q_learner_epsilon_one_explores():
    q = StrategyQLearner(n_states=1, n_actions=4, epsilon=1.0)
    picks = {q.select(0) for _ in range(200)}
    assert len(picks) >= 3


def test_q_learner_allowed_actions_and_epsilon_decay():
    q = StrategyQLearner(n_states=1, n_actions=3, epsilon=0.5, epsilon_min=0.05,
                         epsilon_decay=0.5)
    assert q.select(0, allowed=[2]) == 2
    q.decay_epsilon()
    assert q.epsilon == pytest.approx(0.25)
    q.decay_epsilon()
    assert q.epsilon == pytest.approx(0.125)
    q.decay_epsilon()
    assert q.epsilon == pytest.approx(0.0625)   # 还没跌破 epsilon_min
    for _ in range(5):
        q.decay_epsilon()
    assert q.epsilon == pytest.approx(0.05)     # 触底后不再下降


def test_q_confidence_monotone():
    q = StrategyQLearner(n_states=1, n_actions=2)
    low = q.confidence(0, 0)
    q.visits[0, 0] = 100
    assert q.confidence(0, 0) > low


def test_state_encoder_index_bounds_and_meta():
    encoder = StateEncoder(_fit_quantizer(), progress_bins=5, time_buckets=8)
    for progress, minute in ((0.0, 0.0), (0.5, 480.0), (0.999, 1439.0)):
        idx, sidx, meta = encoder.encode([0.1, 0.2, 18.0, 0.0, 8.0], progress, minute)
        assert 0 <= idx < encoder.n_states
        assert 0 <= sidx < encoder.quantizer.k * encoder.time_buckets
        assert 0 <= meta["progress_bin"] < 5
        assert 0 <= meta["time_bucket"] < 8


def test_state_encoder_is_deterministic():
    encoder = StateEncoder(_fit_quantizer(3))
    features = [0.3, 0.4, 12.0, -1.0, 9.0]
    assert encoder.encode(features, 0.2, 500)[0] == encoder.encode(features, 0.2, 500)[0]


def test_default_strategies_are_diverse():
    t = {s.sid: s for s in DEFAULT_STRATEGIES}
    assert set(t) == {"time", "carbon", "balanced", "smooth"}
    assert t["time"].weights["time"] > t["carbon"].weights["time"]
    assert t["carbon"].weights["carbon"] > t["time"].weights["carbon"]
