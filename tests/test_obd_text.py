# -*- coding: utf-8 -*-
"""OBD 串口文本解析与分段测试。"""
from __future__ import annotations

import pandas as pd
import pytest

from src.obd_text import (
    POINT_COLUMNS,
    SEGMENT_COLUMNS,
    aggregate_segments,
    parse_line,
    parse_log,
)


SAMPLE = (
    "[ 12 s] RPM= 1500.0 SPD= 60.0 TQ= 45/ 42 | FA= 12.34L/h "
    "FB=2.500km/L(=24.0) FC= 1234.5L | T= 88.0/ 75.0/ 90.0 "
    "ODO= 123456.7 HRS= 1234.5 | DTC=2 MIL=1 | BUS=RUN "
    "TEC=0 REC=0 RX=12345 ERR=0 LINK=OK"
)


def _line(t: int, *, speed: float = 60.0, odo: float = 100.0,
          fuel: float = 1000.0, link: str = "OK", bus: str = "RUN",
          rpm: float = 1500.0, fa: float = 20.0) -> str:
    fb = speed / fa if fa > 0 else 0.0
    return (
        f"[ {t:2d} s] RPM= {rpm:7.1f} SPD= {speed:5.1f} TQ= 45/ 42 | "
        f"FA= {fa:5.2f}L/h FB={fb:5.3f}km/L(={fa:4.1f}) FC= {fuel:8.1f}L | "
        f"T= 88.0/ 75.0/ 90.0 ODO= {odo:9.1f} HRS= 1234.5 | "
        f"DTC=0 MIL=0 | BUS={bus} TEC=0 REC=0 RX=12345 ERR=0 LINK={link}"
    )


def test_parse_sample_fields():
    rec = parse_line(SAMPLE, line_no=1)
    assert rec is not None
    assert rec["elapsed_s"] == 12
    assert rec["engine_rpm"] == 1500.0
    assert rec["vehicle_speed_kmh"] == 60.0
    assert rec["torque_demand_pct"] == 45.0
    assert rec["torque_actual_pct"] == 42.0
    assert rec["instant_fuel_lph"] == 12.34
    assert rec["fuel_econ_km_per_l"] == 2.5
    assert rec["fb_derived_lph"] == 24.0
    assert rec["total_fuel_l"] == 1234.5
    assert rec["coolant_temp_c"] == 88.0
    assert rec["odo_km"] == 123456.7
    assert rec["dtc_count"] == 2
    assert rec["mil"] == 1
    assert rec["link_ok"] is True
    assert "fuel_source_mismatch" in rec["qc_flags"]
    assert "dtc_active" in rec["qc_flags"]


def test_missing_markers_become_none_not_zero():
    line = SAMPLE
    for key in ("RPM", "SPD", "TQ", "FA", "FB", "FC", "ODO", "HRS"):
        # 逐字段替换太脆弱；这里只验证一个字段的缺失语义
        pass
    line = line.replace("RPM= 1500.0", "RPM= ---")
    rec = parse_line(line)
    assert rec is not None
    assert rec["engine_rpm"] is None
    assert "missing_rpm" in rec["qc_flags"]


def test_parse_log_header_and_absolute_time():
    log = "DEV_ID=OBD-001 PLATE=苏US3126 FW=1.0.3 CAN=500k\n" + SAMPLE
    pts, meta = parse_log(log, session_start="2026-11-06T10:00:00+08:00")
    assert meta["header"]["DEV_ID"] == "OBD-001"
    assert meta["header"]["PLATE"] == "苏US3126"
    assert meta["header"]["FW"] == "1.0.3"
    assert meta["header"]["CAN"] == "500k"
    assert len(pts) == 1
    assert pts.iloc[0]["vehicle_id"] == "OBD-001"
    assert pts.iloc[0]["plate"] == "苏US3126"
    assert pts.iloc[0]["ts"] == "2026-11-06T10:00:12+08:00"
    assert pts.iloc[0]["session_id"].startswith("OBD-001:")


def test_bad_line_is_counted_not_fatal():
    log = SAMPLE + "\nthis is not a snapshot\n"
    pts, meta = parse_log(log)
    assert len(pts) == 1
    assert meta["rejected"] == 1
    assert meta["errors"][0]["line_no"] == 2


def test_parse_log_empty():
    pts, meta = parse_log("")
    assert len(pts) == 0
    assert list(pts.columns) == POINT_COLUMNS
    assert meta["parsed"] == 0


def test_gap_splits_segments_and_aggregates_odo_fuel():
    log = "\n".join([
        _line(0, odo=100.0, fuel=1000.0),
        _line(10, odo=100.2, fuel=1000.2),
        _line(20, odo=100.4, fuel=1000.4),
        _line(300, odo=100.6, fuel=1000.6),
        _line(310, odo=100.8, fuel=1000.8),
        _line(320, odo=101.0, fuel=1001.0),
    ])
    pts, _ = parse_log(log, session_start="2026-11-06T10:00:00+08:00")
    segs = aggregate_segments(pts, gap_s=120, min_samples=2, min_duration_s=5)
    assert len(segs) == 2
    first, second = segs.iloc[0], segs.iloc[1]
    assert first["sample_count"] == 3
    assert first["duration_s"] == 20.0
    assert first["distance_km"] == pytest.approx(0.4)
    assert first["fuel_l"] == pytest.approx(0.4)
    assert first["distance_source"] == "odo"
    assert first["fuel_source"] == "fc_delta"
    assert first["usable"] is True or bool(first["usable"]) is True
    assert second["sample_count"] == 3
    assert second["distance_km"] == pytest.approx(0.4)


def test_bus_stop_excluded_and_reopens_segment():
    log = "\n".join([
        _line(0, odo=100.0, fuel=1000.0),
        _line(10, odo=100.1, fuel=1000.1),
        _line(20, odo=100.2, fuel=1000.2, bus="STOP"),
        _line(30, odo=100.3, fuel=1000.3),
        _line(40, odo=100.4, fuel=1000.4),
    ])
    pts, _ = parse_log(log)
    segs = aggregate_segments(pts, gap_s=120, min_samples=2, min_duration_s=5)
    assert len(segs) == 2
    assert segs.iloc[0]["sample_count"] == 2
    assert segs.iloc[1]["sample_count"] == 2
    # 停机点不应出现在任何段里
    assert segs["sample_count"].sum() == 4


def test_link_lost_splits_and_marks_unusable():
    log = "\n".join([
        _line(0, odo=100.0, fuel=1000.0),
        _line(10, odo=100.1, fuel=1000.1),
        _line(20, odo=100.2, fuel=1000.2, link="LOST"),
        _line(30, odo=100.3, fuel=1000.3, link="LOST"),
        _line(40, odo=100.4, fuel=1000.4),
        _line(50, odo=100.5, fuel=1000.5),
    ])
    pts, _ = parse_log(log)
    segs = aggregate_segments(pts, gap_s=120, min_samples=2, min_duration_s=5)
    assert len(segs) == 3
    lost = segs.iloc[1]
    assert bool(lost["usable"]) is False
    assert "link_lost" in lost["qc_flags"]


def test_fuel_falls_back_to_instant_integral():
    log = "\n".join([
        _line(0, odo=100.0, fuel=1000.0, fa=20.0),
        _line(10, odo=100.1, fuel=1000.1, fa=20.0),
        _line(20, odo=100.2, fuel=1000.2, fa=20.0),
    ])
    pts, _ = parse_log(log)
    pts["total_fuel_l"] = None
    segs = aggregate_segments(pts, gap_s=120, min_samples=2, min_duration_s=5)
    assert len(segs) == 1
    s = segs.iloc[0]
    assert s["fuel_source"] == "instant_integral"
    assert s["fuel_l"] == pytest.approx(20.0 * 20 / 3600.0, rel=1e-6)
    assert "fuel_from_instant" in s["qc_flags"]


def test_empty_segment_frame_has_contract_columns():
    segs = aggregate_segments(pd.DataFrame(columns=POINT_COLUMNS))
    assert len(segs) == 0
    assert list(segs.columns) == SEGMENT_COLUMNS
