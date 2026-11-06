# -*- coding: utf-8 -*-
"""端到端：训练 → 评估 → 「学到了东西」的回归断言。

这组用例跑得比单测慢（约 3–5 s），但它是**唯一**能证明"算法确实在学"的测试：
只测公式和接口的话，把学习率改成 0 也能全绿。
"""
from __future__ import annotations

import pytest

from src.rl import settings
from src.rl.train import build_timeline, demo_requests, evaluate, train


@pytest.fixture(scope="module")
def trained():
    config = settings.load_config()
    config = dict(config)
    config["scenarios"] = dict(config.get("scenarios") or {}, departure_minutes=[480, 780, 1080],
                              loads_t=[18.0])
    net = __import__("src.rl.network", fromlist=["build_demo_network"]).build_demo_network()
    timeline = build_timeline(net, config)
    policy, curve = train(net, timeline, config, episodes=260, seed=5, verbose=False,
                          time_bias=1.15)
    return config, net, timeline, policy, curve


def test_training_produces_visits_and_curve(trained):
    _config, _net, _timeline, policy, curve = trained
    assert policy.td.visits.sum() > 0
    assert policy.q.visits.sum() > 0
    assert curve and curve[-1]["episode"] > curve[0]["episode"]


def test_learned_policy_matches_or_beats_static_baselines(trained):
    config, net, timeline, policy, _curve = trained
    requests = demo_requests(config)
    report = evaluate(net, timeline, requests, policy, config)
    summary = report["summary"]
    best_static = min(summary[name]["operation_cost"]
                      for name in ("static_time", "static_carbon", "expert_rule"))
    assert summary["learned"]["operation_cost"] <= best_static + 0.01
    # 随机基线必须明显更差，否则说明评估口径坏了（例如没算碳）
    assert summary["random"]["operation_cost"] > 2.0 * summary["learned"]["operation_cost"]


def test_td_eta_beats_model_under_injected_bias(trained):
    """注入 15% 时间偏差后，学习出的 ETA 必须比"纯模型 ETA"准。"""
    config, net, timeline, policy, _curve = trained
    requests = demo_requests(config)
    report = evaluate(net, timeline, requests, policy, config, time_bias=1.15)
    learned = report["summary"]["learned"]
    assert learned["eta_mae_min"] is not None
    assert learned["model_eta_mae_min"] is not None
    assert learned["eta_mae_min"] < learned["model_eta_mae_min"]
    assert learned["eta_p90_coverage"] > 0.7
