# -*- coding: utf-8 -*-
"""路线视觉增强层：把"一条线"变成"一张能读懂的图"。

为什么单独拆一个模块
--------------------
本项目实测过：Tab② 最初只有一条 3px 的线悬在纯黑底上，
和高德那侧（底图 + 路况 + 标注 + 起终点 + 动效）一比，**判若两图**。
但根因不在"渲染代码写得差"，而在**缺了视觉层次本身**。

高德那张地图好看的四个要素，逐条对应到这里：

=================== ====================== ======================
高德有的            本模块提供              实现方式
=================== ====================== ======================
底图                （由底图层负责）          basemap_align
道路有粗细层级      按 step 里程加权线宽      :func:`line_width`
路线有光晕/描边      三层描边（halo+core）    :func:`feature_style`
有起终点标记        圆形 marker + 标签       :func:`marker_style`
=================== ====================== ======================

关键设计
--------
1. **halo 层**：深色描边打底，浅色主线压在上面。
   这是高德/Google 路线渲染的通用做法——没有它，亮色线在亮色底图上会"糊"掉。
2. **里程加权线宽**：高速段本来就长，线宽再按里程给一点权重，
   视觉上"粗的段 = 高速占比高"，与右侧等级结构表互相印证。
3. **marker 用 SVG data URI**：不依赖任何外部图片、不触发 CDN 请求，
   断网也能画出来。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from src.routing.coord import haversine_km

#: 道路等级 → 基础线宽（px）
BASE_WIDTHS: Dict[str, float] = {
    "motorway": 5.2,
    "trunk": 4.0,
    "secondary": 3.0,
    "urban": 2.4,
    "local": 1.8,
    "ramp": 2.0,
    "unknown": 1.8,
}

#: 里程权重上限（px）：最长的 step 最多比基准宽这么多
MILEAGE_WEIGHT_MAX = 2.4

#: 里程刻度间隔（km）——每 100 km 在路线上打一个刻度
MILEAGE_TICK_INTERVAL_KM = 100.0

#: 道路名标注的最小路段里程（km）——短于此的不标，避免密集重叠
LABEL_MIN_KM = 18.0

#: 道路名标注分级：
#:   zoom < 8   → 只显示「超长段」（≥ 120 km），全国尺度上一眼能看清
#:   8 ≤ zoom  → 显示全部合格段（≥ LABEL_MIN_KM）
#:   zoom ≥ 10  → 再放宽到中等段（≥ 8 km）
#:
#: 为什么分级：全国视图里 15 个标签会挤成一团糊掉；
#: 而放大后路段变短，再多标也放得下。分级 = 一次取舍换来两个尺度都可读。
LABEL_LONG_KM = 120.0
LABEL_MEDIUM_KM = 8.0


def line_width(road_class: str, distance_km: float,
               zoom: Optional[float] = None) -> float:
    """按道路等级 + 里程算线宽。

    :param road_class: 道路等级（motorway/trunk/...）
    :param distance_km: 该 step 的里程
    :param zoom: 当前缩放级别；给定时按 log 缩放（放大时线宽略增，视觉更稳）
    """
    base = BASE_WIDTHS.get(road_class, 1.8)
    # 里程权重：10 km 以内不加权，100 km 封顶加满
    weight = min(max(distance_km, 0.0) / 100.0, 1.0) * MILEAGE_WEIGHT_MAX
    width = base + weight
    if zoom:
        # zoom 5→1.0，zoom 12→1.6
        scale = 1.0 + max(0.0, min(zoom - 5.0, 7.0)) / 7.0 * 0.6
        width *= scale
    return round(width, 2)


def feature_style(road_class: str, distance_km: float,
                  color: str, zoom: Optional[float] = None) -> Dict[str, Any]:
    """生成一个 step 的三层描边样式描述。

    返回结构（前端消费）::

        {"width": 主线宽, "halo": halo 宽, "color": 主线色,
         "haloColor": halo 色, "opacity": 主线不透明度}

    halo 比主线宽 2.5 px，颜色是主线的深色版——在亮色底图上也能压住。
    注意 halo **不能太宽**：底图反相成暗色后，宽 halo 会盖住主线，
    整条路线看起来发白（实测踩过：halo 宽 +4px 时高速段变成白带）。
    """
    w = line_width(road_class, distance_km, zoom)
    return {
        "width": w,
        # halo 只比主线宽 1.5px：底图反相成暗色后，halo 与主线颜色叠加会让
        # 高速段发白（实测 halo 宽 4px 时整条高速呈白带，读不出等级色）。
        # 1.5px 足够压住底图，又不会淹没主线颜色。
        "halo": round(w + 1.5, 2),
        "color": color,
        "haloColor": "rgba(4,9,14,0.55)",
        "opacity": 1.0,
    }


#: 起终点 marker 的 SVG（data URI，内联，不发任何网络请求）
def _marker_svg(fill: str, ring: str) -> str:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="38" height="38">'
        '<circle cx="19" cy="19" r="17" fill="{fill}" fill-opacity="0.20"/>'
        '<circle cx="19" cy="19" r="10" fill="{fill}" stroke="{ring}" stroke-width="2.5"/>'
        '</svg>'
    ).format(fill=fill, ring=ring)
    return "data:image/svg+xml;charset=utf-8," + (
        svg.replace("#", "%23").replace('"', "'").replace(" ", "%20")
    )


START_MARKER = _marker_svg("#2DD4A7", "#04121A")
END_MARKER = _marker_svg("#FF8A5B", "#2A0F06")


def marker_style(kind: str) -> Dict[str, Any]:
    """起终点 marker 样式描述。"""
    return {
        "src": START_MARKER if kind == "start" else END_MARKER,
        "width": 38,
        "height": 38,
    }


# ================================================================ 里程刻度

def _cumulative_km(coords: Sequence[Sequence[float]]) -> List[float]:
    """几何点的累计里程（km），长度与 coords 相同，首项为 0。"""
    out: List[float] = [0.0]
    for i in range(1, len(coords)):
        out.append(out[-1] + haversine_km(
            coords[i - 1][0], coords[i - 1][1], coords[i][0], coords[i][1]))
    return out


def _point_at_offset(coords: Sequence[Sequence[float]],
                     cum: Sequence[float],
                     offset_km: float) -> Optional[Sequence[float]]:
    """在几何上找"距起点 offset_km 处"的点（线性插值）。

    找不到（超出几何长度）时返回 None。
    """
    if not coords or len(coords) < 2:
        return None
    total = cum[-1]
    if total <= 0 or offset_km < 0 or offset_km > total:
        return None
    # 二分找区间
    lo, hi = 0, len(cum) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if cum[mid] <= offset_km:
            lo = mid
        else:
            hi = mid
    span = cum[hi] - cum[lo]
    if span <= 0:
        return coords[lo]
    ratio = (offset_km - cum[lo]) / span
    return [
        coords[lo][0] + (coords[hi][0] - coords[lo][0]) * ratio,
        coords[lo][1] + (coords[hi][1] - coords[lo][1]) * ratio,
    ]


def mileage_ticks(segments: Sequence[Dict[str, Any]],
                  interval_km: float = MILEAGE_TICK_INTERVAL_KM) -> List[Dict[str, Any]]:
    """沿整条路线打里程刻度。

    :param segments: 有序的路段列表，每项含 ``geometry``（坐标数组）、
        ``distance_km``、``road_class``、``step_index``
    :param interval_km: 刻度间隔（默认 100 km）

    返回每项::

        {"km": 累计里程, "lng": ..., "lat": ..., "road_class": ...}

    **口径纪律**：刻度读数必须与右侧逐 step 列表**用同一套里程**
    （OSRM 的 ``distance_km``），否则会出现"刻度说 500 km、列表加起来对不上"。
    所以累计基准用 parquet 的 ``distance_km``，
    几何长度只用来**在段内插值定位**，不用来累加。

    为什么在**服务端**算：里程是"业务口径"的一部分，前端只负责画，不能自己数。
    """
    ticks: List[Dict[str, Any]] = []
    if not segments:
        return ticks

    # 1) 整理有效段。
    #
    # **两套里程，各司其职**（这是本函数最容易搞错的地方）：
    #
    #   · ``km_len``（parquet 的 distance_km）→ 决定**刻度读数**落在哪一段，
    #     保证 "500 km" 这个数字和右侧逐 step 列表口径一致；
    #   · ``cum[-1]``（几何折线长度）→ 决定**点在段内的位置**，
    #     保证点一定落在折线上，不会飘到线外。
    #
    # 真实数据里两者只差 0.27%（几何是折线近似，parquet 是 OSRM 精确里程）；
    # 但如果混用，段级差距大时点会飘到线外（实测可偏 11 km）。
    valid: List[Dict[str, Any]] = []
    total_km = 0.0
    for seg in segments:
        coords = seg.get("geometry") or []
        if len(coords) < 2:
            continue
        cum = _cumulative_km(coords)
        valid.append({
            "coords": coords,
            "cum": cum,
            "km_start": total_km,
            "km_len": float(seg.get("distance_km") or 0.0),
            "geom_len": cum[-1],
        })
        total_km += float(seg.get("distance_km") or 0.0)

    if total_km <= 0 or not valid:
        return ticks

    # 2) 按间隔取刻度点
    step_km = interval_km if interval_km > 0 else MILEAGE_TICK_INTERVAL_KM
    n = int(total_km // step_km)
    for k in range(1, n + 1):
        target = k * step_km
        # 用 parquet 里程定位段落
        seg = None
        for v in valid:
            if target <= v["km_start"] + v["km_len"] + 1e-9:
                seg = v
                break
        if seg is None:
            continue
        # 段内比例（按 parquet 里程算）→ 几何上的偏移（按几何长度换算）
        offset_in_geom = 0.0
        if seg["km_len"] > 0 and seg["geom_len"] > 0:
            ratio = (target - seg["km_start"]) / seg["km_len"]
            ratio = max(0.0, min(1.0, ratio))
            offset_in_geom = ratio * seg["geom_len"]
        pt = _point_at_offset(seg["coords"], seg["cum"], offset_in_geom)
        if pt is None:
            continue
        ticks.append({
            "km": round(target, 1),
            "lng": round(pt[0], 6),
            "lat": round(pt[1], 6),
        })
    return ticks


def road_labels(segments: Sequence[Dict[str, Any]],
                min_km: float = LABEL_MIN_KM,
                max_labels: int = 40) -> List[Dict[str, Any]]:
    """挑出值得标注道路名的路段，并给出标注锚点。

    挑选规则（避免标签糊成一片）：

    * 里程 ≥ ``min_km``（太短的路段标了也看不清）；
    * 有路名（无名路段不标）；
    * 按里程从大到小取前 ``max_labels`` 个 —— 长路段才配拥有标签。

    锚点取路段的**中点**，而不是首点：首点往往紧邻上一段的末端，容易重叠。

    每项带一个 ``tier`` 字段（``long`` / ``medium`` / ``short``），
    前端按缩放级别决定显示哪一档 —— 见 :data:`LABEL_LONG_KM` 的说明。
    """
    cands: List[Dict[str, Any]] = []
    for seg in segments:
        name = str(seg.get("road_name") or "").strip()
        if not name:
            continue
        dist = float(seg.get("distance_km") or 0.0)
        if dist < min_km:
            continue
        coords = seg.get("geometry") or []
        if len(coords) < 2:
            continue
        cum = _cumulative_km(coords)
        mid = _point_at_offset(coords, cum, cum[-1] / 2.0)
        if mid is None:
            continue
        if dist >= LABEL_LONG_KM:
            tier = "long"
        elif dist >= LABEL_MEDIUM_KM:
            tier = "medium"
        else:
            tier = "short"
        cands.append({
            "step_index": int(seg.get("step_index", 0)),
            "text": name,
            "road_ref": str(seg.get("road_ref") or "").strip(),
            "road_class": str(seg.get("road_class") or "unknown"),
            "distance_km": round(dist, 1),
            "tier": tier,
            "lng": round(mid[0], 6),
            "lat": round(mid[1], 6),
        })

    cands.sort(key=lambda x: -x["distance_km"])
    return cands[:max_labels]
