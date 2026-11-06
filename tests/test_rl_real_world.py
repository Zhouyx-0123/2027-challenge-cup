# -*- coding: utf-8 -*-
"""真实高德路网、历史 EF 与四策略 Q 求解的回归测试。"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from src.rl.amap_network import build_real_network
from src.rl.historical import (RouteCandidate, ODOption, build_od_catalog,
                               build_profile)
from src.rl.real_policy import fit_real_policy, save_real_policy
from src.rl.real_world import load_policy_payload, solve_real_route


def _master():
    rows = []
    specs = [(10.0, 5.0, 400.0), (30.0, 15.0, 700.0),
             (50.0, 25.0, 900.0), (70.0, 35.0, 1300.0)]
    for index, (speed, load, ef) in enumerate(specs):
        km = 100.0
        rows.append({
            "trip_id": "T%d" % index, "passed": True, "speed_kmh": speed,
            "load_t": load, "distance_km": km, "carbon_kg": ef * km / 1000.0,
            "duration_h": km / speed,
        })
    return pd.DataFrame(rows)


def _option():
    points = []
    for i in range(12):
        points.append("%.5f,%.5f" % (121.0 + i * 0.01, 31.0 + (i % 3) * 0.01))
    polyline = ";".join(points)
    candidates = (
        RouteCandidate(0, "综合", 12.0, 20.0, 5.0, 3),
        RouteCandidate(1, "避开收费", 15.0, 26.0, 0.0, 8),
        RouteCandidate(4, "躲避拥堵", 13.0, 18.0, 5.0, 2),
        RouteCandidate(6, "不走高速", 16.0, 30.0, 0.0, 12),
    )
    return ODOption(
        od_key="OD1", od_site="S1", od_pair="A|B", origin="A", dest="B",
        o_lng=121.0, o_lat=31.0, d_lng=121.11, d_lat=31.02,
        polyline=polyline, trips=10, total_km=120.0, total_carbon_kg=84.0,
        load_t=15.0, speed_kmh=30.0, duration_min=20.0,
        candidates=candidates, departure_minute=480.0)


def test_profile_uses_distance_weighted_ef_and_falls_back():
    profile = build_profile(_master())
    cell = profile.cell(10.0, 5.0)
    assert cell["speed_bin"] == "<20"
    assert cell["load_bin"] == "0-10"
    assert cell["ef_g_km"] == pytest.approx(400.0)
    assert profile.n_trips == 4
    # 90 km/h / 40 t 没有样本，应该回退到载重带或全局，而不是 NaN。
    fallback = profile.cell(90.0, 40.0)
    assert fallback["ef_g_km"] > 0
    assert fallback["source"] != "cell"


def test_catalog_keeps_amap_geometry_and_median_departure():
    master = _master().iloc[:2].copy()
    baseline = pd.DataFrame({
        "trip_id": ["T0", "T1"], "od_site": ["S1", "S1"],
        "depart_ts": ["2026-01-01 06:30:00", "2026-01-01 08:30:00"],
    })
    route_od = pd.DataFrame([{
        "od_key": "OD1", "od_site": "S1", "od_pair": "A|B", "origin": "A", "dest": "B",
        "o_lng": 121.0, "o_lat": 31.0, "d_lng": 121.11, "d_lat": 31.02,
        "polyline": _option().polyline, "source": "amap_rest",
        "distance_km": 12.0, "duration_min": 20.0, "toll_yuan": 5.0,
    }])
    candidates = pd.DataFrame([
        {"od_key": "OD1", "strategy": c.strategy, "strategy_name": c.strategy_name,
         "distance_km": c.distance_km, "duration_min": c.duration_min,
         "toll_yuan": c.toll_yuan, "traffic_lights": c.traffic_lights,
         "source": "amap_rest"}
        for c in _option().candidates
    ])
    options, meta = build_od_catalog(master, baseline, route_od, candidates)
    assert meta["n_od"] == 1
    assert options[0].trips == 2
    assert options[0].departure_minute == pytest.approx(450.0)
    assert len(options[0].candidates) == 4

    # 没有几何的 OD 不应进入目录。
    route_od.loc[0, "polyline"] = "121.0,31.0"
    options, meta = build_od_catalog(master, baseline, route_od, candidates)
    assert options == []
    assert meta["n_od"] == 0


def test_network_has_four_parallel_connected_chains():
    option = _option()
    rn = build_real_network(option.polyline, (option.o_lng, option.o_lat),
                            (option.d_lng, option.d_lat), option.candidates,
                            departure_minute=480)
    assert len(rn.routes) == 4
    assert rn.n_segments >= 6
    # 每条策略一条链，链上节点连续；Dijkstra 从起点可达终点。
    from src.rl.network import dijkstra
    path, _cost = dijkstra(rn.network, "AMAP_ORIGIN", "AMAP_DEST", lambda seg: seg.length_m)
    assert path
    assert rn.route_of_path(path) in {r.route_id for r in rn.routes}
    assert rn.timeline.at(480).minute == 480


def test_solve_route_returns_four_strategies_and_map_geometry():
    option = _option()
    profile = build_profile(_master())
    result = solve_real_route(option, profile, use_q=False)
    assert len(result["solutions"]) == 4
    assert result["selected"]["carbon_kg"] > 0
    assert result["selected"]["time_min"] > 0
    assert result["selected_is_q"] is False
    assert len(result["map_route"]["polyline"]) == 12
    assert result["map_route"]["route_id"].startswith("AMAP-S")
    assert result["notes"]
    # 碳必须来自历史 EF，而不是合成网默认 3.16 WTW。
    assert result["selected"]["carbon_kg"] == pytest.approx(
        result["selected"]["distance_km"] * result["profile_cell"]["ef_g_km"] / 1000.0,
        rel=0.02)


def test_solve_route_rejects_bad_load():
    option = _option()
    profile = build_profile(_master())
    with pytest.raises(ValueError):
        solve_real_route(option, profile, load_t=0.0, use_q=False)

def test_real_policy_fits_roundtrips_and_solves(tmp_path):
    profile = build_profile(_master())
    options = [replace(_option(), od_key="OD%d" % i, departure_minute=480.0 + 30.0 * i)
               for i in range(8)]
    policy, audit = fit_real_policy(options, profile, epochs=2, seed=7)
    assert audit["grain"] == "real_od_strategy_bandit"
    assert audit["n_od"] == 8
    assert audit["n_actions"] == 4
    assert audit["n_updates"] == 2 * 8 * 4
    path, payload = save_real_policy(policy, audit, path=tmp_path / "policy.json")
    assert Path(path).exists()
    assert payload["real_data"]["n_od"] == 8
    loaded = load_policy_payload(path)
    assert loaded is not None
    result = solve_real_route(_option(), profile, policy_payload=loaded)
    assert result["q"]["available"] is True
    assert result["selected"]["route_id"].startswith("AMAP-S")
    assert result["selected_is_q"] in (True, False)
