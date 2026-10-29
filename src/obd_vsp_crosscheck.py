# -*- coding: utf-8 -*-
"""VSP 模型 × OBD 碳平衡实测：**对照验证轨**。

判据（老师原话，2026-10-29 登记）
--------------------------------
    「你测的和这些估的能不能对得上？对不上，那估的可能就不用了。」

本模块只做**对照**，回答上面这句话：

    · **实测轨**：碳平衡法（燃料守恒法）—— OBD 只测油耗，碳由守恒系数推出；
      出自 ``src/obd_gps.py``，结果表 ``obd_points.parquet``。
    · **模型轨**：VSP（速度 / 车型 / 载重）—— 出自 ``src/factor_model.py``；
      本模块提供两种积分口径：
        - ``profile``：**速度剖面积分**（逐点速度 → VSP → 排放率 → 时间积分）
        - ``avg``：**平均速度法**（工程侧现行口径：平均速度 → VSP → 平均排放率）
      两者之差 = 丢掉瞬态（怠速/加速/减速）的代价，即 ``docs/57`` G1 的量化值。

载重口径
--------
VSP 的排放因子按**载重**取（``factor_model.LOAD_ANCHORS`` / ``FACTOR_ANCHORS``）。
真实设备若上报 ``load_kg``，按实测载重取因子；
**没有载重时不做假设**，而是**扫描全部载重锚点**，输出"模型在哪个载重档下能与实测对上"——
这正是老师那句话的实质：**对不上，多半是因为载重对不上**。

因此本模块的输出**永远带 ``load_basis`` 标注**（``measured`` / ``scan``），
扫描得到的最佳档**不构成**"可用于兜底"的结论，只构成"缺载重时模型与实测的关系"。

纪律
----
* **不改任何既有口径**：不动 E₀ 主账、不动 28 条闸门、不改 OBD 守恒法、不改 VSP 因子表；
* **不写碳账**：与碳会计线无关；
* 结果表单独落在 ``data/processed/`` 下，前端只读。

用法::

    python -m src.obd_vsp_crosscheck            # 导出结果表
    python -m src.obd_vsp_crosscheck --no-write # 只打印摘要
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import factor_model, schema  # noqa: E402

PROCESSED = ROOT / "data" / "processed"
POINTS_PATH = PROCESSED / schema.RESULT_FILES["obd_points"]
TRIPS_PATH = PROCESSED / schema.RESULT_FILES["obd_vsp_crosscheck"]
POINTS_CMP_PATH = PROCESSED / schema.RESULT_FILES["obd_vsp_crosscheck_points"]
SUMMARY_PATH = PROCESSED / schema.RESULT_FILES["obd_vsp_crosscheck_summary"]

#: 老师给的验收判据（原话，登记于此，页面与文档同源引用）
ACCEPTANCE_CRITERION = (
    "「你测的和这些估的能不能对得上？对不上，那估的可能就不用了。」"
    "—— 老师原话，2026-10-29"
)

#: 偏差分档阈值（**建议值，待负责人确认**）
TOLERANCE_OK_PCT = 15.0     # |偏差| ≤ 15%：可用于给未实测车辆兜底
TOLERANCE_WARN_PCT = 30.0   # 15%–30%：需修正；> 30%：不可用

#: 逐点载重覆盖率低于此值时，判定为"该趟没有载重输入"
LOAD_COVERAGE_MIN = 0.8

#: 速度下限（km/h）。≤ 1 km/h 视为停车/怠速，VSP 取下限值（与工程侧口径一致）
MIN_SPEED_KMH = 1.0
MAX_SPEED_KMH = 130.0


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _save_parquet(frame: pd.DataFrame, path: Path, tries: int = 4) -> None:
    import time

    path.parent.mkdir(parents=True, exist_ok=True)
    last: Optional[Exception] = None
    for attempt in range(tries):
        try:
            frame.to_parquet(path, index=False)
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.4 * (attempt + 1))
    raise RuntimeError("写 parquet 失败 %s：%s" % (path.name, last))


def _save_json(payload: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")


# ---------------------------------------------------------------------------
# 模型轨：VSP 两种积分口径
# ---------------------------------------------------------------------------

def _speed_series(points: pd.DataFrame) -> pd.Series:
    """取报表车速：优先 OBD 车速，缺失时退 GPS 车速。"""
    for column in ("vehicle_speed_kmh", "gps_speed_kmh"):
        if column in points.columns:
            speed = pd.to_numeric(points[column], errors="coerce")
            if speed.notna().any():
                return speed.fillna(
                    pd.to_numeric(points.get("gps_speed_kmh"), errors="coerce"))
    return pd.Series(np.nan, index=points.index, dtype=float)


def vsp_profile_kg(points: pd.DataFrame, factor_gkm: float) -> Dict[str, Any]:
    """**速度剖面积分**：逐点速度 → VSP → 排放率 → 按 dt 积分。

    公式与 ``factor_model.attach_carbon_columns`` 的物理轨同源，只是把
    「平均速度算一次」换成「逐点算再积分」：

        er(v) = factor_gkm / 72 * mult(vsp(v))
        kg   = Σ er(v_i) * dt_i / 1000
    """
    dt = pd.to_numeric(points.get("dt_s"), errors="coerce").fillna(0.0)
    speed = _speed_series(points)
    v = speed.where(speed > MIN_SPEED_KMH, MIN_SPEED_KMH).clip(MIN_SPEED_KMH, MAX_SPEED_KMH)
    vsp = factor_model.vsp_from_speed(v.to_numpy())
    mult = np.interp(vsp, factor_model.VSP_X, factor_model.VSP_MULT)
    er = float(factor_gkm) / 72.0 * mult
    kg = float((er * dt.to_numpy()).sum() / 1000.0)
    seconds = float(dt[dt > 0].sum())
    return {
        "kg": kg,
        "er_mean": float(np.average(er, weights=dt.to_numpy())) if seconds > 0 else float("nan"),
        "seconds": seconds,
        "v_bar": None,
        "er": er,
        "mult": mult,
        "dt": dt.to_numpy(),
    }


def vsp_average_kg(points: pd.DataFrame, factor_gkm: float) -> Dict[str, Any]:
    """**平均速度法**（工程侧现行口径）：平均速度 → VSP → 平均排放率 × 总时长。"""
    dt = pd.to_numeric(points.get("dt_s"), errors="coerce").fillna(0.0)
    dist = pd.to_numeric(points.get("dist_step_km"), errors="coerce").fillna(0.0)
    speed = _speed_series(points)
    seconds = float(dt[dt > 0].sum())
    total_dist = float(dist.sum())
    if seconds <= 0 or total_dist <= 0:
        return {"kg": float("nan"), "er_mean": float("nan"), "seconds": seconds,
                "v_bar": float("nan"), "er": np.array([]), "mult": np.array([]),
                "dt": dt.to_numpy()}
    v_bar = total_dist / (seconds / 3600.0)
    v_bar = float(min(max(v_bar, MIN_SPEED_KMH), MAX_SPEED_KMH))
    vsp = float(factor_model.vsp_from_speed(v_bar))
    mult = float(np.interp(vsp, factor_model.VSP_X, factor_model.VSP_MULT))
    er = float(factor_gkm) / 72.0 * mult
    kg = er * (total_dist / v_bar) * factor_model.SECONDS_PER_HOUR / 1000.0
    return {"kg": kg, "er_mean": er, "seconds": seconds, "v_bar": v_bar,
            "er": np.array([]), "mult": np.array([]), "dt": dt.to_numpy()}


# ---------------------------------------------------------------------------
# 载重
# ---------------------------------------------------------------------------

def _load_tonnes(points: pd.DataFrame) -> Tuple[Optional[float], float]:
    """返回 (该趟载重 t, 逐点覆盖率)。没有载重输入时返回 (None, 覆盖率)。"""
    if "load_kg" not in points.columns:
        return None, 0.0
    load = pd.to_numeric(points["load_kg"], errors="coerce")
    valid = load.notna() & (load > 0)
    coverage = float(valid.sum()) / float(len(points)) if len(points) else 0.0
    if coverage < LOAD_COVERAGE_MIN:
        return None, coverage
    return float(load[valid].mean()) / 1000.0, coverage


def _load_anchors() -> List[Tuple[float, float]]:
    """模型因子表的载重锚点档：(载重 t, 因子 g/km)。"""
    return [(float(load), float(factor))
            for load, factor in zip(factor_model.LOAD_ANCHORS,
                                    factor_model.FACTOR_ANCHORS)]


# ---------------------------------------------------------------------------
# 对拍
# ---------------------------------------------------------------------------

def build_crosscheck(points: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """逐趟 × 载重档的模型 vs 实测偏差表 + 最佳档的逐点对照表。"""
    if points is None or points.empty:
        return pd.DataFrame(), pd.DataFrame()
    trip_rows: List[Dict[str, Any]] = []
    point_rows: List[Dict[str, Any]] = []
    keys = [c for c in ("vehicle_id", "trip_id") if c in points.columns]
    for key, sub in points.groupby(keys, sort=True):
        sub = sub.sort_values("ts") if "ts" in sub.columns else sub
        key_tuple = key if isinstance(key, tuple) else (key,)
        vehicle_id = str(key_tuple[0])
        trip_id = key_tuple[1] if len(key_tuple) > 1 else 0

        dt = pd.to_numeric(sub.get("dt_s"), errors="coerce").fillna(0.0)
        dist = pd.to_numeric(sub.get("dist_step_km"), errors="coerce").fillna(0.0)
        kg_measured = float(pd.to_numeric(sub.get("kg_co2"), errors="coerce").fillna(0.0).sum())
        fuel_l = float(pd.to_numeric(sub.get("fuel_l_step"), errors="coerce").fillna(0.0).sum())
        er_measured = pd.to_numeric(sub.get("er_gs"), errors="coerce")
        load_t, load_coverage = _load_tonnes(sub)

        # 载重档：有实测载重就用实测的；否则扫描全部锚点档
        if load_t is not None:
            candidates = [(load_t, float(factor_model.factor_lookup(load_t))),
                          ("measured", load_t)]
            load_basis = "measured"
        else:
            candidates = [(load, factor) for load, factor in _load_anchors()]
            load_basis = "scan"

        best = None
        for load_value, factor_gkm in candidates:
            profile = vsp_profile_kg(sub, factor_gkm)
            average = vsp_average_kg(sub, factor_gkm)
            delta_profile = ((profile["kg"] - kg_measured) / kg_measured * 100.0
                             if kg_measured > 0 else float("nan"))
            delta_average = ((average["kg"] - kg_measured) / kg_measured * 100.0
                              if kg_measured > 0 else float("nan"))
            row = {
                "vehicle_id": vehicle_id,
                "trip_id": trip_id,
                "load_basis": load_basis,
                "load_t": load_value,
                "load_coverage": round(load_coverage, 4),
                "factor_gkm": round(float(factor_gkm), 4),
                "factor_version": factor_model.FACTOR_VERSION,
                "dist_km": round(float(dist.sum()), 4),
                "fuel_l": round(fuel_l, 4),
                "duration_s": round(float(dt[dt > 0].sum()), 2),
                "kg_measured": round(kg_measured, 6),
                "kg_vsp_profile": round(float(profile["kg"]), 6),
                "kg_vsp_avg": round(float(average["kg"]), 6),
                "delta_pct_profile": round(float(delta_profile), 4),
                "delta_pct_avg": round(float(delta_average), 4),
                "er_gs_measured_mean": round(
                    float(er_measured.mean()), 4) if er_measured.notna().any() else float("nan"),
                "er_gs_model_mean": round(float(profile["er_mean"]), 4),
                "co2_gkm_measured": round(
                    kg_measured * 1000.0 / float(dist.sum()), 3)
                if float(dist.sum()) > 0 else float("nan"),
                "v_bar_kmh": round(float(average["v_bar"]), 3)
                if average["v_bar"] is not None else float("nan"),
            }
            trip_rows.append(row)
            if best is None or abs(delta_profile) < abs(best[1]):
                best = (row, float(delta_profile), profile, sub)

        # 最佳档的逐点对照（供前端画「实测 ER vs 模型 ER」）
        if best is not None:
            row, _delta, profile, sub = best
            speed = _speed_series(sub)
            for index, (ts, sp, er_m, er_mo, dt_i) in enumerate(zip(
                    sub.get("ts", pd.Series([None] * len(sub))),
                    speed.to_numpy(),
                    pd.to_numeric(sub.get("er_gs"), errors="coerce").to_numpy(),
                    profile["er"], profile["dt"])):
                point_rows.append({
                    "vehicle_id": vehicle_id,
                    "trip_id": trip_id,
                    "ts": ts,
                    "speed_kmh": round(float(sp), 3) if sp == sp else None,
                    "dt_s": round(float(dt_i), 2),
                    "load_t": row["load_t"],
                    "factor_gkm": row["factor_gkm"],
                    "er_gs_measured": None if er_m != er_m else round(float(er_m), 4),
                    "er_gs_model": round(float(er_mo), 4),
                })

    trips = pd.DataFrame(trip_rows)
    points_cmp = pd.DataFrame(point_rows)
    return trips, points_cmp


def build_summary(trips: pd.DataFrame, is_demo: bool) -> Dict[str, Any]:
    """汇总 + 判据结论。"""
    if trips is None or trips.empty:
        return {"criterion": ACCEPTANCE_CRITERION, "rows": 0}
    best_rows: List[Dict[str, Any]] = []
    for (vehicle_id, trip_id), sub in trips.groupby(["vehicle_id", "trip_id"], sort=True):
        best = sub.iloc[sub["delta_pct_profile"].abs().argmin()]
        best_rows.append(best.to_dict())
    best = pd.DataFrame(best_rows)

    def _verdict(delta: float) -> str:
        if delta != delta:
            return "无法判定"
        if abs(delta) <= TOLERANCE_OK_PCT:
            return "可用于兜底"
        if abs(delta) <= TOLERANCE_WARN_PCT:
            return "需修正"
        return "不可用"

    measured_total = float(best["kg_measured"].sum())
    model_total = float(best["kg_vsp_profile"].sum())
    avg_total = float(best["kg_vsp_avg"].sum())
    overall = ((model_total - measured_total) / measured_total * 100.0
               if measured_total > 0 else float("nan"))
    overall_avg = ((avg_total - measured_total) / measured_total * 100.0
                   if measured_total > 0 else float("nan"))
    profile_gain = ((model_total - avg_total) / avg_total * 100.0
                    if avg_total > 0 else float("nan"))

    per_vehicle = []
    for vehicle_id, sub in best.groupby("vehicle_id", sort=True):
        measured = float(sub["kg_measured"].sum())
        model = float(sub["kg_vsp_profile"].sum())
        delta = ((model - measured) / measured * 100.0
                 if measured > 0 else float("nan"))
        per_vehicle.append({
            "vehicle_id": vehicle_id,
            "load_basis": str(sub["load_basis"].iloc[0]),
            "load_t": float(sub["load_t"].iloc[0]),
            "factor_gkm": float(sub["factor_gkm"].iloc[0]),
            "dist_km": round(float(sub["dist_km"].sum()), 3),
            "kg_measured": round(measured, 4),
            "kg_vsp_profile": round(model, 4),
            "delta_pct_profile": round(delta, 4),
            "kg_vsp_avg": round(float(sub["kg_vsp_avg"].sum()), 4),
            "delta_pct_avg": round(
                ((float(sub["kg_vsp_avg"].sum()) - measured) / measured * 100.0
                 if measured > 0 else float("nan")), 4),
            "verdict": _verdict(delta),
        })

    load_basis = "scan" if (best["load_basis"] == "scan").any() else "measured"
    return {
        "generated_at": _now(),
        "criterion": ACCEPTANCE_CRITERION,
        "is_demo": is_demo,
        "factor_version": factor_model.FACTOR_VERSION,
        "factor_source": factor_model.FACTOR_SOURCE,
        "vsp_version_note": "VSP 曲线 = factor_model.VSP_X/VSP_MULT（5 点代理，来源文献见 factor_source）",
        "tolerance_pct": {"ok": TOLERANCE_OK_PCT, "warn": TOLERANCE_WARN_PCT,
                          "note": "建议值，待负责人确认"},
        "load_basis": load_basis,
        "load_basis_note": (
            "载重为**扫描假设**（模型因子表各载重锚点档中取偏差最小的一档）。"
            "真实设备上报 load_kg 后按实测载重取因子，本项自动变为 measured。"
            "**扫描得到的最佳档不构成「可用于兜底」的结论** —— 缺载重时模型与实测的关系，"
            "只能说明「对不上多半是因为载重对不上」。")
        if load_basis == "scan" else
        "载重取自设备读数 load_kg（按该趟实测载重取因子）。",
        "totals": {
            "kg_measured": round(measured_total, 4),
            "kg_vsp_profile": round(model_total, 4),
            "kg_vsp_avg": round(avg_total, 4),
            "delta_pct_profile": round(overall, 4),
            "delta_pct_avg": round(overall_avg, 4),
            "profile_vs_avg_pct": round(profile_gain, 4),
            "dist_km": round(float(best["dist_km"].sum()), 3),
        },
        "profile_gain_note": (
            "速度剖面积分比平均速度法高出的比例 —— 这就是丢掉瞬态"
            "（怠速/加速/减速）的代价，即 docs/57 G1 的量化值。"),
        "verdict": _verdict(overall),
        "per_vehicle": per_vehicle,
        "boundary": (
            "本轨是**对照验证**，不是新口径：实测轨 = 碳平衡法（守恒法），"
            "模型轨 = VSP。两者互不替代、也不相加。"
            "结果表独立于 data/processed/ 的其他表，不进 E₀ 主账、不参与 28 条闸门。"),
    }


def run(out_dir: Optional[Path] = None, write: bool = True) -> Dict[str, Any]:
    """跑一遍对拍，导出结果表。"""
    if not POINTS_PATH.exists():
        raise FileNotFoundError(
            "OBD 逐点表不存在：%s（先跑 python -m src.obd_pipeline --demo）" % POINTS_PATH)
    points = pd.read_parquet(POINTS_PATH)
    summary_source: Dict[str, Any] = {}
    summary_path = PROCESSED / schema.RESULT_FILES["obd_summary"]
    if summary_path.exists():
        try:
            summary_source = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            summary_source = {}
    is_demo = bool(summary_source.get("is_demo"))

    trips, points_cmp = build_crosscheck(points)
    summary = build_summary(trips, is_demo)
    summary["trips_rows"] = int(len(trips))
    summary["points_rows"] = int(len(points_cmp))

    base = Path(out_dir) if out_dir else PROCESSED
    if write:
        _save_parquet(trips, base / schema.RESULT_FILES["obd_vsp_crosscheck"])
        _save_parquet(points_cmp, base / schema.RESULT_FILES["obd_vsp_crosscheck_points"])
        _save_json(summary, base / schema.RESULT_FILES["obd_vsp_crosscheck_summary"])
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="VSP 模型 × OBD 碳平衡实测 对照验证")
    parser.add_argument("--no-write", action="store_true",
                        help="只打印摘要，不写结果表")
    args = parser.parse_args(argv)
    try:
        summary = run(write=not args.no_write)
    except Exception as exc:  # noqa: BLE001
        print("[obd_vsp_crosscheck] 失败：%s" % exc)
        import traceback
        traceback.print_exc()
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
