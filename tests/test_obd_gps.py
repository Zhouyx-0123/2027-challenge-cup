# -*- coding: utf-8 -*-
"""OBD + GPS 实测碳排通道的契约与算法测试（docs/52）。

覆盖四条线：
  1. **契约** —— 字段、别名、因子来源都在契约文件里，代码不许自造；
  2. **守恒法** —— 数值对拍、优先取累计油耗、回退不产生负油耗、熄火不产碳；
  3. **分车聚合** —— 每台车的碳排、碳强度、怠速占比；
  4. **边界** —— 实测轨**不进 E₀ 主口径、不改主结果表、不参与主闸门**。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]

from src import obd_gps, obd_pipeline  # noqa: E402


# ---------------------------------------------------------------------------
# 1. 契约
# ---------------------------------------------------------------------------

def test_contract_file_exists_and_has_required_shape():
    contract = obd_gps.load_contract()
    assert contract, "契约文件缺失或解析失败"
    assert contract.get("version")
    fields = contract.get("fields") or {}
    for name in ("ts", "vehicle_id", "instant_fuel_lph", "total_fuel_l",
                 "engine_rpm", "vehicle_speed_kmh", "lat", "lng"):
        assert name in fields, "契约缺字段：%s" % name
        assert "desc" in fields[name], "字段缺说明：%s" % name


def test_contract_declares_fuel_is_mandatory():
    """本通道的硬要求：没有油耗就没有碳。"""
    contract = obd_gps.load_contract()
    fields = contract.get("fields") or {}
    assert fields["instant_fuel_lph"].get("required") is False
    assert fields["total_fuel_l"].get("required") is False
    notes = json.dumps(contract, ensure_ascii=False)
    assert "instant_fuel_lph" in notes and "total_fuel_l" in notes
    assert "二选一" in notes or "至少要有一个" in notes


def test_alias_map_resolves_device_field_names():
    lookup = obd_gps.build_alias_map()
    assert lookup[obd_gps._norm_key("car_id")] == "vehicle_id"
    assert lookup[obd_gps._norm_key("rpm")] == "engine_rpm"
    assert lookup[obd_gps._norm_key("fuel_rate")] == "instant_fuel_lph"
    assert lookup[obd_gps._norm_key("latitude")] == "lat"
    # 规范名优先级最高
    assert lookup[obd_gps._norm_key("engine_rpm")] == "engine_rpm"


def test_normalize_accepts_aliases_and_flags_missing_fuel():
    records = [
        {"timestamp": "2026-10-29T10:00:00+08:00", "car_id": "TRK-001",
         "rpm": 1450, "speed": 62.5, "fuel_rate": 24.0,
         "latitude": 31.23, "longitude": 121.47},
        {"timestamp": "2026-10-29T10:00:02+08:00", "vehicle_id": "TRK-002",
         "engine_rpm": 800, "vehicle_speed_kmh": 10.0},
    ]
    normalized, rejects = obd_gps.normalize_records(records)
    assert len(normalized) == 1
    assert normalized.iloc[0]["vehicle_id"] == "TRK-001"
    assert normalized.iloc[0]["engine_rpm"] == 1450.0
    assert normalized.iloc[0]["instant_fuel_lph"] == 24.0
    assert normalized.iloc[0]["lat"] == 31.23
    assert len(rejects) == 1
    assert rejects.iloc[0]["reason"] == "missing_fuel"


def test_normalize_rejects_missing_timestamp_and_vehicle():
    records = [
        {"vehicle_id": "TRK-001", "instant_fuel_lph": 10.0},
        {"ts": "2026-10-29T10:00:00+08:00", "instant_fuel_lph": 10.0},
    ]
    normalized, rejects = obd_gps.normalize_records(records)
    assert normalized.empty
    assert len(rejects) == 2
    assert set(rejects["reason"]) == {"missing_vehicle_id", "missing_or_bad_ts"}


def test_out_of_range_value_is_dropped_not_clamped():
    """越界值置空并记标记，不静默夹到边界。"""
    records = [{"timestamp": "2026-10-29T10:00:00+08:00", "vehicle_id": "TRK-001",
                "instant_fuel_lph": 10.0, "engine_rpm": 99999}]
    normalized, _ = obd_gps.normalize_records(records)
    assert pd.isna(normalized.iloc[0]["engine_rpm"])
    assert "out_of_range" in normalized.iloc[0]["qc_flags"]


# ---------------------------------------------------------------------------
# 2. 守恒法
# ---------------------------------------------------------------------------

def test_authoritative_factor_comes_from_calc_factors():
    """出账因子只从 config/calc_factors.json 读（唯一真源）。"""
    value, source = obd_gps.authoritative_factor("diesel")
    calc = obd_gps.load_calc_factors()
    assert value == pytest.approx(calc["co2_factors"]["diesel_ttw_kg_per_l"]["value"])
    assert source


def test_conservation_derivation_matches_authoritative_factor():
    """守恒推导值与权威因子的偏差在容差内（3%）。"""
    value, _ = obd_gps.authoritative_factor("diesel")
    derived = obd_gps.derived_factor("diesel")
    assert derived is not None
    deviation = abs(derived - value) / value * 100.0
    assert deviation < 3.0, "守恒推导偏差过大：%.3f%%" % deviation


def test_carbon_from_instant_fuel_matches_hand_calculation():
    """2 秒、24 L/h、柴油 → 24/1800 L × 2.63 kg/L。"""
    frame = pd.DataFrame([{
        "ts": pd.Timestamp("2026-10-29T10:00:00+08:00"),
        "vehicle_id": "TRK-001", "fuel_type": "diesel",
        "instant_fuel_lph": 24.0, "total_fuel_l": None,
        "vehicle_speed_kmh": 60.0, "odo_km": 100.0, "lat": 31.23, "lng": 121.47,
        "qc_flags": [],
    }, {
        "ts": pd.Timestamp("2026-10-29T10:00:02+08:00"),
        "vehicle_id": "TRK-001", "fuel_type": "diesel",
        "instant_fuel_lph": 24.0, "total_fuel_l": None,
        "vehicle_speed_kmh": 60.0, "odo_km": 100.0333, "lat": 31.2301, "lng": 121.4701,
        "qc_flags": [],
    }])
    carbon = obd_gps.attach_carbon(frame)
    factor, _ = obd_gps.authoritative_factor("diesel")
    expected = 24.0 * 2.0 / 3600.0 * factor
    actual = float(carbon["kg_co2"].iloc[-1])
    assert actual == pytest.approx(expected, abs=1e-6)
    assert carbon["fuel_source"].iloc[-1] == "instant"


def test_cumulative_fuel_takes_priority_over_instant():
    """两条都有时优先用累计油耗差分（仪表累计更稳）。"""
    frame = pd.DataFrame([{
        "ts": pd.Timestamp("2026-10-29T10:00:00+08:00"), "vehicle_id": "TRK-001",
        "fuel_type": "diesel", "instant_fuel_lph": 24.0, "total_fuel_l": 100.0,
        "vehicle_speed_kmh": 60.0, "odo_km": 100.0, "qc_flags": [],
    }, {
        "ts": pd.Timestamp("2026-10-29T10:00:02+08:00"), "vehicle_id": "TRK-001",
        "fuel_type": "diesel", "instant_fuel_lph": 99.0, "total_fuel_l": 100.01,
        "vehicle_speed_kmh": 60.0, "odo_km": 100.0333, "qc_flags": [],
    }])
    carbon = obd_gps.attach_carbon(frame)
    assert carbon["fuel_source"].iloc[-1] == "odometer"
    assert float(carbon["fuel_l_step"].iloc[-1]) == pytest.approx(0.01, abs=1e-6)


def test_cumulative_fuel_rollback_does_not_create_negative_fuel():
    """累计油耗回退（仪表复位）→ 记标记并断开该步，不产生负油耗。"""
    frame = pd.DataFrame([{
        "ts": pd.Timestamp("2026-10-29T10:00:00+08:00"), "vehicle_id": "TRK-001",
        "fuel_type": "diesel", "instant_fuel_lph": None, "total_fuel_l": 100.0,
        "vehicle_speed_kmh": 60.0, "odo_km": 100.0, "qc_flags": [],
    }, {
        "ts": pd.Timestamp("2026-10-29T10:00:02+08:00"), "vehicle_id": "TRK-001",
        "fuel_type": "diesel", "instant_fuel_lph": None, "total_fuel_l": 98.0,
        "vehicle_speed_kmh": 60.0, "odo_km": 100.0333, "qc_flags": [],
    }])
    carbon = obd_gps.attach_carbon(frame)
    step = float(carbon["fuel_l_step"].iloc[-1])
    assert step == 0.0 or pd.isna(step), "回退不得产生负油耗：%r" % step
    assert carbon["fuel_flag"].iloc[-1] == "fuel_negative"


def test_ignition_off_produces_no_carbon():
    """熄火不产碳（不能把驻车算成燃烧）。"""
    frame = pd.DataFrame([{
        "ts": pd.Timestamp("2026-10-29T10:00:00+08:00"), "vehicle_id": "TRK-001",
        "fuel_type": "diesel", "instant_fuel_lph": 24.0, "total_fuel_l": None,
        "vehicle_speed_kmh": 0.0, "odo_km": 100.0, "ignition": True, "qc_flags": [],
    }, {
        "ts": pd.Timestamp("2026-10-29T10:00:02+08:00"), "vehicle_id": "TRK-001",
        "fuel_type": "diesel", "instant_fuel_lph": 24.0, "total_fuel_l": None,
        "vehicle_speed_kmh": 0.0, "odo_km": 100.0, "ignition": False, "qc_flags": [],
    }])
    carbon = obd_gps.attach_carbon(frame)
    assert float(carbon["kg_co2"].iloc[-1]) == 0.0


def test_electric_vehicle_produces_no_carbon():
    """纯电车没有燃料消耗，本通道不产碳。"""
    frame = pd.DataFrame([{
        "ts": pd.Timestamp("2026-10-29T10:00:00+08:00"), "vehicle_id": "EV-001",
        "fuel_type": "electric", "instant_fuel_lph": None, "total_fuel_l": None,
        "vehicle_speed_kmh": 60.0, "odo_km": 100.0, "qc_flags": [],
    }, {
        "ts": pd.Timestamp("2026-10-29T10:00:02+08:00"), "vehicle_id": "EV-001",
        "fuel_type": "electric", "instant_fuel_lph": None, "total_fuel_l": None,
        "vehicle_speed_kmh": 60.0, "odo_km": 100.0333, "qc_flags": [],
    }])
    carbon = obd_gps.attach_carbon(frame)
    assert float(carbon["kg_co2"].iloc[-1]) == 0.0
    assert float(carbon["factor_kg_per_l"].iloc[-1]) == 0.0


def test_sample_gap_does_not_integrate_fuel():
    """相邻间隔过大视为缺样，该步不参与油耗积分。"""
    frame = pd.DataFrame([{
        "ts": pd.Timestamp("2026-10-29T10:00:00+08:00"), "vehicle_id": "TRK-001",
        "fuel_type": "diesel", "instant_fuel_lph": 24.0, "total_fuel_l": None,
        "vehicle_speed_kmh": 60.0, "odo_km": 100.0, "qc_flags": [],
    }, {
        "ts": pd.Timestamp("2026-10-29T10:10:00+08:00"), "vehicle_id": "TRK-001",
        "fuel_type": "diesel", "instant_fuel_lph": 24.0, "total_fuel_l": None,
        "vehicle_speed_kmh": 60.0, "odo_km": 160.0, "qc_flags": [],
    }])
    carbon = obd_gps.attach_carbon(frame)
    step = float(carbon["fuel_l_step"].iloc[-1])
    assert pd.isna(step) or step == 0.0, "缺样不得积分出油耗：%r" % step


# ---------------------------------------------------------------------------
# 3. 分车聚合
# ---------------------------------------------------------------------------

def test_vehicle_summary_totals_are_consistent():
    """每车碳排之和 = 车队总碳排；碳强度 = kg × 1000 / km。"""
    frame = obd_gps.demo_stream()
    normalized, _ = obd_gps.normalize_records(frame.to_dict(orient="records"))
    carbon = obd_gps.attach_carbon(normalized)
    summary = obd_gps.summarize_vehicles(carbon)

    assert len(summary) == 2
    factor, _ = obd_gps.authoritative_factor("diesel")
    assert float(summary["kg_co2"].sum()) == pytest.approx(
        float(summary["fuel_l"].sum()) * factor, rel=1e-6)
    for _, row in summary.iterrows():
        expected = float(row["kg_co2"]) * 1000.0 / float(row["dist_km"])
        # 聚合表里的碳强度按 3 位小数取整，比较时留同等余量
        assert float(row["co2_gkm"]) == pytest.approx(expected, abs=0.01)
        assert 0.0 <= float(row["idle_ratio"]) <= 1.0
        assert float(row["valid_rate"]) <= 1.0


def test_demo_stream_is_deterministic():
    """演示序列必须可复现（评审时不能每刷新一次就变）。"""
    anchor = pd.Timestamp("2026-10-29T08:00:00+08:00")
    first = obd_gps.demo_stream(anchor=anchor)
    second = obd_gps.demo_stream(anchor=anchor)
    assert first.equals(second)
    assert len(first) == 6600


def test_demo_calibration_matches_project_factor_baseline():
    """演示数据的量级必须与项目自家因子表对得上（否则就是编的）。

    口径基准（config/calc_factors.json 体系，见 src/factor_model.py）：
      · 30 t 载重 / VSP=2 → 排放率 14.67 g/s、碳强度 1055.952 g/km。
    演示数据是「长途 + 市区 + 爬坡」混合工况，所以：
      · 巡航段排放率应落在 14.67 附近；
      · 全程碳强度应落在 1055.952 附近（±25%）；
      · 峰值排放率对应爬坡高 VSP 段，应明显高于巡航但不超过 ~30 g/s。
    """
    frame = obd_gps.demo_stream()
    normalized, _ = obd_gps.normalize_records(frame.to_dict(orient="records"))
    carbon = obd_gps.attach_carbon(normalized)
    summary = obd_gps.summarize_vehicles(carbon)
    heavy = summary[summary["vehicle_id"] == "TRK-GZ-001"]
    assert not heavy.empty
    row = heavy.iloc[0]

    # 全程碳强度：与 30 t / v=50 的 1055.952 g/km 同一量级
    assert 800.0 < float(row["co2_gkm"]) < 1320.0, \
        "演示碳强度 %.1f g/km 偏离项目因子基准过远" % float(row["co2_gkm"])
    # 油耗：重载卡车混合工况 30–50 L/100km
    litres_per_100km = float(row["fuel_l"]) / float(row["dist_km"]) * 100.0
    assert 28.0 < litres_per_100km < 52.0, \
        "演示油耗 %.1f L/100km 不像重载卡车" % litres_per_100km
    # 怠速占比：真实车队 10%–25%
    assert 0.08 < float(row["idle_ratio"]) < 0.30
    # 峰值排放率：爬坡高 VSP 段
    heavy_points = carbon[carbon["vehicle_id"] == "TRK-GZ-001"]
    peak = float(heavy_points["er_gs"].max())
    assert 15.0 < peak < 32.0, "演示峰值排放率 %.1f g/s 不合理" % peak


def test_qc_report_counts_flags_and_rejects():
    records = [
        {"timestamp": "2026-10-29T10:00:00+08:00", "vehicle_id": "TRK-001",
         "instant_fuel_lph": 24.0},
        {"timestamp": "2026-10-29T10:00:02+08:00", "vehicle_id": "TRK-002",
         "engine_rpm": 800},
    ]
    normalized, rejects = obd_gps.normalize_records(records)
    report = obd_gps.qc_report(normalized, rejects)
    assert report["n_accepted"] == 1
    assert report["n_rejected"] == 1
    assert report["reject_reasons"].get("missing_fuel") == 1


# ---------------------------------------------------------------------------
# 4. 边界：实测轨不进主口径、不改主结果表
# ---------------------------------------------------------------------------

def test_obd_pipeline_writes_its_own_tables_only(tmp_path, monkeypatch):
    """跑一遍 OBD 流水线，主链路的结果表**一个字节都不能变**。"""
    processed = tmp_path / "processed"
    processed.mkdir()
    guarded = ["wide_data.parquet", "baseline_carbon.parquet", "final_report.parquet"]
    before = {}
    for name in guarded:
        source = ROOT / "data" / "processed" / name
        target = processed / name
        if source.exists():
            target.write_bytes(source.read_bytes())
            before[name] = hashlib.sha256(target.read_bytes()).hexdigest()

    monkeypatch.setattr(obd_pipeline, "PROCESSED", processed)
    monkeypatch.setattr(obd_pipeline, "POINTS_PATH", processed / "obd_points.parquet")
    monkeypatch.setattr(obd_pipeline, "VEHICLE_PATH",
                        processed / "obd_vehicle_carbon.parquet")
    monkeypatch.setattr(obd_pipeline, "TRIPS_PATH", processed / "obd_trips.parquet")
    monkeypatch.setattr(obd_pipeline, "SUMMARY_PATH", processed / "obd_summary.json")

    report = obd_pipeline.run_obd_pipeline(use_demo=True)
    assert report["status"] == "ok"
    assert report["n_vehicles"] == 2
    assert report["total_kg_co2"] > 0

    for name in guarded:
        if name in before:
            after = hashlib.sha256((processed / name).read_bytes()).hexdigest()
            assert after == before[name], "OBD 流水线改动了主结果表：%s" % name


def test_services_layer_does_not_import_the_carbon_algorithm():
    """前端只读层不许 import 守恒法算法（守住「前端只读不算」）。

    用 AST 看**真实的 import**，而不是全文搜字符串 —— 注释里提到算法名是允许的。
    """
    source = (ROOT / "app" / "services.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            for alias in node.names:
                imported.add(alias.name)
    assert not any("obd_gps" in name or "obd_pipeline" in name for name in imported), \
        "只读层不许 import 实测碳排算法：%s" % sorted(imported)
    assert "attach_carbon" not in source
    assert "summarize_vehicles" not in source


def test_obd_results_are_not_in_main_gate_targets():
    """主闸门对拍目标里不能混进实测轨的数（口径冻结）。"""
    source = (ROOT / "src" / "pipeline.py").read_text(encoding="utf-8")
    assert "obd" not in source.lower().replace("obdx", ""), \
        "主 pipeline 不应引用 obd 通道（实测轨必须独立）"


# ---------------------------------------------------------------------------
# 5. 接收端
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OBD_STREAM_DIR", str(tmp_path))
    monkeypatch.setenv("SENSOR_AUTH", "0")
    import server
    server.app.config.update(TESTING=True)
    return server.app.test_client()


def test_upload_endpoint_accepts_and_rejects(client):
    payload = {"records": [
        {"timestamp": "2026-10-29T10:00:00+08:00", "car_id": "TRK-001",
         "rpm": 1450, "speed": 62.5, "fuel_rate": 24.0,
         "latitude": 31.23, "longitude": 121.47},
        {"timestamp": "2026-10-29T10:00:02+08:00", "vehicle_id": "TRK-001",
         "engine_rpm": 1460, "vehicle_speed_kmh": 62.0, "total_fuel": 18422.6},
        {"timestamp": "2026-10-29T10:00:04+08:00", "vehicle_id": "TRK-002",
         "engine_rpm": 800},
    ]}
    response = client.post("/api/upload_obd_gps", json=payload)
    assert response.status_code == 200
    body = response.get_json()
    assert body["accepted"] == 2
    assert body["rejected"] == 1
    assert body["reasons"].get("missing_fuel") == 1

    stream = client.application  # 只用于确认 app 可用
    assert stream is not None


def test_upload_endpoint_requires_device_token_when_auth_enabled(tmp_path, monkeypatch):
    """默认 fail-closed：没令牌一律 401。"""
    monkeypatch.setenv("OBD_STREAM_DIR", str(tmp_path))
    monkeypatch.setenv("SENSOR_AUTH", "1")
    import server
    server.app.config.update(TESTING=True)
    client = server.app.test_client()
    response = client.post("/api/upload_obd_gps", json={
        "records": [{"timestamp": "2026-10-29T10:00:00+08:00",
                     "vehicle_id": "TRK-001", "instant_fuel_lph": 24.0}]})
    assert response.status_code == 401


def test_upload_endpoint_rejects_empty_and_oversized_batches(client):
    empty = client.post("/api/upload_obd_gps", json={"records": []})
    assert empty.status_code == 400
    huge = client.post("/api/upload_obd_gps", json={
        "records": [{"vehicle_id": "TRK-001", "instant_fuel_lph": 1.0}] * 5001})
    assert huge.status_code == 413


def test_stream_file_round_trips_through_the_pipeline(tmp_path, monkeypatch):
    """设备直传落盘的 CSV 能被流水线读回来并算出碳。"""
    monkeypatch.setenv("OBD_STREAM_DIR", str(tmp_path))
    monkeypatch.setenv("SENSOR_AUTH", "0")
    import server
    server.app.config.update(TESTING=True)
    client = server.app.test_client()

    records = [{"timestamp": "2026-10-29T10:00:%02d+08:00" % (index * 2),
                "vehicle_id": "TRK-001", "instant_fuel_lph": 24.0,
                "vehicle_speed_kmh": 60.0, "odo_km": 100.0 + index * 0.0333}
               for index in range(5)]
    assert client.post("/api/upload_obd_gps",
                       json={"records": records}).status_code == 200

    processed = tmp_path / "processed"
    processed.mkdir()
    monkeypatch.setattr(obd_pipeline, "PROCESSED", processed)
    monkeypatch.setattr(obd_pipeline, "POINTS_PATH", processed / "obd_points.parquet")
    monkeypatch.setattr(obd_pipeline, "VEHICLE_PATH",
                        processed / "obd_vehicle_carbon.parquet")
    monkeypatch.setattr(obd_pipeline, "TRIPS_PATH", processed / "obd_trips.parquet")
    monkeypatch.setattr(obd_pipeline, "SUMMARY_PATH", processed / "obd_summary.json")

    report = obd_pipeline.run_obd_pipeline()
    assert report["status"] == "ok"
    assert report["n_vehicles"] == 1
    assert report["total_kg_co2"] > 0


# ---------------------------------------------------------------------------
# 6. 前端面板
# ---------------------------------------------------------------------------

def test_obd_panel_renders_without_exception():
    """面板能正常渲染（结果表在库里时）。"""
    import sys

    from streamlit.testing.v1 import AppTest

    app_dir = str(ROOT / "app")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)

    def script():
        from components import obd_panel
        from demo_state import init_state
        init_state()
        obd_panel.render_obd_panel()

    at = AppTest.from_function(script, default_timeout=60)
    at.run()
    assert not at.exception, "\n".join(str(e.value) for e in at.exception)


def test_obd_panel_shows_empty_state_when_tables_missing(monkeypatch):
    """结果表缺失时给空态说明，不红屏、不编数字。"""
    import sys

    from streamlit.testing.v1 import AppTest

    app_dir = str(ROOT / "app")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)

    import services

    monkeypatch.setattr(services, "obd_vehicles", lambda: pd.DataFrame())

    def script():
        from components import obd_panel
        from demo_state import init_state
        init_state()
        obd_panel.render_obd_panel()

    at = AppTest.from_function(script, default_timeout=60)
    at.run()
    assert not at.exception, "\n".join(str(e.value) for e in at.exception)
    content = "\n".join(item.value for item in at.markdown)
    assert "尚未接入" in content
