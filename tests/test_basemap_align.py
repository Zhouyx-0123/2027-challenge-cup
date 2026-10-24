# -*- coding: utf-8 -*-
"""底图基准对齐 + 路线视觉层的测试。

为什么这层必须有测试
--------------------
坐标对齐是**静默出错**的：转错了不会报错，只会让路线偏 500–800 m，
评审时变成"这图不准"，但所有断言都是绿的。

本文件守住三条线：

1. **转换正确性**：转换后的几何与 :func:`wgs84_to_gcj02` 逐点一致；
2. **数值零污染**：距离/时长/碳排在转换前后**完全不变**（只改坐标）；
3. **基准选择正确**：不同底图组合 → 不同的目标基准。

另外守一条工程纪律：``src`` 不是包，组件层必须用绝对导入
（``from src.routing.coord import ...``），否则 AppTest 会报
``ImportError: attempted relative import beyond top-level package``。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "app") not in sys.path:
    sys.path.insert(0, str(ROOT / "app"))

from components import basemap_align, route_visual  # noqa: E402
from src.routing.coord import wgs84_to_gcj02  # noqa: E402


# ---------------------------------------------------------------- 底图配置

def test_amap_layer_is_xyz_and_gcj02():
    """高德栅格底图：XYZ 类型 + GCJ-02 基准。"""
    layer = basemap_align.amap_xyz_layer()
    assert layer["kind"] == "xyz"
    assert basemap_align.layer_crs(layer) == basemap_align.CRS_GCJ02
    assert "{1-4}" in layer["url"], "必须用 OL 支持的 {1-4} 子域占位符，不能写 {s}"
    assert "{s}" not in layer["url"], "OL 的 XYZ 源不认 {s}，写了会全落到空瓦片"


def test_amap_layer_carries_attribution():
    """合规要求：底图必须带署名。"""
    assert "高德" in basemap_align.amap_xyz_layer()["attribution"]


def test_tianditu_wmts_is_cgcs2000():
    """天地图 WMTS：CGCS2000 基准，与 WGS-84 差 < 1 m，按 WGS-84 处理。"""
    layer = {
        "kind": "wmts",
        "name": "天地图",
        "url": "https://t0.tianditu.gov.cn/vec_w/wmts?...tk=xxx",
    }
    assert basemap_align.layer_crs(layer) == basemap_align.CRS_CGCS2000


def test_geometry_crs_picks_gcj02_when_any_layer_needs_it():
    """只要有一层是 GCJ-02，几何就必须转 GCJ-02 —— 否则那层上会偏 500–800 m。"""
    wmts = {"kind": "wmts", "url": "https://t0.tianditu.gov.cn/vec_w/wmts?tk=x"}
    amap = basemap_align.amap_xyz_layer()
    assert basemap_align.geometry_crs_for([wmts]) == basemap_align.CRS_CGCS2000
    assert basemap_align.geometry_crs_for([amap]) == basemap_align.CRS_GCJ02
    assert basemap_align.geometry_crs_for([wmts, amap]) == basemap_align.CRS_GCJ02
    assert basemap_align.geometry_crs_for([]) == basemap_align.CRS_WGS84


# ---------------------------------------------------------------- 几何转换

def _line(coords):
    return {"type": "LineString", "coordinates": [list(c) for c in coords]}


def test_convert_geometry_matches_reference_implementation():
    """转换结果必须与 coord.wgs84_to_gcj02 逐点一致。"""
    src = _line([(121.310616, 31.016848), (120.339255, 33.271481)])
    out = basemap_align.convert_geometry(src, basemap_align.CRS_WGS84,
                                         basemap_align.CRS_GCJ02)
    assert out["type"] == "LineString"
    assert len(out["coordinates"]) == 2
    for got, (lng, lat) in zip(out["coordinates"], src["coordinates"]):
        exp_lng, exp_lat = wgs84_to_gcj02(lng, lat)
        assert got[0] == pytest.approx(exp_lng, abs=1e-12)
        assert got[1] == pytest.approx(exp_lat, abs=1e-12)


def test_convert_geometry_is_identity_for_same_crs():
    """同基准转换必须是恒等（不产生浮点噪声）。"""
    src = _line([(121.3, 31.0), (121.4, 31.1)])
    out = basemap_align.convert_geometry(src, basemap_align.CRS_WGS84,
                                         basemap_align.CRS_WGS84)
    assert out is src


def test_convert_geometry_skips_non_linestring():
    """非 LineString 原样返回，不炸。"""
    point = {"type": "Point", "coordinates": [121.3, 31.0]}
    assert basemap_align.convert_geometry(point, basemap_align.CRS_WGS84,
                                          basemap_align.CRS_GCJ02) == point


def test_gcj02_shift_is_in_expected_range():
    """GCJ-02 偏移量级自检：上海应在 300–700 m。

    这是"转换真的生效了"的最低门槛——如果偏移是 0，说明转换被短路了。
    """
    import math
    lng, lat = 121.310616, 31.016848
    g_lng, g_lat = wgs84_to_gcj02(lng, lat)
    meters = math.hypot((g_lng - lng) * math.cos(math.radians(lat)) * 111320.0,
                        (g_lat - lat) * 111320.0)
    assert 300.0 < meters < 700.0, "GCJ-02 偏移量级异常：%.1f m" % meters


# ---------------------------------------------------------------- 数值零污染

def test_conversion_does_not_touch_numeric_fields():
    """**关键防线**：转换只改坐标，distance/duration/carbon 一个字节都不动。"""
    feature = {
        "id": 7,
        "geometry": _line([(121.3, 31.0), (121.4, 31.1)]),
        "distance_km": 78.7,
        "duration_min": 53.5,
        "carbon_kg": 83.1,
        "speed_kmh": 90.0,
    }
    out = basemap_align.convert_features(
        [feature], basemap_align.CRS_WGS84, basemap_align.CRS_GCJ02)[0]
    for key in ("id", "distance_km", "duration_min", "carbon_kg", "speed_kmh"):
        assert out[key] == feature[key], "%s 被改动了" % key
    assert out["geometry"]["coordinates"] != feature["geometry"]["coordinates"]


def test_baseline_payload_keeps_numbers_after_conversion():
    """端到端：整条 payload 组装后，step 数值与 parquet 完全一致。"""
    from components import baseline_map
    steps = pd.DataFrame([
        {"step_index": 0, "distance_km": 10.5, "duration_min": 7.0,
         "speed_kmh": 90.0, "carbon_kg": 11.08, "road_class": "motorway",
         "road_name": "G50", "road_ref": "G50",
         "geometry_geojson": '{"type":"LineString","coordinates":[[121.3,31.0],[121.4,31.1]]}'},
    ])
    payload = baseline_map.build_baseline_payload(
        steps, {"road_class_share": {"motorway": 100.0}},
        layers=[basemap_align.amap_xyz_layer()])
    assert payload["geomCrs"] == basemap_align.CRS_GCJ02
    assert payload["steps"][0]["distance_km"] == 10.5
    assert payload["steps"][0]["carbon_kg"] == 11.08
    assert payload["features"][0]["distance_km"] == 10.5
    assert payload["features"][0]["carbon_kg"] == 11.08


def test_baseline_payload_markers_follow_same_crs():
    """起终点 marker 必须与路线几何用**同一基准**，否则会差 500 m。"""
    from components import baseline_map
    steps = pd.DataFrame([
        {"step_index": 0, "distance_km": 1.0, "duration_min": 1.0,
         "speed_kmh": 60.0, "carbon_kg": 1.0, "road_class": "motorway",
         "road_name": "a", "road_ref": "",
         "geometry_geojson": '{"type":"LineString","coordinates":[[121.3,31.0],[121.4,31.1]]}'},
    ])
    payload = baseline_map.build_baseline_payload(
        steps, {}, layers=[basemap_align.amap_xyz_layer()])
    assert len(payload["markers"]) == 2
    start = payload["markers"][0]
    exp_lng, exp_lat = wgs84_to_gcj02(121.3, 31.0)
    assert start["lng"] == pytest.approx(exp_lng, abs=1e-12)
    assert start["lat"] == pytest.approx(exp_lat, abs=1e-12)


# ---------------------------------------------------------------- 视觉层

def test_line_width_is_monotonic_by_road_class():
    """高速线宽 > 国道 > 支路。"""
    widths = {k: route_visual.line_width(k, 50.0)
              for k in ("motorway", "trunk", "secondary", "urban", "local")}
    assert widths["motorway"] > widths["trunk"] > widths["secondary"]
    assert widths["secondary"] > widths["urban"] > widths["local"]


def test_line_width_grows_with_distance():
    """同一等级，里程越长线越宽（视觉上粗的段=高速占比高）。"""
    assert route_visual.line_width("motorway", 100.0) > route_visual.line_width("motorway", 5.0)


def test_line_width_zoom_scales_up():
    """放大时线宽略增，视觉更稳。"""
    assert route_visual.line_width("motorway", 20.0, zoom=12) > \
        route_visual.line_width("motorway", 20.0, zoom=5)


def test_halo_is_narrower_than_it_used_to_be():
    """halo 回归防线：曾因太宽把高速段盖成白带（实测 halo 宽 4px 时整条高速发白）。

    现在 halo 必须只比主线宽 1~2.5px。
    """
    style = route_visual.feature_style("motorway", 80.0, "#FF8A5B")
    delta = style["halo"] - style["width"]
    assert 1.0 <= delta <= 2.5, "halo 宽度失控：%+0.2f px" % delta


def test_feature_style_keeps_color_and_opaque_core():
    """主线必须不透明且保留等级本色（滤镜只能作用底图层，不能碰矢量层）。"""
    style = route_visual.feature_style("motorway", 80.0, "#FF8A5B")
    assert style["color"] == "#FF8A5B"
    assert style["opacity"] >= 0.9


def test_marker_style_returns_svg_data_uri():
    """marker 必须是内联 SVG data URI：不发网络请求，断网也能画。"""
    for kind in ("start", "end"):
        style = route_visual.marker_style(kind)
        assert style["src"].startswith("data:image/svg+xml")
        assert style["width"] == 38


# ---------------------------------------------------------------- 里程刻度

def _seg(index, name, distance_km, coords):
    return {
        "step_index": index,
        "geometry": coords,
        "distance_km": distance_km,
        "road_name": name,
        "road_ref": "",
        "road_class": "motorway",
    }


def test_mileage_ticks_count_follows_total_distance():
    """刻度数 = 总里程 / 间隔。"""
    segs = [_seg(0, "A", 250.0, [(121.0, 31.0), (121.5, 31.5), (122.0, 32.0)]),
            _seg(1, "B", 200.0, [(122.0, 32.0), (122.5, 32.5)])]
    ticks = route_visual.mileage_ticks(segs, interval_km=100.0)
    assert [t["km"] for t in ticks] == [100.0, 200.0, 300.0, 400.0]


def test_mileage_ticks_use_parquet_distance_as_base():
    """刻度累计基准必须是 parquet 的 distance_km，与右侧列表口径一致。

    如果用几何长度累加，读数会和逐 step 列表对不上（实测差 0.27%）。
    """
    # 几何长度约 90 km，但里程写 100 km —— 刻度应按 100 km 口径落点
    segs = [_seg(0, "A", 100.0, [(121.0, 31.0), (121.1, 31.0)])]
    ticks = route_visual.mileage_ticks(segs, interval_km=50.0)
    assert [t["km"] for t in ticks] == [50.0, 100.0]
    # 100 km 的刻度应落在几何末端（因为整段就是 100 km）
    last = ticks[-1]
    assert last["lng"] == pytest.approx(121.1, abs=1e-6)


def _point_to_route_m(point, segs):
    """点到整条路线（折线）的最短距离（米）。

    必须算**点到线段的垂距**，不能算到顶点的距离 ——
    路线顶点稀疏时，线段中点离两端都很远，
    但它**就在路线上**（实测踩过：用顶点距离误判"偏离 11 km"）。
    """
    import math

    def seg_dist(p, a, b):
        # 投影到线段，取最近点
        ax, ay = a
        bx, by = b
        px, py = p
        dx, dy = bx - ax, by - ay
        denom = dx * dx + dy * dy
        if denom <= 0:
            return math.hypot((px - ax) * math.cos(math.radians(ay)) * 111320.0,
                              (py - ay) * 111320.0)
        t = ((px - ax) * dx + (py - ay) * dy) / denom
        t = max(0.0, min(1.0, t))
        cx, cy = ax + t * dx, ay + t * dy
        return math.hypot((px - cx) * math.cos(math.radians(cy)) * 111320.0,
                          (py - cy) * 111320.0)

    best = float("inf")
    for s in segs:
        g = s["geometry"]
        for i in range(len(g) - 1):
            d = seg_dist(point, g[i], g[i + 1])
            if d < best:
                best = d
    return best


def test_mileage_ticks_land_on_route():
    """刻度点必须落在路线上。

    度量用**点到线段的垂距**（不是到顶点的距离）——
    路线顶点稀疏，线段中点离两端远但就在线上。
    """
    segs = [_seg(0, "A", 120.0, [(121.0, 31.0), (120.8, 31.6), (120.6, 32.2)]),
            _seg(1, "B", 80.0, [(120.6, 32.2), (120.2, 32.9)])]
    ticks = route_visual.mileage_ticks(segs, interval_km=50.0)
    assert ticks, "应产生刻度"
    for t in ticks:
        d = _point_to_route_m((t["lng"], t["lat"]), segs)
        assert d < 100.0, "刻度点偏离路线 %.0f m" % d


def test_mileage_ticks_empty_for_no_segments():
    assert route_visual.mileage_ticks([]) == []


# ---------------------------------------------------------------- 道路名标注

def test_road_labels_skip_short_and_unnamed():
    """太短的路段、无名路段都不标注。"""
    segs = [
        _seg(0, "", 100.0, [(121.0, 31.0), (121.5, 31.5)]),      # 无名
        _seg(1, "B", 5.0, [(121.5, 31.5), (121.6, 31.6)]),        # 太短
        _seg(2, "C", 60.0, [(121.6, 31.6), (122.0, 32.0)]),        # 合格
    ]
    labels = route_visual.road_labels(segs, min_km=18.0)
    assert len(labels) == 1
    assert labels[0]["text"] == "C"


def test_road_labels_sorted_by_distance_desc():
    """按里程从大到小排 —— 长路段优先拿到标签位。"""
    segs = [
        _seg(0, "short", 20.0, [(121.0, 31.0), (121.2, 31.2)]),
        _seg(1, "long", 200.0, [(121.2, 31.2), (122.0, 32.0)]),
        _seg(2, "mid", 60.0, [(122.0, 32.0), (122.5, 32.5)]),
    ]
    labels = route_visual.road_labels(segs, min_km=18.0)
    assert [x["text"] for x in labels] == ["long", "mid", "short"]


def test_road_labels_carry_tier_for_zoom_switching():
    """每条标注带 tier：前端按缩放级别分级显示，避免全国视图糊成一片。

    分档规则（见 route_visual 的常量）：
      ≥ LABEL_LONG_KM(120)   → long
      ≥ LABEL_MEDIUM_KM(8)   → medium
      其余                    → short
    """
    segs = [
        _seg(0, "huge", 300.0, [(121.0, 31.0), (122.0, 32.0)]),
        _seg(1, "mid", 60.0, [(122.0, 32.0), (122.5, 32.5)]),
        _seg(2, "small", 20.0, [(122.5, 32.5), (122.8, 32.8)]),
    ]
    labels = route_visual.road_labels(segs, min_km=5.0)
    tiers = {x["text"]: x["tier"] for x in labels}
    assert tiers["huge"] == "long"
    assert tiers["mid"] == "medium"
    # 20 km ≥ LABEL_MEDIUM_KM(8) → medium；short 档要 < 8 km
    assert tiers["small"] == "medium"
    tiny = route_visual.road_labels(
        [_seg(0, "tiny", 3.0, [(121.0, 31.0), (121.05, 31.05)])], min_km=1.0)
    assert tiny[0]["tier"] == "short"


def test_road_labels_anchor_at_midpoint():
    """锚点取路段中点，不是首点（首点容易与上一段末端重叠）。"""
    segs = [_seg(0, "A", 50.0, [(121.0, 31.0), (121.5, 31.5)])]
    labels = route_visual.road_labels(segs, min_km=18.0)
    mid = labels[0]
    assert 121.0 < mid["lng"] < 121.5
    assert 31.0 < mid["lat"] < 31.5


def test_payload_roadnames_key_does_not_clash_with_labels():
    """**命名撞车防线**：payload 里 "labels" 存等级中文名（图例用），
    道路名标注必须用别的 key。

    实测踩过：把标注存进 "labels" → 图例退化���英文（motorway/trunk）。
    """
    import pandas as pd
    from components import baseline_map
    steps = pd.DataFrame([
        {"step_index": 0, "distance_km": 60.0, "duration_min": 40.0,
         "speed_kmh": 90.0, "carbon_kg": 63.0, "road_class": "motorway",
         "road_name": "沈海高速", "road_ref": "G15",
         "geometry_geojson": '{"type":"LineString","coordinates":[[121.3,31.0],[121.9,31.6]]}'},
    ])
    payload = baseline_map.build_baseline_payload(
        steps, {"road_class_share": {"motorway": 100.0}},
        layers=[basemap_align.amap_xyz_layer()])
    # 图例映射必须完好
    assert payload["labels"]["motorway"] == "高速"
    # 标注在另一个 key
    assert "roadNames" in payload
    assert payload["roadNames"][0]["text"] == "沈海高速"


def test_payload_mileage_and_roadnames_present():
    """payload 必须带上里程刻度与道路名标注。"""
    import pandas as pd
    from components import baseline_map
    steps = pd.DataFrame([
        {"step_index": 0, "distance_km": 150.0, "duration_min": 100.0,
         "speed_kmh": 90.0, "carbon_kg": 158.0, "road_class": "motorway",
         "road_name": "G50", "road_ref": "G50",
         "geometry_geojson": '{"type":"LineString","coordinates":[[121.3,31.0],[121.9,31.8]]}'},
    ])
    payload = baseline_map.build_baseline_payload(
        steps, {}, layers=[basemap_align.amap_xyz_layer()])
    assert len(payload["mileage"]) == 1
    assert payload["mileage"][0]["km"] == 100.0
    assert len(payload["roadNames"]) == 1


# ---------------------------------------------------------------- 工程纪律

def test_components_import_src_with_absolute_import():
    """``src`` 不是包——组件层必须用绝对导入。

    相对导入（``from ..routing.coord import``）会抛
    ``ImportError: attempted relative import beyond top-level package``，
    实测会让第 6 页整个渲染失败。
    """
    src = (ROOT / "app" / "components" / "basemap_align.py").read_text(encoding="utf-8")
    assert "from src.routing.coord import" in src
    assert "from ..routing" not in src
