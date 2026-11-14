# -*- coding: utf-8 -*-
"""拥堵概率分布：速度的对数正态模型、CVaR 与拥堵程度。

为什么单独成模块
----------------
之前"拥堵"在决策里只是一个速度点估计，既没有"可能堵"的概率，也没有"最坏会晚
多少"的尾部信息。本模块把每个路段的速度当成分布，给出三个量：

1. ``E[T]``：期望行程时间（注意不是 ``L/v``，方差会把它抬高 ``exp(σ²)`` 倍）；
2. ``CVaR90``：最差 10% 情况下的平均时间（对客户承诺用这个）；
3. ``degree``：拥堵程度 ``1 − v/v_free``（0=畅通，1=完全堵死）。

口径来源
--------
- 速度分布：``ln v ~ N(μ, σ²)``，``μ = ln(v_mean) − σ²/2``；
- 默认 CV 来自 2023 路网级模拟数据集的实测标定（高速 .248 / 城市 .289 /
  国道 .235）；没有实测数据时退回这组值，并在报告里标注"标定值"；
- CVaR 系数 1.755 = φ(z₀.₉)/(1−0.9)，正态近似；覆盖率已在 320 条真实路线上
  验证过（89.1%，见 docs/69）。

边界
----
- 本模块是**分析/决策层**：不写 data/processed、不改 E₀、不进闸门；
- 路段之间默认独立；``rho`` 只用于"长途相关性"的敏感性分析，不是物理模型；
- 事故项是泊松叠加，参数需按线路单独标定，默认值只作演示。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

SQRT2 = math.sqrt(2.0)
Z_90 = 1.2815515655446004
CVAR_COEF_90 = math.exp(-0.5 * Z_90 ** 2) / (math.sqrt(2.0 * math.pi) * 0.1)  # ≈1.755

#: 标定值（2023 路网级模拟数据集，按道路等级的速度 CV）
DEFAULT_CV = {1: 0.248, 2: 0.289, 3: 0.235, 4: 0.289}


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def sigma_from_cv(cv: float) -> float:
    """对数正态的 σ：CV² = exp(σ²) − 1。"""
    return math.sqrt(math.log(1.0 + max(cv, 0.0) ** 2))


@dataclass
class CongestionField:
    """按道路等级给出速度波动，并可做路线级风险汇总。"""

    cv_by_class: Dict[int, float] = field(default_factory=lambda: dict(DEFAULT_CV))
    rho: float = 0.0                 # 段间时间相关性
    incident_per_km: float = 0.0     # 事故/故障率（次/km），泊松
    incident_mean_min: float = 25.0
    incident_cv: float = 0.8

    def cv(self, road_class: int = 2) -> float:
        return float(self.cv_by_class.get(int(road_class), DEFAULT_CV.get(int(road_class), 0.289)))

    def leg_moments(self, length_km: float, mean_speed_kmh: float,
                    road_class: int = 2) -> Tuple[float, float]:
        """单段的 (E[T] 分钟, Var[T] 分钟²)。"""
        if length_km <= 0 or mean_speed_kmh <= 0:
            return 0.0, 0.0
        s2 = sigma_from_cv(self.cv(road_class)) ** 2
        e_t = (length_km / mean_speed_kmh) * 60.0 * math.exp(s2)
        return e_t, e_t * e_t * (math.exp(s2) - 1.0)

    def route_stats(self, legs: Sequence[Tuple[float, float, int]]) -> Dict[str, float]:
        """legs = [(length_km, mean_speed_kmh, road_class), ...] → 路线级统计。"""
        means, sigmas, km = [], [], 0.0
        for length, speed, road_class in legs:
            e, var = self.leg_moments(length, speed, road_class)
            means.append(e)
            sigmas.append(math.sqrt(max(var, 0.0)))
            km += length
        mu = sum(means)
        var = (1.0 - self.rho) * sum(s * s for s in sigmas) \
            + self.rho * sum(sigmas) ** 2
        if self.incident_per_km > 0 and km > 0:
            lam = self.incident_per_km * km
            s2 = sigma_from_cv(self.incident_cv) ** 2
            mu += lam * self.incident_mean_min
            var += lam * (self.incident_mean_min ** 2) * math.exp(s2)
        sigma = math.sqrt(max(var, 1e-12))
        return {"mean": mu, "sigma": sigma,
                "cvar90": mu + CVAR_COEF_90 * sigma,
                "km": km}

    @staticmethod
    def congestion_degree(legs: Sequence[Tuple[float, float, int]],
                          free_speeds: Sequence[float]) -> Dict[str, float]:
        """拥堵程度 d = 1 − v/v_free，按里程加权；max 用于硬约束。"""
        total, acc, worst = 0.0, 0.0, 0.0
        for (length, speed, _), free in zip(legs, free_speeds):
            d = max(0.0, min(1.0, 1.0 - speed / max(free, 1e-9)))
            total += length
            acc += length * d
            worst = max(worst, d)
        return {"degree_mean": acc / max(total, 1e-9), "degree_max": worst}

    @staticmethod
    def on_time(mu: float, sigma: float, deadline_min: float) -> Dict[str, float]:
        """正态近似下的准时概率与期望迟到。"""
        sigma = max(sigma, 1e-9)
        z = (deadline_min - mu) / sigma
        late = sigma * norm_pdf(z) + (mu - deadline_min) * (1.0 - norm_cdf(z))
        return {"p_on_time": norm_cdf(z), "expected_late_min": max(late, 0.0)}

    @classmethod
    def from_config(cls, config: Dict[str, object]) -> "CongestionField":
        """从 config/rl_policy.json 的 ``congestion`` 段构造。"""
        cfg = (config or {}).get("congestion", {}) if isinstance(config, dict) else {}
        cv = {int(k): float(v) for k, v in (cfg.get("cv_by_road_class") or DEFAULT_CV).items()}
        return cls(cv_by_class=cv or dict(DEFAULT_CV),
                   rho=float(cfg.get("correlation_rho", 0.0)),
                   incident_per_km=float(cfg.get("incident_per_km", 0.0)),
                   incident_mean_min=float(cfg.get("incident_mean_min", 25.0)),
                   incident_cv=float(cfg.get("incident_cv", 0.8)))

    def to_dict(self) -> Dict[str, object]:
        return {"cv_by_road_class": {str(k): v for k, v in self.cv_by_class.items()},
                "correlation_rho": self.rho,
                "incident_per_km": self.incident_per_km,
                "incident_mean_min": self.incident_mean_min,
                "incident_cv": self.incident_cv}
