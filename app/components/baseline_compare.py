# -*- coding: utf-8 -*-
"""D · 对照层：高德实时口径 vs OSM 基线口径。

为什么是"表格对照"而不是"两张地图叠一起"
------------------------------------------
用户明确要求：**不把两张地图硬叠**。这是对的，理由有三：

1. **坐标基准不同**——高德是 GCJ-02，OSM/OSRM 是 WGS-84。硬叠必然错位
   500–800 m，评委一眼就能看出"路线对不上"，反而降低可信度；
2. **口径不同**——高德侧是**实时路况**（含拥堵/封路/收费），OSM 侧是
   **freeflow 固定因子基线**。叠在一起看，"哪条更优"是个错误的问题；
3. **车型/载重不同**——现有 AMap 缓存这条线是 9.6 m 厢式货车 18 t，
   OSM 基线是 30 t 牵引车 30 t。碳排本来就不该相等。

所以对照做成**表格**：每一行一个维度，写清数值、差异、差异原因。
**明确声明"高德实时 vs OSM 基线不是同一口径"，不许读者横向比较碳排数字。**

对照对象
--------
任务书 §1 指定的对照是"现有 AMap **苏州→北京通州**缓存"。
注意起点不同（苏州昆山 vs 上海车墩），因此**距离/时长不可直接相减**，
表里必须把"起点不同"写成第一条差异原因。

数据来源
--------
* OSM 侧：``route_baseline_sh_bj_steps.parquet`` + ``summary.json``（只读）
* AMap 侧：``route_od.parquet`` + ``route_candidate_carbon.parquet``（只读）

纪律：**只读不算**。碳排数字全部来自结果表，本模块只做**差值展示**
（差值是展示级换算，不是业务口径），不重算任何一方。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

#: AMap 侧对照 OD（任务书 §1：现有 AMap 苏州→北京通州缓存）
AMAP_OD_KEY = "苏州昆山集货分拣中心|北京通州分拣中心"

#: AMap 侧默认策略（0 = 综合）
AMAP_STRATEGY = 0


def _num(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        out = float(value)
        if out != out:  # NaN
            return None
        return out
    except (TypeError, ValueError):
        return None


def pick_amap_side(route_od: pd.DataFrame,
                   cand_carbon: pd.DataFrame,
                   od_key: str = AMAP_OD_KEY,
                   strategy: int = AMAP_STRATEGY) -> Dict[str, Any]:
    """从现有结果表里取出 AMap 侧对照行。

    返回空 dict 表示该 OD 不在缓存里（调用方据此显示"无对照"）。
    """
    if route_od is None or route_od.empty:
        return {}
    mask = route_od["od_key"].astype(str) == str(od_key)
    if not mask.any():
        return {}
    row = route_od[mask].iloc[0]
    side: Dict[str, Any] = {
        "od_key": str(od_key),
        "od_pair": str(row.get("od_pair") or ("%s → %s" % (row.get("origin", ""), row.get("dest", "")))),
        "origin": str(row.get("origin", "")),
        "dest": str(row.get("dest", "")),
        "distance_km": _num(row.get("distance_km")),
        "duration_min": _num(row.get("duration_min")),
        "toll_yuan": _num(row.get("toll_yuan")),
        "source": str(row.get("source", "")),
        "vehicle_type": "",
        "load_t": None,
        "kg_co2_route": None,
        "road_class_share": None,
        "step_count": None,
    }
    if cand_carbon is not None and not cand_carbon.empty:
        cmask = (cand_carbon["od_key"].astype(str) == str(od_key)) & (
            pd.to_numeric(cand_carbon["strategy"], errors="coerce") == int(strategy))
        if cmask.any():
            crow = cand_carbon[cmask].iloc[0]
            side["vehicle_type"] = str(crow.get("vehicle_type") or "")
            side["load_t"] = _num(crow.get("load_t"))
            side["kg_co2_route"] = _num(crow.get("kg_co2_route"))
            side["strategy_name"] = str(crow.get("strategy_name") or "")
    return side


def _delta(a: Optional[float], b: Optional[float], digits: int = 1) -> str:
    """差值展示：**OSM 基准 − AMap 实时**（展示级换算，不是业务口径）。

    符号约定：基准（OSM）在前，所以正值表示"OSM 基线比高德实时大"。
    """
    if a is None or b is None:
        return "—"
    d = a - b
    return ("%+." + str(digits) + "f") % d


def _pct(a: Optional[float], b: Optional[float]) -> str:
    if a is None or b is None or b == 0:
        return "—"
    return "%+.1f%%" % ((a - b) / b * 100.0)


def build_comparison(steps: pd.DataFrame, summary: Dict[str, Any],
                     amap: Dict[str, Any]) -> List[Dict[str, Any]]:
    """组装对照表行。

    每行：``{dim, amap, osm, delta, reason}``。
    """
    osm_km = _num(summary.get("distance_km"))
    osm_min = _num(summary.get("duration_min"))
    osm_carbon = _num(summary.get("carbon_kg"))
    osm_share = summary.get("road_class_share") or {}
    osm_steps = summary.get("step_count")
    amap_km = _num(amap.get("distance_km"))
    amap_min = _num(amap.get("duration_min"))
    amap_toll = _num(amap.get("toll_yuan"))
    amap_carbon_kg = _num(amap.get("kg_co2_route"))

    # 高速占比（OSM 侧 road_class_share 里 motorway + trunk）
    motorway_pct = _num(osm_share.get("motorway")) or 0.0
    trunk_pct = _num(osm_share.get("trunk")) or 0.0

    rows: List[Dict[str, Any]] = [
        {
            "dim": "起点 → 终点",
            "amap": str(amap.get("od_pair") or "—"),
            "osm": "上海车墩集货分拣中心 → 北京通州分拣中心",
            "delta": "起点不同",
            "reason": "**两列不是同一条 OD**：AMap 缓存里没有上海车墩→北京通州，"
                      "最接近的长途对照是苏州昆山→北京通州。因此距离/时长的绝对差值"
                      "**含出发地差异**，不是同口径对比。",
        },
        {
            "dim": "距离",
            "amap": ("%.1f km" % amap_km) if amap_km is not None else "—",
            "osm": ("%.1f km" % osm_km) if osm_km is not None else "—",
            "delta": _delta(osm_km, amap_km),
            "reason": "① 起点不同（苏州 vs 上海，差约 70 km）；"
                      "② 路网不同（高德用自采路网，OSM 用众包路网）；"
                      "③ OSM 在长三角/山东段可能缺主干道细节，绕行更多。",
        },
        {
            "dim": "时间",
            "amap": ("%.0f min" % amap_min) if amap_min is not None else "—",
            "osm": ("%.0f min" % osm_min) if osm_min is not None else "—",
            "delta": _delta(osm_min, amap_min),
            "reason": "**口径根本不同**：高德是**实时**规划（含当时路况、限行、封路）；"
                      "OSM 基线是 **freeflow**（自由流速度，无路况）。"
                      "freeflow 必然更快，这不是「OSM 更快」，是「没算拥堵」。",
        },
        {
            "dim": "碳排",
            "amap": ("%.1f kg" % amap_carbon_kg) if amap_carbon_kg is not None else "—",
            "osm": ("%.1f kg" % osm_carbon) if osm_carbon is not None else "—",
            "delta": "不可直接相减",
            "reason": "**车型与载重都不同**：AMap 侧是 %s / %.0f t，"
                      "OSM 基线是 30 t 牵引车 / 30 t；且因子口径不同"
                      "（AMap 走项目段级因子表 + VSP，OSM 基线是固定因子 fixed_50）。"
                      "**这两个数不是同一口径，不许横向比较。**"
                      % (amap.get("vehicle_type") or "—", amap.get("load_t") or 0.0),
        },
        {
            "dim": "过路费",
            "amap": ("%.0f 元" % amap_toll) if amap_toll is not None else "—",
            "osm": "—（OSRM 无金额）",
            "delta": "不可比",
            "reason": "OSRM 不返回过路费金额，因此 OSM 侧 **toll_yuan 恒为空**。"
                      "这里不拿 AMap 的金额去填——那是把一个源的数字安到另一个源头上。"
                      "任务书 §9 第 4 条明确要求：只给 toll_flag，不给金额。",
        },
        {
            "dim": "道路结构",
            "amap": "不提供分级",
            "osm": "高速 %.1f%% · 国道 %.1f%% · 匝道 %.1f%%" % (
                motorway_pct, trunk_pct, _num(osm_share.get("ramp")) or 0.0),
            "delta": "AMap 无对应字段",
            "reason": "OSM 侧按道路名称/编号/速度做 **proxy** 推断"
                      "（G+1~2 位=高速、G+3 位=国道、S 开头=省道），"
                      "**不是官方等级**；AMap REST 不返回分级，"
                      "所以这一行只有单边数据。高德实时模式的路网亮图可作路网核对。",
        },
        {
            "dim": "step 数 / 几何精度",
            "amap": "实时路径约 0.1 km 一点（贴公路）",
            "osm": "%s step（逐 step 几何已入库）" % osm_steps,
            "delta": "—",
            "reason": "OSM 侧按 OSRM 的 step 切分（一次 maneuver 一个 step，"
                      "约 60–80 个），几何比高德稀疏；"
                      "但**逐 step 的道路等级/速度/碳排可复核**，这是基线的主要价值。",
        },
        {
            "dim": "数据来源与合规",
            "amap": "高德 Web 服务 + JS API（需自有 key）",
            "osm": "OSRM 公共服务 + OSM 路网（公开，key 无关）",
            "delta": "离线可复现",
            "reason": "OSM 基线**不依赖任何高德 key**，公共 OSRM 只用于 spike，"
                      "正式版换本地 GraphHopper/OSRM；因此这条线可离线复现、可审计。",
        },
    ]
    return rows
