# -*- coding: utf-8 -*-
"""最后一公里测试：可行性、时间窗重排、碳口径、顺序约束。"""
from __future__ import annotations

import math

import pytest

from src.rl.lastmile import (DIESEL_KG_PER_KM, EV_KG_PER_KM, Instance, Stop,
                             LastMileSolver, feasible, ga_hc, make_instance,
                             residual_instance, route_metrics)


def test_make_instance_and_ga_feasible():
    inst = make_instance(8, seed=3)
    assert inst.n == 8
    route, metrics = ga_hc(inst, seed=3, pop=30, gens=40)
    assert sorted(route) == list(range(1, 9))
    assert metrics["late_min"] == pytest.approx(0.0, abs=1e-6)
    assert metrics["cost"] > 0


def test_route_metrics_time_and_carbon():
    inst = Instance(stops=[
        Stop(0, 0.0, 0.0, 0.0, (0.0, 1440.0), 0.0),
        Stop(1, 3.0, 4.0, 10.0, (0.0, 1440.0)),   # 5 km
    ])
    m = route_metrics(inst, [1])
    # 往返 10 km，25 km/h
    assert m["km"] == pytest.approx(10.0, rel=1e-9)
    assert m["time_min"] == pytest.approx(10.0 / 25.0 * 60.0 + 8.0, rel=1e-9)
    assert m["carbon_kg"] == pytest.approx(10.0 * EV_KG_PER_KM, rel=1e-9)


def test_diesel_mode_carbon():
    inst = Instance(stops=[
        Stop(0, 0.0, 0.0, 0.0, (0.0, 1440.0), 0.0),
        Stop(1, 1.0, 0.0, 10.0, (0.0, 1440.0)),
    ], mode="diesel")
    m = route_metrics(inst, [1])
    assert m["carbon_kg"] == pytest.approx(2.0 * DIESEL_KG_PER_KM, rel=1e-9)


def test_late_penalty_shows_in_cost():
    inst = Instance(stops=[
        Stop(0, 0.0, 0.0, 0.0, (0.0, 1440.0), 0.0),
        Stop(1, 5.0, 0.0, 10.0, (0.0, 1.0)),   # 12 min 到，窗 1 min → 迟到
    ])
    m = route_metrics(inst, [1])
    assert m["late_min"] > 0
    assert m["cost"] > m["time_min"]


def test_residual_instance_shift():
    inst = make_instance(6, seed=5)
    served = [1, 2]
    residual, mapping = residual_instance(inst, served, current=2, elapsed_min=30.0)
    assert residual.n == 4
    assert 1 not in mapping.values() and 2 not in mapping.values()
    # 场站移到 current 的位置
    assert residual.stops[0].x == pytest.approx(inst.stops[2].x)
    # 时间窗整体平移
    src = inst.stops[3]
    dst = residual.stops[mapping_inv(mapping, 3)]
    assert dst.window[0] == pytest.approx(max(0.0, src.window[0] - 30.0))
    assert dst.window[1] == pytest.approx(src.window[1] - 30.0)


def mapping_inv(mapping, original):
    for new, old in mapping.items():
        if old == original:
            return new
    raise KeyError(original)


def test_solver_plan_and_replan():
    inst = make_instance(8, seed=6)
    solver = LastMileSolver(inst, seed=6)
    plan = solver.plan()
    assert len(plan.route) == 8
    served = plan.route[:3]
    stops = list(inst.stops) + [Stop(inst.n + 1, 5.5, 1.0, 10.0, (40.0, 120.0))]
    new_inst = Instance(stops=stops, mode=inst.mode)
    elapsed = route_metrics(new_inst, served)["time_min"]
    replanned = solver.replan(new_inst, served, current=served[-1], elapsed_min=elapsed)
    assert all(c not in served for c in replanned.route)
    assert sorted(replanned.route) == sorted(set(range(1, inst.n + 1)) - set(served) | {inst.n + 1})


def test_precedence_feasible():
    inst = Instance(stops=[
        Stop(0, 0.0, 0.0, 0.0, (0.0, 1440.0), 0.0),
        Stop(1, 1.0, 0.0, 0.0, (0.0, 1440.0)),
        Stop(2, 2.0, 0.0, 0.0, (0.0, 1440.0), must_precede=(1,)),
    ])
    assert feasible(inst, [1, 2]) is False
    assert feasible(inst, [2, 1]) is True
