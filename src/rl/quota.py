# -*- coding: utf-8 -*-
"""车队碳配额：让"运营目标"随剩余配额变化（docs/59 §15）。

解决什么问题
------------
v1.1 的运营口径是**固定权重**（时间 0.45 / 碳 0.35 / 费用 0.20）。固定权重下，
"该走哪条路"基本由路况决定，Q 学到的策略与最强静态基线并列——不是算法退化，
是**目标本身不变**，模型自己就能算最优。

真实车队的约束是**会变的**：月初配额宽松时碳几乎不疼；月末配额见底时，
同样的碳排要按越来越高的影子价格买单。于是同一条路，早上该走、晚上可能不该走。
这就是 Q 需要学的东西：**压力档 → 该用哪套目标**。

模型
----
对一张运单，记：

- ``remaining = budget − consumed``：本周期剩余配额（kg）
- ``allowance = remaining / 剩余运单数``：后每单平均还能排多少
- ``ref = budget / trips_total``：本周期平均配额
- ``pressure = ref / allowance``：>1 表示比平均更紧

压力映射到权重（归一化后）：

```
stress = max(pressure − 1, 0)
w_carbon = base_carbon · (1 + 1.5·stress)
w_time   = base_time   · (1 − 0.6·min(stress, 1))
w_cost   = base_cost   · (1 − 0.3·min(stress, 1))     → 再归一化
```

纪律：**权重只影响"裁判"（U），不直接改路由器**。路由器仍只看到 4 套固定权重，
要靠 Q 学会"这个压力档该挑哪套"。否则又是"模型自己会算"，学习没有存在感。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

#: 压力分档上界（用于 Q 的状态离散化）
PRESSURE_BUCKETS = (0.90, 1.10, 1.40)


def pressure_bucket(pressure: float, edges: tuple = PRESSURE_BUCKETS) -> int:
    index = 0
    for edge in edges:
        if pressure < edge:
            return index
        index += 1
    return index


def weights_for_pressure(pressure: float,
                         base: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """压力 → 运营权重。训练里也用这个函数（域随机化），保证训练/评估同一口径。"""
    base = dict(base or {"time": 0.45, "carbon": 0.35, "cost": 0.20})
    total = sum(max(float(v), 0.0) for v in base.values()) or 1.0
    base = {k: max(float(v), 0.0) / total for k, v in base.items()}
    stress = max(min(float(pressure), 3.0) - 1.0, 0.0)
    bump = min(stress, 1.0)
    raw = {
        "time": base.get("time", 0.0) * (1.0 - 0.6 * bump),
        "carbon": base.get("carbon", 0.0) * (1.0 + 1.5 * stress),
        "cost": base.get("cost", 0.0) * (1.0 - 0.3 * bump),
    }
    total = sum(raw.values())
    if total <= 0:
        return dict(base)
    return {key: value / total for key, value in raw.items()}


@dataclass
class FleetQuota:
    """一个周期（日/月）的车队碳配额账本。"""

    budget_kg: float
    trips_total: int
    consumed_kg: float = 0.0
    trips_done: int = 0
    base_weights: Dict[str, float] = field(
        default_factory=lambda: {"time": 0.45, "carbon": 0.35, "cost": 0.20})

    def __post_init__(self) -> None:
        if self.budget_kg <= 0:
            raise ValueError("配额必须为正数")
        if self.trips_total <= 0:
            raise ValueError("运单总数必须为正")
        total = sum(max(v, 0.0) for v in self.base_weights.values())
        if total <= 0:
            raise ValueError("基准权重之和必须为正")
        self.base_weights = {k: max(float(v), 0.0) / total
                             for k, v in self.base_weights.items()}

    # ------------------------------------------------------------------ 台账
    @property
    def remaining_kg(self) -> float:
        return max(self.budget_kg - self.consumed_kg, 0.0)

    @property
    def trips_left(self) -> int:
        return max(self.trips_total - self.trips_done, 1)

    @property
    def reference_kg(self) -> float:
        return self.budget_kg / max(self.trips_total, 1)

    def allowance_kg(self) -> float:
        return self.remaining_kg / self.trips_left

    def pressure(self) -> float:
        """>1 表示比"平均配额"更紧；<1 表示宽裕。

        夹在 `[0.2, 3.0]`：配额耗尽时 allowance→0，比值会发散到 1e9，
        权重会被推到"只看碳"的退化点。夹住是工程处理，不是数学结论。
        """
        allowance = max(self.allowance_kg(), 1e-9)
        return float(min(max(self.reference_kg / allowance, 0.2), 3.0))

    def bucket(self) -> int:
        return pressure_bucket(self.pressure())

    def consume(self, carbon_kg: float) -> None:
        if carbon_kg < 0:
            raise ValueError("碳排不能为负")
        self.consumed_kg += float(carbon_kg)
        self.trips_done += 1

    # ------------------------------------------------------------------ 权重
    def weights(self) -> Dict[str, float]:
        """当前压力下的运营口径权重（归一化）。"""
        return weights_for_pressure(self.pressure(), self.base_weights)

    # ------------------------------------------------------------------ 工具
    def reset(self, budget_kg: Optional[float] = None,
              trips_total: Optional[int] = None) -> "FleetQuota":
        if budget_kg is not None:
            self.budget_kg = float(budget_kg)
        if trips_total is not None:
            self.trips_total = int(trips_total)
        self.consumed_kg = 0.0
        self.trips_done = 0
        return self

    def state(self) -> Dict[str, Any]:
        return {"budget_kg": round(self.budget_kg, 3),
                "consumed_kg": round(self.consumed_kg, 3),
                "remaining_kg": round(self.remaining_kg, 3),
                "trips_done": self.trips_done, "trips_total": self.trips_total,
                "pressure": round(self.pressure(), 4),
                "bucket": self.bucket(),
                "weights": {k: round(v, 4) for k, v in self.weights().items()}}

    def to_dict(self) -> Dict[str, Any]:
        return {"budget_kg": self.budget_kg, "trips_total": self.trips_total,
                "consumed_kg": self.consumed_kg, "trips_done": self.trips_done,
                "base_weights": dict(self.base_weights)}

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "FleetQuota":
        return cls(budget_kg=float(payload["budget_kg"]),
                   trips_total=int(payload["trips_total"]),
                   consumed_kg=float(payload.get("consumed_kg", 0.0)),
                   trips_done=int(payload.get("trips_done", 0)),
                   base_weights=payload.get("base_weights") or None)


__all__ = ["PRESSURE_BUCKETS", "pressure_bucket", "weights_for_pressure", "FleetQuota"]
