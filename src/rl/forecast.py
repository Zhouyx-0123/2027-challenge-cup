# -*- coding: utf-8 -*-
"""15 分钟路段速度预报（docs/59 §16）。

为什么用"物理外推"而不是再训一个时序模型
----------------------------------------
我们要回答的不是"这条路明天几点最堵"，而是"**我们这辆车开到那儿时，它多快**"，
预测窗口只有 10–20 分钟。在这个尺度上，可靠的信息只有三条：

1. 现在的速度（BPR 后的结果）；
2. 时段需求曲线的走向（早晚高峰在涨还是在落）；
3. 有没有事故、事故还剩多久。

于是做法是：把当前速度**反解**成 v/c，按需求曲线比例缩放，再**正解**回速度。
不需要训练数据、没有冷启动问题，误差可解释；要比"保持当前速度"（persistence）
准，才允许它影响 ETA。

核心公式（BPR 反解）：

```
vc_now  = ((v_free_eff / v_now − 1) / 0.15) ** 0.25
vc_pred = vc_now · demand(t+Δ) / demand(t)
v_pred  = v_free_eff / (1 + 0.15 · vc_pred⁴)
```

``v_free_eff`` 已经乘过天气降速系数，所以雨雾天不会把"降水后仍按晴天上限"外推。
事故的处理是**分段**：预测到达时事故还没清 → 保持当前速度；已清 → 按 0.65 的
残余系数恢复（排队要时间消散），这是假设，写在这里而不是藏在代码里。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np

from .network import (BPR_ALPHA, MIN_SPEED_KMH, WEATHER_FACTORS, RoadNetwork,
                      RoadSegment, SegmentState, TrafficSnapshot, TrafficTimeline,
                      demand_scale)

#: 事故清除后的残余拥堵系数（排队消散需要时间）
INCIDENT_RECOVERY = 0.65


class TrafficForecaster:
    """按需求曲线外推路段速度；无训练、可复现、可解释。"""

    def __init__(self, network: RoadNetwork, *, profile: str = "urban",
                 volume: float = 1.0, weather: str = "clear",
                 horizon_min: float = 15.0):
        self.network = network
        self.profile = str(profile)
        self.volume = float(volume)
        self.weather = str(weather)
        self.horizon_min = float(horizon_min)

    # ------------------------------------------------------------------ 工具
    @property
    def speed_factor(self) -> float:
        return float(WEATHER_FACTORS.get(self.weather, WEATHER_FACTORS["clear"])[0])

    def _vc_now(self, segment: RoadSegment, speed_kmh: float) -> float:
        free = max(segment.free_speed_kmh * self.speed_factor, 1.0)
        speed = min(max(float(speed_kmh), MIN_SPEED_KMH), free)
        ratio = free / max(speed, 1e-6)
        return max(((ratio - 1.0) / BPR_ALPHA), 0.0) ** 0.25

    def _incident_factor(self, state: SegmentState, delta_min: float) -> float:
        if not state.incident:
            return 1.0
        if delta_min <= state.incident_min_left:
            return 1.0
        return INCIDENT_RECOVERY

    # ------------------------------------------------------------------ 预测
    def speed_at(self, segment: RoadSegment, state: SegmentState,
                 now_minute: float, delta_min: float) -> float:
        """预测 ``now + delta`` 时刻该路段的速度（km/h）。"""
        delta = max(float(delta_min), 0.0)
        if delta <= 0:
            return float(state.speed_kmh)
        now_demand = max(demand_scale(now_minute, self.profile), 1e-6)
        future_demand = max(demand_scale(now_minute + delta, self.profile), 1e-6)
        vc_pred = (self._vc_now(segment, state.speed_kmh)
                   * (future_demand / now_demand)
                   * self._incident_factor(state, delta))
        free = segment.free_speed_kmh * self.speed_factor
        speed = free / (1.0 + BPR_ALPHA * vc_pred ** 4)
        return float(min(max(speed, MIN_SPEED_KMH), free))

    def travel_time_min(self, path: Sequence[str], snapshot: TrafficSnapshot,
                        start_minute: float, default_segment_min: float = 0.0
                        ) -> float:
        """沿一条路径做**时间依赖**累加：每段用"到达它时"的预测速度。"""
        minute = float(start_minute)
        total = 0.0
        for sid in path:
            segment = self.network.segment(sid)
            state = snapshot.state(sid)
            delta = minute - start_minute
            speed = self.speed_at(segment, state, start_minute, delta)
            leg = segment.length_km / max(speed, 1e-6) * 60.0
            total += leg
            minute += leg
        return float(total)

    def speed_mae(self, timeline: TrafficTimeline, samples: Sequence[Any],
                  horizon_min: Optional[float] = None, step_min: int = 3
                  ) -> Dict[str, float]:
        """对照真值算速度 MAE，并与"保持当前速度"的 persistence 基线比。

        ``samples`` 是 ``(sid, minute)`` 序列；返回 ``{"forecast": x, "persistence": y}``。
        """
        horizon = float(self.horizon_min if horizon_min is None else horizon_min)
        errors_forecast: List[float] = []
        errors_persist: List[float] = []
        for sid, minute in samples:
            segment = self.network.segment(sid)
            state = timeline.at(minute).state(sid)
            truth = timeline.at(minute + horizon).state(sid).speed_kmh
            predicted = self.speed_at(segment, state, minute, horizon)
            errors_forecast.append(abs(predicted - truth))
            errors_persist.append(abs(state.speed_kmh - truth))
        if not errors_forecast:
            raise ValueError("没有可评估的样本")
        return {"samples": len(errors_forecast), "horizon_min": horizon,
                "forecast_mae_kmh": round(float(np.mean(errors_forecast)), 4),
                "persistence_mae_kmh": round(float(np.mean(errors_persist)), 4)}


def sample_points(network: RoadNetwork, minutes: Iterable[float],
                  sids: Optional[Sequence[str]] = None) -> List[Any]:
    """生成 \"(路段, 时刻)\" 评估样本（每隔 step 分钟一条，覆盖指定路段）。"""
    targets = list(sids or list(network.segments))
    return [(sid, float(minute)) for minute in minutes for sid in targets]


__all__ = ["TrafficForecaster", "sample_points", "INCIDENT_RECOVERY"]
