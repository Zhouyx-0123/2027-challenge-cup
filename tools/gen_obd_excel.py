# -*- coding: utf-8 -*-
"""生成「板子原始格式 → Excel → 1 万条」的 OBD/财务联调模拟数据（docs/68）。

为什么单独做一份：
- 现有 data/sim/obd_raw_10000.csv 是 10 车 × 1,000 点连续 10s（单车连跑 2.8 小时），
  没有分趟、没有加油闭环、也没有可核算的财务侧凭证；
- 本脚本按「一天一趟、30s 采样、8 台车 × 5 天 × 250 点」生成 10,000 条，
  并把物理一致性写死在生成过程里，避免「1000 g 碳跑 1 万公里」这类数据。

物理一致性（生成后逐条断言，不通过直接抛错）：
1) odo[i]-odo[i-1] == speed*dt/3600          （里程与车速自洽）
2) total_fuel[i]-total_fuel[i-1] == lph*dt/3600（累计油耗与瞬时油耗自洽）
3) 碳 = 油耗 × 2.63 kg/L（TTW 柴油），趟级碳强度落在 150–1800 g/km
4) 油位 = 累计加油 - 累计消耗，0–100%，只在下限触发加油
5) 分趟只由 >15 分钟的间隔切开（与 src/obd_gps.py TRIP_GAP_MINUTES 一致）

输出（默认 data/sim/excel_10000/）：
- OBD_模拟数据_10000条.xlsx   六个 sheet：说明 / 原始流 / 行程汇总 / 加油发票 / 对账结果 / 随机扩展模板
- obd_stream_10000.csv        与网关落盘完全同构的 26 列（可直接 --from-csv）
- obd_board_batch_30.json     板子上报格式（{"records":[...]}，30 条一批）
- fuel_invoices.csv           加油发票（财务侧）
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
TZ = timezone(timedelta(hours=8))
DT = 30.0                      # 采样间隔（秒）
POINTS_PER_TRIP = 250          # 250 × 30s ≈ 2 小时 5 分
DAYS = ["2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"]
CARBON_KG_PER_L = 2.63         # 与 config/calc_factors.json 的 diesel_ttw 一致（下方按 config 覆盖）
try:  # 以 config 为准，避免硬编码漂移
    _co2f = json.loads((ROOT / "config" / "calc_factors.json").read_text(encoding="utf-8"))["co2_factors"]
    CARBON_KG_PER_L = float(_co2f["diesel_ttw_kg_per_l"]["value"])
    CARBON_KG_PER_L_WTW = float(_co2f["diesel_ttw_kg_per_l"]["value"]) + float(_co2f["diesel_wtt_kg_per_l"]["value"])
except Exception:  # pragma: no cover
    CARBON_KG_PER_L_WTW = CARBON_KG_PER_L + 0.53

COLUMNS = [
    "ts", "vehicle_id", "plate", "fuel_type", "instant_fuel_lph",
    "total_fuel_l", "fuel_level_pct", "engine_rpm", "vehicle_speed_kmh",
    "throttle_pos_pct", "engine_load_pct", "engine_temp_c",
    "intake_press_kpa", "atm_press_kpa", "odo_km", "lat", "lng",
    "gps_speed_kmh", "heading_deg", "altitude_m", "satellites",
    "fix_quality", "load_kg", "ignition", "fault_codes", "qc_flags",
]

# vehicle_id, plate, 车型, 油箱 L, 额定载重 t, 整备质量 t, 起步油位, 基准纬度, 基准经度, 路线形态, 发票偏差
VEHICLES: List[Dict[str, Any]] = [
    dict(vid="TRK-LOOP-001", plate="苏A12345", kind="31t", tank=600.0, cap_t=30.0, curb_t=13.0,
         start_pct=0.32, lat=31.2304, lng=121.4737, style="highway", bias=+0.012),
    dict(vid="TRK-SH-002", plate="苏A12346", kind="31t", tank=600.0, cap_t=30.0, curb_t=13.0,
         start_pct=0.45, lat=31.2600, lng=121.4200, style="highway", bias=+0.335),
    dict(vid="TRK-SH-003", plate="苏A12347", kind="18t", tank=400.0, cap_t=18.0, curb_t=8.0,
         start_pct=0.28, lat=31.2000, lng=121.5000, style="highway", bias=-0.022),
    dict(vid="TRK-SH-004", plate="苏A12348", kind="18t", tank=400.0, cap_t=18.0, curb_t=8.0,
         start_pct=0.55, lat=31.2800, lng=121.4600, style="city", bias=+0.182),
    dict(vid="TRK-SH-005", plate="苏A12349", kind="12t", tank=300.0, cap_t=12.0, curb_t=6.0,
         start_pct=0.38, lat=31.1900, lng=121.3800, style="city", bias=-0.061),
    dict(vid="TRK-SH-006", plate="苏A12350", kind="12t", tank=300.0, cap_t=12.0, curb_t=6.0,
         start_pct=0.62, lat=31.3100, lng=121.5300, style="highway", bias=+0.152),
    dict(vid="TRK-SH-007", plate="苏A12351", kind="4.5t", tank=120.0, cap_t=4.5, curb_t=2.6,
         start_pct=0.50, lat=31.2200, lng=121.6100, style="city", bias=+0.006),
    dict(vid="TRK-SH-008", plate="苏A12352", kind="4.5t", tank=120.0, cap_t=4.5, curb_t=2.6,
         start_pct=0.72, lat=31.1550, lng=121.4400, style="city", bias=-0.013),
]

#: 故意留覆盖缺口的车：跳过 11-01 的加油票 → 11-02 的趟次没有油量覆盖
GAP_VID = "TRK-SH-006"
GAP_SKIP_DATE = "2026-11-01"

PHASES = {
    "highway": [(0.10, 28.0), (0.52, 76.0), (0.60, 46.0), (0.86, 72.0), (1.00, 22.0)],
    "city": [(0.10, 18.0), (0.45, 44.0), (0.55, 26.0), (0.90, 40.0), (1.00, 12.0)],
}


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _target_speed(frac: float, style: str) -> float:
    for end, v in PHASES[style]:
        if frac <= end:
            return v
    return PHASES[style][-1][1]


def _fuel_lph(v: float, mass_kg: float, rng: np.random.Generator, warm: float) -> float:
    """柴油车稳态油耗模型：怠速 + 速度项 + 载重×速度项。

    标定（载重含整备质量）：
      31t 满载 44t @70km/h ≈ 27 L/h ≈ 39 L/100km；空载 13t ≈ 14 L/h ≈ 20 L/100km
      18t 满载 26t @65km/h ≈ 18 L/h ≈ 28 L/100km
      4.5t 满载 7t @70km/h ≈ 11 L/h ≈ 16 L/100km
      城市 20km/h 重载 ≈ 9 L/h ≈ 46 L/100km
    """
    base = 1.9 + 0.085 * v + 6.5e-6 * mass_kg * max(v, 0.0)
    base *= 1.0 + (rng.random() - 0.5) * 0.06
    base *= warm
    if v < 0.5:
        base = 1.9 * (1.0 + (rng.random() - 0.5) * 0.10)
    return _clamp(base, 0.9, 62.0)


def simulate_trip(veh: Dict[str, Any], date: str, day_idx: int,
                  state: Dict[str, Any], rng: np.random.Generator) -> List[Dict[str, Any]]:
    """模拟一趟（250 点）。state 跨天保留 odo / 油量 / 位置。"""
    hh = 6 + day_idx % 2
    start = datetime.fromisoformat(date + "T00:00:00").replace(tzinfo=TZ) \
        + timedelta(hours=hh, minutes=40 + int(rng.integers(0, 20)))
    # 载重：每 3 趟里 2 趟重载、1 趟空返
    loaded = (day_idx + len(veh["vid"])) % 3 != 0
    if loaded:
        load_t = veh["cap_t"] * float(rng.uniform(0.55, 0.95))
    else:
        load_t = veh["cap_t"] * float(rng.uniform(0.0, 0.10))
    mass_kg = (veh["curb_t"] + load_t) * 1000.0

    heading = float(rng.uniform(0, 360))
    rows: List[Dict[str, Any]] = []
    speed = 0.0
    warm = 1.12
    for i in range(POINTS_PER_TRIP):
        frac = i / float(POINTS_PER_TRIP)
        # 去程 → 返程：过中点后掉头
        if i == POINTS_PER_TRIP // 2:
            heading = (heading + 180.0) % 360.0
        tgt = _target_speed(frac, veh["style"]) * (1.0 + (rng.random() - 0.5) * 0.12)
        if veh["style"] == "city" and rng.random() < 0.02:
            tgt = 0.0
        speed = 0.72 * speed + 0.28 * tgt + float(rng.normal(0, 1.2))
        speed = 0.0 if speed < 1.2 else _clamp(speed, 0.0, 88.0)
        warm = 1.0 + 0.12 * math.exp(-i / 12.0)

        lph = _fuel_lph(speed, mass_kg, rng, warm)
        dt_h = DT / 3600.0
        dist_km = speed * dt_h
        state["odo"] += dist_km
        state["fuel"] += lph * dt_h
        state["level"] -= lph * dt_h
        if state["level"] < 0.10 * veh["tank"]:      # 下限保护：不允许油位为负
            state["level"] = 0.10 * veh["tank"]

        heading = (heading + float(rng.normal(0, 1.6))) % 360.0
        rad = math.radians(heading)
        state["lat"] += dist_km * math.cos(rad) / 111.32
        state["lng"] += dist_km * math.sin(rad) / (111.32 * math.cos(math.radians(state["lat"])))

        rpm = _clamp(620.0 + (30.0 if veh["kind"] == "31t" else 38.0) * speed
                     + float(rng.normal(0, 25)), 600.0, 2400.0)
        throttle = _clamp(6.0 + 0.62 * speed + 0.3 * load_t + float(rng.normal(0, 2.5)), 0.0, 100.0)
        eng_load = _clamp(14.0 + 55.0 * (mass_kg / 1000.0) / 46.0 + 0.16 * speed
                          + float(rng.normal(0, 2.0)), 5.0, 95.0)
        temp = _clamp(24.0 + 62.0 * min(1.0, i / 10.0) + float(rng.normal(0, 1.2)), 20.0, 96.0)

        rows.append({
            "ts": (start + timedelta(seconds=int(i * DT))).isoformat(),
            "vehicle_id": veh["vid"],
            "plate": veh["plate"],
            "fuel_type": "diesel",
            "instant_fuel_lph": round(lph, 3),
            "total_fuel_l": round(state["fuel"], 4),
            "fuel_level_pct": round(100.0 * state["level"] / veh["tank"], 2),
            "engine_rpm": round(rpm, 0),
            "vehicle_speed_kmh": round(speed, 1),
            "throttle_pos_pct": round(throttle, 1),
            "engine_load_pct": round(eng_load, 1),
            "engine_temp_c": round(temp, 1),
            "intake_press_kpa": round(_clamp(98.0 + 0.55 * speed + float(rng.normal(0, 2)), 90.0, 210.0), 1),
            "atm_press_kpa": round(101.3 + float(rng.normal(0, 0.25)), 2),
            "odo_km": round(state["odo"], 4),
            "lat": round(state["lat"], 6),
            "lng": round(state["lng"], 6),
            "gps_speed_kmh": round(max(0.0, speed + float(rng.normal(0, 0.6))), 1),
            "heading_deg": round(heading, 1),
            "altitude_m": round(4.0 + float(rng.normal(0, 0.8)), 1),
            "satellites": int(rng.integers(9, 15)),
            "fix_quality": 1,
            "load_kg": round(load_t * 1000.0, 0),
            "ignition": True,
            "fault_codes": "",
            "qc_flags": "",
        })
    state["depart"] = rows[0]["ts"]
    state["arrive"] = rows[-1]["ts"]
    state["trip_fuel_l"] = float(rows[-1]["total_fuel_l"]) - state.get("trip_fuel_start", 0.0)
    return rows


def build() -> Tuple[pd.DataFrame, pd.DataFrame, List[Dict[str, Any]]]:
    rows: List[Dict[str, Any]] = []
    trips: List[Dict[str, Any]] = []
    invoices: List[Dict[str, Any]] = []
    seq = 0

    for vi, veh in enumerate(VEHICLES):
        rng = np.random.default_rng(20261108 + vi)
        state = {
            "odo": 50_000.0 + vi * 12_000.0,
            "fuel": 8_000.0 + vi * 400.0,
            "level": veh["tank"] * veh["start_pct"],
            "lat": veh["lat"], "lng": veh["lng"],
        }
        daily = []          # 每天消耗的油量（用于生成次日晚间加油票）
        day_trips = []
        for di, date in enumerate(DAYS):
            fuel0 = state["fuel"]
            odo0 = state["odo"]
            tr = simulate_trip(veh, date, di, state, rng)
            used = state["fuel"] - fuel0
            daily.append(used)
            trips.append({
                "trip_id": "%s-%s" % (veh["vid"], date.replace("-", "")),
                "vehicle_id": veh["vid"], "plate": veh["plate"], "kind": veh["kind"],
                "date": date, "month": date[:7],
                "depart_ts": tr[0]["ts"], "arrive_ts": tr[-1]["ts"],
                "points": len(tr),
                "distance_km": round(state["odo"] - odo0, 3),
                "fuel_l": round(used, 4),
                "kg_co2": round(used * CARBON_KG_PER_L, 4),
                "avg_speed_kmh": round((state["odo"] - odo0) / (len(tr) * DT / 3600.0), 2),
                "load_kg": tr[0]["load_kg"], "loaded": tr[0]["load_kg"] > 1000,
                "fuel_level_end_pct": tr[-1]["fuel_level_pct"],
            })
            day_trips.append((date, tr))
            rows.extend(tr)

        # 加油票：10-31 首次（覆盖 11-01），之后每天 18:30 买「次日用量」
        plan = [("2026-10-31", daily[0])] + [(DAYS[i], daily[i + 1]) for i in range(len(DAYS) - 1)]
        for date, liters in plan:
            if veh["vid"] == GAP_VID and date == GAP_SKIP_DATE:
                continue                        # 故意留覆盖缺口
            seq += 1
            price = round(7.35 + float(rng.normal(0, 0.12)), 2)
            liters = round(max(5.0, liters * (1.0 + veh["bias"] + float(rng.normal(0, 0.02)))), 2)
            amount = round(liters * price, 2)
            net = round(amount / 1.13, 2)
            ts = datetime.fromisoformat(date + "T18:30:00").replace(tzinfo=TZ)
            invoices.append({
                "invoice_no": "0432%s%03d" % (date.replace("-", "")[2:], seq),
                "invoice_date": date,
                "invoice_time": ts.isoformat(),
                "vehicle_id": veh["vid"],
                "plate": veh["plate"],
                "item_name": "柴油",
                "unit": "L",
                "fuel_liters": liters,
                "unit_price": price,
                "amount_excluding_tax": net,
                "tax_rate": "13%",
                "tax_amount": round(amount - net, 2),
                "invoice_amount": amount,
                "buyer_name": "上海碳感知物流有限公司",
                "seller_name": "中国石化销售股份有限公司上海%d号加油站" % (100 + seq % 40),
                "invoice_type": "增值税专用发票",
                "source": "sim_excel_v1",
            })

    df = pd.DataFrame(rows, columns=COLUMNS)
    trip_df = pd.DataFrame(trips)
    return df, trip_df, invoices


def validate(df: pd.DataFrame, trip_df: pd.DataFrame) -> Dict[str, Any]:
    assert len(df) == 10000, len(df)
    assert df["ts"].is_unique or not df.duplicated(["vehicle_id", "ts"]).any()
    worst = 1e-6
    for vid, g in df.groupby("vehicle_id"):
        g = g.reset_index(drop=True)
        # 跨趟（停车过夜）不适用「里程=车速×Δt」：车停着，odo/累计油量都不动
        gap = pd.to_datetime(g["ts"]).diff().dt.total_seconds().fillna(0.0) > 60.0
        # 第 i 行的增量由第 i 行的车速/油耗决定（累加写在该行）
        d_km = g["odo_km"].diff().where(~gap)
        exp_km = (g["vehicle_speed_kmh"] * DT / 3600.0).where(~gap)
        worst = max(worst, float((d_km - exp_km).abs().max()))
        d_f = g["total_fuel_l"].diff().where(~gap)
        exp_f = (g["instant_fuel_lph"] * DT / 3600.0).where(~gap)
        worst = max(worst, float((d_f - exp_f).abs().max()))
        assert d_km.min() >= -1e-9, (vid, d_km.min())
        assert g["total_fuel_l"].is_monotonic_increasing, vid
    assert worst < 2e-3, worst
    assert df["vehicle_speed_kmh"].between(0, 95).all()
    assert df["engine_rpm"].between(550, 2500).all()
    assert df["fuel_level_pct"].between(0, 100).all()
    assert df["load_kg"].between(0, 30000).all()
    assert df["fix_quality"].eq(1).all()

    trip_df["g_per_km"] = trip_df["kg_co2"] / trip_df["distance_km"] * 1000.0
    assert trip_df["g_per_km"].between(150, 1800).all(), trip_df["g_per_km"].describe()
    assert trip_df["distance_km"].between(20, 200).all()
    return {
        "rows": int(len(df)),
        "vehicles": int(df["vehicle_id"].nunique()),
        "trips": int(len(trip_df)),
        "distance_km": round(float(trip_df["distance_km"].sum()), 2),
        "fuel_l": round(float(trip_df["fuel_l"].sum()), 2),
        "kg_co2": round(float(trip_df["kg_co2"].sum()), 2),
        "fleet_g_per_km": round(float(trip_df["kg_co2"].sum() / trip_df["distance_km"].sum() * 1000), 1),
        "trip_g_per_km_min": round(float(trip_df["g_per_km"].min()), 1),
        "trip_g_per_km_max": round(float(trip_df["g_per_km"].max()), 1),
        "consistency_max_err": worst,
    }


# ===========================================================================
# 财务侧：用项目自己的代码做「发票 → 油箱周期分摊 → 车月油耗 → 偏差」
# ===========================================================================

def reconcile(trip_df: pd.DataFrame, invoices: List[Dict[str, Any]]) -> pd.DataFrame:
    import sys
    sys.path.insert(0, str(ROOT))
    from src.fleet_efficiency import allocate_invoice_fuel, vehicle_efficiency_table

    segs = trip_df.rename(columns={"distance_km": "trip_km"}).copy()
    # 发票日期带时区：趟次的 depart_ts 是 +08:00，naive 日期会触发 tz-aware 比较错误
    recs = [dict(inv, invoice_date=inv["invoice_time"]) for inv in invoices]
    alloc = allocate_invoice_fuel(recs, segs)
    eff = vehicle_efficiency_table(alloc)
    # ---- TTW 口径校正列 -------------------------------------------------
    # 项目内 diesel_kg_per_l() 返回 TTW+WTT=3.16，而 kg_co2 是 TTW 2.63，
    # 直接相除会让「理论 L/100km」系统性偏低 20.2%，偏差恒为 +20.2%。
    # 这里不改项目代码，只在表里并列一列用 TTW 复算，供判定用。
    from src.fleet_efficiency import _label
    alloc["_km"] = pd.to_numeric(alloc.get("trip_km"), errors="coerce")
    cov = alloc[alloc["allocated_liters"].notna()]
    g = cov.groupby(["plate", "month"]).apply(
        lambda s: (float(s["kg_co2"].fillna(0).sum()) / float(s["_km"].fillna(0).sum()) * 1000.0)
        if float(s["_km"].fillna(0).sum()) > 0 else float("nan"))
    g = g.rename("legal_g_km").reset_index()
    eff = eff.merge(g, on=["plate", "month"], how="left")
    eff["theoretical_l_100km_ttw"] = (eff["legal_g_km"] / 1000.0 / CARBON_KG_PER_L * 100.0).round(3)
    eff["deviation_pct_ttw"] = ((eff["measured_l_100km"] - eff["theoretical_l_100km_ttw"])
                                / eff["theoretical_l_100km_ttw"] * 100.0).round(2)
    eff["label_ttw"] = [ _label(v, c) for v, c in zip(eff["deviation_pct_ttw"], eff["confidence"]) ]
    return eff


# ===========================================================================
# Excel
# ===========================================================================

DARK = "07080A"
ACCENT = "46B7FF"
FILL_HEAD = PatternFill("solid", fgColor="1B2A33")
FONT_HEAD = Font(bold=True, color="FFFFFF", size=10)


def _sheet_head(ws, headers: List[str], widths: List[int]) -> None:
    for j, (h, w) in enumerate(zip(headers, widths), start=1):
        c = ws.cell(row=1, column=j, value=h)
        c.fill = FILL_HEAD
        c.font = FONT_HEAD
        c.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = "A1:%s1" % get_column_letter(len(headers))


def _write_readme(ws) -> None:
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 30
    ws.column_dimensions["D"].width = 92

    title = ws.cell(row=1, column=1, value="碳感知物流 · OBD/财务 联调模拟数据（10,000 条）")
    title.font = Font(bold=True, size=15, color=ACCENT)
    lines = [
        ("版本", "sim-excel-v1 · 2026-11-08 · 固定随机种子 20261108（任何人重跑结果一致）"),
        ("覆盖面", "8 台车 × 5 天 × 250 点 = 10,000 条，30 秒采样；40 趟、40 张加油票（含 1 处故意缺口）"),
        ("板子格式", "第 01 页 26 列 = 网关 data/raw/obd_gps/obd_stream.csv 的列序；板子实际只上报前 25 列，qc_flags 由网关写"),
        ("怎么灌数据", "① 第 01 页另存为 CSV UTF-8 →  python -m src.obd_pipeline --from-csv <csv> --no-write；"
                      "② 或转成 {\"records\":[...]} 直接 POST http://<PC_IP>:5000/api/upload_obd_gps（30 条一批）"),
        ("财务侧", "第 03 页发票可直接喂 src/fleet_efficiency.py 的 allocate_invoice_fuel / vehicle_efficiency_table；"
                  "第 04 页就是这两步在本数据上的真实输出"),
        ("物理底线", "碳 = 油耗 × 2.63 kg/L；doc 生成时逐条断言 odo 增量 = 车速×Δt、油量增量 = 瞬时油耗×Δt，"
                    "趟级碳强度 150–1800 g/km，杜绝「1000 g 碳跑 1 万公里」"),
        ("扩充到任意条数", "用第 05 页模板：RAND() 生成车速 → 公式推油耗/里程/碳 → 「合理性」列自动判 g/km 是否越界；"
                          "拖满后整列复制 → 选择性粘贴为「数值」再导出 CSV（RAND 会重算）"),
        ("故意注入", "TRK-SH-006（苏A12350）跳过 2026-11-01 的加油票 → 11-02 趟次无油量覆盖（测 no_invoice_cover）；"
                    "各车发票量按 bias 投放（-7%~+34%）：TRK-SH-002 多买 33.5% → 异常·偏高，TRK-SH-004 多买 18.2% → 偏高（关注），其余在 ±10% 内；三档标签都能测到"),
    ]
    r = 3
    for k, v in lines:
        ws.cell(row=r, column=1, value=k).font = Font(bold=True, size=10)
        c = ws.cell(row=r, column=2, value=v)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=4)
        ws.row_dimensions[r].height = 30 if len(v) < 80 else 46
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="原始流字段（与契约 obd-gps-contract-v1 对齐）").font = Font(bold=True, size=12, color=ACCENT)
    r += 1
    for j, h in enumerate(["字段", "单位", "取值范围（本数据）", "含义 / 生成规则"], start=1):
        c = ws.cell(row=r, column=j, value=h)
        c.fill = FILL_HEAD
        c.font = FONT_HEAD
    r += 1
    fields = [
        ("ts", "ISO8601+08:00", "2026-11-01 ~ 11-05，30s 间隔", "采样时刻，带时区；网关落盘时归一为 UTC"),
        ("vehicle_id", "-", "8 个", "车辆唯一标识；TRK-LOOP-001 与固件/设备注册表同名"),
        ("plate", "-", "8 个", "车牌（台账对外须脱敏）"),
        ("fuel_type", "-", "diesel", "决定 CO₂/L 因子"),
        ("instant_fuel_lph", "L/h", "0.9 ~ 62", "稳态模型：1.9 + 0.085v + 6.5e-6·m·v，±3% 噪声，冷机 +12% 衰减"),
        ("total_fuel_l", "L", "单调递增", "ECU 累计油量，逐点累加 instant×Δt/3600"),
        ("fuel_level_pct", "%", "10 ~ 100", "油位 = 累计加油 − 累计消耗；低于 10% 时保护性封底"),
        ("engine_rpm", "rpm", "600 ~ 2400", "620 + 30v（重卡）/ 38v（轻卡），怠速 650"),
        ("vehicle_speed_kmh", "km/h", "0 ~ 88", "分相位目标速度（高速 76 / 城市 40）+ 一阶平滑 + 2% 概率停车"),
        ("throttle_pos_pct", "%", "0 ~ 100", "6 + 0.62v + 0.3·载重(t) + 噪声"),
        ("engine_load_pct", "%", "5 ~ 95", "随总质量与车速上升"),
        ("engine_temp_c", "°C", "20 ~ 96", "10 点内从 24 暖机到 86"),
        ("intake_press_kpa", "kPa", "90 ~ 210", "98 + 0.55v"),
        ("atm_press_kpa", "kPa", "≈101.3", "上海海拔常数 + 噪声"),
        ("odo_km", "km", "单调递增", "起点 5 万~13.4 万；逐点加 车速×Δt/3600"),
        ("lat / lng", "deg", "31.1~31.4 / 121.3~121.7", "按航向做 haversine 反解，去程/返程掉头"),
        ("gps_speed_kmh", "km/h", "≈车速", "车速 + N(0,0.6)"),
        ("heading_deg", "deg", "0 ~ 360", "航向，逐点 ±1.6° 抖动"),
        ("altitude_m", "m", "2 ~ 7", "上海平原"),
        ("satellites", "颗", "9 ~ 14", "定位星数"),
        ("fix_quality", "-", "1", "1=GPS 有效定位"),
        ("load_kg", "kg", "0 ~ 28500", "每 3 趟 1 趟空返，其余 55%~95% 额定载重"),
        ("ignition", "bool", "TRUE", "点火状态"),
        ("fault_codes", "-", "空", "无故障码（空串，不写 0/null）"),
        ("qc_flags", "-", "空", "网关质检标记，原始数据留空"),
    ]
    for row in fields:
        for j, v in enumerate(row, start=1):
            ws.cell(row=r, column=j, value=v).alignment = Alignment(wrap_text=True, vertical="top")
        r += 1


def write_workbook(path: Path, df: pd.DataFrame, trip_df: pd.DataFrame,
                   invoices: List[Dict[str, Any]], eff: pd.DataFrame, stats: Dict[str, Any]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "00_说明"
    _write_readme(ws)

    ws1 = wb.create_sheet("01_原始流_10000")
    fmts = {
        "instant_fuel_lph": "0.000", "total_fuel_l": "0.0000", "fuel_level_pct": "0.00",
        "engine_rpm": "0", "vehicle_speed_kmh": "0.0", "throttle_pos_pct": "0.0",
        "engine_load_pct": "0.0", "engine_temp_c": "0.0", "intake_press_kpa": "0.0",
        "atm_press_kpa": "0.00", "odo_km": "0.000", "lat": "0.000000", "lng": "0.000000",
        "gps_speed_kmh": "0.0", "heading_deg": "0.0", "altitude_m": "0.0",
        "satellites": "0", "fix_quality": "0", "load_kg": "#,##0",
    }
    widths = [27, 15, 10, 9, 14, 13, 14, 11, 15, 14, 15, 14, 14, 13, 12, 11, 11, 13, 12, 11, 10, 10, 10, 9, 11, 10]
    _sheet_head(ws1, COLUMNS, widths)
    for i, rec in enumerate(df.itertuples(index=False), start=2):
        for j, (name, val) in enumerate(zip(COLUMNS, rec), start=1):
            c = ws1.cell(row=i, column=j, value=val)
            if name in fmts:
                c.number_format = fmts[name]

    ws2 = wb.create_sheet("02_行程汇总")
    tcols = ["trip_id", "vehicle_id", "plate", "kind", "date", "month", "depart_ts", "arrive_ts",
             "points", "distance_km", "fuel_l", "kg_co2", "g_per_km", "avg_speed_kmh", "load_kg", "loaded"]
    trip_out = trip_df.copy()
    trip_out["g_per_km"] = (trip_out["kg_co2"] / trip_out["distance_km"] * 1000).round(1)
    _sheet_head(ws2, tcols, [30, 15, 10, 8, 11, 9, 27, 27, 7, 12, 10, 11, 11, 13, 10, 9])
    for i, rec in enumerate(trip_out[tcols].itertuples(index=False), start=2):
        for j, v in enumerate(rec, start=1):
            c = ws2.cell(row=i, column=j, value=v)
            if tcols[j - 1] in ("distance_km", "fuel_l", "kg_co2", "avg_speed_kmh"):
                c.number_format = "0.000"

    ws3 = wb.create_sheet("03_加油发票")
    icols = ["invoice_no", "invoice_date", "invoice_time", "vehicle_id", "plate", "item_name", "unit",
             "fuel_liters", "unit_price", "amount_excluding_tax", "tax_rate", "tax_amount",
             "invoice_amount", "buyer_name", "seller_name", "invoice_type", "source"]
    _sheet_head(ws3, icols, [20, 12, 27, 15, 10, 8, 5, 12, 10, 16, 8, 11, 13, 26, 38, 18, 14])
    for i, rec in enumerate(invoices, start=2):
        for j, name in enumerate(icols, start=1):
            c = ws3.cell(row=i, column=j, value=rec.get(name))
            if name in ("fuel_liters", "unit_price", "amount_excluding_tax", "tax_amount", "invoice_amount"):
                c.number_format = "0.00"

    ws4 = wb.create_sheet("04_对账结果")
    ecols = ["plate", "month", "segments_total", "segments_covered", "km_total", "km_covered",
             "fuel_liters_covered", "measured_l_100km", "theoretical_l_100km", "deviation_pct",
             "label", "confidence", "theoretical_l_100km_ttw", "deviation_pct_ttw", "label_ttw",
             "invoice_nos"]
    eheads = [{"theoretical_l_100km_ttw": "理论L/100km_TTW", "deviation_pct_ttw": "偏差%_TTW",
               "label_ttw": "标签_TTW"}.get(c, c) for c in ecols]
    _sheet_head(ws4, eheads, [10, 9, 15, 16, 11, 11, 18, 18, 20, 12, 16, 11, 22, 18, 14, 34])
    for i, rec in enumerate(eff[ecols].itertuples(index=False), start=2):
        for j, v in enumerate(rec, start=1):
            c = ws4.cell(row=i, column=j, value=v)
            if ecols[j - 1] in ("km_total", "km_covered", "fuel_liters_covered"):
                c.number_format = "0.00"
            if ecols[j - 1] in ("measured_l_100km", "theoretical_l_100km", "deviation_pct",
                                 "theoretical_l_100km_ttw", "deviation_pct_ttw"):
                c.number_format = "0.00"
    r = len(eff) + 3
    ws4.cell(row=r, column=1, value="前 12 列").font = Font(bold=True)
    ws4.cell(row=r, column=2,
             value="项目代码原样输出（src/fleet_efficiency.py 的 vehicle_efficiency_table）："
                   "理论 L/100km = 法定轨碳排 ÷ diesel_kg_per_l() ÷ 覆盖里程；偏差 = (实测 − 理论) / 理论。")
    ws4.merge_cells(start_row=r, start_column=2, end_row=r, end_column=16)
    ws4.cell(row=r, column=2).alignment = Alignment(wrap_text=True, vertical="top")
    ws4.row_dimensions[r].height = 28

    r += 1
    ws4.cell(row=r, column=1, value="后 3 列").font = Font(bold=True)
    c = ws4.cell(row=r, column=2,
                 value="TTW 口径校正（本表新增，只加列不改项目代码）：kg_co2 来自柴油 TTW 因子 "
                       f"{CARBON_KG_PER_L:.2f} kgCO₂/L，而 diesel_kg_per_l() 返回 TTW+WTT = "
                       f"{CARBON_KG_PER_L_WTW:.2f} kgCO₂/L，两者相差 "
                       f"{(CARBON_KG_PER_L_WTW / CARBON_KG_PER_L - 1) * 100:.1f}%。"
                       "用 3.16 去除 2.63 算出的碳，会把理论油耗压低同一比例，偏差因此恒为 +20% 左右——"
                       "这是口径差，不是数据缺陷。判定请以后 3 列为准；改不改项目代码另行评审。")
    ws4.merge_cells(start_row=r, start_column=2, end_row=r, end_column=16)
    c.alignment = Alignment(wrap_text=True, vertical="top")
    c.font = Font(color="E9C46A", size=10)
    ws4.row_dimensions[r].height = 58

    r += 1
    ws4.cell(row=r, column=1, value="已知注入").font = Font(bold=True)
    ws4.cell(row=r, column=2,
             value="TRK-SH-006（苏A12350）缺 2026-11-01 的加油票 → 该车部分趟次无油量覆盖，"
                   "偏差会明显偏低，用来测「缺票/未覆盖」分支，不是算错。")
    ws4.merge_cells(start_row=r, start_column=2, end_row=r, end_column=16)
    ws4.cell(row=r, column=2).alignment = Alignment(wrap_text=True, vertical="top")
    ws4.row_dimensions[r].height = 28

    ws5 = wb.create_sheet("05_随机扩展模板")
    h5 = ["ts（可留空）", "vehicle_id", "载重 kg", "车速 km/h", "瞬时油耗 L/h", "本行里程 km",
          "本行油耗 L", "本行碳 kg", "碳强度 g/km", "合理性判定"]
    _sheet_head(ws5, h5, [30, 15, 12, 12, 15, 13, 12, 12, 13, 22])
    for i in range(2, 32):
        ws5.cell(row=i, column=1, value='=TEXT(DATE(2026,11,1)+TIME(6,40,0)+(ROW()-2)*TIME(0,0,30),"yyyy-mm-dd""T""hh:mm:ss""+08:00""")')
        ws5.cell(row=i, column=2, value="TRK-LOOP-001")
        ws5.cell(row=i, column=3, value="=RANDBETWEEN(8000,26000)")
        ws5.cell(row=i, column=4, value="=ROUND(MAX(0,MIN(88,22+40*RAND()+20*RAND())),1)")
        ws5.cell(row=i, column=5, value="=ROUND(MAX(1.2,1.9+0.085*D%d+0.0000065*C%d*D%d)*(1+(RAND()-0.5)*0.06),2)" % (i, i, i))
        ws5.cell(row=i, column=6, value="=ROUND(D%d*30/3600,4)" % i)
        ws5.cell(row=i, column=7, value="=ROUND(E%d*30/3600,4)" % i)
        ws5.cell(row=i, column=8, value="=ROUND(G%d*2.63,4)" % i)
        ws5.cell(row=i, column=9, value='=IF(F%d>0,ROUND(H%d/F%d*1000,0),"")' % (i, i, i))
        ws5.cell(row=i, column=10, value='=IF(F%d<=0,"0 里程",IF(OR(I%d>1800,I%d<150),"超界·检查","OK"))' % (i, i, i))
        for j in (4, 5, 6, 7, 8):
            ws5.cell(row=i, column=j).number_format = "0.0000"
    r = 34
    notes = [
        "用法：① 把 D 列 RAND 公式向下拖到需要的行数；② 全选 → 复制 → 选择性粘贴「数值」，否则 RAND 每次重算；",
        "③ 把第 01 页的列补齐（ts/vehicle_id/plate/…）后另存 CSV，即可 --from-csv 或打包成 records 上报；",
        "④ 关键不是条数，而是「合理性判定」列必须全 OK：g/km 越界说明油耗与车速的组合物理上不成立；",
        "⑤ 真实车辆碳强度参考：轻型柴油货车空载 200–350 g/km、满载 350–600；重型半挂满载 900–1300、空载 450–650。",
    ]
    for k, t in enumerate(notes):
        c = ws5.cell(row=r + k, column=1, value=t)
        c.font = Font(size=10, italic=(k >= 2))
        ws5.merge_cells(start_row=r + k, start_column=1, end_row=r + k, end_column=10)

    wb.save(path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "sim" / "excel_10000"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    df, trip_df, invoices = build()
    stats = validate(df, trip_df)
    eff = reconcile(trip_df, invoices)

    xlsx = out / "OBD_模拟数据_10000条.xlsx"
    write_workbook(xlsx, df, trip_df, invoices, eff, stats)

    # utf-8-sig：Excel 双击打开不乱码；pandas / 网关读取会自动剥 BOM
    df.to_csv(out / "obd_stream_10000.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(invoices).to_csv(out / "fuel_invoices.csv", index=False, encoding="utf-8-sig")

    board_fields = [c for c in COLUMNS if c != "qc_flags"]
    payload = {"records": df.head(30)[board_fields].to_dict(orient="records")}
    (out / "obd_board_batch_30.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    stats["eff_rows"] = int(len(eff))
    stats["eff_labels"] = eff["label"].value_counts().to_dict()
    stats["eff_labels_ttw"] = eff["label_ttw"].value_counts().to_dict()
    stats["eff_dev_pct_ttw"] = [round(float(x), 2) for x in eff["deviation_pct_ttw"].fillna(0)]
    stats["invoices"] = len(invoices)
    stats["xlsx_bytes"] = xlsx.stat().st_size
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
