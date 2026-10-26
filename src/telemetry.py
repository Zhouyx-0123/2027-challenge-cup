# -*- coding: utf-8 -*-
"""实时监测（单片机 · 颗粒物 PM）数据契约内核：校验、归一、采样窗排放率、落盘。

定位（与 src/invoice.py 同构）：**纯函数、可单测、不算账**。
    · 只读 config/telemetry_contract.json（设备端与后端共用同一份契约）；
    · 不读宽表、不写 data/processed、不进闸门、不改任何结果表；
    · 落盘只写调用方给的路径（data/raw/实时/ 或 outputs/）。

**这个模块解决什么**
    颗粒物是**污染物浓度**（mg/m³），CO₂ 是**排放质量**（kg/km）—— 两者不是一回事。
    所以 PM 读数不能直接进 E₀ 主账。它的价值是当**排放因子的独立旁证**，
    角色和「发票油量」一样：发票给「消耗了多少油」（实测），PM 给「实际排了多少颗粒」
    （实测），因子表给「估算值」。三者交叉才能审计因子表、标定路段系数、标记异常。

    而要把 PM 变成可比的**排放率**，光有瞬时浓度不够 ——
    **必须能算出 g/km**。所以契约把 flow_m3h（流量）列为必填，
    并要求给出 dist_km 或 speed_kmh（里程或车速）。本模块的
    :func:`window_emission_rate` 负责把「相邻两条读数」折算成一个采样窗的 g/km。

**口径纪律**
    本模块**不含任何 PM→碳排 / PM→油耗 的换算系数**。
    没有可核查来源的系数一律不写 —— 照 config/invoice_spend_factors.json 的规矩
    （默认不放任何无来源因子）。理论值与实测值的比对留给后续标定，
    标定出来之前，PM 数据只做「测什么标什么」，不折成碳。

单位：PM 浓度 mg/m³；流量 m³/h；排放率 g/km；里程 km；时间 ISO8601 带时区。
"""

from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "telemetry_contract.json"

#: 契约里声明的必填字段（与 config/telemetry_contract.json 的 fields.required 保持一致）
REQUIRED_FIELDS: Tuple[str, ...] = ("device_id", "ts", "pm25_mg_m3", "flow_m3h")

#: 数值字段的合法范围：min <= x <= max
NUM_RANGES: Dict[str, Tuple[float, float]] = {
    "pm25_mg_m3": (0.0, 1000.0),
    "pm10_mg_m3": (0.0, 1000.0),
    "flow_m3h": (0.0, 1000.0),
    "dist_km": (0.0, 2_000_000.0),
    "speed_kmh": (0.0, 300.0),
    "load_kg": (0.0, 200_000.0),
    "engine_rpm": (0.0, 10_000.0),
    "co2_ppm": (0.0, 100_000.0),
    "so2_mg_m3": (0.0, 1000.0),
    "nox_mg_m3": (0.0, 1000.0),
    "temp_c": (-80.0, 100.0),
    "press_hpa": (300.0, 1200.0),
}

#: 整数/浮点字段
NUMERIC_FIELDS: Tuple[str, ...] = tuple(NUM_RANGES.keys())

#: 文本字段
TEXT_FIELDS: Tuple[str, ...] = ("device_id", "od_key", "plate", "fuel_type")

#: 允许的质控标记位（越界即判为未知标记）
KNOWN_QC_FLAGS: Tuple[str, ...] = (
    "preheat", "sensor_fault", "calib_stale", "low_flow", "overflow",
    "power_reset", "clock_drift", "duplicate", "manual",
)

#: 这些质控位标记的数据**不可用于排放率标定**
QC_EXCLUDE_FROM_RATE: Tuple[str, ...] = (
    "preheat", "sensor_fault", "calib_stale", "low_flow", "overflow",
    "power_reset", "clock_drift",
)


# ===========================================================================
# 契约
# ===========================================================================

def load_contract() -> Dict[str, Any]:
    """读契约文件 config/telemetry_contract.json。读不到返回空 dict（不编造默认契约）。"""
    try:
        return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def contract_version() -> str:
    return str(load_contract().get("version") or "unknown")


# ===========================================================================
# 单条读数：归一 + 校验
# ===========================================================================

def parse_ts(value: Any) -> Optional[datetime]:
    """解析带时区偏移的 ISO8601 时间戳。

    裸时间戳（无偏移）判为无效 —— 设备必须对时，否则跨设备的时序无法比较。
    """
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt


def _to_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return f if math.isfinite(f) else None
    text = str(value).strip()
    if not text or text.lower() in ("null", "none", "nan", ""):
        return None
    text = text.replace(",", "")
    try:
        f = float(text)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def normalize_reading(record: Dict[str, Any]) -> Dict[str, Any]:
    """归一并校验**一条**读数。

    返回：``{"ok": bool, "reading": dict, "errors": [...], "warnings": [...]}``

    纪律：
      · 缺必填 / 必填数值无效 → ``ok=False``，**不丢弃也不填 0**；
      · 可选字段缺失 → 留 None，不影响 ok；
      · 数值越界 → 记 error 并把该字段置 None（不拿越界值算账）。
    """
    errors: List[str] = []
    warnings: List[str] = []
    out: Dict[str, Any] = {}

    for field in TEXT_FIELDS:
        value = record.get(field)
        text = "" if value is None else str(value).strip()
        if field == "device_id" and not text:
            errors.append("缺必填字段 device_id")
        out[field] = text

    ts = parse_ts(record.get("ts"))
    if ts is None:
        errors.append("ts 缺失或不是带时区偏移的 ISO8601 时间")
    out["ts"] = ts.isoformat() if ts is not None else ""

    for field in NUMERIC_FIELDS:
        value = _to_float(record.get(field))
        if value is None:
            if field in REQUIRED_FIELDS:
                errors.append("缺必填数值字段 %s" % field)
            out[field] = None
            continue
        low, high = NUM_RANGES[field]
        if not (low <= value <= high):
            errors.append("%s=%.4g 超出合法范围 [%g, %g]" % (field, value, low, high))
            out[field] = None
            continue
        out[field] = value

    # 里程与车速：至少要有一个，否则算不出 g/km
    if out.get("dist_km") is None and out.get("speed_kmh") is None:
        errors.append("dist_km 与 speed_kmh 至少要给一个（否则无法折算 g/km）")

    flags = record.get("qc_flags")
    if isinstance(flags, str):
        flags = [f.strip() for f in flags.split(",") if f.strip()]
    if flags is None:
        flags = []
    if not isinstance(flags, list):
        errors.append("qc_flags 必须是数组")
        flags = []
    clean_flags = []
    for flag in flags:
        name = str(flag).strip()
        if not name:
            continue
        if name not in KNOWN_QC_FLAGS:
            warnings.append("未知质控标记 %s（已忽略）" % name)
            continue
        clean_flags.append(name)
    out["qc_flags"] = clean_flags

    # 必填字段的最终体检（数值字段若上面已报错就不重复报）
    for field in REQUIRED_FIELDS:
        if field in TEXT_FIELDS:
            if not out.get(field):
                errors.append("缺必填字段 %s" % field)
        elif out.get(field) is None and not any(field in e for e in errors):
            errors.append("缺必填数值字段 %s" % field)

    if out.get("flow_m3h") is not None and out["flow_m3h"] <= 0:
        errors.append("flow_m3h 必须 > 0（流量为 0 算不出排放率）")

    return {
        "ok": not errors,
        "reading": out,
        "errors": errors,
        "warnings": warnings,
    }


# ===========================================================================
# 批量：去重 + 时序
# ===========================================================================

def dedupe_key(reading: Dict[str, Any]) -> Tuple[str, str]:
    """同设备同秒视为重复。"""
    return (str(reading.get("device_id") or ""), str(reading.get("ts") or ""))


def validate_batch(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """批量校验：逐条归一 → 去重 → 按设备分组排序。

    返回：``{"ok": [...], "rejected": [...], "duplicates": [...], "counts": {...}}``
      · ``ok``       —— 可用读数（已归一、按设备与时间排序）
      · ``rejected`` —— 不可用读数 + 原因（**保留原始行，不丢证据**）
      · ``duplicates``—— 被判重复而剔除的读数
    """
    ok: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    seen: Dict[Tuple[str, str], int] = {}

    for index, record in enumerate(records or []):
        result = normalize_reading(record or {})
        reading = result["reading"]
        if not result["ok"]:
            rejected.append({"index": index, "raw": record,
                             "errors": result["errors"]})
            continue
        key = dedupe_key(reading)
        if key in seen:
            rejected.append({"index": index, "raw": record,
                             "errors": ["与第 %d 条重复（设备 %s @ %s）"
                                        % (seen[key], key[0], key[1])]})
            continue
        seen[key] = index
        ok.append(reading)

    by_device: Dict[str, List[Dict[str, Any]]] = {}
    for reading in ok:
        by_device.setdefault(str(reading.get("device_id") or ""), []).append(reading)
    for rows in by_device.values():
        rows.sort(key=lambda r: (r.get("ts") or ""))
    # 输出也按 (设备, 时间) 排好 —— 落盘顺序可复现，便于逐行回查。
    ok = [r for rows in by_device.values() for r in rows]

    counts = {
        "input": len(records or []),
        "ok": len(ok),
        "rejected": len(rejected),
        "devices": len(by_device),
    }
    return {"ok": ok, "rejected": rejected, "counts": counts,
            "by_device": by_device}


# ===========================================================================
# 采样窗 → 质量排放率 g/km
# ===========================================================================

def window_emission_rate(prev: Dict[str, Any],
                          curr: Dict[str, Any]) -> Dict[str, Any]:
    """把**相邻两条读数**折算成一个采样窗的质量排放率（g/km）。

    物理式：
        排放质量(g) = 质量浓度(mg/m³) × 流量(m³/h) × 采样时长(h)
        排放率(g/km) = 排放质量(g) ÷ 窗内里程(km)

    为什么必须按"窗"而不是按"条"：瞬时浓度是速率量，没有时间跨度和里程跨度
    就不是排放量。**这正是契约把 flow_m3h 列为必填、要求给里程或车速的原因。**

    返回：``{"ok": bool, "g_per_km": float|None, "reason": str, ...}``
    质控位命中的窗（QC_EXCLUDE_FROM_RATE）一律 ``ok=False``，不参与标定。
    """
    out: Dict[str, Any] = {
        "ok": False, "g_per_km": None, "reason": "",
        "device_id": curr.get("device_id"), "ts_start": prev.get("ts"),
        "ts_end": curr.get("ts"),
    }
    if str(prev.get("device_id")) != str(curr.get("device_id")):
        out["reason"] = "两条读数不属于同一设备"
        return out

    flags = set(prev.get("qc_flags") or []) | set(curr.get("qc_flags") or [])
    bad = sorted(flags & set(QC_EXCLUDE_FROM_RATE))
    if bad:
        out["reason"] = "质控标记 %s：数据不可用于排放率标定" % ",".join(bad)
        return out

    t0, t1 = parse_ts(prev.get("ts")), parse_ts(curr.get("ts"))
    if t0 is None or t1 is None:
        out["reason"] = "时间戳不可解析"
        return out
    dt_h = (t1 - t0).total_seconds() / 3600.0
    if dt_h <= 0:
        out["reason"] = "采样间隔非正（时间戳未递增或相同）"
        return out
    if dt_h > 24:
        out["reason"] = "采样间隔 %.2f h 过大，跨度过长的窗不做标定" % dt_h
        return out

    # 窗内里程：优先用累计里程差；没有就用车速 × 时间
    d_prev, d_curr = prev.get("dist_km"), curr.get("dist_km")
    if d_prev is not None and d_curr is not None:
        dkm = float(d_curr) - float(d_prev)
        km_source = "odometer_delta"
    else:
        speeds = [s for s in (prev.get("speed_kmh"), curr.get("speed_kmh")) if s is not None]
        if not speeds:
            out["reason"] = "缺里程与车速，无法折算窗内里程"
            return out
        dkm = sum(speeds) / len(speeds) * dt_h
        km_source = "speed_x_time"
    if dkm <= 0:
        out["reason"] = "窗内里程非正（%.3f km）" % dkm
        return out

    concs = [c for c in (prev.get("pm25_mg_m3"), curr.get("pm25_mg_m3")) if c is not None]
    flows = [q for q in (prev.get("flow_m3h"), curr.get("flow_m3h")) if q is not None]
    if not concs or not flows:
        out["reason"] = "缺浓度或流量"
        return out
    conc = sum(concs) / len(concs)
    flow = sum(flows) / len(flows)

    mass_g = conc * flow * dt_h
    out.update({
        "ok": True,
        "reason": "",
        "g_per_km": round(mass_g / dkm, 6),
        "mass_g": round(mass_g, 6),
        "dist_km": round(dkm, 4),
        "km_source": km_source,
        "hours": round(dt_h, 4),
        "pm25_mg_m3": round(conc, 6),
        "flow_m3h": round(flow, 6),
    })
    return out


def device_rates(validated: Dict[str, Any]) -> List[Dict[str, Any]]:
    """对每台设备的相邻读数逐窗算排放率。"""
    out: List[Dict[str, Any]] = []
    for device, rows in (validated.get("by_device") or {}).items():
        for i in range(1, len(rows)):
            out.append(window_emission_rate(rows[i - 1], rows[i]))
    return out


# ===========================================================================
# 质控统计
# ===========================================================================

def qc_summary(validated: Dict[str, Any]) -> Dict[str, Any]:
    """质控统计：各标记命中数、可用窗数、覆盖率。"""
    ok_rows = validated.get("ok") or []
    flag_counts: Dict[str, int] = {}
    for row in ok_rows:
        for flag in (row.get("qc_flags") or []):
            flag_counts[flag] = flag_counts.get(flag, 0) + 1
    rates = device_rates(validated)
    usable = [r for r in rates if r.get("ok")]
    return {
        "flag_counts": flag_counts,
        "windows_total": len(rates),
        "windows_usable": len(usable),
        "window_coverage_pct": round(100.0 * len(usable) / len(rates), 2) if rates else None,
        "g_per_km": {
            "min": round(min((r["g_per_km"] for r in usable), default=0.0), 6) or None,
            "max": round(max((r["g_per_km"] for r in usable), default=0.0), 6) or None,
        } if usable else {"min": None, "max": None},
        "note": "g/km 是**颗粒物质量排放率**，不是 CO₂ 排放量；不可直接进 E₀ 主账。",
    }


# ===========================================================================
# 落盘
# ===========================================================================

LEDGER_COLUMNS: List[str] = [
    "device_id", "ts", "plate", "od_key", "pm25_mg_m3", "pm10_mg_m3",
    "flow_m3h", "dist_km", "speed_kmh", "load_kg", "engine_rpm",
    "fuel_type", "co2_ppm", "so2_mg_m3", "nox_mg_m3",
    "temp_c", "press_hpa", "qc_flags",
]


def write_ledger(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    """把归一后的读数写成 CSV（追加模式，带表头）。

    落盘位置由调用方给（约定 ``data/raw/实时/``），本函数不做目录策略判断。
    返回写入行数。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=LEDGER_COLUMNS)
        if not exists:
            writer.writeheader()
        count = 0
        for row in rows:
            item = {c: row.get(c) for c in LEDGER_COLUMNS}
            if isinstance(item.get("qc_flags"), list):
                item["qc_flags"] = ";".join(str(f) for f in item["qc_flags"])
            writer.writerow(item)
            count += 1
    return count


def read_csv(path: Path) -> List[Dict[str, Any]]:
    """读设备上报的 CSV（表头用契约字段名）。读不了返回空列表。"""
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                rows.append({k: v for k, v in row.items() if k is not None})
    except Exception:
        return []
    return rows


# ===========================================================================
# 自测
# ===========================================================================

def self_test() -> Dict[str, Any]:
    """内核自测（给 CI 与 tools/ 冒烟用）。"""
    base = {
        "device_id": "PDM-GZ-001", "ts": "2026-10-25T14:00:00+08:00",
        "pm25_mg_m3": 0.142, "flow_m3h": 2.4,
        "dist_km": 1000.0, "speed_kmh": 62.0, "plate": "苏US3126",
        "qc_flags": [],
    }
    nxt = dict(base, ts="2026-10-25T15:00:00+08:00", dist_km=1062.0)
    out: Dict[str, Any] = {"contract": contract_version()}

    single = normalize_reading(base)
    out["single_ok"] = single["ok"]

    missing = normalize_reading({"device_id": "X", "ts": base["ts"]})
    out["missing_errors"] = missing["errors"]

    bad_ts = normalize_reading(dict(base, ts="2026-10-25 14:00:00"))
    out["naive_ts_rejected"] = not bad_ts["ok"]

    batch = validate_batch([base, nxt, dict(base)])
    out["batch_counts"] = batch["counts"]

    rate = window_emission_rate(base, nxt)
    out["g_per_km"] = rate.get("g_per_km")
    out["rate_ok"] = rate.get("ok")

    preheat = window_emission_rate(dict(base, qc_flags=["preheat"]), nxt)
    out["preheat_excluded"] = not preheat.get("ok")

    out["qc"] = qc_summary(batch)
    return out


if __name__ == "__main__":
    print(json.dumps(self_test(), ensure_ascii=False, indent=2, default=str))
