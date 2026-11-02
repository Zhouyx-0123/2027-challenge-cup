# -*- coding: utf-8 -*-
"""J1939 网关文本帧 -> OBD 契约记录（`config/obd_gps_contract.json`）。

设备侧输出的是**定宽文本行**，不是 JSON：

    [  123 s] RPM=   800.0 SPD=    45.3 TQ=   30/   28 FA=  4.51L/h
    FB= 10.020km/L(=  4.5) FC=    1234.5L T=  88.0/  45.0/  90.0
    ODO=  123456.7 HRS=    1234.5 DTC=  0 MIL=  0 BUS=RUN TEC=  0
    REC=  0 RX=   1234 ERR=  0 LINK=OK

本模块只做三件事，**不算碳**（算碳在 src/obd_gps.py，口径唯一真源）：

1. `parse_frame`  文本行 -> 原始键值（不依赖定宽切片，见 `_parse_keyed`）；
2. `UptimeClock`  相对开机时间 -> 带时区的绝对时间戳（锚定 + 重启复位）；
3. `to_record`    原始键值 -> 契约规范字段 + `qc_flags`。

三条硬纪律（与契约一致）：

* **`---` 一律转 `None`**，绝不填 0 —— 0 是有物理含义的值（怠速油耗、停车、液位空）；
* **`LINK=OK` 不作为有效性判据** —— 它只代表总线上有 29 位帧，不代表有关键数据，
  逐字段的 `---` 才是唯一信号；
* **不自己造因子** —— 本模块不出现任何 kgCO2/L 常数。

字段顺序与 PGN 出处见 `config/obd_gps_contract.json` 的 `serial_frame` 段。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

#: 设备无值填充符
MISSING = "---"

#: 帧头：`[  123 s]`，%5lu 右对齐。**注意 `%5lu` 是最小宽度**，运行到 100000 s
#: （约 27.8 小时）后括号会变成 6 位，因此这里只做正则匹配，不做定宽切片。
_FRAME_RE = re.compile(r"^\s*\[\s*(?P<uptime>\d+)\s*s\s*\]")

#: 键名边界扫描：1~4 位大写字母 + `=`，且左侧不是字母数字下划线。
#: 用"找键、切区间"而不是逐字段正则，好处是设备将来加字段不会打断解析。
_KEY_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Z]{1,4})=")

#: `FB= 10.020km/L(=  4.5)` —— 括号里是"车速÷油耗"反算出的 L/h，用于反证 FA 口径。
_FB_RE = re.compile(
    r"^\s*(?P<eco>[-+]?\d+(?:\.\d+)?)\s*km/L\s*(?:\(\s*=\s*(?P<lph>[-+]?\d+(?:\.\d+)?)\s*\))?",
    re.IGNORECASE,
)

#: 已知键（未知键保留在 raw 里，前向兼容）
KNOWN_KEYS: Tuple[str, ...] = (
    "RPM", "SPD", "TQ", "FA", "FB", "FC", "T",
    "ODO", "HRS", "DTC", "MIL", "BUS", "TEC", "REC", "RX", "ERR", "LINK",
)

#: 质控阈值（只影响打标，不影响碳排计算）
DEFAULT_COLD_THRESHOLD_C = 70.0     # 冷却液低于此值视为冷态
DEFAULT_FUEL_LPH_MAX = 200.0        # 瞬时油耗物理上限（重卡满载量级）
DEFAULT_CROSSCHECK_TOL = 0.05       # FA 与括号反算值的相对偏差容差


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def _parse_keyed(body: str) -> Dict[str, str]:
    """把 `KEY=value KEY=value` 切成字典。

    区间切分天然容忍值里的空格和斜杠（`TQ=  30/  28`、`T= 88.0/ 45.0/ 90.0`、
    `FB= 10.020km/L(=  4.5)`），也不怕 `%5lu` 溢出后的列宽变化。
    """
    hits = list(_KEY_RE.finditer(body))
    raw: Dict[str, str] = {}
    for index, hit in enumerate(hits):
        key = hit.group(1)
        start = hit.end()
        end = hits[index + 1].start() if index + 1 < len(hits) else len(body)
        raw[key] = body[start:end].strip()
    return raw


def _num(text: Optional[str]) -> Optional[float]:
    """`---` / 空串 -> None；其余转 float。转不动就返回 None（不抛异常）。"""
    if text is None:
        return None
    cleaned = text.strip()
    if not cleaned or cleaned == MISSING:
        return None
    try:
        value = float(cleaned)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN 自反判据
        return None
    return value


def _split(text: Optional[str], count: int) -> List[Optional[float]]:
    """`a/b/c` -> [a, b, c]；`---/---/---` -> [None, None, None]。"""
    if text is None:
        return [None] * count
    parts = [part.strip() for part in text.split("/")]
    if len(parts) < count:
        parts.extend([""] * (count - len(parts)))
    return [_num(part) for part in parts[:count]]


def _strip_unit(text: Optional[str], unit: str) -> Optional[str]:
    """去掉紧贴数字的单位后缀（`4.51L/h` -> `4.51`，`1234.5L` -> `1234.5`）。"""
    if text is None:
        return None
    cleaned = text.strip()
    if cleaned == MISSING:
        return cleaned
    if cleaned.lower().endswith(unit.lower()):
        cleaned = cleaned[: -len(unit)].strip()
    return cleaned


def parse_frame(line: str) -> Optional[Dict[str, Any]]:
    """解析一行文本帧。

    返回 ``{"uptime_s": int, "raw": {...}, "values": {...}}``；
    不是帧（空行、启动横幅、乱码）返回 ``None``。
    """
    if not isinstance(line, str) or not line.strip():
        return None
    match = _FRAME_RE.match(line)
    if not match:
        return None

    uptime_s = int(match.group("uptime"))
    raw = _parse_keyed(line[match.end():])

    fb_match = _FB_RE.match(raw.get("FB", "")) if "FB" in raw else None
    fuel_economy = _num(fb_match.group("eco")) if fb_match else None
    fuel_lph_from_eco = _num(fb_match.group("lph")) if fb_match else None

    engine_temp, fuel_temp, oil_temp = _split(raw.get("T"), 3)
    torque_req, torque_actual = _split(raw.get("TQ"), 2)

    values: Dict[str, Any] = {
        "engine_rpm": _num(raw.get("RPM")),
        "vehicle_speed_kmh": _num(raw.get("SPD")),
        "torque_req_nm": torque_req,
        "torque_actual_nm": torque_actual,
        "instant_fuel_lph": _num(_strip_unit(raw.get("FA"), "L/h")),
        "fuel_economy_km_per_l": fuel_economy,
        #: 设备自带的"车速÷油耗"反算值，专门用来反证 FA 的口径
        "fuel_lph_from_economy": fuel_lph_from_eco,
        "total_fuel_l": _num(_strip_unit(raw.get("FC"), "L")),
        #: 默认按 PGN 65262 ET1 的标准顺序：冷却液 / 燃油 / 机油。
        #: ⚠️ 设备文档的"水·油·柴"与此矛盾，接线前须实测确认（见 README）。
        "engine_temp_c": engine_temp,
        "fuel_temp_c": fuel_temp,
        "oil_temp_c": oil_temp,
        "odo_km": _num(raw.get("ODO")),
        "engine_hours": _num(raw.get("HRS")),
        "dtc_count": _num(raw.get("DTC")),
        "mil_on": _num(raw.get("MIL")),
        "bus_state": raw.get("BUS", "").strip() or None,
        "link_state": raw.get("LINK", "").strip() or None,
    }
    return {"uptime_s": uptime_s, "raw": raw, "values": values}


def frame_diagnostics(frame: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """总线诊断计数（TEC/REC/RX/ERR），不进契约主字段。"""
    if not frame:
        return {}
    raw = frame.get("raw") or {}
    return {
        "bus_tec": _num(raw.get("TEC")),
        "bus_rec": _num(raw.get("REC")),
        "bus_rx": _num(raw.get("RX")),
        "bus_err": _num(raw.get("ERR")),
    }


# ---------------------------------------------------------------------------
# 时间锚定
# ---------------------------------------------------------------------------
class UptimeClock:
    """把相对开机时间换算成带时区的绝对时间戳。

    设备只有 ``[%5lu s]``，没有绝对时钟。做法：首次见到帧时取
    ``boot_wall = now - uptime``，之后 ``ts = boot_wall + uptime``，
    这样同一批回传的**历史帧也能被正确回溯**，而不是全部盖成"收到时刻"。

    设备重启会让 uptime 回退，检测到回退就重新锚定。
    """

    #: 判定"重启"的回退阈值（秒）；容忍少量乱序
    RESET_THRESHOLD_S = 5

    def __init__(self, tz: Optional[timezone] = None) -> None:
        self.tz = tz or timezone(timedelta(hours=8))
        self._boot_wall: Optional[datetime] = None
        self._last_uptime: Optional[int] = None
        self.reanchors = 0

    @property
    def anchored(self) -> bool:
        return self._boot_wall is not None

    def stamp(self, uptime_s: int, now: Optional[datetime] = None) -> datetime:
        moment = now or datetime.now(self.tz)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=self.tz)

        if self._last_uptime is not None and uptime_s < self._last_uptime - self.RESET_THRESHOLD_S:
            self._boot_wall = None
            self.reanchors += 1

        if self._boot_wall is None:
            self._boot_wall = moment - timedelta(seconds=uptime_s)

        self._last_uptime = uptime_s
        return self._boot_wall + timedelta(seconds=uptime_s)


# ---------------------------------------------------------------------------
# 契约映射
# ---------------------------------------------------------------------------
def to_record(
    frame: Dict[str, Any],
    *,
    vehicle_id: str,
    ts: datetime,
    fuel_type: Optional[str] = None,
    plate: Optional[str] = None,
    cold_threshold_c: float = DEFAULT_COLD_THRESHOLD_C,
    fuel_lph_max: float = DEFAULT_FUEL_LPH_MAX,
    crosscheck_tol: float = DEFAULT_CROSSCHECK_TOL,
    has_gps_source: bool = False,
) -> Dict[str, Any]:
    """原始键值 -> 契约规范字段。

    只出现在契约里的字段一律给值；帧里没有的（GPS 全套、载重、液位…）留 ``None``。
    """
    values = frame.get("values") or {}
    flags: List[str] = []

    instant = values.get("instant_fuel_lph")
    total = values.get("total_fuel_l")

    # ---- 链路与总线：只打标，不否决（LINK=OK 不代表有关键数据）----
    if (values.get("link_state") or "").upper() == "LOST":
        flags.append("link_lost")
    bus_state = (values.get("bus_state") or "").upper()
    if bus_state and bus_state != "RUN":
        flags.append("bus_off")

    # ---- ECU 故障 ----
    dtc = values.get("dtc_count") or 0
    mil = values.get("mil_on") or 0
    if dtc > 0 or mil >= 1:
        flags.append("ecu_fault")

    # ---- 冷态 ----
    engine_temp = values.get("engine_temp_c")
    if engine_temp is not None and engine_temp < cold_threshold_c:
        flags.append("engine_cold")

    # ---- 油耗来源 ----
    if instant is None and total is None:
        flags.append("no_fuel_value")
    if instant is not None:
        if instant < 0 or instant > fuel_lph_max:
            flags.append("impossible_fuel")
    if instant is None and total is not None:
        # 只有累计油耗时，碳排要靠差分，标注来源切换
        flags.append("fuel_source_switch")

    # ---- FA 反证：与设备自带的"车速÷油耗"反算值比 ----
    derived = values.get("fuel_lph_from_economy")
    if instant and derived and instant > 0:
        deviation = abs(derived - instant) / instant
        if deviation > crosscheck_tol:
            flags.append("fuel_rate_mismatch")

    if fuel_type is None:
        flags.append("fuel_type_default")

    # ---- 本通道的两项结构性限制 ----
    flags.append("ts_from_uptime")     # 无绝对时钟，时间戳由桥接锚定
    if not has_gps_source:
        flags.append("no_gps_source")  # 本帧格式不含 GPS

    record: Dict[str, Any] = {
        "ts": ts.isoformat(timespec="seconds"),
        "vehicle_id": vehicle_id,
        "plate": plate,
        "instant_fuel_lph": instant,
        "total_fuel_l": total,
        "fuel_type": fuel_type,
        "engine_rpm": values.get("engine_rpm"),
        "vehicle_speed_kmh": values.get("vehicle_speed_kmh"),
        "engine_temp_c": engine_temp,
        "odo_km": values.get("odo_km"),
        # ---- 本次契约新增 ----
        "engine_hours": values.get("engine_hours"),
        "torque_req_nm": values.get("torque_req_nm"),
        "torque_actual_nm": values.get("torque_actual_nm"),
        "fuel_temp_c": values.get("fuel_temp_c"),
        "oil_temp_c": values.get("oil_temp_c"),
        # ---- 本帧格式不提供 ----
        "throttle_pos_pct": None,
        "engine_load_pct": None,
        "fuel_level_pct": None,
        "intake_press_kpa": None,
        "atm_press_kpa": None,
        "lat": None,
        "lng": None,
        "gps_speed_kmh": None,
        "heading_deg": None,
        "altitude_m": None,
        "satellites": None,
        "fix_quality": None,
        "load_kg": None,
        "ignition": None,
        "fault_codes": ("DTC=%d" % dtc) if dtc > 0 else "",
        "qc_flags": flags,
    }
    return record


def parse_stream(
    lines: Iterable[str],
    *,
    clock: UptimeClock,
    vehicle_id: str,
    fuel_type: Optional[str] = None,
    plate: Optional[str] = None,
    has_gps_source: bool = False,
    now: Optional[datetime] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]], Dict[str, int]]:
    """整批文本行 -> ``(records, rejects, stats)``。

    rejects 覆盖"不是帧"和"帧里没有可用油耗"两类；后者交给
    ``src/obd_gps.normalize_records`` 判会重复，这里只做粗筛并给原因。
    """
    records: List[Dict[str, Any]] = []
    rejects: List[Dict[str, str]] = []
    stats = {"lines": 0, "frames": 0, "records": 0, "rejects": 0}

    for line in lines:
        stats["lines"] += 1
        frame = parse_frame(line)
        if frame is None:
            if line.strip():
                stats["rejects"] += 1
                rejects.append({"reason": "not_a_frame", "detail": line.strip()[:120]})
            continue

        stats["frames"] += 1
        ts = clock.stamp(frame["uptime_s"], now=now)
        record = to_record(
            frame,
            vehicle_id=vehicle_id,
            ts=ts,
            fuel_type=fuel_type,
            plate=plate,
            has_gps_source=has_gps_source,
        )
        if "no_fuel_value" in record["qc_flags"]:
            stats["rejects"] += 1
            rejects.append({
                "reason": "no_fuel_value",
                "detail": "uptime=%d" % frame["uptime_s"],
            })
            continue

        records.append(record)
        stats["records"] += 1

    return records, rejects, stats


#: 供契约 `serial_frame` 段引用的格式摘要（单一事实来源，避免文档与代码漂移）
FRAME_SPEC: Dict[str, Any] = {
    "version": "j1939-gateway-text-v1",
    "header": "[%5lu s]  相对开机时间，无绝对时间戳",
    "missing_token": MISSING,
    "split_rule": "按 1~4 位大写键名 + '=' 切区间；禁用定宽切片（%5lu 在 >=100000 s 后溢出 5 位）",
    "keys": {
        "RPM": ("engine_rpm", "rpm", "61444 EEC1"),
        "SPD": ("vehicle_speed_kmh", "km/h", "65265 CCV1"),
        "TQ": ("torque_req_nm / torque_actual_nm", "N·m", "61444 SPN512/513"),
        "FA": ("instant_fuel_lph", "L/h", "65276 Dash Display（仪表显示值，非 ECM 真实燃油率）"),
        "FB": ("fuel_economy_km_per_l (+ fuel_lph_from_economy)", "km/L", "65266 LFE"),
        "FC": ("total_fuel_l", "L", "65257 LFC"),
        "T": ("engine_temp_c / fuel_temp_c / oil_temp_c", "°C", "65262 ET1"),
        "ODO": ("odo_km", "km", "65248 VD"),
        "HRS": ("engine_hours", "h", "65253 HOURS"),
        "DTC": ("dtc_count", "count", "65226 DM1"),
        "MIL": ("mil_on", "0/1", "65226 DM1"),
        "BUS": ("bus_state", "STOP/RUN/BOFF/RECV", "-"),
        "TEC": ("bus_tec", "count", "-"),
        "REC": ("bus_rec", "count", "-"),
        "RX": ("bus_rx", "count", "-"),
        "ERR": ("bus_err", "count", "-"),
        "LINK": ("link_state", "OK/LOST", "-"),
    },
}
