# -*- coding: utf-8 -*-
"""四维路线进化：NSGA-II + Q-learning 自适应变异 + 记忆库。

输入支持高德原始字段 distance(m)/duration(s)，也支持现有沙盘的
distance_km/duration_min；carbon/carbon_kg 单位为 kg。统一输出 km、min、kg、元。
虚拟进化路线仅用于 What-if，不写入主碳账；只有 save_route_to_memory 才落盘。
"""
from __future__ import annotations

import csv
import math
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.core.callback import Callback
from pymoo.core.problem import Problem
from pymoo.operators.crossover.sbx import SBX
from pymoo.operators.mutation.pm import PM
from pymoo.optimize import minimize

ROOT = Path(__file__).resolve().parents[2]
MEMORY_PATH = ROOT / "data" / "route_memory.csv"
MEMORY_COLUMNS = [
    "route_id", "distance", "duration", "tolls", "carbon", "cost",
    "is_exploration", "is_recommended", "exploration_strategy", "saved_at",
]
_memory_lock = threading.Lock()


def _finite(value: Any, name: str, minimum: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("路线字段 %s 必须是数值" % name) from exc
    if not math.isfinite(number) or number < minimum:
        raise ValueError("路线字段 %s 必须 >= %s 且为有限值" % (name, minimum))
    return number


def _cost(distance: float, tolls: float, carbon: float,
          carbon_price: float, energy_cost_per_km: float) -> float:
    return tolls + distance * energy_cost_per_km + carbon / 1000.0 * carbon_price


def _normalize_routes(routes: Sequence[Dict[str, Any]], carbon_price: float,
                      energy_cost_per_km: float) -> List[Dict[str, Any]]:
    if not isinstance(routes, Sequence) or isinstance(routes, (str, bytes)) or not routes:
        raise ValueError("routes 必须是非空路线列表")
    normalized = []
    for index, route in enumerate(routes, start=1):
        if not isinstance(route, dict):
            raise ValueError("第 %d 条路线不是字典" % index)
        if route.get("distance_km") is not None:
            distance = _finite(route["distance_km"], "distance_km", 0.001)
        else:
            distance = _finite(route.get("distance"), "distance", 0.001) / 1000.0
        if route.get("duration_min") is not None:
            duration = _finite(route["duration_min"], "duration_min", 0.001)
        else:
            duration = _finite(route.get("duration"), "duration", 0.001) / 60.0
        tolls = _finite(route.get("tolls", 0.0), "tolls")
        carbon_value = route.get("carbon", route.get("carbon_kg"))
        carbon = _finite(carbon_value, "carbon", 0.000001)
        energy_rate = energy_cost_per_km
        if route.get("energy_cost") is not None:
            energy_rate = _finite(route["energy_cost"], "energy_cost") / distance
        normalized.append({
            "route_id": route.get("route_id", index), "distance": distance,
            "duration": duration, "tolls": tolls, "carbon": carbon,
            "cost": _cost(distance, tolls, carbon, carbon_price, energy_rate),
            "is_exploration": bool(route.get("is_exploration", False)),
            "is_recommended": False,
            "exploration_strategy": str(route.get("exploration_strategy") or "initial"),
            "_energy_rate": energy_rate,
        })
    return normalized


class RouteProblem(Problem):
    """四维最小化问题：成本、碳排、时间、距离。基因为四项路线指标归一值。"""

    def __init__(self, scale: np.ndarray, carbon_price: float, energy_rate: float):
        self.scale = scale
        self.carbon_price = float(carbon_price)
        self.energy_rate = float(energy_rate)
        super().__init__(n_var=4, n_obj=4, n_constr=0,
                         xl=np.zeros(4), xu=np.ones(4) * 1.25)

    def _evaluate(self, x: np.ndarray, out: Dict[str, Any], *args, **kwargs) -> None:
        genes = np.maximum(np.asarray(x, dtype=float), 0.0) * self.scale
        distance, duration, tolls, carbon = genes.T
        cost = _cost(distance, tolls, carbon, self.carbon_price, self.energy_rate)
        out["F"] = np.column_stack([cost, carbon, duration, distance])


class QLearningAgent:
    """三状态、三动作的轻量智能体；每代依据多样性与改进奖励调整变异率。"""

    def __init__(self, rng: np.random.Generator):
        self.rng = rng
        self.q = np.zeros((3, 3), dtype=float)
        self.previous: Optional[Tuple[int, int]] = None
        self.mutation_rate = 0.2

    @staticmethod
    def state(diversity: float) -> int:
        return 0 if diversity < 0.005 else (1 if diversity < 0.03 else 2)

    def act(self, state: int) -> int:
        if self.rng.random() < 0.2:
            return int(self.rng.integers(0, 3))
        return int(np.argmax(self.q[state]))

    def update(self, state: int, action: int, reward: float, next_state: int) -> None:
        old = self.q[state, action]
        self.q[state, action] = old + 0.25 * (
            reward + 0.85 * float(np.max(self.q[next_state])) - old)


def _signature(route: Dict[str, Any]) -> Tuple[float, float, float, float]:
    return tuple(round(float(route[k]), 3)
                 for k in ("distance", "duration", "carbon", "cost"))


def _memory_signatures(memory: pd.DataFrame) -> set:
    if memory.empty:
        return set()
    return {_signature(row) for row in memory.to_dict("records")}


def _pareto_mask(values: np.ndarray) -> np.ndarray:
    """四维非支配筛选；重复点只保留一个。"""
    count = len(values)
    keep = np.ones(count, dtype=bool)
    for i in range(count):
        if not keep[i]:
            continue
        for j in range(count):
            if i == j:
                continue
            if np.all(values[j] <= values[i]) and np.any(values[j] < values[i]):
                keep[i] = False
                break
    return keep


def load_route_memory(memory_path: Optional[Path] = None) -> pd.DataFrame:
    """读取记忆库；不存在则创建表头，损坏时备份意识下返回空表而不阻断求解。"""
    path = Path(memory_path or MEMORY_PATH)
    with _memory_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.stat().st_size == 0:
            pd.DataFrame(columns=MEMORY_COLUMNS).to_csv(path, index=False, encoding="utf-8-sig")
            return pd.DataFrame(columns=MEMORY_COLUMNS)
        try:
            frame = pd.read_csv(path, encoding="utf-8-sig")
        except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeError):
            return pd.DataFrame(columns=MEMORY_COLUMNS)
    for column in MEMORY_COLUMNS:
        if column not in frame.columns:
            frame[column] = "" if column == "exploration_strategy" else None
    frame["route_id"] = frame["route_id"].map(
        lambda value: "" if pd.isna(value) else str(value))
    return frame[MEMORY_COLUMNS]


def save_route_to_memory(route_data: Dict[str, Any],
                         memory_path: Optional[Path] = None) -> Dict[str, Any]:
    """用户采纳路线后追加写入；返回实际落盘记录。"""
    path = Path(memory_path or MEMORY_PATH)
    load_route_memory(path)  # 确保目录与表头存在
    record = {
        "route_id": str(route_data.get("route_id") or "unknown"),
        "distance": _finite(route_data.get("distance"), "distance", 0.001),
        "duration": _finite(route_data.get("duration"), "duration", 0.001),
        "tolls": _finite(route_data.get("tolls", 0.0), "tolls"),
        "carbon": _finite(route_data.get("carbon"), "carbon", 0.000001),
        "cost": _finite(route_data.get("cost"), "cost"),
        "is_exploration": bool(route_data.get("is_exploration", False)),
        "is_recommended": bool(route_data.get("is_recommended", False)),
        "exploration_strategy": str(route_data.get("exploration_strategy") or "exploitation"),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    with _memory_lock:
        with path.open("a", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(file, fieldnames=MEMORY_COLUMNS)
            writer.writerow(record)
    return record


def evolve_routes(routes: Sequence[Dict[str, Any]], explore_prob: float = 0.1,
                  carbon_price: float = 100.0, energy_cost_per_km: float = 0.0,
                  n_gen: int = 8, pop_size: int = 16,
                  seed: Optional[int] = None,
                  memory_path: Optional[Path] = None,
                  decision_log: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """返回原始路线 + 进化帕累托路线；仅一条 is_recommended=True。

    探索规则：成本前两名差异 <=5% 时，以 explore_prob 推荐次优或重组路线。
    decision_log 传入列表时可收集 Q-learning 每代动作，便于前端展示。
    """
    explore_prob = _finite(explore_prob, "explore_prob")
    carbon_price = _finite(carbon_price, "carbon_price")
    energy_cost_per_km = _finite(energy_cost_per_km, "energy_cost_per_km")
    if explore_prob > 1:
        raise ValueError("explore_prob 必须在 0–1 之间")
    n_gen, pop_size = max(2, int(n_gen)), max(4, int(pop_size))
    rng = np.random.default_rng(seed)
    original = _normalize_routes(routes, carbon_price, energy_cost_per_km)
    energy_rate = float(np.mean([r["_energy_rate"] for r in original]))
    memory = load_route_memory(memory_path)
    history = _memory_signatures(memory)
    logs: List[str] = []

    def log(message: str) -> None:
        logs.append(message)
        if decision_log is not None:
            decision_log.append(message)

    genes = np.array([[r["distance"], r["duration"], r["tolls"], r["carbon"]]
                      for r in original], dtype=float)
    scale = np.maximum(genes.max(axis=0) * 1.25, 1e-6)
    base_sampling = np.clip(genes / scale, 0.0, 1.25)
    # 初始种群始终包含全部高德路线；不足部分用其邻域扰动补齐。
    sampling = np.zeros((pop_size, 4), dtype=float)
    sampling[:len(base_sampling)] = base_sampling
    for i in range(len(base_sampling), pop_size):
        parent = base_sampling[i % len(base_sampling)]
        sampling[i] = np.clip(parent + rng.normal(0, 0.04, 4), 0.0, 1.25)
    problem = RouteProblem(scale, carbon_price, energy_rate)
    agent = QLearningAgent(rng)
    last_diversity = [None]

    class AdaptiveCallback(Callback):
        def notify(self, algorithm: NSGA2) -> None:
            x = np.asarray(algorithm.pop.get("X"), dtype=float)
            diversity = float(np.mean(np.var(x, axis=0))) if len(x) > 1 else 0.0
            state = agent.state(diversity)
            action = agent.act(state)
            if action == 0:
                agent.mutation_rate = max(0.05, agent.mutation_rate - 0.05)
                action_text = "降低变异率，集中利用优质基因"
            elif action == 1:
                action_text = "保持变异率，平衡收敛与探索"
            else:
                agent.mutation_rate = min(0.6, agent.mutation_rate + 0.1)
                action_text = "上调变异率，跳出低多样性区域"
            algorithm.mating.mutation.prob = agent.mutation_rate

            reward = 0.0
            if last_diversity[0] is not None:
                reward += 0.1 if diversity > last_diversity[0] else -0.05
            if state == 0:
                reward -= 0.2
            objectives = np.asarray(algorithm.pop.get("F"), dtype=float)
            if len(objectives):
                best = objectives[np.lexsort((objectives[:, 1], objectives[:, 0]))[0]]
                candidate = {
                    "distance": best[3], "duration": best[2],
                    "carbon": best[1], "cost": best[0],
                }
                is_new = _signature(candidate) not in history
                improved = False
                if not memory.empty:
                    historical_cost = pd.to_numeric(memory["cost"], errors="coerce").min()
                    historical_carbon = pd.to_numeric(memory["carbon"], errors="coerce").min()
                    improved = ((pd.notna(historical_cost) and best[0] < historical_cost * 0.98)
                                or (pd.notna(historical_carbon) and best[1] < historical_carbon * 0.98))
                if is_new and improved:
                    reward += 1.0
            if agent.previous is not None:
                agent.update(agent.previous[0], agent.previous[1], reward, state)
            agent.previous = (state, action)
            log("第 %d 代：多样性 %.5f，%s（变异率 %.2f，奖励 %+.2f）"
                % (algorithm.n_gen, diversity, action_text, agent.mutation_rate, reward))
            last_diversity[0] = diversity

    algorithm = NSGA2(
        pop_size=pop_size, sampling=sampling,
        crossover=SBX(prob=0.9, eta=15), mutation=PM(prob=agent.mutation_rate, eta=20),
        eliminate_duplicates=True,
    )
    try:
        result = minimize(problem, algorithm, ("n_gen", n_gen),
                          callback=AdaptiveCallback(), seed=seed, verbose=False)
    except Exception as exc:
        # 算法失败仍返回原始路线的确定性非支配结果，保证演示不断链。
        log("NSGA-II 降级：%s: %s；改用原始路线帕累托筛选" % (type(exc).__name__, exc))
        result = None

    evolved: List[Dict[str, Any]] = []
    if result is not None and getattr(result, "X", None) is not None:
        result_x = np.atleast_2d(result.X)
        result_f = np.atleast_2d(result.F)
        for index, (x, f) in enumerate(zip(result_x, result_f), start=1):
            distance, duration, tolls, carbon = np.maximum(x, 0.0) * scale
            candidate = {
                "route_id": "evo_%03d" % index,
                "distance": float(distance), "duration": float(duration),
                "tolls": float(tolls), "carbon": float(carbon),
                "cost": float(f[0]), "is_exploration": True, "is_recommended": False,
                "exploration_strategy": "nsga2_q_learning",
                "_energy_rate": energy_rate,
            }
            if _signature(candidate) not in {_signature(r) for r in original}:
                evolved.append(candidate)

    # 对原始解与进化解做四维非支配筛选；原始点保留作对照，进化点仅保留前沿。
    all_candidates = original + evolved
    objectives = np.array([[r["cost"], r["carbon"], r["duration"], r["distance"]]
                           for r in all_candidates])
    pareto = _pareto_mask(objectives)
    candidates = [r for i, r in enumerate(all_candidates)
                  if not r["is_exploration"] or pareto[i]]
    if not candidates:
        candidates = original
    candidates.sort(key=lambda r: (r["cost"], r["carbon"], r["duration"]))

    best = candidates[0]
    second = candidates[1] if len(candidates) > 1 else None
    close = second is not None and abs(second["cost"] - best["cost"]) / max(best["cost"], 1e-9) <= 0.05
    recommended = best
    if close and rng.random() < explore_prob:
        if second is not None and rng.random() < 0.5:
            recommended = second
            recommended["is_exploration"] = True
            recommended["exploration_strategy"] = "epsilon_greedy_second_best"
            log("前两名成本差异 <=5%：Epsilon-Greedy 选择次优路线进行主动探索")
        else:
            alpha = float(rng.uniform(0.25, 0.75))
            hybrid = {
                "route_id": "hybrid_%s" % datetime.now().strftime("%H%M%S%f"),
                "is_exploration": True, "is_recommended": False,
                "exploration_strategy": "recombination_alpha_%.3f" % alpha,
                "_energy_rate": energy_rate,
            }
            for field in ("distance", "duration", "tolls", "carbon"):
                hybrid[field] = alpha * best[field] + (1 - alpha) * second[field]
            hybrid["cost"] = _cost(hybrid["distance"], hybrid["tolls"], hybrid["carbon"],
                                   carbon_price, energy_rate)
            candidates.append(hybrid)
            recommended = hybrid
            log("前两名成本差异 <=5%：执行重组变异，生成 1 条虚拟探索路线")
    else:
        log("采用利用策略：推荐当前四维帕累托候选中的最低成本路线")
    recommended["is_recommended"] = True

    output = []
    for route in candidates:
        item = {key: value for key, value in route.items() if not key.startswith("_")}
        for field in ("distance", "duration", "tolls", "carbon", "cost"):
            item[field] = round(float(item[field]), 6)
        item["route_id"] = str(item["route_id"])
        item["is_exploration"] = bool(item["is_exploration"])
        item["is_recommended"] = bool(item["is_recommended"])
        item["exploration_strategy"] = str(item["exploration_strategy"])
        output.append(item)
    output.sort(key=lambda r: (not r["is_recommended"], r["cost"]))
    log("本次输出 %d 条路线，其中 %d 条进化/探索路线"
        % (len(output), sum(r["is_exploration"] for r in output)))
    return output


__all__ = [
    "RouteProblem", "evolve_routes", "load_route_memory", "save_route_to_memory",
    "MEMORY_PATH", "MEMORY_COLUMNS",
]
