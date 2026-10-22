# -*- coding: utf-8 -*-
"""道路等级 proxy 分类（docs/35 §6）。

定位：**这是 proxy，不是官方等级**。

OSRM 公共实例不返回可靠的 ``road_class``/``surface`` 字段（实测响应里
压根没有这两个 key），所以等级只能从 OSRM 给的 **name / ref / 速度 / 距离 /
maneuver** 反推。规则按 docs/35 §6 实现，输出三件事：

* ``road_class``       —— 等级标签
* ``road_class_source``—— 判定依据（可复核）
* ``road_class_confidence`` —— 置信度 high / medium / low

.. warning:: 两个必须记住的坑

   1. **G 开头不等于高速。** ``G15``（沈海高速）是高速，``G103``（京台线）
      是国道。判据是 **数字位数**：G + 1~2 位 = 高速，G + 3 位 = 国道。
   2. **道路等级不参与碳排计算。** 等级只用来解释速度/结构差异，
      **不给碳排乘任何权重**（docs/35 §9 第 7 条）。
      碳排只由距离 × 固定因子决定。

置信度口径（docs/35 §6）
------------------------
======== =========================================================
high     名称/编号 + 速度 + 结构特征三者一致
medium   只有名称或编号（缺速度佐证，或结构矛盾）
low      仅靠速度推断（无名称无编号）
======== =========================================================
"""

from __future__ import annotations

import re
from typing import Tuple

#: 等级标签全集（顺序即展示优先级）
ROAD_CLASSES = ("motorway", "trunk", "secondary", "urban", "local", "ramp", "unknown")

#: 中国高速编号：G + 1~2 位数字（G1 沈哈、G15 沈海、G25 长深、G42 沪蓉…）
_RE_MOTORWAY = re.compile(r"\bG(\d{1,2})\b")

#: 国道编号：G + 3 位数字（G103、G205、G220、G320…）
_RE_TRUNK = re.compile(r"\bG\d{3}\b")

#: 省道编号：S + 1~3 位数字（S27 盐高速、S32…）
_RE_SECONDARY = re.compile(r"\bS\d{1,3}\b")

#: 名称里的高速特征词。
#: "联络线/连接线"也是高速体系的一部分（如"荣乌联络线"是 G18 的联络线，
#: OSM 里的 ref 却给成 S5），名称证据优先于编号。
_MOTORWAY_WORDS = ("高速", "高速公路", "高速路", "联络线", "连接线")

#: 名称里的城市道路特征词（用于 urban 佐证）
_URBAN_WORDS = ("大道", "路", "街", "路", "道")

#: ramp 的 maneuver 类型
_RAMP_MANEUVERS = ("on ramp", "off ramp", "fork", "merge")


def _has_motorway_ref(ref: str) -> bool:
    """G + 1~2 位数字 → 高速。注意 G103 不匹配（G+3 位走 trunk）。"""
    for m in _RE_MOTORWAY.finditer(ref or ""):
        # 后面紧跟数字的（如 G1521 中的 G152）不算 1~2 位
        if not re.match(r"\d", (ref[m.end():m.end() + 1] or " ")):
            return True
    return False


def classify_road(name: str, ref: str, speed_kmh: float,
                  distance_km: float, maneuver_type: str = "") -> Tuple[str, str, str]:
    """给一个 step 判道路等级。

    参数
    ----
    name:
        OSRM 的 ``name``（道路名，可能为空）。
    ref:
        OSRM 的 ``ref``（国道/高速编号，可能为空）。
    speed_kmh:
        本 step 的平均速度（km/h），由距离/时长算出。
    distance_km:
        本 step 距离（km）。
    maneuver_type:
        OSRM 的 ``maneuver.type``（turn/fork/merge/on ramp/off ramp…）。

    返回
    ----
    ``(road_class, source, confidence)``
    """
    name = (name or "").strip()
    ref = (ref or "").strip()
    maneuver_type = (maneuver_type or "").strip()
    has_speed = speed_kmh is not None and speed_kmh > 0

    # ---- 1. 高速：名称含"高速"，或 ref 为 G + 1~2 位 ----
    if any(w in name for w in _MOTORWAY_WORDS):
        conf = "high" if (has_speed and speed_kmh >= 55) else "medium"
        return "motorway", "name_motorway", conf
    if _has_motorway_ref(ref):
        conf = "high" if (has_speed and speed_kmh >= 55) else "medium"
        return "motorway", "ref_motorway", conf

    # ---- 2. 国道：ref 为 G + 3 位（三位国道路网，与高速同前缀但不同级） ----
    if _RE_TRUNK.search(ref):
        conf = "high" if (has_speed and speed_kmh >= 40) else "medium"
        return "trunk", "ref_trunk", conf

    # ---- 3. 省道：ref 以 S 开头 ----
    if _RE_SECONDARY.search(ref):
        conf = "high" if (has_speed and 30 <= speed_kmh < 60) else "medium"
        return "secondary", "ref_secondary", conf

    # ---- 4. 匝道：maneuver 是匝道类且距离短 ----
    # docs/35 §6：ramp = on ramp/off ramp/fork/merge 且距离 < 3 km
    if maneuver_type in _RAMP_MANEUVERS and distance_km < 3.0:
        return "ramp", "maneuver_ramp", "medium"

    # ---- 5. 速度推断（low 置信度） ----
    if has_speed:
        if speed_kmh >= 55 and distance_km >= 3.0:
            return "trunk", "speed_infer", "low"
        if 40 <= speed_kmh < 55:
            return "secondary", "speed_infer", "low"
        if 20 <= speed_kmh < 40:
            return "urban", "speed_infer", "low"
        if speed_kmh < 20:
            return "local", "speed_infer", "low"

    # ---- 6. 名称兜底：无名无编号且无速度 → unknown ----
    if name and any(w in name for w in ("大道", "路", "街")):
        return "urban", "name_urban", "medium"

    return "unknown", "no_signal", "low"


def summarize_road_class(steps) -> dict:
    """逐 step 列表 → 等级里程占比 + 等级数。

    steps 需具备 ``distance_km`` 与 ``road_class`` 属性。
    返回 ``{"share_pct": {...}, "count": {...}, "distance_km": {...}}``，
    占比按里程算（不是按 step 数）—— 里程占比才是"路网结构"的正确表达。
    """
    dist: dict = {}
    cnt: dict = {}
    for step in steps:
        klass = str(getattr(step, "road_class", "unknown") or "unknown")
        km = float(getattr(step, "distance_km", 0.0) or 0.0)
        dist[klass] = dist.get(klass, 0.0) + km
        cnt[klass] = cnt.get(klass, 0) + 1
    total = sum(dist.values())
    share = {k: (v / total * 100.0 if total > 0 else 0.0) for k, v in dist.items()}
    return {
        "share_pct": {k: round(v, 2) for k, v in sorted(share.items(), key=lambda x: -x[1])},
        "count": dict(sorted(cnt.items(), key=lambda x: -x[1])),
        "distance_km": {k: round(v, 3) for k, v in sorted(dist.items(), key=lambda x: -x[1])},
    }
