# -*- coding: utf-8 -*-
"""底图基准对齐层：让 WGS-84 路线安全地叠在任何合规底图上。

为什么需要这一层
----------------
第 6 页 Tab② 的路线几何来自 OSRM/OSM，基准是 **WGS-84**。
而国内合���底图的基准各不相同：

=============== ============== ===============================
底图             数据基准         直接叠加 WGS-84 路线的后果
=============== ============== ===============================
高德 / 腾讯      GCJ-02          ❌ 偏 500–800 m（肉眼可见错位）
百度             BD-09          ❌ 偏 500–800 m + 二次偏移
天地图 WMTS      CGCS2000        ✅ ≈ WGS-84，差 < 1 m，可直接叠
=============== ============== ===============================

所以**不是"不能用高德底图"，而是"用之前必须先对齐基准"**。
本模块提供这层对齐：
高德底图 → 服务端把几何转成 GCJ-02 再下发；
天地图底图 → 几何原样下发（CGCS2000 ≈ WGS-84）。

关键纪律
--------
转换只发生在**显示层**，且**只改坐标不改数值**：

* 距离 / 时长 / 碳排全部来自 parquet，**一个字节都不动**；
* 转换发生在 ``baseline_map`` 组装 payload 时，不是前端算的；
* payload 里带 ``geom_crs`` 标记，前端据此知道几何是什么基准。

这样做的两个好处：

1. 底图可以自由切换（高德 / 天地图 / 无底图），**数值口径始终是 OSM 基线**；
2. 评审时"这条线画在高德底图上却不偏"这件事本身就是个可解释的技术点。

合规
----
底图只使用持牌服务（高德 / 天地图 / 腾讯 / 百度），符合国内地图数据规范。
本模块**不引入任何 key**：高德栅格瓦片是公开端点，天地图 token 由服务端读取注入。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.routing.coord import wgs84_to_gcj02

__all__ = [
    "CRS_WGS84", "CRS_GCJ02", "CRS_CGCS2000",
    "AMAP_XYZ_URL", "AMAP_CRS", "AMAP_ATTRIBUTION",
    "wgs84_to_gcj02",
    "amap_xyz_layer", "convert_geometry", "convert_features",
    "layer_crs", "geometry_crs_for",
]

#: 底图基准标识
CRS_WGS84 = "wgs84"
CRS_GCJ02 = "gcj02"
CRS_CGCS2000 = "cgcs2000"   # 天地图：与 WGS-84 差 < 1 m，按 WGS-84 处理


# ---------------------------------------------------------------- 高德栅格底图

#: 高德栅格瓦片（公开端点，无需 key）。
#:
#: - ``webst0{1-4}``：4 个子域负载均衡，``{1-4}`` 是 OL XYZ 源的子域占位符写法。
#:   注意**不能写 ``{s}``**——OL 的 XYZ 源不认这个占位符，会全部请求到
#:   ``webst0`` 这一个主机；本项目实测踩过：写 ``{s}`` 时瓦片全白（179 B 空瓦片），
#:   改 ``{1-4}`` 后同一张图正常出内容（8.3 KB → 61 KB）。
#: - ``style=8``：标准矢量路网（含省道/国道/高速分级与文字标注）。
#:   实测 style=8 在上海 z=12 有 61 KB 内容；style=7 偏淡，style=6 是卫星影像。
AMAP_XYZ_URL = ("https://webst0{1-4}.is.autonavi.com/appmaptile"
                "?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}")

#: 高德栅格底图的基准
AMAP_CRS = CRS_GCJ02

#: 高德栅格底图的署名（合规要求，必须显示）
AMAP_ATTRIBUTION = "© 高德地图 AutoNavi"


def amap_xyz_layer(name: str = "高德栅格",
                   opacity: float = 1.0,
                   visible: bool = True) -> Dict[str, Any]:
    """高德栅格底图配置（XYZ）。

    基准是 GCJ-02，配它**必须**把路线几何转成 GCJ-02（见 :func:`convert_geometry`）。
    """
    return {
        "kind": "xyz",
        "name": name,
        "url": AMAP_XYZ_URL,
        "opacity": float(opacity),
        "visible": bool(visible),
        "attribution": AMAP_ATTRIBUTION,
    }


# ---------------------------------------------------------------- 几何转换

def convert_geometry(geom: Optional[Dict[str, Any]],
                     src: str = CRS_WGS84,
                     dst: str = CRS_WGS84) -> Optional[Dict[str, Any]]:
    """GeoJSON LineString 几何做基准转换。

    只支持 LineString（路线几何）。非 LineString 原样返回。

    WGS-84 → CGCS2000 **不做转换**（差 < 1 m），
    直接返回原对象，避免无意义的浮点噪声。
    """
    if not geom or geom.get("type") != "LineString":
        return geom
    if src == dst:
        return geom
    if dst not in (CRS_GCJ02,):
        return geom

    coords = geom.get("coordinates") or []
    out: List[List[float]] = []
    for pt in coords:
        lng, lat = float(pt[0]), float(pt[1])
        g_lng, g_lat = wgs84_to_gcj02(lng, lat)
        out.append([g_lng, g_lat])
    return {"type": "LineString", "coordinates": out}


def convert_features(features: Sequence[Dict[str, Any]],
                     src: str = CRS_WGS84,
                     dst: str = CRS_WGS84) -> List[Dict[str, Any]]:
    """批量转换 feature 列表里的几何。"""
    if src == dst:
        return list(features)
    out: List[Dict[str, Any]] = []
    for f in features:
        item = dict(f)
        item["geometry"] = convert_geometry(item.get("geometry"), src, dst)
        out.append(item)
    return out


def layer_crs(layer: Optional[Dict[str, Any]]) -> str:
    """推断某个底图图层的基准。"""
    if not layer:
        return CRS_WGS84
    kind = str(layer.get("kind", "")).lower()
    name = str(layer.get("name", "")).lower()
    url = str(layer.get("url", "")).lower()
    if kind == "wmts" and "tianditu" in url:
        return CRS_CGCS2000
    if kind == "xyz" and "autonavi" in url:
        return CRS_GCJ02
    if kind == "xyz" and "tianditu" in url:
        return CRS_CGCS2000
    if "autonavi" in name or "高德" in name:
        return CRS_GCJ02
    return CRS_WGS84


def geometry_crs_for(layers: Sequence[Dict[str, Any]]) -> str:
    """一组底图里"最严格的基准"——决定路线几何该转成什么。

    取 GCJ-02 > CGCS2000 > WGS-84 的**最严格者**：
    只要有一层是 GCJ-02，几何就必须转成 GCJ-02，
    否则那一层上的路线会偏 500–800 m。

    天地图（CGCS2000）严格来说比 GCJ-02 更"正"，但两者不能同时满足，
    所以**以 GCJ-02 底图为准**——这是唯一能保证所有可见底图都对齐的选择。
    """
    worst = CRS_WGS84
    for layer in layers or []:
        crs = layer_crs(layer)
        if crs == CRS_GCJ02:
            return CRS_GCJ02
        if crs == CRS_CGCS2000:
            worst = CRS_CGCS2000
    return worst
