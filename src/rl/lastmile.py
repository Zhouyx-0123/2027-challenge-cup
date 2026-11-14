# -*- coding: utf-8 -*-
"""最后一公里：GA+爬山 排单 + 扰动后的剩余子问题重排。

为什么用 GA 而不是表格 Q
------------------------
在 12 个客户、带时间窗的实例上做过对照（docs/69）：GA+爬山 0.5 s 出解、5 个
随机种子全部 0 迟到；表格 Q-learning 需要 4 万回合才能追平，且 5 个种子里
有 1 个仍差 300%。所以本模块用 GA 做排单，Q 只保留为"决策层"的候选方案。

口径
----
- 电动车碳：``kWh/km × 电网因子``（默认 0.25 × 0.5568 = 0.1392 kg/km）；
- 柴油车碳：``L/km × 2.63``（默认 0.12 × 2.63 = 0.3156 kg/km）；
- 成本 = 时间 + 迟到罚 + 等待权重 + 碳权重 × 碳。
所有系数都可从 ``config/rl_policy.json`` 的 ``lastmile`` 段读取。

边界
----
- 本模块不写 data/processed、不改主账；
- 时间窗用分钟表示，场站为第 0 个点；
- ``must_precede`` 支持"先取后送"这类顺序约束。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

SPEED_KMH = 25.0
SERVICE_MIN = 8.0
LATE_PENALTY = 100.0
WAIT_WEIGHT = 0.5
CARBON_WEIGHT = 1.0
EV_KG_PER_KM = 0.25 * 0.5568
DIESEL_KG_PER_KM = 0.12 * 2.63


@dataclass
class Stop:
    idx: int
    x: float
    y: float
    demand_kg: float = 0.0
    window: Tuple[float, float] = (0.0, 1440.0)
    service_min: float = SERVICE_MIN
    must_precede: Tuple[int, ...] = ()


@dataclass
class Instance:
    stops: List[Stop]
    mode: str = "ev"
    congestion: Dict[Tuple[int, int], float] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return len(self.stops) - 1

    @property
    def carbon_per_km(self) -> float:
        return EV_KG_PER_KM if self.mode == "ev" else DIESEL_KG_PER_KM

    def dist(self, i: int, j: int) -> float:
        a, b = self.stops[i], self.stops[j]
        return math.hypot(a.x - b.x, a.y - b.y)

    def travel_min(self, i: int, j: int) -> float:
        return self.dist(i, j) / SPEED_KMH * 60.0 * self.congestion.get((i, j), 1.0)


def make_instance(n: int = 12, seed: int = 7, mode: str = "ev") -> Instance:
    """生成一个可行的时间窗实例：先随机排一个序，再按它算窗（±20 min）。"""
    rng = random.Random(seed)
    pts = [(rng.uniform(0.4, 6.0), rng.uniform(0.4, 6.0)) for _ in range(n)]
    order = list(range(1, n + 1))
    rng.shuffle(order)
    arrive: Dict[int, float] = {}
    t, prev = 0.0, 0
    for c in order:
        px, py = pts[c - 1]
        qx, qy = (0.0, 0.0) if prev == 0 else pts[prev - 1]
        t += math.hypot(px - qx, py - qy) / SPEED_KMH * 60.0
        arrive[c] = t
        t += SERVICE_MIN
        prev = c
    stops = [Stop(0, 0.0, 0.0, 0.0, (0.0, 1440.0), 0.0)]
    for c in range(1, n + 1):
        x, y = pts[c - 1]
        stops.append(Stop(c, x, y, rng.uniform(5, 30),
                          (max(0.0, arrive[c] - 20.0), arrive[c] + 20.0)))
    return Instance(stops=stops, mode=mode)


def route_metrics(inst: Instance, route: Sequence[int]) -> Dict[str, float]:
    t, late_min, wait, km = 0.0, 0.0, 0.0, 0.0
    prev = 0
    for c in route:
        leg = inst.travel_min(prev, c)
        km += inst.dist(prev, c)
        t += leg
        e, l = inst.stops[c].window
        if t < e:
            wait += e - t
            t = e
        if t > l:
            late_min += t - l
        t += inst.stops[c].service_min
        prev = c
    km += inst.dist(prev, 0)
    t += inst.travel_min(prev, 0)
    carbon = km * inst.carbon_per_km
    cost = t + LATE_PENALTY * late_min + WAIT_WEIGHT * wait + CARBON_WEIGHT * carbon
    return {"cost": cost, "time_min": t, "late_min": late_min,
            "wait_min": wait, "km": km, "carbon_kg": carbon}


def feasible(inst: Instance, route: Sequence[int]) -> bool:
    pos = {c: i for i, c in enumerate(route)}
    for c in route:
        for p in inst.stops[c].must_precede:
            # c 必须先于 p：若 c 排在 p 后面，则违反约束
            if p in pos and pos[c] > pos[p]:
                return False
    return True


def ga_hc(inst: Instance, seed: int = 1, pop: int = 60, gens: int = 250
          ) -> Tuple[List[int], Dict[str, float]]:
    """遗传算法（OX 交叉 + 交换变异）+ 2-opt 爬山。"""
    rng = random.Random(seed)
    n = inst.n
    base = list(range(1, n + 1))

    def fit(r):
        return route_metrics(inst, r)["cost"]

    population = [rng.sample(base, n) for _ in range(pop)]
    best = min(population, key=fit)
    for _ in range(gens):
        population.sort(key=fit)
        new = population[:5]
        while len(new) < pop:
            p1 = min(rng.sample(population[:30], 2), key=fit)
            p2 = min(rng.sample(population[:30], 2), key=fit)
            a, b = sorted(rng.sample(range(n), 2))
            child = [x for x in p1 if x not in p2[a:b]]
            child[a:a] = p2[a:b]
            if rng.random() < 0.3:
                i, j = rng.sample(range(n), 2)
                child[i], child[j] = child[j], child[i]
            new.append(child)
        population = new
        cand = min(population, key=fit)
        if fit(cand) < fit(best):
            best = cand
        improved = True
        while improved:
            improved = False
            for i in range(n - 1):
                for j in range(i + 2, n + 1):
                    cand = best[:i] + best[i:j][::-1] + best[j:]
                    if fit(cand) < fit(best) - 1e-9:
                        best, improved = cand, True
    return best, route_metrics(inst, best)


def residual_instance(inst: Instance, served: Sequence[int], current: int,
                      elapsed_min: float) -> Tuple[Instance, Dict[int, int]]:
    """把「当前位置 + 未送点」建成新实例，时间窗整体平移 −elapsed_min。"""
    dep = inst.stops[current]
    stops = [Stop(0, dep.x, dep.y, 0.0, (0.0, 1440.0), 0.0)]
    mapping: Dict[int, int] = {}
    for c in range(1, inst.n + 1):
        if c in served:
            continue
        s = inst.stops[c]
        stops.append(Stop(len(stops), s.x, s.y, s.demand_kg,
                          (max(0.0, s.window[0] - elapsed_min), s.window[1] - elapsed_min),
                          s.service_min))
        mapping[len(stops) - 1] = c
    return Instance(stops=stops, mode=inst.mode, congestion={}), mapping


@dataclass
class Plan:
    route: List[int]
    metrics: Dict[str, float]
    seconds: float
    algorithm: str


class LastMileSolver:
    """排单 + 扰动重排；所有结果都是纯数据，方便写进报告。"""

    def __init__(self, inst: Instance, seed: int = 7):
        self.inst = inst
        self.seed = seed

    def plan(self) -> Plan:
        t0 = _now()
        route, metrics = ga_hc(self.inst, seed=self.seed)
        return Plan(route, metrics, _now() - t0, "GA+HC")

    def replan(self, new_inst: Instance, served: Sequence[int],
               current: int = 0, elapsed_min: float = 0.0) -> Plan:
        """扰动后：剩余点子问题重解（GA）。"""
        residual, mapping = residual_instance(new_inst, served, current, elapsed_min)
        t0 = _now()
        route, metrics = ga_hc(residual, seed=self.seed)
        return Plan([mapping[c] for c in route], metrics, _now() - t0, "GA+HC(剩余)")


def _now() -> float:
    import time
    return time.perf_counter()
