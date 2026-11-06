# -*- coding: utf-8 -*-
"""学习器：多目标 TD 值函数（预测层）+ 策略 Q-learning（决策层）。

分两层不是凑复杂度，是"一个表装不下两件事"
==========================================
把「时间/碳/费用」三个目标塞进一个表的 Q-learning 需要给每个目标定死权重，
而权重本身会随工况变（高峰该让时间优先、空载夜行该让碳优先）。所以拆成：

- **预测层**：``MultiObjectiveTD`` 学 ``V(s) = (剩余时间, 剩余碳, 剩余费用)``。
  它是**向量值**的，不做标量化，因此不需要权重；ETA、碳排放预测、置信区间
  都从这里出。TD(λ) 的资格迹让"多段之后才知道堵不堵"的信用分配一次算清。
- **决策层**：``StrategyQLearner`` 学 ``Q(s, 策略)``，策略 = 一套权重预设。
  奖励是真实运营口径（默认 0.45 时间 / 0.35 碳 / 0.20 费用）下的综合成本，
  即"在当前工况下，用哪套权重去搜路最划算"。

这样 Q 表只有 ``状态簇 × 4 个策略``，几十行就能收敛；ETA 与碳预测则共享
同一个值函数，不需要再训一个神经网络。**这与"用 Q-learning 直接背整张
路网"的区别是：本方案不依赖状态-动作全枚举，冷启动路段也有预测。**

关键实现细节
------------
- **Double Q**：取最大值的那一步本身带噪声（拥堵数据抖动），单 Q 会系统性高估，
  故用两套 Q 交替做"选动作"和"估价值"。
- **资格迹**：``e ← γλe + 1``，一次回溯更新整条轨迹，避免"最后一段的教训
  传不回第一段"。
- **访问计数**：``visits`` 用于置信度输出（前端显示"样本少，仅供参考"）。
- **状态编码**：``cluster(工况簇) × 进度档 × 时段档``，三个维度都来自
  config，换 k 不用改代码。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .clustering import StateQuantizer

#: 目标顺序固定：时间(min) / 碳(kg) / 费用(元)。改顺序要 bump 状态版本。
OBJECTIVES: Tuple[str, str, str] = ("time", "carbon", "cost")
#: 进度档 / 时段档默认数量
DEFAULT_PROGRESS_BINS = 5
DEFAULT_TIME_BUCKETS = 8


@dataclass(frozen=True)
class Strategy:
    """一套权重预设。权重作用在**归一化后**的目标上，所以量纲无关。"""

    sid: str
    label: str
    weights: Dict[str, float]
    description: str = ""

    def vector(self) -> np.ndarray:
        return np.array([float(self.weights.get(k, 0.0)) for k in OBJECTIVES], dtype=float)

    def scalarize(self, normalized: Sequence[float]) -> float:
        return float(np.dot(self.vector(), np.asarray(normalized, dtype=float)))

    def to_dict(self) -> Dict[str, Any]:
        return {"sid": self.sid, "label": self.label,
                "weights": dict(self.weights), "description": self.description}


#: 四套预设策略。为什么是 4 套而不是连续权重：Q 表要有限动作；
#: 4 套已覆盖"赶时间/减碳/均衡/抗堵"四种运营诉求，且每套都能向司机解释。
DEFAULT_STRATEGIES: Tuple[Strategy, ...] = (
    Strategy("time", "时间优先", {"time": 1.00, "carbon": 0.20, "cost": 0.30},
             "赶时效，碳与费用只做兜底"),
    Strategy("carbon", "低碳优先", {"time": 0.30, "carbon": 1.00, "cost": 0.25},
             "配额吃紧或披露口径敏感时用"),
    Strategy("balanced", "均衡", {"time": 0.55, "carbon": 0.55, "cost": 0.45},
             "日常默认，三维度都不吃亏"),
    Strategy("smooth", "抗堵稳健", {"time": 0.75, "carbon": 0.35, "cost": 0.55},
             "事故/高峰时段优先绕开拥堵与收费"),
)

#: 运营口径（"真实成本"）：Q 学习的奖励按这套权重算，与策略预设解耦。
DEFAULT_OPERATION_WEIGHTS: Dict[str, float] = {"time": 0.45, "carbon": 0.35, "cost": 0.20}


class ObjectiveNormalizer:
    """按"本单参考路线"归一化三个目标，让权重可解释、可跨单比较。"""

    def __init__(self, ref_time_min: float, ref_carbon_kg: float, ref_cost_yuan: float):
        self.ref = np.array([max(float(ref_time_min), 1e-3),
                             max(float(ref_carbon_kg), 1e-3),
                             max(float(ref_cost_yuan), 1e-3)], dtype=float)

    @classmethod
    def from_totals(cls, time_min: float, carbon_kg: float, cost_yuan: float
                    ) -> "ObjectiveNormalizer":
        return cls(time_min, carbon_kg, cost_yuan)

    def normalize(self, vector: Sequence[float]) -> np.ndarray:
        return np.asarray(vector, dtype=float) / self.ref

    def denormalize(self, vector: Sequence[float]) -> np.ndarray:
        return np.asarray(vector, dtype=float) * self.ref

    def to_dict(self) -> Dict[str, float]:
        return {"ref_time_min": float(self.ref[0]), "ref_carbon_kg": float(self.ref[1]),
                "ref_cost_yuan": float(self.ref[2])}

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ObjectiveNormalizer":
        return cls(payload["ref_time_min"], payload["ref_carbon_kg"],
                   payload["ref_cost_yuan"])


class StateEncoder:
    """连续特征 → 离散格子：``cluster × progress_bin × time_bucket``。"""

    def __init__(self, quantizer: StateQuantizer, progress_bins: int = DEFAULT_PROGRESS_BINS,
                 time_buckets: int = DEFAULT_TIME_BUCKETS,
                 pressure_buckets: int = 1):
        self.quantizer = quantizer
        self.progress_bins = max(int(progress_bins), 1)
        self.time_buckets = max(int(time_buckets), 1)
        # 配额压力只进"决策层"（Q 状态），不进预测层：ETA 不该随配额变
        self.pressure_buckets = max(int(pressure_buckets), 1)

    @property
    def n_states(self) -> int:
        return self.quantizer.k * self.progress_bins * self.time_buckets

    @property
    def n_q_states(self) -> int:
        return self.quantizer.k * self.time_buckets * self.pressure_buckets

    def encode(self, features: Sequence[float], progress: float,
               minute_of_day: float, pressure: float = 0.0
               ) -> Tuple[int, int, Dict[str, Any]]:
        cluster, distance = self.quantizer.encode(features)
        progress = min(max(float(progress), 0.0), 0.999)
        progress_bin = min(int(progress * self.progress_bins), self.progress_bins - 1)
        bucket_minutes = 1440.0 / self.time_buckets
        bucket = int((float(minute_of_day) % 1440.0) // bucket_minutes)
        bucket = min(bucket, self.time_buckets - 1)
        idx = (cluster * self.progress_bins + progress_bin) * self.time_buckets + bucket
        from .quota import pressure_bucket
        p_bucket = pressure_bucket(float(pressure)) if self.pressure_buckets > 1 else 0
        p_bucket = min(p_bucket, self.pressure_buckets - 1)
        sidx = (cluster * self.time_buckets + bucket) * self.pressure_buckets + p_bucket
        return idx, sidx, {"cluster": cluster, "cluster_distance": round(distance, 4),
                           "progress_bin": progress_bin, "time_bucket": bucket,
                           "pressure_bucket": p_bucket}


class MultiObjectiveTD:
    """向量值 TD(λ)：学习「从当前状态到终点还要花多少时间/碳/钱」。

    **默认 γ = 1.0**（无折扣）。这不是笔误：值函数要当 ETA 用，折现会让
    「剩余时间」被乘上 γ^k 而系统性偏低（γ=0.95、10 段就低估约 30%）。
    折扣只在决策层有意义（策略 Q 用 γ=0.9 摊薄远期），预测层必须无折扣。
    """

    def __init__(self, n_states: int, gamma: float = 1.00, alpha: float = 0.20,
                 lam: float = 0.70, alpha_var: float = 0.05):
        if n_states < 1:
            raise ValueError("n_states 必须 >= 1")
        self.n_states = int(n_states)
        self.gamma = float(gamma)
        self.alpha = float(alpha)
        self.lam = float(lam)
        self.alpha_var = float(alpha_var)
        self.V = np.zeros((self.n_states, 3), dtype=float)
        self.Var = np.zeros((self.n_states, 3), dtype=float)
        self.abs_err = np.zeros((self.n_states, 3), dtype=float)
        self.traces = np.zeros(self.n_states, dtype=float)
        self.visits = np.zeros(self.n_states, dtype=int)

    def start_episode(self) -> None:
        self.traces[:] = 0.0

    def update(self, state: int, costs: Sequence[float], next_state: Optional[int] = None,
               terminal: bool = False) -> Dict[str, Any]:
        """走完一段后调用：``costs`` 是该段实际花费（时间/碳/费用）。"""
        if not 0 <= state < self.n_states:
            raise IndexError("state 越界：%s" % state)
        cost = np.asarray(costs, dtype=float)
        if cost.shape != (3,):
            raise ValueError("costs 必须是长度 3 的向量")
        if cost.min() < 0:
            raise ValueError("costs 不能为负")
        next_value = np.zeros(3) if terminal else self.V[int(next_state)]
        delta = cost + self.gamma * next_value - self.V[state]
        self.traces *= self.gamma * self.lam
        self.traces[state] += 1.0
        self.V += self.alpha * delta * self.traces[:, None]
        self.Var += self.alpha_var * (delta * delta - self.Var[state]) * self.traces[:, None]
        self.Var = np.maximum(self.Var, 0.0)
        # 区间宽度用「|TD 误差| 的滑动平均」而不是 σ：真实路况的偏差是系统性的，
        # 用正态 σ 算出来的 P90 覆盖率只有 0.37（实测），会被司机骂"每次都低估"。
        self.abs_err[state] += self.alpha_var * (np.abs(delta) - self.abs_err[state])
        self.visits[state] += 1
        self._last_delta = delta
        return {"td_error": [round(float(x), 4) for x in delta]}

    def predict(self, state: int) -> Dict[str, float]:
        """预测剩余量 + 不确定性（±1.96σ，近似 95% 区间）。"""
        value = self.V[int(state)]
        var = np.maximum(self.Var[int(state)], 0.0)
        std = np.sqrt(var)
        return {
            "remaining_time_min": float(value[0]),
            "remaining_carbon_kg": float(value[1]),
            "remaining_cost_yuan": float(value[2]),
            "time_std_min": float(std[0]),
            "time_abs_err_min": float(self.abs_err[int(state), 0]),
            "carbon_std_kg": float(std[1]),
            "cost_std_yuan": float(std[2]),
            "time_p90_min": float(value[0] + 1.645 * self.abs_err[int(state), 0]),
            "visits": int(self.visits[int(state)]),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {"n_states": self.n_states, "gamma": self.gamma, "alpha": self.alpha,
                "lambda": self.lam, "V": self.V.tolist(), "Var": self.Var.tolist(),
                "abs_err": self.abs_err.tolist(), "visits": self.visits.tolist()}

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "MultiObjectiveTD":
        obj = cls(int(payload["n_states"]), float(payload.get("gamma", 0.95)),
                  float(payload.get("alpha", 0.2)), float(payload.get("lambda", 0.7)))
        obj.V = np.asarray(payload["V"], dtype=float).reshape(obj.n_states, 3)
        obj.Var = np.asarray(payload["Var"], dtype=float).reshape(obj.n_states, 3)
        obj.abs_err = np.asarray(payload.get("abs_err", obj.Var), dtype=float).reshape(obj.n_states, 3)
        obj.visits = np.asarray(payload.get("visits", np.zeros(obj.n_states)), dtype=int)
        return obj


class StrategyQLearner:
    """表格式 Q-learning：状态 = 工况簇 × 时段，动作 = 策略预设。"""

    def __init__(self, n_states: int, n_actions: int, gamma: float = 0.90,
                 alpha: float = 0.15, epsilon: float = 0.20, epsilon_min: float = 0.02,
                 epsilon_decay: float = 0.995, double: bool = True,
                 seed: int = 20261103):
        if n_states < 1 or n_actions < 1:
            raise ValueError("n_states / n_actions 必须 >= 1")
        self.n_states = int(n_states)
        self.n_actions = int(n_actions)
        self.gamma = float(gamma)
        self.alpha = float(alpha)
        self.epsilon = float(epsilon)
        self.epsilon_min = float(epsilon_min)
        self.epsilon_decay = float(epsilon_decay)
        self.double = bool(double)
        self.Q1 = np.zeros((self.n_states, self.n_actions), dtype=float)
        self.Q2 = np.zeros((self.n_states, self.n_actions), dtype=float)
        self.visits = np.zeros((self.n_states, self.n_actions), dtype=int)
        self.rng = np.random.default_rng(int(seed))

    @property
    def Q(self) -> np.ndarray:
        return (self.Q1 + self.Q2) / 2.0

    def q_values(self, state: int) -> np.ndarray:
        return self.Q[int(state)].copy()

    def select(self, state: int, epsilon: Optional[float] = None,
               allowed: Optional[Sequence[int]] = None) -> int:
        eps = self.epsilon if epsilon is None else float(epsilon)
        actions = list(allowed) if allowed is not None else list(range(self.n_actions))
        if not actions:
            raise ValueError("可选动作为空")
        if self.rng.random() < eps:
            return int(actions[int(self.rng.integers(0, len(actions)))])
        values = self.Q[int(state)]
        best = max(values[a] for a in actions)
        tied = [a for a in actions if values[a] >= best - 1e-12]
        return int(tied[int(self.rng.integers(0, len(tied)))])

    def update(self, state: int, action: int, reward: float,
               next_state: Optional[int] = None, terminal: bool = False) -> float:
        """Double Q：用「正在更新的那套表」选下一动作，用「另一套表」估价值。"""
        if self.double and self.rng.random() < 0.5:
            table, other = self.Q1, self.Q2
        else:
            table, other = self.Q2, self.Q1
        if terminal:
            best_next = 0.0
        else:
            a_star = int(np.argmax(table[int(next_state)]))
            best_next = float(self.gamma * other[int(next_state), a_star])
        target = float(reward) + best_next
        error = target - table[int(state), int(action)]
        table[int(state), int(action)] += self.alpha * error
        self.visits[int(state), int(action)] += 1
        return float(error)

    def decay_epsilon(self) -> None:
        self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)

    def best_strategy(self, state: int) -> int:
        return int(np.argmax(self.Q[int(state)]))

    def confidence(self, state: int, action: int) -> float:
        """样本量置信度（0–1）：``1 - exp(-n/20)``，前端据此决定是否加"仅供参考"。"""
        return float(1.0 - math.exp(-self.visits[int(state), int(action)] / 20.0))

    def to_dict(self) -> Dict[str, Any]:
        return {"n_states": self.n_states, "n_actions": self.n_actions,
                "gamma": self.gamma, "alpha": self.alpha, "epsilon": self.epsilon,
                "epsilon_min": self.epsilon_min, "epsilon_decay": self.epsilon_decay,
                "double": self.double, "Q1": self.Q1.tolist(), "Q2": self.Q2.tolist(),
                "visits": self.visits.tolist()}

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "StrategyQLearner":
        obj = cls(int(payload["n_states"]), int(payload["n_actions"]),
                  float(payload.get("gamma", 0.9)), float(payload.get("alpha", 0.15)),
                  float(payload.get("epsilon", 0.2)),
                  float(payload.get("epsilon_min", 0.02)),
                  float(payload.get("epsilon_decay", 0.995)),
                  bool(payload.get("double", True)))
        obj.Q1 = np.asarray(payload["Q1"], dtype=float).reshape(obj.n_states, obj.n_actions)
        obj.Q2 = np.asarray(payload["Q2"], dtype=float).reshape(obj.n_states, obj.n_actions)
        obj.visits = np.asarray(payload.get("visits", np.zeros_like(obj.Q1)), dtype=int)
        return obj


class ResidualCorrector:
    """在线估计「实际 / 模型」的比值，把模型偏差学回来。

    为什么需要它（docs/59 §14.1）
    ---------------------------
    路由（Dijkstra）用的边权是模型算的：	 = 里程 / 快照车速。但真实世界
    模型必然偏——最典型的是**匝道/合流段的排队**：模型按自由流算 0.56 min，
    实际要 0.85 min。模型自己永远不会知道这件事，于是它会把高速方案算便宜、
    把车导上高架，而真实最快的是地面。这个偏差**只能从实测反推**：

        比值 = 实际 / 模型，按 (道路等级 × 时段) 分组做 EWMA

    学到的比值回灌给 `SegmentCostModel`，路由用的边权就变成
    	_corrected = t_model × ratio。于是学习第一次真正改变了**选哪条路**，
    而不只是改变 ETA 的显示数字。

    与 Q 表的分工：Q 学该偏向哪个目标，本类学模型哪里算错了。两者都从
    实测更新，但一个作用在权重、一个作用在边权。
    """

    MAX_CLASSES = 5

    def __init__(self, n_buckets: int = 8, alpha: float = 0.25,
                 min_samples: int = 3, floor: float = 0.5, ceiling: float = 2.5,
                 time_ratio: Optional[np.ndarray] = None,
                 carbon_ratio: Optional[np.ndarray] = None,
                 counts: Optional[np.ndarray] = None):
        self.n_buckets = max(int(n_buckets), 1)
        self.alpha = float(alpha)
        self.min_samples = max(int(min_samples), 1)
        self.floor = float(floor)
        self.ceiling = float(ceiling)
        shape = (self.MAX_CLASSES, self.n_buckets)
        self.time_ratio = (np.ones(shape) if time_ratio is None
                           else np.asarray(time_ratio, dtype=float).reshape(shape))
        self.carbon_ratio = (np.ones(shape) if carbon_ratio is None
                             else np.asarray(carbon_ratio, dtype=float).reshape(shape))
        self.counts = (np.zeros(shape, dtype=int) if counts is None
                       else np.asarray(counts, dtype=int).reshape(shape))

    # ------------------------------------------------------------------ 工具
    def bucket(self, minute: float) -> int:
        index = int((float(minute) % 1440.0) // (1440.0 / self.n_buckets))
        return min(index, self.n_buckets - 1)

    @staticmethod
    def _index(road_class: int) -> int:
        return min(max(int(road_class), 0), ResidualCorrector.MAX_CLASSES - 1)

    def _clip(self, value: float) -> float:
        return float(min(max(value, self.floor), self.ceiling))

    # ------------------------------------------------------------------ 更新
    def observe(self, road_class: int, minute: float, model_time: float,
                actual_time: float, model_carbon: float = 0.0,
                actual_carbon: float = 0.0) -> Dict[str, float]:
        """用一次真实行程观测更新两组比值；样本越少步长越大（早期快速收敛）。"""
        row = self._index(road_class)
        column = self.bucket(minute)
        count = int(self.counts[row, column])
        step = max(self.alpha, 1.0 / (count + 1))
        updated: Dict[str, float] = {}
        if model_time > 1e-9 and actual_time > 0:
            ratio = self._clip(actual_time / model_time)
            self.time_ratio[row, column] += step * (ratio - self.time_ratio[row, column])
            updated["time"] = round(float(self.time_ratio[row, column]), 4)
        if model_carbon > 1e-12 and actual_carbon > 0:
            ratio = self._clip(actual_carbon / model_carbon)
            self.carbon_ratio[row, column] += step * (
                ratio - self.carbon_ratio[row, column])
            updated["carbon"] = round(float(self.carbon_ratio[row, column]), 4)
        self.counts[row, column] = count + 1
        return updated

    # ------------------------------------------------------------------ 查询
    def _factor(self, table: np.ndarray, road_class: int, minute: float) -> float:
        row = self._index(road_class)
        column = self.bucket(minute)
        if self.counts[row, column] < self.min_samples:
            return 1.0
        return float(table[row, column])

    def factor_time(self, road_class: int, minute: float) -> float:
        return self._factor(self.time_ratio, road_class, minute)

    def factor_carbon(self, road_class: int, minute: float) -> float:
        return self._factor(self.carbon_ratio, road_class, minute)

    def readiness(self) -> float:
        """已标定的格子占比（0–1），前端可用于"修正是否可信"。"""
        return float(np.mean(self.counts >= self.min_samples))

    # ------------------------------------------------------------ 序列化/构造
    def to_dict(self) -> Dict[str, Any]:
        return {"n_buckets": self.n_buckets, "alpha": self.alpha,
                "min_samples": self.min_samples, "floor": self.floor,
                "ceiling": self.ceiling, "time_ratio": self.time_ratio.tolist(),
                "carbon_ratio": self.carbon_ratio.tolist(),
                "counts": self.counts.tolist()}

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ResidualCorrector":
        return cls(n_buckets=int(payload.get("n_buckets", 8)),
                   alpha=float(payload.get("alpha", 0.25)),
                   min_samples=int(payload.get("min_samples", 3)),
                   floor=float(payload.get("floor", 0.5)),
                   ceiling=float(payload.get("ceiling", 2.5)),
                   time_ratio=payload.get("time_ratio"),
                   carbon_ratio=payload.get("carbon_ratio"),
                   counts=payload.get("counts"))

    @classmethod
    def from_bias_table(cls, table: Dict[str, Any],
                        n_buckets: int = 8) -> "ResidualCorrector":
        """用"真实偏差表"造一个 oracle 修正器：事后最优基线专用。"""
        obj = cls(n_buckets=n_buckets, min_samples=1)
        for key, attr in (("time", obj.time_ratio), ("carbon", obj.carbon_ratio)):
            rows = (table or {}).get(key) or {}
            for road_class, values in rows.items():
                index = obj._index(int(road_class))
                values = list(values)
                if len(values) < n_buckets:
                    values = values + [values[-1] if values else 1.0] * (n_buckets - len(values))
                attr[index, :] = values[:n_buckets]
        obj.counts[:] = 10 ** 6
        return obj


__all__ = ["OBJECTIVES", "DEFAULT_PROGRESS_BINS", "DEFAULT_TIME_BUCKETS", "Strategy",
           "DEFAULT_STRATEGIES", "DEFAULT_OPERATION_WEIGHTS", "ObjectiveNormalizer",
           "StateEncoder", "MultiObjectiveTD", "StrategyQLearner", "ResidualCorrector"]
