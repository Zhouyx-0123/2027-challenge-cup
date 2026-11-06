# -*- coding: utf-8 -*-
"""路网、交通演化与行程环境：可复现性、记账一致性、约束生效。"""
from __future__ import annotations

import numpy as np
import pytest

from src.rl import network
from src.rl.env import TripEnv, TripRequest
from src.rl.network import (RoadNetwork, RoadSegment, TrafficConfig, TrafficSimulator,
                            build_demo_network, demand_scale, dijkstra,
                            free_flow_shortest_km)


@pytest.fixture(scope="module")
def net():
    return build_demo_network()


@pytest.fixture(scope="module")
def timeline(net):
    return TrafficSimulator(net, TrafficConfig(seed=11)).simulate(0, 1440)


def test_demo_network_has_two_families(net):
    assert "G00" in net.nodes and "G33" in net.nodes
    out = net.out_edges("G00")
    assert any(sid.startswith("RU_") for sid in out)      # 高架入口
    assert any(sid.startswith("E_") or sid.startswith("S_") for sid in out)  # 地面
    grid = free_flow_shortest_km(net, "G00", "G33")
    highway, _ = dijkstra(net, "G00", "G33",
                          lambda seg: 1.0 if seg.road_class == 1 else 1e6)
    assert highway and grid > 0


def test_timeline_is_reproducible():
    net = build_demo_network()
    first = TrafficSimulator(net, TrafficConfig(seed=3)).simulate(0, 200)
    second = TrafficSimulator(net, TrafficConfig(seed=3)).simulate(0, 200)
    for minute in (0, 60, 120, 199):
        left = first.at(minute).states
        right = second.at(minute).states
        for sid in left:
            assert left[sid].speed_kmh == pytest.approx(right[sid].speed_kmh)


def test_different_seed_changes_incidents():
    net = build_demo_network()
    left = TrafficSimulator(net, TrafficConfig(seed=1)).simulate(0, 600)
    right = TrafficSimulator(net, TrafficConfig(seed=2)).simulate(0, 600)
    left_speeds = np.array([s.speed_kmh for s in left.at(600).states.values()])
    right_speeds = np.array([s.speed_kmh for s in right.at(600).states.values()])
    assert not np.allclose(left_speeds, right_speeds)


def test_demand_scale_has_peaks():
    assert demand_scale(480) > demand_scale(180)      # 早高峰 > 凌晨
    assert demand_scale(1080) > demand_scale(720)     # 晚高峰 > 正午
    assert 0.2 < demand_scale(0) < 2.0


def test_congestion_label_thresholds():
    assert network.congestion_label(0.1) == "畅通"
    assert network.congestion_label(0.3) == "缓行"
    assert network.congestion_label(0.6) == "拥堵"
    assert network.congestion_label(0.9) == "严重拥堵"


def test_segment_state_congestion_index():
    state = network.SegmentState(sid="x", minute=0, speed_kmh=25.0,
                                 vc_ratio=0.8, free_speed_kmh=50.0)
    assert state.congestion_index == pytest.approx(0.5)
    payload = state.to_dict(RoadSegment(sid="x", u="a", v="b", length_m=1000,
                                        road_class=2, free_speed_kmh=50.0,
                                        capacity_vph=1000))
    assert payload["travel_time_min"] == pytest.approx(2.4)


def test_network_rejects_bad_segments():
    with pytest.raises(ValueError):
        RoadNetwork([RoadSegment("a", "u", "v", 0.0, 2, 50.0, 1000)])
    with pytest.raises(ValueError):
        RoadNetwork([])
    with pytest.raises(ValueError):
        RoadNetwork([RoadSegment("a", "u", "v", 10.0, 2, 50.0, 1000),
                     RoadSegment("a", "v", "u", 10.0, 2, 50.0, 1000)])


def test_env_accounting_sums_outcomes(net, timeline):
    request = TripRequest("G00", "G33", load_t=18.0, departure_minute=480)
    env = TripEnv(net, timeline, request)
    assert env.candidates()
    path, _ = dijkstra(net, "G00", "G33", lambda seg: seg.length_km)
    for sid in path:
        assert not env.done
        env.step(sid)
    assert env.done
    assert env.total_time_min == pytest.approx(sum(o.time_min for o in env.outcomes))
    assert env.total_carbon_kg == pytest.approx(sum(o.carbon_kg for o in env.outcomes))
    assert env.total_carbon_kg > 0
    assert all(o.carbon_kg > 0 and o.ef_g_per_km > 0 for o in env.outcomes)
    assert env.progress() == pytest.approx(1.0)


def test_env_carbon_consistent_with_emission_module(net, timeline):
    from src.rl import emission
    request = TripRequest("G00", "G33", load_t=30.0, departure_minute=600)
    env = TripEnv(net, timeline, request)
    sid = env.candidates()[0]
    outcome = env.step(sid)
    segment = net.segment(sid)
    direct = emission.segment_carbon(segment.length_km, outcome.speed_kmh,
                                     vclass=3, load_t=30.0, k_factor=outcome.k_factor,
                                     grade_pct=segment.grade_pct)
    assert outcome.carbon_kg == pytest.approx(direct["co2_kg"], rel=1e-6)


def test_env_blocks_segments(net, timeline):
    request = TripRequest("G00", "G33", load_t=18.0, departure_minute=480)
    blocked = {"RU_G00_X1"}
    env = TripEnv(net, timeline, request, blocked=blocked)
    assert "RU_G00_X1" not in env.candidates()
    with pytest.raises(ValueError):
        env.step("RU_G00_X1")


def test_env_stops_at_step_limit(net, timeline):
    request = TripRequest("G00", "G33", load_t=18.0, departure_minute=480)
    env = TripEnv(net, timeline, request, max_steps=2)
    while not env.done:
        candidates = env.candidates()
        assert candidates, "还有剩余步数时不应无路可走"
        env.step(candidates[0])
    assert len(env.path) == 2


def test_env_bias_injection_changes_totals(net, timeline):
    request = TripRequest("G00", "G33", load_t=18.0, departure_minute=480)
    plain = TripEnv(net, timeline, request)
    biased = TripEnv(net, timeline, request, time_bias=1.2, carbon_bias=1.1)
    assert biased.time_bias == pytest.approx(1.2)
    assert biased.carbon_bias == pytest.approx(1.1)
    assert plain.total_time_min == pytest.approx(0.0)


def test_env_rejects_same_origin_destination(net, timeline):
    with pytest.raises(ValueError):
        TripEnv(net, timeline, TripRequest("G00", "G00"))


def test_dijkstra_returns_contiguous_path(net):
    path, cost = dijkstra(net, "G00", "G33", lambda seg: seg.length_km)
    assert path and cost > 0
    assert net.segment(path[0]).u == "G00"
    current = "G00"
    for sid in path:
        segment = net.segment(sid)
        assert segment.u == current
        current = segment.v
    assert current == "G33"
    assert cost == pytest.approx(sum(net.segment(sid).length_km for sid in path))


def test_dijkstra_unreachable_returns_empty(net):
    isolated = RoadNetwork([RoadSegment("iso", "A", "B", 100.0, 2, 50.0, 1000)])
    path, cost = dijkstra(isolated, "A", "Z", lambda seg: 1.0)
    assert path == [] and cost == float("inf")
