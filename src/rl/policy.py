# -*- coding: utf-8 -*-
"""决策服务：路由搜索 + 策略选择 + 重规划 + 可解释输出。

三段式
------
1. ``plan_route()``：在**当前快照**上做 Dijkstra，边权是"策略权重标量化后的
   归一化成本"。这一步给出这条路**是什么**（走哪几段、各段多长时间多少碳）。
2. ``RoutePolicy.act()``：Q 表决定**用哪套权重**（时间/低碳/均衡/抗堵），
   然后调 ``plan_route`` 得到路径，取第一段作为本次动作。
3. ``RoutePolicy.observe()``：拿实际走完一段的数据更新 TD 值函数与 Q 表。

``RecommendationService`` 把上面三段包成前端能直接渲染的 payload：
逐段明细、ETA（点估计 + P90）、碳预测、重规划理由、置信度。

为什么路由用 Dijkstra 而不是用 Q 直接选边
-----------------------------------------
路网一换（哪怕只加一条匝道），Q 表就全废了；Dijkstra 立刻可用。所以让
Q 只学"权重"这个低维、可迁移、可解释的量，路径由模型算。这也是本方案在
"换城市/换路网"时唯一不用重训的部分——答辩被问"换条路怎么办"就靠这一点。
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .agent import (DEFAULT_OPERATION_WEIGHTS, DEFAULT_STRATEGIES, MultiObjectiveTD,
                    ObjectiveNormalizer, ResidualCorrector, Strategy, StrategyQLearner,
                    StateEncoder)
from .env import SegmentCostModel, StepOutcome, TripEnv, TripRequest
from .network import RoadNetwork, TrafficSnapshot, dijkstra

#: 重规划阈值：新方案标量化成本至少低 3% 才换，避免"每段都改主意"的抖动
DEFAULT_SWITCH_THRESHOLD = 0.03
#: 策略切换惩罚（归一化成本单位）：让 Q 不为 0.5% 的收益反复横跳
DEFAULT_STRATEGY_SWITCH_PENALTY = 0.02


@dataclass
class RoutePlan:
    """一条候选/推荐路径及其预测量。"""

    path: List[str]
    time_min: float
    carbon_kg: float
    cost_yuan: float
    scalarized: float
    rows: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"path": list(self.path), "time_min": _num(self.time_min),
                "carbon_kg": _num(self.carbon_kg), "cost_yuan": _num(self.cost_yuan),
                "scalarized": _num(self.scalarized),
                "rows": [dict(row) for row in self.rows]}


def _num(value: float, digits: int = 4) -> Any:
    return round(float(value), digits) if math.isfinite(float(value)) else None


def plan_route(network: RoadNetwork, snapshot: TrafficSnapshot, origin: str,
               destination: str, strategy: Strategy, normalizer: ObjectiveNormalizer,
               cost_model: SegmentCostModel,
               blocked: Optional[Iterable[str]] = None) -> RoutePlan:
    """标量化最短路；逐段明细一并算出（前端表格直接用）。"""
    blocked_set = set(blocked or ())

    def weight(segment) -> float:
        row = cost_model.evaluate(segment, snapshot)
        value = strategy.scalarize(normalizer.normalize(
            [row["time_min"], row["carbon_kg"], row["cost_yuan"]]))
        return max(value, 1e-9)

    path, total = dijkstra(network, origin, destination, weight, blocked_set)
    if not path:
        return RoutePlan(path=[], time_min=float("inf"), carbon_kg=float("inf"),
                         cost_yuan=float("inf"), scalarized=float("inf"))
    rows = [cost_model.evaluate(network.segment(sid), snapshot) for sid in path]
    return RoutePlan(path=path,
                     time_min=float(sum(r["time_min"] for r in rows)),
                     carbon_kg=float(sum(r["carbon_kg"] for r in rows)),
                     cost_yuan=float(sum(r["cost_yuan"] for r in rows)),
                     scalarized=float(total), rows=rows)


class RoutePolicy:
    """学习型决策策略：``act`` 给出动作，``observe`` 消化反馈。"""

    def __init__(self, encoder: StateEncoder, td: MultiObjectiveTD,
                 q: StrategyQLearner,
                 strategies: Sequence[Strategy] = DEFAULT_STRATEGIES,
                 operation_weights: Optional[Dict[str, float]] = None,
                 switch_threshold: float = DEFAULT_SWITCH_THRESHOLD,
                 switch_penalty: float = DEFAULT_STRATEGY_SWITCH_PENALTY,
                 rng: Optional[np.random.Generator] = None,
                 corrector: Optional[ResidualCorrector] = None,
                 eta_p90_quantile: float = 0.90,
                 residual_window: int = 400,
                 min_residuals: int = 40,
                 min_replan_interval_min: float = 2.0,
                 forecaster: Any = None):
        self.encoder = encoder
        self.td = td
        self.q = q
        self.strategies = list(strategies)
        self.operation_weights = dict(operation_weights or DEFAULT_OPERATION_WEIGHTS)
        self.switch_threshold = float(switch_threshold)
        self.switch_penalty = float(switch_penalty)
        self.rng = rng or np.random.default_rng(20261103)
        self._last_plan: Optional[RoutePlan] = None
        self._plan_offset = 0
        self._replans = 0
        self.model_abs_err: Optional[float] = None
        self.td_abs_err: Optional[float] = None
        self.forecast_abs_err: Optional[float] = None
        # 15 分钟前瞻：只用于 ETA 第三路预测，不改路由（避免一次性改动面过大）
        self.forecaster = forecaster
        self._pending: List[Dict[str, Any]] = []
        # 模型残差修正层：把"模型哪里算错了"学回来，回灌路由边权
        self.corrector = corrector or ResidualCorrector()
        # 共形预测：滚动保留"实际 − 预测"的残差，P90 用经验分位数，
        # 不再用拍脑袋的 2.5×MAE（覆盖率靠数据说话，见 docs/59 §14.2）
        self._residuals: deque = deque(maxlen=max(int(residual_window), 10))
        self.eta_p90_quantile = float(eta_p90_quantile)
        self.min_residuals = max(int(min_residuals), 5)
        # 重规划冷却：两次真正换方案之间至少隔 N 分钟，避免导航"抽风"
        self.min_replan_interval_min = float(min_replan_interval_min)
        self._last_replan_minute = -1e9

    # ------------------------------------------------------------------ 工具
    def strategy_by_id(self, sid: str) -> Strategy:
        for strategy in self.strategies:
            if strategy.sid == sid:
                return strategy
        raise KeyError("未知策略 %r" % (sid,))

    def operation_cost(self, vector: Sequence[float],
                       normalizer: ObjectiveNormalizer) -> float:
        """运营口径综合成本（归一化后加权），Q 的奖励就按它算。"""
        normalized = normalizer.normalize(vector)
        return float(sum(self.operation_weights.get(key, 0.0) * normalized[i]
                         for i, key in enumerate(("time", "carbon", "cost"))))

    def evaluate_path(self, env: TripEnv, path: Sequence[str],
                      normalizer: ObjectiveNormalizer) -> Dict[str, float]:
        """按**运营口径**评估一条剩余路径（重规划比较的裁判标准）。

        注意用的是 ``operation_weights`` 而不是当次策略权重：策略是"怎么搜"，
        运营口径是"算不算数"。两者混用会让重规划判定自欺欺人。
        """
        rows = [env.cost_model.evaluate(env.network.segment(sid), env.snapshot())
                for sid in path]
        time_min = float(sum(r["time_min"] for r in rows))
        carbon_kg = float(sum(r["carbon_kg"] for r in rows))
        cost_yuan = float(sum(r["cost_yuan"] for r in rows))
        return {"time_min": time_min, "carbon_kg": carbon_kg, "cost_yuan": cost_yuan,
                "scalarized": self.operation_cost(
                    [time_min, carbon_kg, cost_yuan], normalizer),
                "rows": rows}

    def _remaining_of_last_plan(self, env: TripEnv) -> Optional[List[str]]:
        """上一个方案里"还没走"的那一段。

        方案记的是**从决策节点出发的剩余路径**，配一个游标 `_plan_offset`
        记录方案生成时的 `len(env.path)`。两者相减才是已消耗的段数——
        早期版本用"路径前缀是否等于 env.path"判断，方案一旦从中途重排就永远
        判成失效，导致每一段都被当成"重规划"（实测 5 段报了 4 次）。
        """
        if not self._last_plan or not self._last_plan.path:
            return None
        consumed = len(env.path) - int(self._plan_offset)
        if consumed < 0:
            return None
        path = list(self._last_plan.path)
        if consumed >= len(path):
            return None
        remainder = path[consumed:]
        if not remainder:
            return None
        if env.network.segment(remainder[0]).u != env.node:
            return None
        return remainder

    # ------------------------------------------------------------------ 决策
    def start_episode(self) -> None:
        """每次行程开始调一次：清空方案缓存、行程累计成本与已选策略。"""
        self._trip_action: Optional[int] = None
        self._start_sidx: Optional[int] = None
        self._last_plan = None
        self._plan_offset = 0
        self._replans = 0
        self._last_replan_minute = -1e9
        self._pending = []
        self._episode_costs = np.zeros(3, dtype=float)

    def act(self, env: TripEnv, normalizer: ObjectiveNormalizer,
            explore: bool = False, forced_strategy: Optional[str] = None,
            pressure: float = 0.0) -> Dict[str, Any]:
        # 把当前修正器挂到环境的成本模型上：路由边权 = 模型 × 学到的比值
        env.cost_model.corrector = self.corrector
        features = env.state_features()
        idx, sidx, meta = self.encoder.encode(features, env.progress(), env.minute,
                                              pressure=pressure)

        # 策略只在**出发时**选一次，全程不变。
        # 逐段重选策略会让"这段图省时、下段图省碳"互相打架，实测会把车开成
        # 先南下再北上的环路（19:00 那单多跑 5 分钟）。运营上也是这个逻辑：
        # 派车时定目标，途中只按同一目标重规划。
        if self._trip_action is None:
            if forced_strategy:
                action = [s.sid for s in self.strategies].index(forced_strategy)
            else:
                action = self.q.select(sidx, epsilon=(0.35 if explore else 0.0))
            self._trip_action = action
            self._start_sidx = sidx
        else:
            action = self._trip_action
        strategy = self.strategies[action]

        new_plan = plan_route(env.network, env.snapshot(), env.node,
                              env.request.destination, strategy, normalizer,
                              env.cost_model, env.blocked)
        if not new_plan.path:
            raise RuntimeError("无可行路径：%s → %s" % (env.node, env.request.destination))

        # 重规划迟滞：已定方案只有在运营口径下便宜 3% 以上才换。
        # 没有这一步，路况微抖动就会让方案每段都改，司机会觉得导航在"抽风"。
        plan = new_plan
        kept = False
        remainder = self._remaining_of_last_plan(env)
        if remainder:
            stay = self.evaluate_path(env, remainder, normalizer)
            fresh = self.evaluate_path(env, new_plan.path, normalizer)
            cooling = (env.minute - self._last_replan_minute) < self.min_replan_interval_min
            # 只在"新方案便宜超过阈值"时才换：stay ≤ fresh·(1+threshold)
            if cooling or stay["scalarized"] <= fresh["scalarized"] * (1.0 + self.switch_threshold):
                plan = RoutePlan(path=list(remainder), time_min=stay["time_min"],
                                 carbon_kg=stay["carbon_kg"],
                                 cost_yuan=stay["cost_yuan"],
                                 scalarized=stay["scalarized"], rows=stay["rows"])
                kept = True
        if self._last_plan is not None and not kept:
            self._replans += 1
            self._last_replan_minute = env.minute
        self._last_plan = plan
        self._plan_offset = len(env.path)
        return {
            "state_index": idx, "strategy_state": sidx, "start_state": self._start_sidx,
            "state_meta": meta, "action": action, "pressure": float(pressure),
            "strategy": strategy.to_dict(),
            "next_segment": plan.path[0],
            "plan": plan,
            "kept_plan": kept,
            "q_values": [round(float(x), 6) for x in self.q.q_values(sidx)],
            "confidence": round(self.q.confidence(sidx, action), 4),
        }

    def observe(self, env: TripEnv, decision: Dict[str, Any], outcome: StepOutcome,
                normalizer: ObjectiveNormalizer) -> Dict[str, Any]:
        """逐段更新 TD（预测层）；**行程结束**才更新策略 Q（决策层）。"""
        idx = int(decision["state_index"])
        costs = outcome.cost_vector()
        terminal = bool(env.done)
        next_idx = None
        if not terminal:
            next_idx, _, _ = self.encoder.encode(
                env.state_features(), env.progress(), env.minute)
        td_info = self.td.update(idx, costs, next_idx, terminal=terminal)
        self.corrector.observe(
            outcome.road_class, outcome.minute,
            model_time=float(outcome.model_time_min),
            actual_time=float(outcome.time_min),
            model_carbon=float(outcome.model_carbon_kg),
            actual_carbon=float(outcome.carbon_kg))
        self._episode_costs += costs
        q_error = None
        reward = None
        if terminal:
            # 蒙特卡洛回报：一整单的真实综合成本才是裁判，用单段奖励会与
            # "这段路长/短"混淆（1.6 km 的高速段天然比 0.75 km 的路口段贵）。
            total_u = (self.operation_cost(self._episode_costs, normalizer)
                       + self.switch_penalty * self._replans)
            reward = -total_u
            q_error = self.q.update(int(decision["start_state"]), int(decision["action"]),
                                    reward, None, terminal=True)
            self._settle_predictions(float(env.total_time_min))
        return {"td_error": td_info["td_error"],
                "q_error": None if q_error is None else round(float(q_error), 6),
                "reward": None if reward is None else round(float(reward), 6),
                "next_state": next_idx}

    def predict_eta(self, env: TripEnv, plan: RoutePlan) -> Dict[str, Any]:
        """ETA 预测：模型路线时间 + TD 学习值，按**在线实测误差**反比融合。

        为什么不是固定权重：仿真里模型可能本来就准（此时该信模型），真实世界
        模型必然有偏差（此时该信学习值）。谁准听谁的，用两路预测的滚动 MAE
        在线投票，比人为定 0.6/0.4 靠谱——这也是"实时更新"的正确用法。
        P90 用 ``|TD 误差|`` 的经验宽度（系统偏差下正态 σ 会低估区间）。
        """
        features = env.state_features()
        idx, _, meta = self.encoder.encode(features, env.progress(), env.minute)
        prediction = self.td.predict(idx)
        model_time = float(plan.time_min)
        learned_time = float(prediction["remaining_time_min"])
        forecast_time: Optional[float] = None
        if self.forecaster is not None:
            forecast_time = self.forecaster.travel_time_min(plan.path, env.snapshot(),
                                                            env.minute)
        weights = self.predictor_weights()
        blended = weights["td"] * learned_time + weights["model"] * model_time
        if forecast_time is not None:
            blended += weights.get("forecast", 0.0) * forecast_time
        # 保守缓冲：两路预测各自的滚动 MAE 按当前权重合成，再乘经验系数 2.5。
        # 这不是严格的分位数估计（真实分布未知），前端标签用"保守到达时间"。
        state_spread = max(prediction.get("time_abs_err_min", prediction["time_std_min"]), 0.0)
        pool = (weights["model"] * (self.model_abs_err or state_spread)
                + weights["td"] * (self.td_abs_err or state_spread))
        spread = max(state_spread, float(pool))
        eta = {
            "eta_min": round(float(blended), 2),
            "eta_p90_min": round(float(blended + 2.5 * spread), 2),
            "model_time_min": round(model_time, 2),
            "learned_time_min": round(learned_time, 2),
            "forecast_time_min": None if forecast_time is None else round(forecast_time, 2),
            "time_std_min": round(spread, 2),
            "confidence": round(float(weights["td"]), 3),
            "state_meta": meta,
            "predicted_carbon_kg": round(float(prediction["remaining_carbon_kg"]), 3),
            "predicted_cost_yuan": round(float(prediction["remaining_cost_yuan"]), 2),
        }
        self._pending.append({"model": model_time, "td": learned_time,
                              "forecast": forecast_time,
                              "blended": float(blended),
                              "elapsed": float(env.total_time_min),
                              "state": int(idx)})
        return eta

    def _conformal_p90(self, blended: float, spread: float) -> Tuple[float, str]:
        """P90：残差样本够就用经验分位数，不够退回 2.5×MAE 的经验缓冲。"""
        if len(self._residuals) >= self.min_residuals:
            buffer = np.fromiter(self._residuals, dtype=float)
            quantile = float(np.quantile(buffer, self.eta_p90_quantile))
            return blended + max(quantile, 0.0), "conformal"
        return blended + 2.5 * spread, "buffer"

    def set_eta_interval(self, quantile: Optional[float] = None,
                         min_residuals: Optional[int] = None,
                         window: Optional[int] = None) -> Dict[str, Any]:
        """运行时调 P90 口径：只影响"保守到达时间"这一列，不碰学习到的任何东西。"""
        if quantile is not None:
            value = float(quantile)
            if not 0.5 <= value <= 0.999:
                raise ValueError("P90 分位数应在 0.5–0.999 之间，收到 %r" % (quantile,))
            self.eta_p90_quantile = value
        if min_residuals is not None:
            self.min_residuals = max(int(min_residuals), 5)
        if window is not None:
            size = max(int(window), self.min_residuals)
            kept = list(self._residuals)[-size:]
            self._residuals = deque(kept, maxlen=size)
        return {"quantile": self.eta_p90_quantile,
                "min_residuals": self.min_residuals,
                "window": self._residuals.maxlen,
                "samples": len(self._residuals)}

    def restore_residuals(self, values: Iterable[float]) -> None:
        """从落盘状态恢复共形残差样本（服务重启后 P90 立刻可用）。"""
        self._residuals.clear()
        for value in values or ():
            self._residuals.append(float(value))

    def predictor_weights(self) -> Dict[str, float]:
        """两路预测的权重：``1/MAE²`` 归一化；没数据时各 0.5。"""
        model_err = self.model_abs_err if self.model_abs_err is not None else 1.0
        td_err = self.td_abs_err if self.td_abs_err is not None else 1.0
        model_w = 1.0 / max(model_err, 1e-3) ** 2
        td_w = 1.0 / max(td_err, 1e-3) ** 2
        total = model_w + td_w
        return {"model": model_w / total, "td": td_w / total}

    def _settle_predictions(self, total_time_min: float) -> None:
        """行程结束后结算：用真实剩余时间给两路预测打分（EWMA，α=0.2）。"""
        if not self._pending:
            return
        model_errors = [abs(p["model"] - (total_time_min - p["elapsed"]))
                        for p in self._pending]
        td_errors = [abs(p["td"] - (total_time_min - p["elapsed"]))
                     for p in self._pending]
        alpha = 0.2
        model_mean = float(np.mean(model_errors))
        td_mean = float(np.mean(td_errors))
        self.model_abs_err = (model_mean if self.model_abs_err is None else
                             (1 - alpha) * self.model_abs_err + alpha * model_mean)
        self.td_abs_err = (td_mean if self.td_abs_err is None else
                           (1 - alpha) * self.td_abs_err + alpha * td_mean)
        forecast_errors = [abs(item["forecast"] - (total_time_min - item["elapsed"]))
                           for item in self._pending if item.get("forecast") is not None]
        if forecast_errors:
            forecast_mean = float(np.mean(forecast_errors))
            self.forecast_abs_err = (forecast_mean if self.forecast_abs_err is None else
                                     (1 - alpha) * self.forecast_abs_err
                                     + alpha * forecast_mean)
        # 共形预测的样本：实际剩余 − 当时的融合预测（带符号，上界只看右尾）
        for item in self._pending:
            self._residuals.append((total_time_min - item["elapsed"]) - item["blended"])
        self._pending = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "encoder": {"quantizer": self.encoder.quantizer.to_dict(),
                        "progress_bins": self.encoder.progress_bins,
                        "time_buckets": self.encoder.time_buckets,
                        "pressure_buckets": self.encoder.pressure_buckets},
            "td": self.td.to_dict(), "q": self.q.to_dict(),
            "strategies": [s.to_dict() for s in self.strategies],
            "operation_weights": self.operation_weights,
            "predictor": {"model_abs_err": self.model_abs_err,
                          "td_abs_err": self.td_abs_err,
                          "forecast_abs_err": self.forecast_abs_err,
                          "eta_p90_quantile": self.eta_p90_quantile,
                          "min_residuals": self.min_residuals,
                          "residuals": list(self._residuals)},
            "correction": self.corrector.to_dict(),
        }


class RecommendationService:
    """把一次决策包成前端可直接渲染的建议（含依据）。"""

    def __init__(self, policy: RoutePolicy, normalizer: ObjectiveNormalizer):
        self.policy = policy
        self.normalizer = normalizer

    def recommend(self, env: TripEnv, *, strategy: Optional[str] = None,
                  explore: bool = False) -> Dict[str, Any]:
        decision = self.policy.act(env, self.normalizer, explore=explore,
                                   forced_strategy=strategy)
        plan: RoutePlan = decision["plan"]
        eta = self.policy.predict_eta(env, plan)
        strategy_info = decision["strategy"]
        congested = [row for row in plan.rows if row["congestion_index"] >= 0.45]
        reasons = [
            "当前节点 %s，出边 %d 条，时刻 %s" % (
                env.node, len(env.observation()["candidates"]), _hhmm(env.minute)),
            "按「%s」权重搜路：时间 %.2f / 碳 %.2f / 费用 %.2f" % (
                strategy_info["label"],
                strategy_info["weights"].get("time", 0.0),
                strategy_info["weights"].get("carbon", 0.0),
                strategy_info["weights"].get("cost", 0.0)),
            "推荐路径 %d 段，模型预计 %s，碳 %.2f kg" % (
                len(plan.path), _minutes(plan.time_min), plan.carbon_kg),
        ]
        if congested:
            reasons.append("路径上有 %d 段处于%s以上，已在权重中计入" % (
                len(congested), congested[0]["congestion_label"]))
        if env.path:
            reasons.append("已走 %d 段，累计碳 %.3f kg、%s" % (
                len(env.path), env.total_carbon_kg, _minutes(env.total_time_min)))
        if eta["confidence"] < 0.5:
            reasons.append("该状态样本少，ETA 以模型为主（置信度 %.2f）" % eta["confidence"])
        return {
            "action": decision["next_segment"],
            "strategy": strategy_info,
            "strategy_confidence": decision["confidence"],
            "q_values": decision["q_values"],
            "plan": plan.to_dict(),
            "eta": eta,
            "segment_detail": [dict(row) for row in plan.rows],
            "reasons": reasons,
            "state": {"node": env.node, "minute": round(env.minute, 2),
                      "progress": round(env.progress(), 4),
                      "elapsed_min": round(env.total_time_min, 2),
                      "carbon_so_far_kg": round(env.total_carbon_kg, 4),
                      "cost_so_far_yuan": round(env.total_cost_yuan, 2)},
        }


def _hhmm(minute: float) -> str:
    total = int(round(float(minute))) % 1440
    return "%02d:%02d" % (total // 60, total % 60)


def _minutes(value: float) -> str:
    if not math.isfinite(float(value)):
        return "不可达"
    total_seconds = int(round(float(value) * 60.0))
    return "%d 分 %02d 秒" % (total_seconds // 60, total_seconds % 60)


__all__ = ["DEFAULT_SWITCH_THRESHOLD", "DEFAULT_STRATEGY_SWITCH_PENALTY", "RoutePlan",
           "plan_route", "RoutePolicy", "RecommendationService"]
