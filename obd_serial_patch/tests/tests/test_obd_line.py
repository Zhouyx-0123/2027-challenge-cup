# -*- coding: utf-8 -*-
"""`src/obd_line.py` 的验收脚本（可直接 `python tests/test_obd_line.py` 跑）。

不依赖 pytest：仓库的 selftest 场景要能在干净环境里单独复现。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import obd_line as ol  # noqa: E402

CST = timezone(timedelta(hours=8))
PASSED: list = []
FAILED: list = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
    else:
        FAILED.append("%s%s" % (name, (" -> " + detail) if detail else ""))


# 设备真实格式样例（按 %8.1f / %7.1f / %5.0f / %6.2fL/h / %.3fkm/L(=%.1f) / %9.1fL 推）
FULL = (
    "[  123 s] RPM=   800.0 SPD=    45.3 TQ=   30/   28 FA=  4.51L/h "
    "FB= 10.020km/L(=  4.5) FC=    1234.5L T=  88.0/  45.0/  90.0 "
    "ODO=  123456.7 HRS=    1234.5 DTC=  0 MIL=  0 BUS=RUN TEC=  0 "
    "REC=  0 RX=   1234 ERR=  0 LINK=OK"
)


def test_parse_full_frame():
    frame = ol.parse_frame(FULL)
    check("能识别帧", frame is not None)
    check("运行时长", frame["uptime_s"] == 123, str(frame["uptime_s"]))
    v = frame["values"]
    check("RPM", v["engine_rpm"] == 800.0, str(v["engine_rpm"]))
    check("SPD", v["vehicle_speed_kmh"] == 45.3, str(v["vehicle_speed_kmh"]))
    check("TQ 需求", v["torque_req_nm"] == 30.0, str(v["torque_req_nm"]))
    check("TQ 实际", v["torque_actual_nm"] == 28.0, str(v["torque_actual_nm"]))
    check("FA 去单位", v["instant_fuel_lph"] == 4.51, str(v["instant_fuel_lph"]))
    check("FC 去单位", v["total_fuel_l"] == 1234.5, str(v["total_fuel_l"]))
    check("FB 经济性", v["fuel_economy_km_per_l"] == 10.020, str(v["fuel_economy_km_per_l"]))
    check("FB 括号反算 L/h", v["fuel_lph_from_economy"] == 4.5, str(v["fuel_lph_from_economy"]))
    check("温度三值", (v["engine_temp_c"], v["fuel_temp_c"], v["oil_temp_c"]) == (88.0, 45.0, 90.0),
          str((v["engine_temp_c"], v["fuel_temp_c"], v["oil_temp_c"])))
    check("ODO", v["odo_km"] == 123456.7, str(v["odo_km"]))
    check("HRS", v["engine_hours"] == 1234.5, str(v["engine_hours"]))
    check("BUS", v["bus_state"] == "RUN", str(v["bus_state"]))
    check("LINK", v["link_state"] == "OK", str(v["link_state"]))
    diag = ol.frame_diagnostics(frame)
    check("诊断计数 RX", diag["bus_rx"] == 1234.0, str(diag["bus_rx"]))


def test_missing_token_becomes_none():
    line = (
        "[   12 s] RPM=   --- SPD=   --- TQ=  ---/  --- FA=    ---L/h "
        "FB=    ---km/L(=  ---) FC=      ---L T=   ---/   ---/   --- "
        "ODO=       --- HRS=     --- DTC=  0 MIL=  0 BUS=RUN TEC=  0 "
        "REC=  0 RX=     0 ERR=  0 LINK=OK"
    )
    frame = ol.parse_frame(line)
    v = frame["values"]
    check("--- 转 None（不填 0）", v["instant_fuel_lph"] is None and v["odo_km"] is None,
          "%r %r" % (v["instant_fuel_lph"], v["odo_km"]))
    check("--- 温度转 None", v["engine_temp_c"] is None, str(v["engine_temp_c"]))
    check("TQ 双 ---", v["torque_req_nm"] is None, str(v["torque_req_nm"]))


def test_link_lost_and_bus_off():
    line = FULL.replace("LINK=OK", "LINK=LOST").replace("BUS=RUN", "BUS=BOFF")
    frame = ol.parse_frame(line)
    rec = ol.to_record(frame, vehicle_id="TRK-GZ-001", ts=datetime.now(CST), fuel_type="diesel")
    check("LINK=LOST 打标", "link_lost" in rec["qc_flags"], str(rec["qc_flags"]))
    check("BOFF 打标", "bus_off" in rec["qc_flags"], str(rec["qc_flags"]))


def test_link_ok_is_not_validity_proxy():
    """LINK=OK 但油耗为 --- 时，不能因为链路好就当成有效数据。"""
    line = FULL.replace("FA=  4.51L/h", "FA=    ---L/h").replace("FC=    1234.5L", "FC=      ---L")
    frame = ol.parse_frame(line)
    rec = ol.to_record(frame, vehicle_id="TRK-GZ-001", ts=datetime.now(CST), fuel_type="diesel")
    check("LINK=OK 但无油耗 -> no_fuel_value", "no_fuel_value" in rec["qc_flags"], str(rec["qc_flags"]))


def test_ecu_fault_and_cold():
    line = FULL.replace("DTC=  0 MIL=  0", "DTC=  3 MIL=  1").replace(
        "T=  88.0/  45.0/  90.0", "T=  40.0/  45.0/  90.0")
    rec = ol.to_record(ol.parse_frame(line), vehicle_id="TRK-GZ-001",
                       ts=datetime.now(CST), fuel_type="diesel")
    check("故障码打标", "ecu_fault" in rec["qc_flags"], str(rec["qc_flags"]))
    check("故障码落 fault_codes", rec["fault_codes"] == "DTC=3", str(rec["fault_codes"]))
    check("冷态打标", "engine_cold" in rec["qc_flags"], str(rec["qc_flags"]))


def test_fa_crosscheck_against_fb():
    """FA 是仪表显示报文；与设备自带反算值偏离超 5% 要打标。"""
    line = FULL.replace("(=  4.5)", "(=  6.2)")   # FA=4.51 vs 反算 6.2 -> 37% 偏差
    rec = ol.to_record(ol.parse_frame(line), vehicle_id="TRK-GZ-001",
                       ts=datetime.now(CST), fuel_type="diesel")
    check("FA 与反算值不符打标", "fuel_rate_mismatch" in rec["qc_flags"], str(rec["qc_flags"]))


def test_no_gps_and_uptime_flags():
    rec = ol.to_record(ol.parse_frame(FULL), vehicle_id="TRK-GZ-001",
                       ts=datetime.now(CST), fuel_type="diesel")
    check("标注无 GPS 源", "no_gps_source" in rec["qc_flags"], str(rec["qc_flags"]))
    check("标注时间戳来自 uptime", "ts_from_uptime" in rec["qc_flags"], str(rec["qc_flags"]))
    check("GPS 字段留空", rec["lat"] is None and rec["lng"] is None, "lat/lng")


def test_uptime_clock_anchors_and_backdates():
    clock = ol.UptimeClock(CST)
    now = datetime(2026, 11, 2, 21, 0, 0, tzinfo=CST)
    first = clock.stamp(600, now=now)          # 开机 600 s 的帧，刚到
    second = clock.stamp(660, now=now)         # 同一批里更晚的帧
    # 首帧：到达时刻就是采样时刻（设备没有绝对时钟，只能以到达为锚）
    check("首帧锚定为到达时刻", first == now, str(first))
    # 后续帧：按 uptime 差值推进，而不是全盖成同一个"收到时刻"
    check("后续帧按 uptime 推进", second - first == timedelta(seconds=60),
          str(second - first))
    check("锚定后 boot_wall = 到达 - uptime", clock.anchored is True, "anchored")


def test_uptime_clock_detects_device_restart():
    clock = ol.UptimeClock(CST)
    now = datetime(2026, 11, 2, 21, 0, 0, tzinfo=CST)
    clock.stamp(5000, now=now)
    clock.stamp(5060, now=now)
    ts_after_reset = clock.stamp(10, now=now + timedelta(minutes=10))
    check("检测到设备重启并重锚", clock.reanchors == 1, str(clock.reanchors))
    check("重锚后时间戳不倒退到开机前", ts_after_reset > now, str(ts_after_reset))


def test_not_a_frame():
    for line in ("", "   ", "OBD-II Gateway v3.2 booting...", "garbage", None):
        check("非帧返回 None: %r" % (line,), ol.parse_frame(line) is None)


def test_parse_stream_counts():
    clock = ol.UptimeClock(CST)
    lines = [FULL, "boot banner", FULL.replace("FA=  4.51L/h", "FA=    ---L/h")
             .replace("FC=    1234.5L", "FC=      ---L")]
    records, rejects, stats = ol.parse_stream(
        lines, clock=clock, vehicle_id="TRK-GZ-001", fuel_type="diesel",
        now=datetime(2026, 11, 2, 21, 0, 0, tzinfo=CST))
    check("批量：1 条有效", stats["records"] == 1, str(stats))
    check("批量：2 条拒绝", stats["rejects"] == 2, str(rejects))
    check("批量：帧数 2", stats["frames"] == 2, str(stats))
    check("拒绝原因可读", {r["reason"] for r in rejects} == {"not_a_frame", "no_fuel_value"},
          str([r["reason"] for r in rejects]))


def test_uptime_overflow_column_shift():
    """%5lu 到 100000 s 后括号变 6 位，定宽切片会错；正则切分必须仍然正确。"""
    overflow = FULL.replace("[  123 s]", "[100000 s]")
    frame = ol.parse_frame(overflow)
    check("uptime 溢出后仍解析", frame is not None and frame["uptime_s"] == 100000,
          str(frame["uptime_s"] if frame else None))
    check("溢出后油耗不错位", frame["values"]["instant_fuel_lph"] == 4.51,
          str(frame["values"]["instant_fuel_lph"]))


def main() -> int:
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
            except Exception as exc:  # noqa: BLE001 - 汇总所有异常
                FAILED.append("%s 抛异常: %r" % (name, exc))
    print("PASSED %d / FAILED %d" % (len(PASSED), len(FAILED)))
    for item in FAILED:
        print("  FAIL %s" % item)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
