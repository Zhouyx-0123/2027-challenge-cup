# -*- coding: utf-8 -*-
"""J6L220（国六，EDC17 类）UDS 0x22 抓包 → 契约字段解码器。

定位（与 src/obd_gps.py 同构）：纯函数、可单测、不算账、不写库。

本模块解决什么
--------------
实车只有诊断上位机抓下来的 29-bit CAN 原始帧（seq,timestamp_ms,can_id,ext,dlc,data），
**没有厂家 DID 表**。config/did_map_j6l220.json 是逆向出来的映射表，本模块负责：

1. 读原始 CSV，按 seq/timestamp_ms 回绕切出**连续采集段**（一次抓包是一个文件，
   但文件内会拼接多次会话，时间轴必须分段，否则会把 57 分钟的断档当成连续采样）；
2. 做 ISO-TP 组帧，从 0x18DAF100 的 "62 DID_HI DID_LO <payload>" 应答里取 DID 值；
3. 按映射表把原始整数换成物理量（转速、喷油量、轨压、排温、油门、液位……）；
4. 用**质量守恒**从「每循环喷油量 × 转速」算瞬时油耗（L/h）——这是本通道唯一的出账入口，
   满足 config/obd_gps_contract.json 的 hard_requirement（没有油耗就没有碳）；
5. 输出与 src/obd_gps.py 契约同名的行（CANONICAL_FIELDS 子集 + qc 标记）。

口径纪律
--------
1. **不填 0 冒充缺失**：没有的字段写 None（落盘成 CSV 空 / JSON null），并置 qc 标记；
2. 车速、里程、大气压在本次抓包里**确实没有**（见映射表 missing_channels），
   所以 vehicle_speed_kmh/odo_km/atm_press_kpa 一律 null + qc channel_absent；
3. 0x0199 的语义（累计油耗 vs 里程）**未定标**，scale 在映射表里是 null：
   本模块只在 --assume-0x0199-scale 显式给出时才写 total_fuel_l，否则只给
   counter_0x0199 原始计数 + qc counter_scale_unverified；
4. 油耗积分按采样时刻做梯形积分，超过 MAX_GAP_S 的断档**不计入**（置 qc sample_gap）。

单位：时间 s（段内相对）或 ISO8601；油耗 L/h、L；转速 rpm；温度 ℃；压力 bar/kPa。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DID_MAP_PATH = ROOT / "config" / "did_map_j6l220.json"

#: 发动机物理参数（与映射表 derived.instant_fuel_lph 一致；改这里要同步改 JSON）
CYLINDERS = 6
DENSITY_KG_PER_L = 0.835

#: 断档阈值：超过则不参与油耗积分（5 s 快通道，30 s 已算异常）
MAX_GAP_S = 90.0

#: 分段的判定：seq 回绕或时间戳倒退超过该毫秒数，视为新一轮抓包
SEG_TIME_TOLERANCE_MS = 1000

#: 契约里本模块能提供的字段（其余保持 null）
EMITTED_FIELDS = ("ts", "vehicle_id", "fuel_type", "instant_fuel_lph", "engine_rpm",
                  "throttle_pos_pct", "fuel_level_pct", "engine_temp_c")

#: 抓包里没有的通道，统一打这个 qc
ABSENT_CHANNELS = ("vehicle_speed_kmh", "odo_km", "atm_press_kpa", "engine_load_pct")


# --------------------------------------------------------------------------------------
# 映射表
# --------------------------------------------------------------------------------------
def load_did_map(path: Optional[Path] = None) -> Dict[str, Any]:
    """读 DID 映射表并做最小校验（key 必须是 0x 前缀的十六进制字符串）。"""
    p = Path(path) if path else DID_MAP_PATH
    with open(p, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    for key, entry in doc["dids"].items():
        if not re.fullmatch(r"0x[0-9A-Fa-f]{4}", key):
            raise ValueError(f"DID key 格式不对: {key}")
        if "bytes" not in entry:
            raise ValueError(f"{key} 缺 bytes 字段")
        if entry.get("scale") is not None and not isinstance(entry["scale"], (int, float)):
            raise ValueError(f"{key} 的 scale 必须是数字或 null")
    return doc


def did_of(hex_key: str) -> int:
    return int(hex_key, 16)


def _entry_by_did(doc: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    return {did_of(k): v for k, v in doc["dids"].items()}


# --------------------------------------------------------------------------------------
# 原始帧 → 连续段
# --------------------------------------------------------------------------------------
def parse_data_hex(text: str) -> bytes:
    """'03 22 01 65 00 00 00 00' → bytes。容忍 ':' 分隔与全小写。"""
    clean = text.strip().replace(":", " ").replace(",", " ")
    return bytes(int(tok, 16) for tok in clean.split() if tok)


def parse_can_csv(path: Path) -> pd.DataFrame:
    """读一个 all_*.csv / raw_*.csv，返回 [seq, ts_ms, can_id, data]。"""
    df = pd.read_csv(path)
    cols = {c.lower().strip(): c for c in df.columns}
    need = ("seq", "timestamp_ms", "can_id", "data")
    missing = [c for c in need if c not in cols]
    if missing:
        raise ValueError(f"{path.name} 缺列 {missing}，实际列 {list(df.columns)}")
    out = pd.DataFrame({
        "seq": df[cols["seq"]].astype("int64"),
        "ts_ms": df[cols["timestamp_ms"]].astype("float64"),
        "can_id": df[cols["can_id"]].astype(str).str.strip().map(
            lambda s: int(s, 16) if s.lower().startswith("0x") else int(s, 16)),
        "data": df[cols["data"]].astype(str).map(parse_data_hex),
    })
    out["file"] = path.name
    return out


def split_segments(frames: pd.DataFrame) -> pd.DataFrame:
    """按 seq 回绕 / 时间戳倒退切段，加 seg（形如 660#2）与段内相对时间。"""
    if frames.empty:
        frames = frames.copy()
        frames["seg"] = pd.Series(dtype="object")
        frames["t_s"] = pd.Series(dtype="float64")
        return frames
    f = frames.reset_index(drop=True).copy()
    seq = f["seq"].to_numpy()
    ts = f["ts_ms"].to_numpy()
    new_seg = np.zeros(len(f), dtype=bool)
    new_seg[0] = True
    for i in range(1, len(f)):
        if seq[i] <= seq[i - 1] or ts[i] < ts[i - 1] - SEG_TIME_TOLERANCE_MS:
            new_seg[i] = True
    seg_idx = np.cumsum(new_seg) - 1
    stem = str(f["file"].iloc[0]).split(".")[0]
    f["seg"] = [f"{stem}#{int(i)}" for i in seg_idx]
    rel = np.zeros(len(f))
    start = 0
    for i in range(1, len(f)):
        if new_seg[i]:
            start = i
        rel[i] = (ts[i] - ts[start]) / 1000.0
    f["t_s"] = rel
    return f


# --------------------------------------------------------------------------------------
# ISO-TP 解码
# --------------------------------------------------------------------------------------
def decode_iso_tp(frames: pd.DataFrame, response_ids: Sequence[int]) -> pd.DataFrame:
    """把应答帧还原成 [seg, t_s, ts_ms, resp_id, did, payload]。

    只处理 0x62（正响应）与 0x7F（负响应）两种 SID；多帧按 ISO-TP 序号拼接。
    """
    wanted = set(response_ids)
    rows: List[Dict[str, Any]] = []
    buf: Dict[Tuple[str, int, int], Dict[str, Any]] = {}
    for seg, t_s, ts_ms, cid, data in zip(frames["seg"], frames["t_s"], frames["ts_ms"],
                                          frames["can_id"], frames["data"]):
        if cid not in wanted or not data:
            continue
        pci = data[0] >> 4
        if pci == 0:  # 单帧
            length = data[0] & 0x0F
            body = data[1:1 + length]
            _emit(rows, seg, t_s, ts_ms, cid, body)
        elif pci == 1:  # 首帧
            if len(data) < 2:
                continue
            total = ((data[0] & 0x0F) << 8) | data[1]
            key = (seg, cid, 1)
            buf = {k: v for k, v in buf.items() if k[0] != seg or k[1] != cid}
            buf[key] = {"total": total, "body": bytearray(data[2:]), "t_s": t_s, "ts_ms": ts_ms,
                        "next": 1}
        elif pci == 2:  # 连续帧
            key = (seg, cid, 1)
            state = buf.get(key)
            if state is None:
                continue
            idx = data[0] & 0x0F
            if idx != state["next"]:
                buf.pop(key, None)
                continue
            state["body"].extend(data[1:])
            state["next"] = idx + 1
            if len(state["body"]) >= state["total"]:
                body = bytes(state["body"][: state["total"]])
                buf.pop(key, None)
                _emit(rows, seg, state["t_s"], state["ts_ms"], cid, body)
    return pd.DataFrame(rows, columns=["seg", "t_s", "ts_ms", "resp_id", "did", "payload"])


def _emit(rows: List[Dict[str, Any]], seg: str, t_s: float, ts_ms: float, cid: int, body: bytes) -> None:
    if len(body) < 3:
        return
    sid = body[0]
    if sid == 0x62:  # ReadDataByIdentifier 正响应
        did = (body[1] << 8) | body[2]
        rows.append({"seg": seg, "t_s": t_s, "ts_ms": ts_ms, "resp_id": cid,
                     "did": did, "payload": body[3:]})
    elif sid == 0x7F:  # 负响应：0x7F <sid> <nrc>
        rows.append({"seg": seg, "t_s": t_s, "ts_ms": ts_ms, "resp_id": cid,
                     "did": -1, "payload": body[1:]})


# --------------------------------------------------------------------------------------
# 原始值 → 物理量
# --------------------------------------------------------------------------------------
def raw_to_int(payload: bytes, signed: bool) -> Optional[int]:
    if not payload:
        return None
    n = len(payload)
    return int.from_bytes(payload[:n], byteorder="big", signed=signed and n <= 2)


def decode_value(entry: Dict[str, Any], payload: bytes) -> Optional[float]:
    """按映射表把 payload 换成物理量；scale 为 null 时返回原始整数（float）。"""
    raw = raw_to_int(payload, bool(entry.get("signed")))
    if raw is None:
        return None
    scale = entry.get("scale")
    offset = entry.get("offset") or 0.0
    if scale is None:
        return float(raw)
    return float(raw) * float(scale) + float(offset)


def build_series(responses: pd.DataFrame, doc: Dict[str, Any]) -> pd.DataFrame:
    """[seg, t_s, did, nbytes, value, name, unit, confidence]。"""
    entries = _entry_by_did(doc)
    rows = []
    for seg, t_s, did, payload in zip(responses["seg"], responses["t_s"],
                                      responses["did"], responses["payload"]):
        if did < 0:
            continue
        entry = entries.get(did)
        if entry is None:
            entry = {"bytes": len(payload), "signed": False, "scale": None,
                     "name": "unknown", "unit": "?"}
        rows.append({
            "seg": seg, "t_s": t_s, "did": did, "nbytes": len(payload),
            "value": decode_value(entry, payload),
            "name": entry.get("name", "unknown"),
            "unit": entry.get("unit", "?"),
            "confidence": entry.get("confidence", "unknown"),
        })
    return pd.DataFrame(rows, columns=["seg", "t_s", "did", "nbytes", "value", "name", "unit", "confidence"])


def build_fast_frame(series: pd.DataFrame, max_tol_s: float = 6.0) -> pd.DataFrame:
    """以 0x0166（每循环喷油量）为主轴，就近并入 0x0165（转速），得到 5 s 快通道。

    两个快通道交替采样（相隔约 1.3 s），必须按时间就近配对，不能按同一时刻 pivot。
    容忍度取 6 s：两通道各 5 s 一次，偶发丢一帧不该让这个点整条作废（否则约 10% 的点
    会因缺 rpm 而算不出油耗）。
    没有 0x0166 的段会整段缺席（本次抓包里每个快通道段都有，故不影响）。
    """
    q = series[series["did"] == 0x0166][["seg", "t_s", "value"]].rename(columns={"value": "D0166"})
    base = q.sort_values(["seg", "t_s"]).reset_index(drop=True)
    if base.empty:
        return pd.DataFrame(columns=["seg", "t_s", "D0166", "D0165"])
    base = _asof_merge(base, series[series["did"] == 0x0165], "D0165", tol_s=max_tol_s)
    return base.sort_values(["seg", "t_s"]).reset_index(drop=True)


def widen(series: pd.DataFrame, dids: Sequence[int]) -> pd.DataFrame:
    """把指定 DID 拉成 [seg, t_s, D0165, D0166, ...] 宽表。"""
    sub = series[series["did"].isin(dids)].copy()
    if sub.empty:
        return pd.DataFrame(columns=["seg", "t_s"])
    sub["col"] = sub["did"].map(lambda d: f"D{d:04X}")
    return sub.pivot_table(index=["seg", "t_s"], columns="col", values="value", aggfunc="last").reset_index()


# --------------------------------------------------------------------------------------
# 油耗（质量守恒）
# --------------------------------------------------------------------------------------
def fuel_lph_from_injection(rpm: float, q_raw: float, q_scale: float = 0.1,
                            cylinders: int = CYLINDERS, density: float = DENSITY_KG_PER_L) -> float:
    """每循环喷油量 (0.1 mg/stroke) + 转速 → L/h。

    mass_g_s = q_mg * 1e-3 * (rpm / (2*60)) * cylinders（四冲程：每转喷油缸数 = 缸数/2）
    """
    if rpm is None or q_raw is None:
        return float("nan")
    # 缺样（NaN）必须返回 NaN，不能返回 0——契约禁止用 0 冒充缺失；
    # 只有"转速为 0 / 断油"这种**真的没有喷油**才返回 0.0。
    if not np.isfinite(rpm) or not np.isfinite(q_raw):
        return float("nan")
    if rpm <= 0 or q_raw <= 0:
        return 0.0
    q_mg = q_raw * q_scale
    mass_g_s = q_mg * 1e-3 * (rpm / 120.0) * cylinders
    return mass_g_s * 3.6 / density


def _trapezoid(y: np.ndarray, x: np.ndarray) -> float:
    fn = getattr(np, "trapezoid", None) or getattr(np, "trapz")
    return float(fn(y, x))


def integrate_fuel(wide: pd.DataFrame, max_gap_s: float = MAX_GAP_S) -> pd.DataFrame:
    """对每个段做梯形积分，返回 [seg, n, dur_s, fuel_l, gaps, idle_share]。"""
    rows = []
    for seg, g in wide.groupby("seg"):
        g = g.dropna(subset=["D0165", "D0166"]).sort_values("t_s")
        if len(g) < 2:
            rows.append({"seg": seg, "n": int(len(g)), "dur_s": 0.0, "fuel_l": 0.0,
                         "gaps": 0, "idle_share": float("nan")})
            continue
        t = g["t_s"].to_numpy()
        # 同上：宽表里的 D0166 已经是 mg/stroke，q_scale 传 1.0
        lph = np.array([fuel_lph_from_injection(r, q, q_scale=1.0)
                        for r, q in zip(g["D0165"], g["D0166"])])
        dt = np.diff(t)
        fuel = 0.0
        gaps = 0
        for i in range(len(t) - 1):
            if dt[i] <= 0:
                continue
            if dt[i] > max_gap_s:
                gaps += 1
                continue
            fuel += 0.5 * (lph[i] + lph[i + 1]) / 3600.0 * dt[i]
        idle = float(np.mean(g["D0165"] < 800))
        rows.append({"seg": seg, "n": int(len(g)), "dur_s": float(t[-1] - t[0]),
                     "fuel_l": fuel, "gaps": gaps, "idle_share": idle})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------------------
# 契约行
# --------------------------------------------------------------------------------------
def _asof_merge(wide: pd.DataFrame, sub: pd.DataFrame, col: str, tol_s: float = 60.0) -> pd.DataFrame:
    """在段内按时间就近合并一个低频通道（page1/page2 每 20/120 s 一次）。

    手写 searchsorted 而不用 merge_asof：pandas 3.0 的 merge_asof 对 by 分组的
    排序要求很脆（分段键是字符串、全局 t_s 不单调就会报 "left keys must be sorted"），
    这里按段独立取最近邻，语义更明确也更好测。
    """
    wide = wide.copy()
    wide[col] = np.nan
    if sub.empty:
        return wide
    lookup = {seg: (g["t_s"].to_numpy(), g["value"].to_numpy())
              for seg, g in sub[["seg", "t_s", "value"]].sort_values(["seg", "t_s"]).groupby("seg")}
    for seg, idx in wide.groupby("seg").groups.items():
        pair = lookup.get(seg)
        if pair is None:
            continue
        ts_sub, vals = pair
        t = wide.loc[idx, "t_s"].to_numpy(dtype=float)
        pos = np.searchsorted(ts_sub, t)
        lo = np.clip(pos - 1, 0, len(ts_sub) - 1)
        hi = np.clip(pos, 0, len(ts_sub) - 1)
        pick = np.where(np.abs(ts_sub[lo] - t) <= np.abs(ts_sub[hi] - t), lo, hi)
        near = ts_sub[pick]
        ok = np.abs(near - t) <= tol_s
        out = np.where(ok, vals[pick], np.nan)
        wide.loc[idx, col] = out
    return wide


def to_contract_rows(wide: pd.DataFrame, doc: Dict[str, Any], vehicle_id: str,
                     ts_base: Optional[pd.Timestamp] = None,
                     assume_counter_scale: Optional[float] = None) -> pd.DataFrame:
    """宽表 → 契约字段行（EMITTED_FIELDS + qc）。"""
    entries = _entry_by_did(doc)
    # build_series 已按映射表把 0x0166 的原始计数乘过 scale（0.1 → mg/stroke），
    # 这里拿到的就是物理量，再乘一次 scale 会把油耗算小 10 倍（踩过的坑）。
    q_scale = 1.0
    q_entry = entries.get(0x0166, {})
    if q_entry.get("scale") not in (None, 0.1):
        q_scale = float(q_entry["scale"]) / 0.1
    rows = []
    for _, r in wide.iterrows():
        rpm = r.get("D0165")
        q = r.get("D0166")
        lph = fuel_lph_from_injection(rpm, q, q_scale)
        qc = []
        if rpm is None or (isinstance(rpm, float) and not np.isfinite(rpm)):
            qc.append("rpm_missing")
        if q is None or (isinstance(q, float) and not np.isfinite(q)):
            qc.append("fuel_qty_missing")
        qc.extend([f"{c}_absent" for c in ABSENT_CHANNELS])
        if ts_base is None:
            ts = None
            qc.append("clock_unknown")
        else:
            ts = (pd.Timestamp(ts_base) + pd.to_timedelta(float(r["t_s"]), unit="s")).isoformat()
        row = {
            "ts": ts,
            "vehicle_id": vehicle_id,
            "fuel_type": "diesel",
            "instant_fuel_lph": round(float(lph), 4) if np.isfinite(lph) else None,
            "engine_rpm": float(rpm) if rpm is not None and np.isfinite(rpm) else None,
            "throttle_pos_pct": _finite_or_none(r.get("D0180")),
            "fuel_level_pct": _finite_or_none(r.get("D01D7")),
            "engine_temp_c": _finite_or_none(r.get("D012B")),
            "total_fuel_l": None,
            "odo_km": None,
            "vehicle_speed_kmh": None,
            "atm_press_kpa": None,
            "engine_load_pct": None,
            "counter_0x0199": _finite_or_none(r.get("D0199")),
            "seg": r["seg"],
            "t_s": round(float(r["t_s"]), 3),
            "qc": ";".join(qc),
        }
        if assume_counter_scale is not None and row["counter_0x0199"] is not None:
            row["total_fuel_l"] = round(row["counter_0x0199"] * assume_counter_scale, 4)
        rows.append(row)
    return pd.DataFrame(rows)


def _finite_or_none(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# --------------------------------------------------------------------------------------
# 一键：目录 → 结果
# --------------------------------------------------------------------------------------
def decode_directory(input_dir: Path, doc: Optional[Dict[str, Any]] = None,
                     vehicle_id: str = "TRK-J6L220-001",
                     ts_base: Optional[pd.Timestamp] = None,
                     assume_counter_scale: Optional[float] = None) -> Dict[str, Any]:
    """解码目录下全部 all_*.csv；返回 {points, series, fuel, frames, responses, summary}。"""
    doc = doc or load_did_map()
    files = sorted(Path(input_dir).glob("all_*.csv"))
    if not files:
        raise FileNotFoundError(f"{input_dir} 下没有 all_*.csv")
    frames = pd.concat([split_segments(parse_can_csv(f)) for f in files], ignore_index=True)
    responses = decode_iso_tp(frames, [0x18DAF100, 0x18DAF121])
    series = build_series(responses, doc)
    # 0x0165 与 0x0166 虽然同为 5 s 快通道，但彼此错开约 1.3 s 采样，
    # 所以不能用 pivot（同一时刻只有一列有值），要以喷油量为主轴、按时间就近并入转速。
    fast = build_fast_frame(series)
    for did, col, tol in ((0x0180, "D0180", 60.0), (0x01D7, "D01D7", 90.0),
                          (0x012B, "D012B", 30.0), (0x0199, "D0199", 30.0)):
        fast = _asof_merge(fast, series[series["did"] == did], col, tol_s=tol)
    points = to_contract_rows(fast, doc, vehicle_id, ts_base, assume_counter_scale)
    fuel = integrate_fuel(fast)
    return {"points": points, "series": series, "fuel": fuel, "frames": frames,
            "responses": responses,
            "summary": _summary(points, series, fuel, frames, responses)}


def _summary(points: pd.DataFrame, series: pd.DataFrame, fuel: pd.DataFrame,
             frames: pd.DataFrame, responses: pd.DataFrame) -> Dict[str, Any]:
    total = float(fuel["fuel_l"].sum()) if len(fuel) else 0.0
    lph = points["instant_fuel_lph"].dropna()
    return {
        "frames": int(len(frames)),
        "segments": int(frames["seg"].nunique()),
        "responses": int(len(responses)),
        "dids": int(series["did"].nunique()),
        "points": int(len(points)),
        "fuel_l_total": round(total, 4),
        "fuel_lph": {
            "mean": round(float(lph.mean()), 3) if len(lph) else None,
            "p50": round(float(lph.median()), 3) if len(lph) else None,
            "p95": round(float(lph.quantile(0.95)), 3) if len(lph) else None,
            "max": round(float(lph.max()), 3) if len(lph) else None,
        },
        "segments_with_fuel": int((fuel["fuel_l"] > 0).sum()) if len(fuel) else 0,
        "max_gap_breaks": int(fuel["gaps"].sum()) if len(fuel) else 0,
    }


# --------------------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------------------
def selftest() -> None:
    # 1) 喷油量 × 转速 → L/h 的三个已知锚点
    assert abs(fuel_lph_from_injection(700, 155) - 2.335) < 0.05, fuel_lph_from_injection(700, 155)
    assert abs(fuel_lph_from_injection(1449, 590) - 18.28) < 0.3, fuel_lph_from_injection(1449, 590)
    assert fuel_lph_from_injection(1700, 1) < 0.1  # 断油滑行（原始计数 1 = 0.1 mg）
    assert fuel_lph_from_injection(0, 155) == 0.0
    # 解码链路：build_series 已把 155 换算成 15.5 mg/stroke，此处必须用 q_scale=1.0
    assert abs(fuel_lph_from_injection(700, 15.5, q_scale=1.0) - 2.335) < 0.05
    # 2) ISO-TP 单帧解析
    frames = pd.DataFrame({
        "seg": ["s#0", "s#0"], "t_s": [0.0, 0.012], "ts_ms": [0.0, 12.0],
        "can_id": [0x18DAF100, 0x18DAF100],
        "data": [bytes.fromhex("04 62 01 93 00"), bytes.fromhex("05 62 01 65 02 BD")],
    })
    resp = decode_iso_tp(frames, [0x18DAF100])
    assert list(resp["did"]) == [0x0193, 0x0165], resp
    assert resp["payload"].iloc[1].hex() == "02bd"
    # 3) 多帧重组（0xF802 VIN）
    body = b"\x62\xf8\x02" + b"LFNAFULXXN1E17982"
    total = len(body)
    ff = bytes([0x10 | (total >> 8), total & 0xFF]) + body[:6]
    rest = body[6:]
    cfs = []
    idx = 1
    while rest:
        chunk, rest = rest[:7], rest[7:]
        cfs.append(bytes([0x20 | idx]) + chunk)
        idx += 1
    mf_frames = pd.DataFrame({
        "seg": ["s#0"] * (1 + len(cfs)), "t_s": [0.0] * (1 + len(cfs)),
        "ts_ms": [0.0] * (1 + len(cfs)), "can_id": [0x18DAF100] * (1 + len(cfs)),
        "data": [ff] + cfs,
    })
    resp2 = decode_iso_tp(mf_frames, [0x18DAF100])
    assert resp2["did"].iloc[0] == 0xF802
    assert resp2["payload"].iloc[0].decode("ascii") == "LFNAFULXXN1E17982"
    # 4) 映射表可加载且 scale 校验通过
    doc = load_did_map()
    assert did_of("0x0165") in _entry_by_did(doc)
    print("selftest ok")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="J6L220 UDS 0x22 抓包解码（DID 反推表见 config/did_map_j6l220.json）")
    ap.add_argument("--input", type=Path, help="含 all_*.csv 的目录")
    ap.add_argument("--out", type=Path, default=ROOT / "outputs" / "obd_j6l220" / "decoded_points.csv")
    ap.add_argument("--summary", type=Path, default=ROOT / "outputs" / "obd_j6l220" / "summary.json")
    ap.add_argument("--series", type=Path, default=None, help="可选：逐 DID 序列落盘路径")
    ap.add_argument("--vehicle-id", default="TRK-J6L220-001")
    ap.add_argument("--ts-base", default=None, help="抓包起始时刻（ISO8601，带时区）；不给则 ts 留空并置 clock_unknown")
    ap.add_argument("--assume-0x0199-scale", type=float, default=None,
                    help="仅在已定标时给出（L/计数），否则 total_fuel_l 保持 null")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest or not args.input:
        selftest()
        if not args.input:
            return 0
    ts_base = pd.Timestamp(args.ts_base) if args.ts_base else None
    res = decode_directory(args.input, vehicle_id=args.vehicle_id, ts_base=ts_base,
                           assume_counter_scale=args.assume_0x0199_scale)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    res["points"].to_csv(args.out, index=False, encoding="utf-8-sig")
    if args.series:
        args.series.parent.mkdir(parents=True, exist_ok=True)
        res["series"].to_csv(args.series, index=False, encoding="utf-8-sig")
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    with open(args.summary, "w", encoding="utf-8") as fh:
        json.dump(res["summary"], fh, ensure_ascii=False, indent=2)
    print(json.dumps(res["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
