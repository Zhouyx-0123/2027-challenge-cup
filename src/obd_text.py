# -*- coding: utf-8 -*-
"""OBD 串口文本解析与分段（并列轨，不碰主账）。

把固件 printf 出来的定宽快照行：

    [ 12 s] RPM= 1500.0 SPD= 60.0 TQ= 45/ 42 | FA= 12.34L/h
    FB=2.500km/L(=24.0) FC= 1234.5L | T= 88.0/ 75.0/ 90.0
    ODO= 123456.7 HRS= 1234.5 | DTC=2 MIL=1 | BUS=RUN
    TEC=0 REC=0 RX=12345 ERR=0 LINK=OK

解析成逐点表，再按时间间隔/断链/停机切成 segment_telemetry 段表。
本模块只做文本 -> 规范字段，不算碳、不改 E0、不依赖 sklearn。

设计边界：
  · 无值一律 '---' -> None，不填 0；0 有物理含义（怠速/停止）。
  · FB 括号内是车速/油耗换算的 L/h，只做交叉校验，不覆盖 FA。
  · 时间只有相对开机秒，没有绝对时间戳；传 session_start 才生成 ts。
  · 解析失败的行计数并保留前若干条错误，不静默吞掉。
"""
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

#: 快照行的规范列（顺序即输出顺序）
POINT_COLUMNS: List[str] = [
    "line_no", "elapsed_s", "ts", "session_id", "vehicle_id", "plate",
    "engine_rpm", "vehicle_speed_kmh", "torque_demand_pct", "torque_actual_pct",
    "instant_fuel_lph", "fuel_econ_km_per_l", "fb_derived_lph", "total_fuel_l",
    "coolant_temp_c", "fuel_temp_c", "oil_temp_c", "odo_km", "engine_hours",
    "dtc_count", "mil", "bus_state", "tec", "rec", "rx_frames", "rx_errors",
    "link_ok", "qc_flags", "raw_line",
]

#: 段表列：与 segment_telemetry 契约对齐；kg_co2 不在此处，评估时另行 join
SEGMENT_COLUMNS: List[str] = [
    "segment_id", "trip_id", "vehicle_id", "plate", "route_id",
    "start_ts", "end_ts", "start_elapsed_s", "end_elapsed_s", "duration_s",
    "distance_km", "distance_source", "speed_mean_kmh", "speed_p95_kmh",
    "speed_max_kmh", "rpm_mean", "rpm_p95", "torque_demand_mean_pct",
    "torque_actual_mean_pct", "fuel_rate_mean_lph", "fuel_l", "fuel_source",
    "fuel_econ_l_per_100km", "idle_ratio", "coolant_mean_c", "odo_delta_km",
    "engine_hours_delta", "dtc_count", "mil", "link_ok_ratio", "sample_count",
    "missing_ratio", "usable", "qc_flags", "grain", "source", "feature_set",
]

_NUM = r"[-+]?\d+(?:\.\d+)?"
_MISS = r"---"
_FIELD = rf"(?:{_NUM}|{_MISS})"

#: 固件一行快照的完整正则。允许 '---'、TQ=a/b、FB 括号派生值、LINK=LOST。
_SNAPSHOT_RE = re.compile(
    rf"^\[\s*(?P<elapsed>\d+)\s*s\]\s*"
    rf"RPM=\s*(?P<rpm>{_FIELD})\s+"
    rf"SPD=\s*(?P<spd>{_FIELD})\s+"
    rf"TQ=\s*(?P<tqd>{_FIELD})\s*/\s*(?P<tqa>{_FIELD})\s*\|\s*"
    rf"FA=\s*(?P<fa>{_FIELD})\s*L/h\s+"
    rf"FB=\s*(?P<fb>{_FIELD})\s*km/L\s*\(=\s*(?P<fbd>{_FIELD})\s*\)\s*"
    rf"FC=\s*(?P<fc>{_FIELD})\s*L\s*\|\s*"
    rf"T=\s*(?P<t1>{_FIELD})\s*/\s*(?P<t2>{_FIELD})\s*/\s*(?P<t3>{_FIELD})\s+"
    rf"ODO=\s*(?P<odo>{_FIELD})\s+"
    rf"HRS=\s*(?P<hrs>{_FIELD})\s*\|\s*"
    rf"DTC=\s*(?P<dtc>{_FIELD})\s+MIL=\s*(?P<mil>{_FIELD})\s*\|\s*"
    rf"BUS=\s*(?P<bus>[\w.-]+)\s+"
    rf"TEC=\s*(?P<tec>{_FIELD})\s+REC=\s*(?P<rec>{_FIELD})\s+"
    rf"RX=\s*(?P<rx>{_FIELD})\s+ERR=\s*(?P<err>{_FIELD})\s+"
    rf"LINK=\s*(?P<link>[A-Za-z]+)"
)

_HEADER_RE = re.compile(
    r"\b(DEV_ID|DEVICE_ID|VEHICLE_ID|PLATE|FW|CAN)\s*=\s*([^\s|,;]+)",
    re.IGNORECASE,
)


def _num(value: Optional[str]) -> Optional[float]:
    """'---'/空串 -> None；其余转 float；非法值也 None（由调用方计 qc）。"""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s == "---":
        return None
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _int(value: Optional[str]) -> Optional[int]:
    v = _num(value)
    return None if v is None else int(round(v))


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat(timespec="seconds")


def _parse_session_start(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip())
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _looks_like_header(line: str) -> bool:
    u = line.strip()
    if not u:
        return True
    if u.startswith("#") or u.startswith("//"):
        return True
    if _HEADER_RE.search(u):
        return True
    # 固件启动 banner：BOOT / FW / CAN，不含 RPM= 快照
    if "RPM=" not in u and re.search(r"\b(BOOT|FW|CAN|DEV_ID)\b", u, re.I):
        return True
    return False


def parse_line(line: str, *, line_no: int = 0,
               session_start: Optional[datetime] = None,
               session_id: Optional[str] = None,
               vehicle_id: Optional[str] = None,
               plate: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """解析一行快照；不是快照/格式不符返回 None。"""
    m = _SNAPSHOT_RE.match(line.strip())
    if m is None:
        return None
    g = m.groupdict()

    elapsed = _int(g["elapsed"])
    rpm = _num(g["rpm"])
    speed = _num(g["spd"])
    tqd = _num(g["tqd"])
    tqa = _num(g["tqa"])
    fa = _num(g["fa"])
    fb = _num(g["fb"])
    fbd = _num(g["fbd"])
    fc = _num(g["fc"])
    temps = [_num(g[k]) for k in ("t1", "t2", "t3")]
    odo = _num(g["odo"])
    hrs = _num(g["hrs"])
    dtc = _int(g["dtc"])
    mil = _int(g["mil"])
    bus = (g["bus"] or "").strip().upper()
    tec = _int(g["tec"])
    rec = _int(g["rec"])
    rx = _int(g["rx"])
    err = _int(g["err"])
    link = (g["link"] or "").strip().upper()

    qc: List[str] = []
    if speed is None:
        qc.append("missing_speed")
    elif speed < 0:
        qc.append("speed_negative")
    if rpm is None:
        qc.append("missing_rpm")
    if fa is None and fc is None and fbd is None:
        qc.append("missing_fuel")
    if odo is None:
        qc.append("missing_odo")
    if tqd is None or tqa is None:
        qc.append("missing_torque")
    if any(t is None for t in temps):
        qc.append("missing_temp")
    if fa is not None and fbd is not None:
        denom = max(abs(fa), abs(fbd), 1e-9)
        if abs(fa - fbd) / denom > 0.10:
            qc.append("fuel_source_mismatch")
    if dtc is not None and dtc > 0:
        qc.append("dtc_active")
    if mil == 1:
        qc.append("mil_on")
    if link == "LOST":
        qc.append("link_lost")
    if bus in {"STOP", "BOFF"}:
        qc.append("bus_stop")
    if elapsed is None:
        qc.append("missing_elapsed")

    ts = None
    if session_start is not None and elapsed is not None:
        ts = _iso(session_start + timedelta(seconds=elapsed))

    sid = session_id or (vehicle_id or "unknown")
    return {
        "line_no": int(line_no),
        "elapsed_s": elapsed,
        "ts": ts,
        "session_id": sid,
        "vehicle_id": vehicle_id,
        "plate": plate,
        "engine_rpm": rpm,
        "vehicle_speed_kmh": speed,
        "torque_demand_pct": tqd,
        "torque_actual_pct": tqa,
        "instant_fuel_lph": fa,
        "fuel_econ_km_per_l": fb,
        "fb_derived_lph": fbd,
        "total_fuel_l": fc,
        "coolant_temp_c": temps[0],
        "fuel_temp_c": temps[1],
        "oil_temp_c": temps[2],
        "odo_km": odo,
        "engine_hours": hrs,
        "dtc_count": dtc,
        "mil": mil,
        "bus_state": bus or None,
        "tec": tec,
        "rec": rec,
        "rx_frames": rx,
        "rx_errors": err,
        "link_ok": (None if link not in {"OK", "LOST"} else link == "OK"),
        "qc_flags": qc,
        "raw_line": line.rstrip("\r\n"),
    }


def _empty_points() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in POINT_COLUMNS})


def parse_log(text: str, *, session_start: Any = None,
              vehicle_id: Optional[str] = None,
              plate: Optional[str] = None,
              session_id: Optional[str] = None) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """解析整段串口日志，返回 (逐点表, 元数据)。

    元数据含 header（DEV_ID/FW/CAN/PLATE）、计数、前 10 条坏行。
    坏行不会让整个文件失败；只要至少一行成功即可。
    """
    start_dt = _parse_session_start(session_start)
    header: Dict[str, str] = {}
    rows: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    total = 0
    snapshot_seen = 0

    for i, raw in enumerate(str(text).splitlines(), start=1):
        total += 1
        line = raw.rstrip("\r\n")
        if not line.strip():
            continue
        hms = list(_HEADER_RE.finditer(line))
        if hms:
            for hm in hms:
                header[hm.group(1).upper()] = hm.group(2)
            continue
        if _looks_like_header(line):
            continue
        if "RPM=" in line:
            snapshot_seen += 1
        rec = parse_line(
            line, line_no=i, session_start=start_dt, session_id=session_id,
            vehicle_id=vehicle_id or header.get("VEHICLE_ID") or header.get("DEV_ID"),
            plate=plate or header.get("PLATE"),
        )
        if rec is None:
            if len(rejected) < 10:
                rejected.append({"line_no": i, "line": line[:240]})
            continue
        rows.append(rec)

    if not rows:
        meta = {
            "total_lines": total, "snapshot_lines": snapshot_seen,
            "parsed": 0, "rejected": len(rejected), "header": header,
            "session_start": _iso(start_dt), "session_id": session_id,
            "errors": rejected,
        }
        return _empty_points(), meta

    df = pd.DataFrame(rows, columns=POINT_COLUMNS)
    # session_id 允许在解析后补全：优先显式参数，其次 header 的 DEV_ID/VEHICLE_ID
    if not session_id:
        sid = (vehicle_id or header.get("VEHICLE_ID") or header.get("DEV_ID")
               or "unknown")
        if start_dt is not None:
            sid = f"{sid}:{_iso(start_dt)}"
        df["session_id"] = sid
    meta = {
        "total_lines": total, "snapshot_lines": snapshot_seen,
        "parsed": int(len(df)), "rejected": len(rejected), "header": header,
        "session_start": _iso(start_dt),
        "session_id": str(df["session_id"].iloc[0]) if len(df) else session_id,
        "errors": rejected,
    }
    return df, meta


# --------------------------------------------------------------------------
# 分段
# --------------------------------------------------------------------------
def _link_is(value: Any, expected: bool) -> bool:
    """None 安全的 bool 比较（pandas object 里可能是 numpy.bool_）。"""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return False
    try:
        return bool(value) is expected
    except (TypeError, ValueError):
        return False


def _first_valid(s: pd.Series) -> Optional[float]:
    v = pd.to_numeric(s, errors="coerce").dropna()
    return None if v.empty else float(v.iloc[0])


def _last_valid(s: pd.Series) -> Optional[float]:
    v = pd.to_numeric(s, errors="coerce").dropna()
    return None if v.empty else float(v.iloc[-1])


def _mean_or_none(s: pd.Series) -> Optional[float]:
    v = pd.to_numeric(s, errors="coerce").dropna()
    return None if v.empty else float(v.mean())


def _p95_or_none(s: pd.Series) -> Optional[float]:
    v = pd.to_numeric(s, errors="coerce").dropna()
    return None if v.empty else float(np.percentile(v.to_numpy(), 95))


def _integrate_rate(seg: pd.DataFrame, rate_col: str,
                    elapsed_col: str = "elapsed_s") -> Optional[float]:
    """梯形积分 rate(L/h) over elapsed(s) -> L；缺时间/全缺速率返回 None。"""
    t = pd.to_numeric(seg[elapsed_col], errors="coerce").to_numpy(dtype=float)
    r = pd.to_numeric(seg[rate_col], errors="coerce").to_numpy(dtype=float)
    total = 0.0
    used = False
    for i in range(1, len(seg)):
        if not np.isfinite(t[i - 1]) or not np.isfinite(t[i]):
            continue
        dt = t[i] - t[i - 1]
        if dt <= 0:
            continue
        r0 = r[i - 1] if np.isfinite(r[i - 1]) else None
        r1 = r[i] if np.isfinite(r[i]) else None
        if r0 is None and r1 is None:
            continue
        if r0 is None:
            r0 = r1
        if r1 is None:
            r1 = r0
        total += (r0 + r1) / 2.0 * dt / 3600.0
        used = True
    return total if used else None


def _segment_qc(seg: pd.DataFrame, duration: Optional[float],
                distance: Optional[float], fuel: Optional[float],
                distance_source: Optional[str], fuel_source: Optional[str],
                min_samples: int, min_duration_s: float) -> List[str]:
    qc: List[str] = []
    if len(seg) < min_samples:
        qc.append("short_segment")
    if duration is None or duration < min_duration_s:
        qc.append("short_duration")
    if distance is None or distance <= 0:
        qc.append("no_distance")
    if fuel is None:
        qc.append("no_fuel")
    if distance_source == "speed_integral":
        qc.append("distance_from_speed")
    if fuel_source == "instant_integral":
        qc.append("fuel_from_instant")
    if distance_source == "odo_reset" or fuel_source == "fuel_reset":
        qc.append("counter_reset")
    if "link_ok" in seg.columns and seg["link_ok"].notna().any():
        if not bool(seg["link_ok"].dropna().astype(bool).any()):
            qc.append("link_lost")
    if "dtc_count" in seg.columns and seg["dtc_count"].notna().any() and (seg["dtc_count"].fillna(0) > 0).any():
        qc.append("dtc_active")
    if "mil" in seg.columns and seg["mil"].notna().any() and (seg["mil"].fillna(0) == 1).any():
        qc.append("mil_on")
    return qc


def aggregate_segments(points: pd.DataFrame, *, gap_s: float = 120.0,
                       min_duration_s: float = 60.0,
                       min_samples: int = 30,
                       usable_only: bool = False) -> pd.DataFrame:
    """逐点表 -> 段表。

    分段规则（按文件顺序）：
      · 首点；elapsed 回退/缺失、相邻间隔 > gap_s；
      · LINK 由 OK/未知 -> LOST，或 LOST -> OK；
      · BUS=STOP/BOFF 的点不进入行驶段（停机不是工况），RUN 后重新开段。
    段级油耗优先 FC 差分，其次 FA 积分；里程优先 ODO 差分，
    缺失时用速度积分并打 distance_from_speed。所有回退都进 qc_flags。
    """
    if points is None or len(points) == 0:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in SEGMENT_COLUMNS})

    df = points.copy()
    if "line_no" in df.columns:
        df = df.sort_values("line_no", kind="stable")
    df = df.reset_index(drop=True)
    for c in ("elapsed_s", "vehicle_speed_kmh", "engine_rpm",
              "instant_fuel_lph", "total_fuel_l", "odo_km", "engine_hours",
              "torque_demand_pct", "torque_actual_pct", "coolant_temp_c"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    stop_before = pd.Series(False, index=df.index)
    if "bus_state" in df.columns:
        df["bus_state"] = df["bus_state"].astype("string").str.upper()
        stop = df["bus_state"].isin(["STOP", "BOFF"])
        # 停机点本身不进段，但它后面的第一个 RUN 点必须另起一段
        stop_before = stop.shift(1, fill_value=False).astype(bool)
        df = df[~stop].reset_index(drop=True)
        stop_before = stop_before[~stop].reset_index(drop=True)
    if len(df) == 0:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in SEGMENT_COLUMNS})

    sess = (df["session_id"].astype("string").fillna("unknown")
            if "session_id" in df.columns else pd.Series(["unknown"] * len(df)))
    link = (df["link_ok"] if "link_ok" in df.columns
            else pd.Series([None] * len(df), dtype=object))
    t = df["elapsed_s"].to_numpy(dtype=float) if "elapsed_s" in df.columns else np.full(len(df), np.nan)

    group_ids = np.zeros(len(df), dtype=int)
    gid = 0
    for i in range(1, len(df)):
        new = False
        if sess.iloc[i] != sess.iloc[i - 1]:
            new = True
        elif bool(stop_before.iloc[i]):
            new = True
        elif not np.isfinite(t[i]) or not np.isfinite(t[i - 1]):
            new = False  # 没有时间信息时不硬切；整段标 missing_elapsed
        elif t[i] < t[i - 1]:
            new = True
        elif t[i] - t[i - 1] > gap_s:
            new = True
        elif _link_is(link.iloc[i], False) and not _link_is(link.iloc[i - 1], False):
            new = True
        elif _link_is(link.iloc[i], True) and _link_is(link.iloc[i - 1], False):
            new = True
        if new:
            gid += 1
        group_ids[i] = gid
    df["_gid"] = group_ids

    rows: List[Dict[str, Any]] = []
    for seq, (_, seg) in enumerate(df.groupby("_gid", sort=True)):
        seg = seg.reset_index(drop=True)
        n = len(seg)
        t0 = _first_valid(seg["elapsed_s"]) if "elapsed_s" in seg else None
        t1 = _last_valid(seg["elapsed_s"]) if "elapsed_s" in seg else None
        duration = None if t0 is None or t1 is None else max(0.0, t1 - t0)

        # 里程：ODO 差分优先；回退/缺失用速度积分
        odo0 = _first_valid(seg["odo_km"]) if "odo_km" in seg else None
        odo1 = _last_valid(seg["odo_km"]) if "odo_km" in seg else None
        distance = None
        distance_source = None
        if odo0 is not None and odo1 is not None:
            d = odo1 - odo0
            if d >= 0:
                distance, distance_source = float(d), "odo"
            else:
                distance_source = "odo_reset"
        if distance is None and "vehicle_speed_kmh" in seg:
            tt = pd.to_numeric(seg["elapsed_s"], errors="coerce").to_numpy(dtype=float)
            vv = pd.to_numeric(seg["vehicle_speed_kmh"], errors="coerce").to_numpy(dtype=float)
            total = 0.0
            used = False
            for i in range(1, n):
                if not np.isfinite(tt[i - 1]) or not np.isfinite(tt[i]):
                    continue
                dt = tt[i] - tt[i - 1]
                if dt <= 0:
                    continue
                v0 = vv[i - 1] if np.isfinite(vv[i - 1]) else None
                v1 = vv[i] if np.isfinite(vv[i]) else None
                if v0 is None and v1 is None:
                    continue
                if v0 is None:
                    v0 = v1
                if v1 is None:
                    v1 = v0
                total += (v0 + v1) / 2.0 * dt / 3600.0
                used = True
            if used:
                distance = float(total)
                distance_source = "speed_integral" if distance_source is None else distance_source

        # 油耗：FC 差分优先；回退/缺失用 FA 积分
        fc0 = _first_valid(seg["total_fuel_l"]) if "total_fuel_l" in seg else None
        fc1 = _last_valid(seg["total_fuel_l"]) if "total_fuel_l" in seg else None
        fuel = None
        fuel_source = None
        if fc0 is not None and fc1 is not None:
            d = fc1 - fc0
            if 0 <= d <= 10000.0:
                fuel, fuel_source = float(d), "fc_delta"
            else:
                fuel_source = "fuel_reset"
        if fuel is None and "instant_fuel_lph" in seg:
            integ = _integrate_rate(seg, "instant_fuel_lph")
            if integ is not None:
                fuel = float(integ)
                fuel_source = "instant_integral"
        if fuel is None and "fb_derived_lph" in seg:
            integ = _integrate_rate(seg, "fb_derived_lph")
            if integ is not None:
                fuel = float(integ)
                fuel_source = "fb_integral"

        speed = _mean_or_none(seg["vehicle_speed_kmh"]) if "vehicle_speed_kmh" in seg else None
        speed_p95 = _p95_or_none(seg["vehicle_speed_kmh"]) if "vehicle_speed_kmh" in seg else None
        speed_max = None
        if "vehicle_speed_kmh" in seg:
            v = pd.to_numeric(seg["vehicle_speed_kmh"], errors="coerce").dropna()
            speed_max = None if v.empty else float(v.max())
        rpm_mean = _mean_or_none(seg["engine_rpm"]) if "engine_rpm" in seg else None
        rpm_p95 = _p95_or_none(seg["engine_rpm"]) if "engine_rpm" in seg else None
        tqd_mean = _mean_or_none(seg["torque_demand_pct"]) if "torque_demand_pct" in seg else None
        tqa_mean = _mean_or_none(seg["torque_actual_pct"]) if "torque_actual_pct" in seg else None
        fuel_rate_mean = None
        if duration is not None and duration > 0 and fuel is not None:
            fuel_rate_mean = fuel / (duration / 3600.0)
        elif "instant_fuel_lph" in seg:
            fuel_rate_mean = _mean_or_none(seg["instant_fuel_lph"])
        fuel_econ = None
        if fuel is not None and distance is not None and distance > 0:
            fuel_econ = fuel / distance * 100.0
        idle_ratio = None
        if "vehicle_speed_kmh" in seg:
            v = pd.to_numeric(seg["vehicle_speed_kmh"], errors="coerce")
            valid = v.dropna()
            if len(valid):
                idle_ratio = float((valid <= 1.0).mean())
        coolant = _mean_or_none(seg["coolant_temp_c"]) if "coolant_temp_c" in seg else None
        hrs0 = _first_valid(seg["engine_hours"]) if "engine_hours" in seg else None
        hrs1 = _last_valid(seg["engine_hours"]) if "engine_hours" in seg else None
        hrs_delta = None if hrs0 is None or hrs1 is None or hrs1 < hrs0 else float(hrs1 - hrs0)
        dtc = None if "dtc_count" not in seg else (None if seg["dtc_count"].dropna().empty
                                                   else int(seg["dtc_count"].max()))
        mil = None if "mil" not in seg else (None if seg["mil"].dropna().empty
                                             else int(seg["mil"].max()))
        link_ratio = None
        if "link_ok" in seg and seg["link_ok"].notna().any():
            link_ratio = float(seg["link_ok"].dropna().astype(bool).mean())
        key_cols = [c for c in ("vehicle_speed_kmh", "engine_rpm",
                                "instant_fuel_lph", "odo_km") if c in seg.columns]
        if key_cols:
            missing_ratio = float(seg[key_cols].isna().to_numpy().mean())
        else:
            missing_ratio = None

        qc = _segment_qc(seg, duration, distance, fuel, distance_source,
                         fuel_source, min_samples, min_duration_s)
        if t0 is None:
            qc.append("missing_elapsed")
        usable = not any(f in qc for f in ("short_segment", "short_duration",
                                           "no_distance", "no_fuel", "link_lost",
                                           "missing_elapsed"))
        session_id = str(seg["session_id"].iloc[0]) if "session_id" in seg else "unknown"
        start_ts = None
        end_ts = None
        if "ts" in seg:
            ts = seg["ts"].dropna()
            if len(ts):
                start_ts, end_ts = str(ts.iloc[0]), str(ts.iloc[-1])
        seg_id = f"{session_id}:{int(round(t0)) if t0 is not None else 0:09d}:{seq:03d}"
        rows.append({
            "segment_id": seg_id,
            "trip_id": None,
            "vehicle_id": (None if "vehicle_id" not in seg or seg["vehicle_id"].dropna().empty
                           else str(seg["vehicle_id"].dropna().iloc[0])),
            "plate": (None if "plate" not in seg or seg["plate"].dropna().empty
                      else str(seg["plate"].dropna().iloc[0])),
            "route_id": None,
            "start_ts": start_ts,
            "end_ts": end_ts,
            "start_elapsed_s": None if t0 is None else int(round(t0)),
            "end_elapsed_s": None if t1 is None else int(round(t1)),
            "duration_s": None if duration is None else round(float(duration), 3),
            "distance_km": None if distance is None else round(float(distance), 6),
            "distance_source": distance_source,
            "speed_mean_kmh": None if speed is None else round(float(speed), 3),
            "speed_p95_kmh": None if speed_p95 is None else round(float(speed_p95), 3),
            "speed_max_kmh": None if speed_max is None else round(float(speed_max), 3),
            "rpm_mean": None if rpm_mean is None else round(float(rpm_mean), 2),
            "rpm_p95": None if rpm_p95 is None else round(float(rpm_p95), 2),
            "torque_demand_mean_pct": None if tqd_mean is None else round(float(tqd_mean), 2),
            "torque_actual_mean_pct": None if tqa_mean is None else round(float(tqa_mean), 2),
            "fuel_rate_mean_lph": None if fuel_rate_mean is None else round(float(fuel_rate_mean), 4),
            "fuel_l": None if fuel is None else round(float(fuel), 6),
            "fuel_source": fuel_source,
            "fuel_econ_l_per_100km": None if fuel_econ is None else round(float(fuel_econ), 4),
            "idle_ratio": None if idle_ratio is None else round(float(idle_ratio), 4),
            "coolant_mean_c": None if coolant is None else round(float(coolant), 2),
            "odo_delta_km": None if odo0 is None or odo1 is None or odo1 < odo0 else round(float(odo1 - odo0), 6),
            "engine_hours_delta": None if hrs_delta is None else round(float(hrs_delta), 4),
            "dtc_count": dtc,
            "mil": mil,
            "link_ok_ratio": None if link_ratio is None else round(float(link_ratio), 4),
            "sample_count": int(n),
            "missing_ratio": None if missing_ratio is None else round(float(missing_ratio), 4),
            "usable": bool(usable),
            "qc_flags": qc,
            "grain": "segment",
            "source": "obd_text",
            "feature_set": "obd_v1",
        })
    out = pd.DataFrame(rows, columns=SEGMENT_COLUMNS)
    if usable_only:
        out = out[out["usable"]].reset_index(drop=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="OBD 串口文本 -> 逐点表 + 分段表")
    ap.add_argument("--in", dest="inp", required=True, help="输入 .txt/.log")
    ap.add_argument("--out", default="data/processed", help="输出目录")
    ap.add_argument("--vehicle-id", default=None)
    ap.add_argument("--plate", default=None)
    ap.add_argument("--session-start", default=None,
                    help="ISO8601 带时区，如 2026-11-06T10:00:00+08:00")
    ap.add_argument("--gap-s", type=float, default=120.0)
    ap.add_argument("--min-samples", type=int, default=30)
    args = ap.parse_args()

    text = Path(args.inp).read_text(encoding="utf-8", errors="replace")
    points, meta = parse_log(text, session_start=args.session_start,
                             vehicle_id=args.vehicle_id, plate=args.plate)
    segs = aggregate_segments(points, gap_s=args.gap_s, min_samples=args.min_samples)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    points.to_parquet(out / "obd_text_points.parquet", index=False)
    segs.to_parquet(out / "obd_text_segments.parquet", index=False)
    meta = dict(meta)
    meta["segments"] = int(len(segs))
    meta["usable_segments"] = int(segs["usable"].sum()) if len(segs) else 0
    (out / "obd_text_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[obd_text] points={len(points)} segments={len(segs)} "
          f"usable={meta['usable_segments']}")


if __name__ == "__main__":
    main()
