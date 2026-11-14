# -*- coding: utf-8 -*-
"""拥堵概率模型测试：矩、CVaR、拥堵度、准时概率、配置读取。"""
from __future__ import annotations

import math

import pytest

from src.rl.congestion import (CVAR_COEF_90, DEFAULT_CV, CongestionField,
                               norm_cdf, sigma_from_cv)


def test_leg_moments_formula():
    field = CongestionField({2: 0.30})
    e, var = field.leg_moments(10.0, 50.0, 2)
    s2 = sigma_from_cv(0.30) ** 2
    assert e == pytest.approx((10.0 / 50.0) * 60.0 * math.exp(s2), rel=1e-9)
    assert var == pytest.approx(e * e * (math.exp(s2) - 1.0), rel=1e-9)
    # 方差拖累：E[T] 必须大于 L/v
    assert e > (10.0 / 50.0) * 60.0


def test_cvar_coefficient():
    assert CVAR_COEF_90 == pytest.approx(1.755, abs=0.01)
    # 正态 90% 分位
    assert norm_cdf(1.2816) == pytest.approx(0.90, abs=0.001)


def test_higher_cv_higher_cvar():
    low = CongestionField({2: 0.10})
    high = CongestionField({2: 0.40})
    legs = [(10.0, 50.0, 2)]
    assert high.route_stats(legs)["cvar90"] > low.route_stats(legs)["cvar90"]
    assert high.route_stats(legs)["mean"] > low.route_stats(legs)["mean"]


def test_rho_increases_sigma():
    field = CongestionField({1: 0.25, 2: 0.30})
    legs = [(5.0, 80.0, 1), (5.0, 50.0, 2), (5.0, 80.0, 1)]
    indep = field.route_stats(legs)
    field.rho = 0.5
    corr = field.route_stats(legs)
    assert corr["sigma"] > indep["sigma"]
    assert corr["cvar90"] > indep["cvar90"]


def test_incident_increases_mean():
    field = CongestionField({2: 0.30})
    legs = [(20.0, 50.0, 2)]
    base = field.route_stats(legs)
    field.incident_per_km = 0.01
    with_inc = field.route_stats(legs)
    assert with_inc["mean"] == pytest.approx(base["mean"] + 0.01 * 20.0 * 25.0, rel=1e-9)
    assert with_inc["sigma"] > base["sigma"]


def test_congestion_degree():
    legs = [(4.0, 40.0, 2), (6.0, 60.0, 1)]
    out = CongestionField.congestion_degree(legs, [80.0, 80.0])
    assert out["degree_mean"] == pytest.approx((4 * 0.5 + 6 * 0.25) / 10.0, rel=1e-9)
    assert out["degree_max"] == pytest.approx(0.5, rel=1e-9)


def test_on_time_monotone():
    field = CongestionField({2: 0.30})
    mu, sigma = 60.0, 12.0
    tight = field.on_time(mu, sigma, 55.0)
    loose = field.on_time(mu, sigma, 120.0)
    assert loose["p_on_time"] > tight["p_on_time"]
    assert tight["expected_late_min"] > 0
    assert loose["expected_late_min"] < 0.01


def test_from_config():
    cfg = {"congestion": {"cv_by_road_class": {"1": 0.2, "2": 0.3},
                          "correlation_rho": 0.3, "incident_per_km": 0.004}}
    field = CongestionField.from_config(cfg)
    assert field.cv(1) == pytest.approx(0.2)
    assert field.rho == pytest.approx(0.3)
    assert field.incident_per_km == pytest.approx(0.004)
    # 未给的等级退回默认值
    assert field.cv(3) == pytest.approx(DEFAULT_CV[3])
