# -*- coding: utf-8 -*-
"""四维路线进化与记忆库测试。"""
from __future__ import annotations

import numpy as np
import pytest

from src.routing.evolve_routes import (
    MEMORY_COLUMNS, RouteProblem, evolve_routes, load_route_memory, save_route_to_memory)


def _routes():
    return [
        {"route_id": 1, "distance": 100000, "duration": 6000,
         "tolls": 30, "carbon": 90},
        {"route_id": 2, "distance_km": 102, "duration_min": 98,
         "tolls": 31, "carbon_kg": 89},
        {"route_id": 3, "distance_km": 130, "duration_min": 80,
         "tolls": 55, "carbon_kg": 105},
    ]


def test_problem_has_four_objectives():
    problem = RouteProblem(np.array([100, 100, 50, 100]), 100, 0.8)
    assert problem.n_obj == 4
    out = {}
    problem._evaluate(np.array([[0.5, 0.5, 0.5, 0.5]]), out)
    assert out["F"].shape == (1, 4)
    # distance=50km, tolls=25, carbon=50kg, energy=40, carbon cost=5
    assert out["F"][0].tolist() == pytest.approx([70, 50, 50, 50])


def test_evolve_returns_required_schema_and_one_recommendation(tmp_path):
    logs = []
    result = evolve_routes(
        _routes(), explore_prob=0, n_gen=3, pop_size=8, seed=7,
        memory_path=tmp_path / "memory.csv", decision_log=logs)
    required = {"route_id", "distance", "duration", "tolls", "carbon", "cost",
                "is_exploration", "is_recommended", "exploration_strategy"}
    assert result and all(required <= set(route) for route in result)
    assert sum(route["is_recommended"] for route in result) == 1
    assert all(route["distance"] > 0 and route["duration"] > 0 for route in result)
    assert result[0]["is_recommended"] is True
    assert logs and "变异率" in logs[0]
    assert (tmp_path / "memory.csv").exists()


def test_epsilon_greedy_explores_when_costs_are_close(tmp_path):
    result = evolve_routes(
        _routes()[:2], explore_prob=1, n_gen=2, pop_size=4, seed=3,
        memory_path=tmp_path / "memory.csv")
    recommended = next(route for route in result if route["is_recommended"])
    assert recommended["is_exploration"] is True
    strategy = recommended["exploration_strategy"]
    assert strategy in {"epsilon_greedy_second_best", "nsga2_q_learning"} \
        or strategy.startswith("recombination_alpha_")


def test_memory_save_and_reload(tmp_path):
    path = tmp_path / "route_memory.csv"
    route = evolve_routes(
        _routes(), explore_prob=0, n_gen=2, pop_size=4, seed=1,
        memory_path=path)[0]
    saved = save_route_to_memory(route, path)
    saved["exploration_strategy"] = "manual_acceptance_test"
    # 采用时可覆盖策略字段，文件追加而不重写历史。
    save_route_to_memory(saved, path)
    memory = load_route_memory(path)
    assert list(memory.columns) == MEMORY_COLUMNS
    assert len(memory) == 2
    assert memory.iloc[-1]["exploration_strategy"] == "manual_acceptance_test"
    assert memory.iloc[-1]["route_id"] == route["route_id"]


def test_invalid_inputs_fail_clearly(tmp_path):
    with pytest.raises(ValueError, match="非空"):
        evolve_routes([], memory_path=tmp_path / "m.csv")
    with pytest.raises(ValueError, match="0–1"):
        evolve_routes(_routes(), explore_prob=1.2, memory_path=tmp_path / "m.csv")
    with pytest.raises(ValueError, match="carbon"):
        evolve_routes([{"distance": 1000, "duration": 60}],
                      memory_path=tmp_path / "m.csv")
