# -*- coding: utf-8 -*-
"""配置装配：把 ``config/rl_policy.json`` 变成可运行的对象。

纪律（与 ``config/calc_factors.json`` 一致）
-------------------------------------------
1. **代码里不硬编码任何系数**；缺配置时退回 ``src/rl`` 里的默认常量，
   并用 ``config_missing`` 标记告诉调用方"这是默认值，不是项目口径"。
2. 配置里的 ``version`` 必须存在；改值要 bump 版本，否则测试不认。
3. 载重分箱边界（``k_ij.load_bins_edges``）与聚类特征顺序（``features``）
   **必须**与训练时一致，所以它们只能来自这一份配置，不接受调用方临时传。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .agent import (DEFAULT_OPERATION_WEIGHTS, DEFAULT_STRATEGIES,
                    DEFAULT_PROGRESS_BINS, DEFAULT_TIME_BUCKETS,
                    MultiObjectiveTD, ObjectiveNormalizer, ResidualCorrector,
                    Strategy, StrategyQLearner, StateEncoder)
from .clustering import StateQuantizer
from .emission import (DEFAULT_COEFFICIENTS, DEFAULT_VEHICLES, EmissionCoefficients,
                       VehicleParams)
from .network import RoadNetwork, TrafficConfig

#: 仓库根（src/rl/settings.py → parents[2] = 仓库根）
ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "rl_policy.json"


def load_config(path: Optional[Any] = None) -> Dict[str, Any]:
    """读配置；文件不存在时返回空 dict（调用方全部走代码默认值）。"""
    target = Path(path) if path else CONFIG_PATH
    if not target.exists():
        return {}
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("rl_policy.json 顶层必须是对象")
    if "version" not in payload:
        raise ValueError("rl_policy.json 缺少 version 字段")
    return payload


def emission_table(config: Optional[Dict[str, Any]] = None
                   ) -> Dict[int, EmissionCoefficients]:
    config = config or {}
    rows = (config.get("emission") or {}).get("coefficients")
    if not rows:
        return dict(DEFAULT_COEFFICIENTS)
    table: Dict[int, EmissionCoefficients] = {}
    for key, row in rows.items():
        table[int(key)] = EmissionCoefficients(
            A=float(row.get("A", 0.0)), a=float(row.get("a", 0.0)),
            B=float(row.get("B", 0.0)), b=float(row.get("b", 0.0)),
            C=float(row.get("C", 0.0)), c=float(row.get("c", 0.0)),
            D=float(row.get("D", 0.0)), d=float(row.get("d", 0.0)))
    return table


def vehicle_table(config: Optional[Dict[str, Any]] = None
                  ) -> Dict[int, VehicleParams]:
    config = config or {}
    rows = config.get("vehicles")
    if not rows:
        return dict(DEFAULT_VEHICLES)
    table: Dict[int, VehicleParams] = {}
    for key, row in rows.items():
        table[int(key)] = VehicleParams(
            curb_mass_kg=float(row["curb_mass_kg"]), cd_a_m2=float(row["cd_a_m2"]),
            crr=float(row["crr"]), driveline_eff=float(row["driveline_eff"]),
            aux_kw=float(row["aux_kw"]), max_power_kw=float(row["max_power_kw"]),
            idle_kw=float(row.get("idle_kw", 8.0)))
    return table


def k_table(config: Optional[Dict[str, Any]] = None
            ) -> Tuple[Dict[str, List[float]], List[float]]:
    config = config or {}
    block = config.get("k_ij") or {}
    table = block.get("table") or {}
    bins = block.get("load_bins_edges") or []
    return table, [float(x) for x in bins]


def strategies(config: Optional[Dict[str, Any]] = None) -> List[Strategy]:
    config = config or {}
    rows = config.get("strategies")
    if not rows:
        return list(DEFAULT_STRATEGIES)
    out: List[Strategy] = []
    for row in rows:
        out.append(Strategy(sid=str(row["sid"]), label=str(row["label"]),
                            weights={k: float(v) for k, v in row["weights"].items()},
                            description=str(row.get("description", ""))))
    if not out:
        raise ValueError("strategies 不能为空")
    return out


def operation_weights(config: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    config = config or {}
    rows = config.get("operation_weights") or {}
    weights = {key: float(rows[key]) for key in ("time", "carbon", "cost")
               if key in rows}
    return weights or dict(DEFAULT_OPERATION_WEIGHTS)


def traffic_config(config: Optional[Dict[str, Any]] = None,
                   overrides: Optional[Dict[str, Any]] = None) -> TrafficConfig:
    config = config or {}
    block = dict(config.get("traffic") or {})
    if overrides:
        block.update(overrides)
    return TrafficConfig(
        seed=int(block.get("seed", 20261103)),
        profile=str(block.get("profile", "urban")),
        weather=str(block.get("weather", "clear")),
        volume=float(block.get("volume", 1.0)),
        incident_rate_per_hour=float(block.get("incident_rate_per_hour", 1.5)),
        min_incident_min=float(block.get("min_incident_min", 10)),
        max_incident_min=float(block.get("max_incident_min", 40)),
    )


def economics(config: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    config = config or {}
    block = config.get("economics") or {}
    return {
        "fuel_price": float(block.get("fuel_price_yuan_per_l", 7.5)),
        "carbon_price": float(block.get("carbon_price_yuan_per_t", 100.0)),
    }


def correction_model(config: Optional[Dict[str, Any]] = None) -> ResidualCorrector:
    """模型残差修正器：参数全部来自 config 的 `correction` 段。"""
    config = config or {}
    block = config.get("correction") or {}
    if not bool(block.get("enabled", True)):
        # 关掉修正层（A/B 对照用）：样本门槛设到不可能达到，等价于恒等映射
        return ResidualCorrector(n_buckets=int(block.get("n_buckets", 8)),
                                 min_samples=10 ** 9,
                                 floor=float(block.get("floor", 0.5)),
                                 ceiling=float(block.get("ceiling", 2.5)))
    return ResidualCorrector(
        n_buckets=int(block.get("n_buckets", 8)),
        alpha=float(block.get("alpha", 0.25)),
        min_samples=int(block.get("min_samples", 3)),
        floor=float(block.get("floor", 0.5)),
        ceiling=float(block.get("ceiling", 2.5)))


def model_bias_table(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """仿真用的"模型偏差表"（只影响环境记账，学习器看不到）。"""
    config = config or {}
    block = (config.get("sim") or {}).get("model_bias") or {}
    return {key: dict(value) for key, value in block.items()
            if key in ("time", "carbon") and isinstance(value, dict)}


def sample_feature_space(n: int = 600, seed: int = 20261103) -> np.ndarray:
    """在没有历史数据时，用特征空间的均匀覆盖拟合 k-means。

    为什么不直接拿随机行程的样本：冷启动阶段没有路网数据，而 k-means 只需要
    "特征可能取值范围"的覆盖。均匀覆盖比真实分布更保守（簇更分散），
    真实数据接入后换成 ``collect_state_samples`` 的采样即可。
    """
    rng = np.random.default_rng(int(seed))
    return np.column_stack([
        rng.uniform(0.0, 1.0, n),      # 上游拥堵
        rng.uniform(0.0, 1.0, n),      # 下游平均拥堵
        rng.uniform(0.0, 40.0, n),     # 载重 t
        rng.uniform(-4.0, 4.0, n),     # 坡度 %
        rng.uniform(0.0, 24.0, n),     # 时段 h
    ])


def build_forecaster(network: Optional[RoadNetwork],
                     config: Optional[Dict[str, Any]] = None):
    """按配置建 15 分钟预报器；`forecast.enabled=false` 时返回 None。"""
    config = config or {}
    block = config.get("forecast") or {}
    if not bool(block.get("enabled", True)) or network is None:
        return None
    from .forecast import TrafficForecaster
    traffic = config.get("traffic") or {}
    return TrafficForecaster(
        network, profile=str(block.get("profile") or traffic.get("profile", "urban")),
        volume=float(block.get("volume", traffic.get("volume", 1.0))),
        weather=str(block.get("weather") or traffic.get("weather", "clear")),
        horizon_min=float(block.get("horizon_min", 15.0)))


def build_policy(network: Optional[RoadNetwork] = None,
                 config: Optional[Dict[str, Any]] = None,
                 state_samples: Optional[np.ndarray] = None,
                 seed: Optional[int] = None) -> Any:
    """按配置装配一个未训练（但可立即推理）的 ``RoutePolicy``。"""
    from .policy import RoutePolicy

    config = config if config is not None else load_config()
    cluster = config.get("clustering") or {}
    k_state = int(cluster.get("k_state", 8))
    fit_seed = int(seed if seed is not None else cluster.get("seed", 20261103))
    quantizer = StateQuantizer(k=k_state, seed=fit_seed,
                               features=cluster.get("features"))
    features = state_samples if state_samples is not None else sample_feature_space(600, fit_seed)
    quantizer.fit(features)
    quota_block = config.get("quota") or {}
    encoder = StateEncoder(quantizer,
                           progress_bins=int(cluster.get("progress_bins", DEFAULT_PROGRESS_BINS)),
                           time_buckets=int(cluster.get("time_buckets", DEFAULT_TIME_BUCKETS)),
                           pressure_buckets=int(quota_block.get("pressure_buckets", 1)))
    rl = config.get("rl") or {}
    td = MultiObjectiveTD(encoder.n_states,
                          gamma=float(rl.get("gamma", 0.95)),
                          alpha=float(rl.get("alpha_v", 0.20)),
                          lam=float(rl.get("lambda", 0.70)),
                          alpha_var=float(rl.get("alpha_var", 0.05)))
    strategy_list = strategies(config)
    q = StrategyQLearner(encoder.n_q_states, len(strategy_list),
                         gamma=float(rl.get("gamma_q", 0.90)),
                         alpha=float(rl.get("alpha_q", 0.15)),
                         epsilon=float(rl.get("epsilon", 0.25)),
                         epsilon_min=float(rl.get("epsilon_min", 0.03)),
                         epsilon_decay=float(rl.get("epsilon_decay", 0.99)),
                         double=bool(rl.get("double_q", True)), seed=fit_seed)
    eta_block = config.get("eta") or {}
    forecaster = build_forecaster(network, config)
    return RoutePolicy(encoder, td, q, strategy_list, operation_weights(config),
                       switch_threshold=float(rl.get("switch_threshold", 0.03)),
                       switch_penalty=float(rl.get("switch_penalty", 0.02)),
                       rng=np.random.default_rng(fit_seed),
                       corrector=correction_model(config),
                       eta_p90_quantile=float(eta_block.get("p90_quantile", 0.90)),
                       residual_window=int(eta_block.get("residual_window", 400)),
                       min_residuals=int(eta_block.get("min_residuals", 40)),
                       min_replan_interval_min=float(rl.get("min_replan_interval_min", 2.0)),
                       forecaster=forecaster)


def policy_from_dict(payload: Dict[str, Any],
                     config: Optional[Dict[str, Any]] = None,
                     network: Optional[RoadNetwork] = None) -> Any:
    """从 ``to_dict()`` 落盘的结果恢复策略（线上/离线评估用）。"""
    from .policy import RoutePolicy

    config = config or {}
    encoder_block = payload["encoder"]
    quantizer = StateQuantizer.from_dict(encoder_block["quantizer"])
    encoder = StateEncoder(quantizer,
                           progress_bins=int(encoder_block.get("progress_bins", 5)),
                           time_buckets=int(encoder_block.get("time_buckets", 8)),
                           pressure_buckets=int(encoder_block.get("pressure_buckets", 1)))
    td = MultiObjectiveTD.from_dict(payload["td"])
    q = StrategyQLearner.from_dict(payload["q"])
    strategy_list = [Strategy(sid=row["sid"], label=row["label"],
                              weights={k: float(v) for k, v in row["weights"].items()},
                              description=row.get("description", ""))
                     for row in payload.get("strategies", [])] or strategies(config)
    correction = payload.get("correction")
    corrector = (ResidualCorrector.from_dict(correction) if correction
                 else correction_model(config))
    eta_block = config.get("eta") or {}
    predictor = payload.get("predictor") or {}
    # 配置里的 eta 段是"运营旋钮"：显式写了就以它为准，没写才沿用落盘值。
    # 这样改 P90 分位数不用重训、也不用改 policy.json。
    quantile = float(eta_block["p90_quantile"]) if "p90_quantile" in eta_block \
        else float(predictor.get("eta_p90_quantile", 0.90))
    min_residuals = int(eta_block["min_residuals"]) if "min_residuals" in eta_block \
        else int(predictor.get("min_residuals", 40))
    policy = RoutePolicy(
        encoder, td, q, strategy_list,
        payload.get("operation_weights") or operation_weights(config),
        corrector=corrector, eta_p90_quantile=quantile,
        residual_window=int(eta_block.get("residual_window", 400)),
        min_residuals=min_residuals,
        forecaster=build_forecaster(network, config))
    policy.model_abs_err = predictor.get("model_abs_err")
    policy.td_abs_err = predictor.get("td_abs_err")
    policy.forecast_abs_err = predictor.get("forecast_abs_err")
    policy.restore_residuals(predictor.get("residuals") or [])
    return policy


def reference_normalizer(env: Any, strategy: Strategy,
                         config: Optional[Dict[str, Any]] = None) -> ObjectiveNormalizer:
    """用"出发时刻的均衡路线"作为归一化基准，保证权重跨单可比。"""
    from .policy import plan_route

    plan = plan_route(env.network, env.snapshot(), env.node,
                      env.request.destination, strategy,
                      ObjectiveNormalizer(1.0, 1.0, 1.0), env.cost_model, env.blocked)
    if not plan.path:
        raise RuntimeError("参考路线不可达，无法归一化")
    return ObjectiveNormalizer(plan.time_min, plan.carbon_kg, plan.cost_yuan)


__all__ = ["ROOT", "CONFIG_PATH", "load_config", "emission_table", "vehicle_table",
           "k_table", "strategies", "operation_weights", "traffic_config", "economics",
           "sample_feature_space", "build_policy", "policy_from_dict",
           "reference_normalizer"]
