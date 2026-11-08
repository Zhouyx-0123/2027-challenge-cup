# -*- coding: utf-8 -*-
"""生成 OBD 原始数据样例与 1 万条车队模拟数据（docs/63）。

设计约束：
- 字段顺序与 data/raw/obd_gps/obd_stream.csv 完全一致；
- 10 台车 × 1,000 条 = 10,000 条，避免“单车连续 27 小时”这种不合理场景；
- 油耗由 速度 + 载重 驱动，碳排 = 累计油耗差 × 2.63 kg/L；
- 生成后做物理校验：每 10s 位移 <= 0.264 km，碳强度 0.3–3.0 kg/km。
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "sim"
TZ = timezone(timedelta(hours=8))
DT_SECONDS = 10
CARBON_KG_PER_L = 2.63

COLUMNS = [
    "ts", "vehicle_id", "plate", "fuel_type", "instant_fuel_lph",
    "total_fuel_l", "fuel_level_pct", "engine_rpm", "vehicle_speed_kmh",
    "throttle_pos_pct", "engine_load_pct", "engine_temp_c",
    "intake_press_kpa", "atm_press_kpa", "odo_km", "lat", "lng",
    "gps_speed_kmh", "heading_deg", "altitude_m", "satellites",
    "fix_quality", "load_kg", "ignition", "fault_codes", "qc_flags",
]

VEHICLES = [
    # vehicle_id, plate, load_t, mean_speed, start_odo, start_fuel, lat, lng
    ("TRK-LOOP-001", "苏A12345", 12, 52, 50000.0, 800.0, 31.2304, 121.4737),
    ("TRK-LOOP-002", "苏A12346", 18, 58, 62000.0, 820.0, 31.2400, 121.4900),
    ("TRK-LOOP-003", "苏A12347", 30, 48, 74000.0, 900.0, 31.2100, 121.4400),
    ("TRK-LOOP-004", "苏A12348", 12, 35, 86000.0, 780.0, 31.2600, 121.5200),
    ("TRK-LOOP-005", "苏A12349", 18, 62, 98000.0, 840.0, 31.2000, 121.4000),
    ("TRK-LOOP-006", "苏A12350", 30, 55, 110000.0, 950.0, 31.2800, 121.5500),
    ("TRK-LOOP-007", "苏A12351", 12, 42, 122000.0, 760.0, 31.1800, 121.3800),
    ("TRK-LOOP-008", "苏A12352", 18, 50, 134000.0, 880.0, 31.3000, 121.6000),
    ("TRK-LOOP-009", "苏A12353", 30, 45, 146000.0, 920.0, 31.1500, 121.3500),
    ("TRK-LOOP-010", "苏A12354", 12, 60, 158000.0, 810.0, 31.3200, 121.6500),
]


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _vehicle_frame(
    vehicle_id: str,
    plate: str,
    load_t: float,
    mean_speed: float,
    start_odo: float,
    start_fuel: float,
    lat0: float,
    lng0: float,
    *,
    n: int,
    start_ts: datetime,
    seed: int,
    deterministic: bool = False,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    speed = 0.0
    fuel = float(start_fuel)
    odo = float(start_odo)
    lat, lng = float(lat0), float(lng0)
    heading = float(rng.uniform(0, 360))
    tank_l = 600.0
    rows = []

    for i in range(n):
        if deterministic:
            speed = 50.0
            fuel_lph = 18.0
            rpm = 1500.0
            throttle = 35.0
            engine_load = 45.0
            gps_speed = 49.5
            heading = 90.0
        else:
            # 每 120 个点（20 分钟）换一个目标速度；3% 概率怠速。
            if i % 120 == 0:
                target = _clamp(rng.normal(mean_speed, 12.0), 0.0, 92.0)
            # 怠速只持续一个采样点，不能把 target 置 0 后拖 120 行；
            # 否则 3% 的怠速事件会吃掉三分之一的路程。
            if rng.random() < 0.025:
                speed = 0.0
                fuel_lph = _clamp(2.5 * rng.normal(1.0, 0.05), 1.2, 6.0)
            else:
                speed = 0.72 * speed + 0.28 * target + rng.normal(0, 2.2)
                speed = _clamp(speed, 0.0, 95.0)
                if speed < 1.0:
                    speed = 0.0
                # 柴油车油耗模型：怠速 2.5 L/h + 速度项 + 载重×速度项。
                fuel_lph = (
                    2.5
                    + 0.18 * speed
                    + 0.583 * (load_t * speed / 100.0)
                    + 0.25 * load_t * (speed / 50.0)
                )
                fuel_lph *= rng.normal(1.0, 0.06)
                fuel_lph = _clamp(fuel_lph, 1.2 if speed == 0 else 3.0, 55.0)

            rpm = _clamp(22.0 * speed + 600.0 + rng.normal(0, 40), 650, 2600)
            throttle = _clamp(10.0 + 0.7 * speed + 0.01 * load_t + rng.normal(0, 3), 0, 100)
            engine_load = _clamp(20.0 + 0.5 * load_t + 0.3 * speed + rng.normal(0, 3), 5, 95)
            gps_speed = max(0.0, speed + rng.normal(0, 1.5))

        fuel += fuel_lph * DT_SECONDS / 3600.0
        odo += speed * DT_SECONDS / 3600.0

        dist = speed * DT_SECONDS / 3600.0
        if not deterministic:
            heading = (heading + rng.normal(0, 5)) % 360.0
        lat += (dist / 111.0) * math.cos(math.radians(heading))
        lng += (dist / (111.0 * math.cos(math.radians(lat)))) * math.sin(math.radians(heading))

        ts = start_ts + timedelta(seconds=i * DT_SECONDS)
        rows.append({
            "ts": ts.isoformat(timespec="seconds"),
            "vehicle_id": vehicle_id,
            "plate": plate,
            "fuel_type": "diesel",
            "instant_fuel_lph": round(fuel_lph, 2),
            "total_fuel_l": round(fuel, 4),
            "fuel_level_pct": round(_clamp(100.0 - (fuel - start_fuel) / tank_l * 100.0, 5, 100), 2),
            "engine_rpm": int(round(rpm)),
            "vehicle_speed_kmh": round(speed, 2),
            "throttle_pos_pct": round(throttle, 2),
            "engine_load_pct": round(engine_load, 2),
            "engine_temp_c": round(_clamp(85.0 + (0 if deterministic else rng.normal(0, 1.5)), 60, 110), 2),
            "intake_press_kpa": round(_clamp(40.0 + (0 if deterministic else rng.normal(0, 3)), 20, 80), 2),
            "atm_press_kpa": 101.3,
            "odo_km": round(odo, 4),
            "lat": round(lat, 6),
            "lng": round(lng, 6),
            "gps_speed_kmh": round(gps_speed, 2),
            "heading_deg": round(heading % 360.0, 2),
            "altitude_m": round(_clamp(20.0 + (0 if deterministic else rng.normal(0, 3)), 0, 200), 2),
            "satellites": int(rng.integers(6, 15)),
            "fix_quality": 1,
            "load_kg": int(round(_clamp(load_t * 1000.0 + (0 if deterministic else rng.normal(0, 300)), 3000, 30000))),
            "ignition": True,
            "fault_codes": "",
            "qc_flags": "",
        })
    return pd.DataFrame(rows, columns=COLUMNS)


def build_sample() -> pd.DataFrame:
    v = VEHICLES[0]
    return _vehicle_frame(
        *v,
        n=30,
        start_ts=datetime(2026, 11, 7, 10, 0, 0, tzinfo=TZ),
        seed=20261107,
        deterministic=True,
    )


def build_fleet(n_vehicles: int = 10, rows_per_vehicle: int = 1000) -> pd.DataFrame:
    frames = []
    for idx, v in enumerate(VEHICLES[:n_vehicles]):
        frames.append(_vehicle_frame(
            *v,
            n=rows_per_vehicle,
            start_ts=datetime(2026, 11, 7, 6, 0, 0, tzinfo=TZ) + timedelta(minutes=idx * 12),
            seed=20261107 + idx,
        ))
    return pd.concat(frames, ignore_index=True)


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for vid, g in df.groupby("vehicle_id", sort=True):
        g = g.sort_values("ts")
        fuel_l = float(g["total_fuel_l"].max() - g["total_fuel_l"].min())
        km = float(g["odo_km"].max() - g["odo_km"].min())
        carbon_kg = fuel_l * CARBON_KG_PER_L
        ef = carbon_kg * 1000.0 / km if km > 0 else float("nan")
        rows.append({
            "vehicle_id": vid,
            "rows": len(g),
            "distance_km": round(km, 3),
            "fuel_l": round(fuel_l, 3),
            "carbon_kg": round(carbon_kg, 3),
            "ef_g_km": round(ef, 2) if km > 0 else None,
            "avg_speed_kmh": round(km / (len(g) * DT_SECONDS / 3600.0), 2),
            "idle_share_pct": round(float((g["vehicle_speed_kmh"] < 1).mean() * 100), 2),
            "max_speed_kmh": float(g["vehicle_speed_kmh"].max()),
            "max_fuel_lph": float(g["instant_fuel_lph"].max()),
            "max_km_per_row": round(float((g["vehicle_speed_kmh"] * DT_SECONDS / 3600.0).max()), 4),
            "max_fuel_per_row": round(float((g["instant_fuel_lph"] * DT_SECONDS / 3600.0).max()), 4),
        })
    return pd.DataFrame(rows)


def validate(df: pd.DataFrame, summary: pd.DataFrame) -> None:
    assert list(df.columns) == COLUMNS
    assert len(df) == 10000, len(df)
    assert df["vehicle_id"].nunique() == 10
    assert df["vehicle_speed_kmh"].between(0, 95).all()
    assert df["instant_fuel_lph"].between(1.0, 55).all()
    assert df["load_kg"].between(3000, 30000).all()
    for _, g in df.groupby("vehicle_id"):
        assert g["total_fuel_l"].is_monotonic_increasing
        assert g["odo_km"].is_monotonic_increasing
    assert summary["ef_g_km"].between(300, 3000).all(), summary[["vehicle_id", "ef_g_km"]]
    assert (summary["carbon_kg"] / summary["distance_km"]).between(0.3, 3.0).all()
    assert summary["max_km_per_row"].max() <= 0.264 + 1e-6


def _style_header(ws, ncols: int) -> None:
    fill = PatternFill("solid", fgColor="1F4E79")
    for c in range(1, ncols + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(ncols)}1"


def _widths(ws, widths: dict[int, float]) -> None:
    for idx, w in widths.items():
        ws.column_dimensions[get_column_letter(idx)].width = w


def write_excel(sample: pd.DataFrame, fleet: pd.DataFrame, summary: pd.DataFrame, path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "说明"
    lines = [
        "OBD 原始数据模拟集（2026-11-07）",
        "",
        "文件用途：",
        "1) 原始数据_30：板子当前输出的 30 条基准样本，字段顺序与后端 obd_stream.csv 一致。",
        "2) 模拟_10000：10 台车 × 1,000 条 = 10,000 条，10 秒一条，约 2.78 小时/车。",
        "3) 校验：每台车的里程、油耗、碳排、碳强度与物理上限。",
        "4) 公式模板：Excel 可向下填充的 RAND 公式，按同一口径扩样。",
        "",
        "关键口径：",
        "碳排 kg = 累计油耗差 L × 2.63 kgCO2/L（TTW 柴油）。",
        "油耗不是随机独立列：由速度、载重、怠速共同决定；速度 0 时只计怠速油耗。",
        "每 10 秒位移上限 0.264 km（95 km/h），每 10 秒油耗上限约 0.153 L（55 L/h）。",
        "lat/lng 是设备原始 WGS84 坐标，入高德地图前需转 GCJ-02。",
        "",
        "不要做的事：",
        "- 不要给 speed 和 fuel 各自独立 RAND，否则会出现“1000 g 碳跑 1 万公里”。",
        "- 不要把 total_fuel_l 当成每行油耗；它是累计值。",
        "- 不要把 qc_flags/fault_codes 填成随机数；它们应由规则产生。",
    ]
    for i, line in enumerate(lines, 1):
        c = ws.cell(row=i, column=1, value=line)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        if i == 1:
            c.font = Font(bold=True, size=14)
    ws.column_dimensions["A"].width = 110

    ws2 = wb.create_sheet("原始数据_30")
    ws2.append(COLUMNS)
    for row in sample.itertuples(index=False):
        ws2.append(list(row))
    _style_header(ws2, len(COLUMNS))
    _widths(ws2, {1: 26, 2: 14, 3: 12, 4: 10, 5: 16, 6: 14, 7: 14, 8: 10, 9: 16, 10: 14,
                  11: 14, 12: 14, 13: 14, 14: 12, 15: 12, 16: 11, 17: 11, 18: 14,
                  19: 12, 20: 10, 21: 10, 22: 10, 23: 10, 24: 10, 25: 12, 26: 12})

    ws3 = wb.create_sheet("模拟_10000")
    ws3.append(COLUMNS)
    for row in fleet.itertuples(index=False):
        ws3.append(list(row))
    _style_header(ws3, len(COLUMNS))
    _widths(ws3, {1: 26, 2: 14, 3: 12, 4: 10, 5: 16, 6: 14, 7: 14, 8: 10, 9: 16, 10: 14,
                  11: 14, 12: 14, 13: 14, 14: 12, 15: 12, 16: 11, 17: 11, 18: 14,
                  19: 12, 20: 10, 21: 10, 22: 10, 23: 10, 24: 10, 25: 12, 26: 12})

    ws4 = wb.create_sheet("校验")
    ws4.append(list(summary.columns))
    for row in summary.itertuples(index=False):
        ws4.append(list(row))
    _style_header(ws4, len(summary.columns))
    _widths(ws4, {1: 16, 2: 8, 3: 12, 4: 10, 5: 12, 6: 12, 7: 14, 8: 14, 9: 14, 10: 14, 11: 16, 12: 16})

    ws5 = wb.create_sheet("公式模板")
    ws5.append(COLUMNS)
    # 第一行数据：常量；第 2 行起：公式，向下填充即可。
    first = {
        "ts": datetime(2026, 11, 7, 10, 0, 0),
        "vehicle_id": "TRK-LOOP-001",
        "plate": "苏A12345",
        "fuel_type": "diesel",
        "instant_fuel_lph": 2.5,
        "total_fuel_l": 800.0,
        "fuel_level_pct": 100.0,
        "engine_rpm": 650,
        "vehicle_speed_kmh": 0.0,
        "throttle_pos_pct": 8.0,
        "engine_load_pct": 26.0,
        "engine_temp_c": 85.0,
        "intake_press_kpa": 40.0,
        "atm_press_kpa": 101.3,
        "odo_km": 50000.0,
        "lat": 31.2304,
        "lng": 121.4737,
        "gps_speed_kmh": 0.0,
        "heading_deg": 90.0,
        "altitude_m": 20.0,
        "satellites": 10,
        "fix_quality": 1,
        "load_kg": 12000,
        "ignition": True,
        "fault_codes": "",
        "qc_flags": "",
    }
    ws5.append([first[c] for c in COLUMNS])
    formulas = {
        "ts": '=A2+TIME(0,0,10)',
        "vehicle_id": '=$B$2',
        "plate": '=$C$2',
        "fuel_type": '="diesel"',
        "instant_fuel_lph": '=MAX(1.2,2.5+0.18*I3+0.583*(W3/1000)*I3/100+0.25*(W3/1000)*(I3/50)+NORM.INV(RAND(),0,0.6))',
        "total_fuel_l": '=F2+E3*10/3600',
        "fuel_level_pct": '=MAX(5,100-(F3-$F$2)/600*100)',
        "engine_rpm": '=MAX(650,22*I3+600+NORM.INV(RAND(),0,40))',
        "vehicle_speed_kmh": '=MAX(0,MIN(95,0.72*I2+0.28*NORM.INV(RAND(),55,18)+NORM.INV(RAND(),0,2)))',
        "throttle_pos_pct": '=MAX(0,MIN(100,10+0.7*I3+0.01*(W3/1000)+NORM.INV(RAND(),0,3)))',
        "engine_load_pct": '=MAX(5,MIN(95,20+0.5*(W3/1000)+0.3*I3+NORM.INV(RAND(),0,3)))',
        "engine_temp_c": '=85+NORM.INV(RAND(),0,1.5)',
        "intake_press_kpa": '=40+NORM.INV(RAND(),0,3)',
        "atm_press_kpa": '=101.3',
        "odo_km": '=O2+I3*10/3600',
        "lat": '=ROUND(P2+(I3*10/3600)/111*COS(RADIANS(S3)),6)',
        "lng": '=ROUND(Q2+(I3*10/3600)/(111*COS(RADIANS(P3)))*SIN(RADIANS(S3)),6)',
        "gps_speed_kmh": '=MAX(0,I3+NORM.INV(RAND(),0,1.5))',
        "heading_deg": '=MOD(S2+NORM.INV(RAND(),0,5),360)',
        "altitude_m": '=20+NORM.INV(RAND(),0,3)',
        "satellites": '=RANDBETWEEN(6,14)',
        "fix_quality": '=1',
        "load_kg": '=MAX(3000,MIN(30000,12000+NORM.INV(RAND(),0,300)))',
        "ignition": '=TRUE',
        "fault_codes": '=""',
        "qc_flags": '=""',
    }
    # 写 100 行公式，演示“向下填充”。
    for r in range(3, 103):
        ws5.append([formulas[c] for c in COLUMNS])
    _style_header(ws5, len(COLUMNS))
    _widths(ws5, {1: 30, 2: 14, 3: 12, 4: 10, 5: 16, 6: 14, 7: 14, 8: 10, 9: 16, 10: 14,
                  11: 14, 12: 14, 13: 14, 14: 12, 15: 12, 16: 11, 17: 11, 18: 14,
                  19: 12, 20: 10, 21: 10, 22: 10, 23: 10, 24: 10, 25: 12, 26: 12})
    for cell in ws5["A"][1:]:
        cell.number_format = 'yyyy-mm-dd"T"hh:mm:ss"+08:00"'
    for row in ws5.iter_rows(min_row=2, max_row=102, min_col=2, max_col=2):
        for cell in row:
            cell.number_format = "@"

    wb.save(path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--vehicles", type=int, default=10)
    ap.add_argument("--rows-per-vehicle", type=int, default=1000)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    sample = build_sample()
    fleet = build_fleet(args.vehicles, args.rows_per_vehicle)
    if len(fleet) != 10000:
        raise SystemExit(f"expected 10000 rows, got {len(fleet)}")
    summary = summarize(fleet)
    validate(fleet, summary)

    sample.to_csv(out / "obd_raw_sample_30.csv", index=False, encoding="utf-8-sig", lineterminator="\n")
    fleet.to_csv(out / "obd_raw_10000.csv", index=False, encoding="utf-8-sig", lineterminator="\n")
    records = json.loads(sample.to_json(orient="records", force_ascii=False))
    (out / "obd_raw_sample_30.json").write_text(
        json.dumps({"records": records}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "obd_raw_sample_30.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8"
    )
    write_excel(sample, fleet, summary, out / "obd_raw_10000.xlsx")

    print(json.dumps({
        "out": str(out),
        "sample_rows": len(sample),
        "fleet_rows": len(fleet),
        "vehicles": int(fleet["vehicle_id"].nunique()),
        "distance_km": round(float(summary["distance_km"].sum()), 3),
        "fuel_l": round(float(summary["fuel_l"].sum()), 3),
        "carbon_kg": round(float(summary["carbon_kg"].sum()), 3),
        "ef_g_km": round(float(summary["carbon_kg"].sum() * 1000 / summary["distance_km"].sum()), 2),
        "ef_min": round(float(summary["ef_g_km"].min()), 2),
        "ef_max": round(float(summary["ef_g_km"].max()), 2),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
