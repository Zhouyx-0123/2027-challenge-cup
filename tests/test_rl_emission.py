# -*- coding: utf-8 -*-
"""路段级排放因子：公式、量纲、标定、与既有碳账的锚点一致性。"""
from __future__ import annotations

import math

import numpy as np
import pytest

from src.rl import emission


def test_ef_formula_matches_manual_computation():
    coeff = emission.EmissionCoefficients(A=100.0, a=800.0, B=0.0, b=0.0,
                                          C=-2.0, c=0.0, D=0.01, d=0.0)
    v, p, k = 40.0, 60.0, 1.2
    expected = (100.0 + 800.0 * 60.0 / 40.0 + (-2.0) * 40.0 + 0.01 * 40.0 ** 2) * 1.2
    assert coeff.ef_g_per_km(v, p, k) == pytest.approx(expected, rel=1e-12)


def test_ef_increases_with_power_and_never_negative():
    coeff = emission.coefficients_for(3)
    values = [coeff.ef_g_per_km(50.0, p, 1.0) for p in (10.0, 30.0, 60.0, 120.0)]
    assert all(later > earlier for earlier, later in zip(values, values[1:]))
    assert all(value > 0 for value in values)


def test_ef_rejects_non_positive_k_and_non_finite():
    coeff = emission.coefficients_for(3)
    with pytest.raises(ValueError):
        coeff.ef_g_per_km(50.0, 40.0, 0.0)
    with pytest.raises(ValueError):
        coeff.ef_g_per_km(float("nan"), 40.0, 1.0)


def test_low_speed_is_clamped_and_flagged():
    result = emission.segment_carbon(1.0, 2.0, vclass=3, load_t=18.0)
    assert result["low_speed_clamped"] is True
    assert result["speed_kmh"] == pytest.approx(emission.MIN_SPEED_KMH)
    # 5 km/h 下的排放因子必须显著高于 50 km/h（起停/怠速惩罚）
    fast = emission.segment_carbon(1.0, 50.0, vclass=3, load_t=18.0)
    assert result["ef_g_per_km"] > fast["ef_g_per_km"]


def test_segment_carbon_is_self_consistent():
    result = emission.segment_carbon(2.5, 45.0, vclass=3, load_t=30.0, k_factor=1.1)
    assert result["co2_kg"] == pytest.approx(result["ef_g_per_km"] * 2.5 / 1000.0, rel=1e-6)
    assert result["fuel_l"] == pytest.approx(
        result["co2_kg"] / emission.DIESEL_KG_CO2_PER_L, rel=1e-6)
    assert result["power_source"] == "model"
    assert result["k_factor"] == pytest.approx(1.1)


def test_power_grows_with_load_grade_and_speed():
    base = emission.tractive_power_kw(60.0, 0.0, vclass=3)
    loaded = emission.tractive_power_kw(60.0, 30.0, vclass=3)
    uphill = emission.tractive_power_kw(60.0, 30.0, vclass=3, grade_pct=3.0)
    faster = emission.tractive_power_kw(90.0, 30.0, vclass=3)
    assert loaded > base
    assert uphill > loaded
    assert faster > loaded


def test_power_has_idle_floor_on_downhill():
    """1% 下坡在 30 km/h 时阻力接近抵消，但发动机不可能零功率。"""
    power = emission.tractive_power_kw(30.0, 30.0, vclass=3, grade_pct=-1.0)
    assert power == pytest.approx(emission.vehicle_for(3).idle_kw)
    assert emission.tractive_power_kw(0.0, 18.0, vclass=3) == pytest.approx(
        emission.vehicle_for(3).idle_kw)


def test_power_from_obd_uses_iso_load_definition():
    power = emission.power_from_obd(rpm=1500.0, engine_load_pct=50.0,
                                    max_torque_nm=1200.0, aux_kw=0.0)
    omega = 1500.0 * 2.0 * math.pi / 60.0
    assert power == pytest.approx(0.5 * 1200.0 * omega / 1000.0, rel=1e-9)
    assert emission.power_from_obd(0.0, 0.0, 1200.0, aux_kw=2.0) == pytest.approx(2.0)


def test_k_factor_table_lookup_and_fallback():
    table = {"2": [1.03, 1.06, 1.10], "3": [1.10, 1.16, 1.20]}
    bins = [5.0, 15.0]
    assert emission.lookup_k_factor(table, 2, 4.5, bins) == pytest.approx(1.03)
    assert emission.lookup_k_factor(table, 2, 10.0, bins) == pytest.approx(1.06)
    assert emission.lookup_k_factor(table, 3, 40.0, bins) == pytest.approx(1.20)
    assert emission.lookup_k_factor({}, 2, 10.0, bins) == 1.0
    assert emission.lookup_k_factor(table, 9, 10.0, bins) == 1.0


def test_anchor_consistent_with_repo_factor_table():
    """重型 30 t / 50 km/h 必须落在既有因子表（1055.95 g/km）±15% 内。"""
    coeff = emission.coefficients_for(3)
    power = emission.tractive_power_kw(50.0, 30.0, vclass=3)
    ef = coeff.ef_g_per_km(50.0, power, 1.0)
    assert abs(ef - 1055.952) / 1055.952 < 0.15


def test_speed_bin_index_boundaries():
    assert emission.speed_bin_index(0.0) == 0
    assert emission.speed_bin_index(19.9) == 0
    assert emission.speed_bin_index(20.0) == 1
    assert emission.speed_bin_index(130.0) == 4


def test_calibrate_coefficients_recovers_known_surface():
    truth = emission.EmissionCoefficients(A=120.0, a=820.0, C=-4.0, D=0.03)
    samples = []
    for v in range(15, 95, 5):
        for load in (4.5, 18.0, 30.0):
            power = emission.tractive_power_kw(v, load, vclass=3)
            samples.append({"speed_kmh": float(v), "power_kw": power,
                            "ef_g_per_km": truth.ef_g_per_km(float(v), power, 1.0)})
    fit = emission.calibrate_coefficients(samples, vclass=3)
    assert fit["r2"] > 0.999
    assert fit["coefficients"]["A"] == pytest.approx(120.0, rel=0.02)
    assert fit["coefficients"]["a"] == pytest.approx(820.0, rel=0.02)
    # B/b/c/d 默认不参与拟合，必须显式为 0，避免"看起来有值其实是噪声"
    for name in ("B", "b", "c", "d"):
        assert fit["coefficients"][name] == 0.0


def test_calibrate_refuses_tiny_sample():
    samples = [{"speed_kmh": 40.0, "power_kw": 50.0, "ef_g_per_km": 900.0}] * 3
    with pytest.raises(ValueError):
        emission.calibrate_coefficients(samples, vclass=3, min_samples=12)


def test_calibrate_rejects_collinear_terms():
    samples = [{"speed_kmh": 40.0, "power_kw": 50.0, "ef_g_per_km": 900.0}] * 12
    with pytest.raises(ValueError):
        emission.calibrate_coefficients(samples, vclass=3, terms=("A", "B"))


def test_self_check_returns_three_classes():
    payload = emission.self_check()
    assert set(payload) == {"轻型", "中型", "重型"}
    assert all(row["ef_g_per_km"] > 0 for row in payload.values())
