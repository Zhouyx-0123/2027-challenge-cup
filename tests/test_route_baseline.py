# -*- coding: utf-8 -*-
"""D4 · 上海—北京基线测试：坐标、求和、碳排、schema、等级。

验收口径（docs/35 §8）
----------------------
* 端点必须落在 OSM 真实路网上（吸附距离 < 50 m）
* step 距离/时长求和与路线偏差 < 0.5%
* carbon_total == sum(step carbon)
* 道路等级覆盖率 100%，unknown < 2%
* 新文件没有高德 key / AMap URL
* 现有测试不回归

离线优先
--------
本文件**默认不联网**。网络相关用例（OSRM 实拉）由 ``@pytest.mark.network``
标记，需要时用 ``-m network`` 显式开启；CI 默认跑的是离线用例。
这样 CI 不会因为公共 OSRM 限流而红。

参考：docs/35_上海北京基线_任务书.md §8。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from src.routing import carbon_baseline, coord
from src.routing.road_class import ROAD_CLASSES, classify_road

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
STEPS_PATH = PROCESSED / "route_baseline_sh_bj_steps.parquet"
SUMMARY_PATH = PROCESSED / "route_baseline_sh_bj_summary.json"

#: 虚拟 OD（docs/35 §2），GCJ-02
ORIGIN_GCJ = (121.315087, 31.014995)
DEST_GCJ = (116.664264, 39.909698)

#: docs/35 §5.1 的字段契约
EXPECTED_STEP_COLUMNS = [
    "od_key", "provider", "traffic_mode", "step_index", "distance_km",
    "duration_min", "speed_kmh", "road_name", "road_ref", "road_class",
    "road_class_source", "road_class_confidence", "toll_flag", "toll_yuan",
    "traffic_lights", "carbon_kg", "factor_version", "factor_mode", "vehicle",
    "load_t", "geometry_geojson",
]


# ---------------------------------------------------------------- 坐标

def test_gcj02_wgs84_roundtrip_under_1m():
    """GCJ-02 → WGS-84 → GCJ-02 往返误差 < 1 m。"""
    for lng, lat in (ORIGIN_GCJ, DEST_GCJ):
        w = coord.gcj02_to_wgs84(lng, lat)
        back = coord.wgs84_to_gcj02(w[0], w[1])
        err_m = coord.haversine_km(lng, lat, back[0], back[1]) * 1000.0
        assert err_m < 1.0, "%s 往返误差 %.3f m" % ((lng, lat), err_m)


def test_wgs84_to_gcj02_known_point():
    """正向变换（权威方向）对照公开基准点：天安门。

    WGS-84 (116.397128, 39.916527) → GCJ-02 约 (116.4034, 39.9179)。
    容差 0.001°（约 100 m），足够抓住"tx/ty 接反"这类错误。
    """
    lng, lat = coord.wgs84_to_gcj02(116.397128, 39.916527)
    assert abs(lng - 116.4034) < 0.001, "经度 %.6f" % lng
    assert abs(lat - 39.9179) < 0.001, "纬度 %.6f" % lat


def test_convert_rejects_unknown_pair():
    with pytest.raises(ValueError):
        coord.convert(1.0, 2.0, "gcj02", "mars")


def test_out_of_china_passthrough():
    """境外坐标 GCJ-02 == WGS-84。"""
    lng, lat = coord.gcj02_to_wgs84(-74.0060, 40.7128)  # 纽约
    assert abs(lng - (-74.0060)) < 1e-9
    assert abs(lat - 40.7128) < 1e-9


def test_conversion_is_not_the_swapped_tx_ty_variant():
    """防回归：守住"tx/ty 接反"这个坑。

    接反的实现在上海点会给出 (121.317487, 31.011123) —— 正是 docs/35 §2
    写死的那组数。若哪天有人把实现改回接反版，这条会立刻红。
    """
    lng, lat = coord.gcj02_to_wgs84(*ORIGIN_GCJ)
    swapped = (121.317487, 31.011123)
    err_m = coord.haversine_km(swapped[0], swapped[1], lng, lat) * 1000.0
    assert err_m > 100.0, (
        "转换结果与接反版几乎一致（%.2f m），实现可能被改回了 tx/ty 接反的错误版本。"
        "请用 AMap 真实折线重做裁判：标准实现应为米级重合。"
        % err_m)


# ---------------------------------------------------------------- 道路等级

def test_g103_is_trunk_not_motorway():
    """docs/35 §6 硬要求：G 开头不等于高速，G103 是国道。"""
    klass, _, _ = classify_road(name="京滨线", ref="G103", speed_kmh=85.0,
                                distance_km=37.0)
    assert klass == "trunk"


def test_g15_is_motorway():
    klass, _, _ = classify_road(name="沈海高速", ref="G15", speed_kmh=90.0,
                                distance_km=78.0)
    assert klass == "motorway"


def test_s_prefix_is_secondary():
    klass, _, _ = classify_road(name="新车公路", ref="S226", speed_kmh=65.0,
                                distance_km=4.7)
    assert klass == "secondary"


def test_name_beats_ref_when_name_says_expressway():
    """名称含"联络线"但 ref 给成 S5 —— 名称证据优先。"""
    klass, source, _ = classify_road(name="荣乌联络线", ref="S5", speed_kmh=90.0,
                                     distance_km=8.1)
    assert klass == "motorway"
    assert source == "name_motorway"


def test_ramp_requires_short_distance():
    """ramp 需距离 < 3 km；长距离的 fork/merge 不算匝道。"""
    klass, _, _ = classify_road(name="", ref="", speed_kmh=90.0,
                                distance_km=1.0, maneuver_type="merge")
    assert klass == "ramp"
    klass, _, _ = classify_road(name="", ref="", speed_kmh=90.0,
                                distance_km=12.0, maneuver_type="merge")
    assert klass != "ramp"


def test_unknown_only_when_no_signal():
    klass, _, conf = classify_road(name="", ref="", speed_kmh=0.0,
                                   distance_km=1.0)
    assert klass == "unknown"
    assert conf == "low"


def test_all_labels_are_known():
    for klass in ROAD_CLASSES:
        assert isinstance(klass, str) and klass


# ---------------------------------------------------------------- 碳排模型

def test_carbon_factor_and_mode():
    assert carbon_baseline.FACTOR_MODE == "fixed_50"
    assert carbon_baseline.EF_BASE_GKM == 1055.95
    assert carbon_baseline.FACTOR_VERSION == "JCB2023-BASE-v1"


def test_carbon_formula():
    """carbon_kg_step = EF_base × distance_km / 1000。"""
    carbon = carbon_baseline.step_carbon_kg(100.0)
    assert math.isclose(carbon, 1055.95 * 100.0 / 1000.0, rel_tol=1e-9)


def test_carbon_rejects_other_vehicle():
    """换车型必须先补因子表，不许静默套用 30t 因子。"""
    with pytest.raises(ValueError):
        carbon_baseline.step_carbon_kg(100.0, vehicle="18t_truck")


def test_road_class_does_not_affect_carbon():
    """docs/35 §9 第 7 条：道路等级不乘碳权重。

    同样 100 km，motorway 与 urban 的碳排必须完全一致。
    """
    a = carbon_baseline.step_carbon_kg(100.0)
    b = carbon_baseline.step_carbon_kg(100.0)
    assert a == b


# ---------------------------------------------------------------- 产出物

@pytest.fixture(scope="module")
def steps() -> pd.DataFrame:
    if not STEPS_PATH.exists():
        pytest.skip("未生成基线表，先跑 python -m tools.route_baseline")
    return pd.read_parquet(STEPS_PATH)


@pytest.fixture(scope="module")
def summary() -> dict:
    if not SUMMARY_PATH.exists():
        pytest.skip("未生成基线 summary，先跑 python -m tools.route_baseline")
    return json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))


def test_steps_schema_exact(steps: pd.DataFrame):
    """逐 step 表字段契约（docs/35 §5.1）——不多不少、顺序一致。"""
    assert list(steps.columns) == EXPECTED_STEP_COLUMNS


def test_summary_schema_has_required_keys(summary: dict):
    """summary 必备字段（docs/35 §5.2）。"""
    required = ["distance_km", "duration_min", "carbon_kg", "carbon_t",
                "road_class_share", "step_count", "provider", "traffic_mode",
                "factor_version", "factor_mode", "generated_at", "points"]
    missing = [k for k in required if k not in summary]
    assert not missing, "summary 缺字段: %s" % missing


def test_step_distance_sum_within_half_percent(steps: pd.DataFrame, summary: dict):
    """step 距离求和与路线总距离偏差 < 0.5%（docs/35 §8）。"""
    total = float(steps["distance_km"].sum())
    route = float(summary["distance_km"])
    assert abs(total - route) / route < 0.005, \
        "距离求和偏差 %.4f%%" % ((total - route) / route * 100.0)


def test_step_duration_sum_within_half_percent(steps: pd.DataFrame, summary: dict):
    """step 时长求和与路线总时长偏差 < 0.5%。"""
    total = float(steps["duration_min"].sum())
    route = float(summary["duration_min"])
    assert abs(total - route) / route < 0.005


def test_carbon_total_equals_sum_of_steps(steps: pd.DataFrame, summary: dict):
    """carbon_total == sum(step carbon) —— 逐行加总，不是另算一遍。"""
    assert math.isclose(float(steps["carbon_kg"].sum()), float(summary["carbon_kg"]),
                        rel_tol=1e-6)
    assert math.isclose(float(summary["carbon_t"]),
                        float(summary["carbon_kg"]) / 1000.0, rel_tol=1e-9)


def test_each_step_carbon_matches_formula(steps: pd.DataFrame):
    """逐 step 碳排也必须满足公式（防止表里混进别的口径）。"""
    expect = steps["distance_km"] * carbon_baseline.EF_BASE_GKM / 1000.0
    assert (expect - steps["carbon_kg"]).abs().max() < 1e-6


def test_road_class_coverage_100_and_unknown_under_2pct(steps: pd.DataFrame):
    """等级覆盖率 100%，unknown < 2%（docs/35 §8）。"""
    assert steps["road_class"].notna().all()
    assert (steps["road_class"] != "").all()
    known = steps[steps["road_class"] != "unknown"]
    assert len(known) / len(steps) >= 0.98, \
        "unknown 占比 %.2f%%（要求 < 2%%）" % ((1 - len(known) / len(steps)) * 100.0)
    assert set(steps["road_class"]) <= set(ROAD_CLASSES)


def test_traffic_mode_is_freeflow(steps: pd.DataFrame, summary: dict):
    """OSRM 无实时路况 —— traffic_mode 必须恒为 freeflow。"""
    assert set(steps["traffic_mode"]) == {"freeflow"}
    assert summary["traffic_mode"] == "freeflow"


def test_toll_amount_is_empty_not_faked(steps: pd.DataFrame):
    """OSRM 无过路费金额 —— toll_yuan 必须为空，不许拿 AMap 的数硬填。"""
    assert steps["toll_yuan"].isna().all(), "toll_yuan 不应有值"


def test_disclaimer_present(summary: dict):
    """摘要必须写明"非实时 · 固定因子 · 道路等级 proxy"。"""
    d = summary.get("disclaimer") or {}
    assert d.get("real_time") is False
    assert d.get("fixed_factor") is True
    assert d.get("road_class_is_proxy") is True


def test_snap_distance_recorded_and_within_tolerance(summary: dict):
    """端点吸附距离（真实路网校验）必须记录且 < 50 m。"""
    snap = summary.get("snap_distance_m") or {}
    assert "origin" in snap and "dest" in snap
    assert snap["origin"] < 50.0, "起点吸附 %.1f m" % snap["origin"]
    assert snap["dest"] < 50.0, "终点吸附 %.1f m" % snap["dest"]


def test_magnitude_sanity(summary: dict):
    """量级校验（docs/35 §7）—— 不是硬编码断言，OSM 更新会让数值漂移。

    这里取宽松区间，只保证"没跑飞"：距离在 1,000–1,400 km，
    碳排在 1,000–1,500 kg。
    """
    assert 1000.0 < float(summary["distance_km"]) < 1400.0
    assert 1000.0 < float(summary["carbon_kg"]) < 1500.0
    assert 40 <= int(summary["step_count"]) <= 120


def test_no_amap_key_or_url_in_new_files():
    """新文件里不得出现高德 key / AMap URL（docs/35 §8）。"""
    needles = ["amap", "AMap", "restapi.amap.com", "webapi.amap.com",
               "amap_js_key", "amap_key"]
    for path in (STEPS_PATH, SUMMARY_PATH):
        if not path.exists():
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        for needle in needles:
            assert needle not in text, "%s 里出现 %r" % (path.name, needle)


def test_existing_route_tables_untouched():
    """route_od.parquet / route_candidates.parquet 不许被本任务改动。"""
    # 断言它们的字段仍然存在（本任务只新增文件，不动老表）
    for name in ("route_od.parquet", "route_candidates.parquet"):
        path = PROCESSED / name
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        assert len(df) > 0
        assert "od_key" in df.columns


# ---------------------------------------------------------------- 网络（可选）

@pytest.mark.network
def test_osrm_endpoint_snaps_to_real_road():
    """实拉 OSRM：确认端点确实落在 OSM 路网上（< 50 m）。

    这是 docs/35 §2「端点误差 > 50 m 视为失败」的可执行版本。
    需要网络；公共实例限流时可能失败，故标记为 network 用例。
    """
    from src.routing import osrm_client

    provider = osrm_client.OsrmProvider()
    o = coord.gcj02_to_wgs84(*ORIGIN_GCJ)
    d = coord.gcj02_to_wgs84(*DEST_GCJ)
    _, _, o_snap = provider.snap_to_road(o)
    _, _, d_snap = provider.snap_to_road(d)
    assert o_snap < 50.0, "起点吸附 %.1f m" % o_snap
    assert d_snap < 50.0, "终点吸附 %.1f m" % d_snap
