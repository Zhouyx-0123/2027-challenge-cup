# -*- coding: utf-8 -*-
"""OBD 稀疏校准：在没有大量实测 OBD 的前提下，把论文先验校准到可解释的区间。

边界
----
1. 本模块不改 E0。主账 carbon_kg 仍来自油票/法定轨；这里只产出
   carbon_kg_calibrated 场景列和校准因子，供试点/答辩说明使用。
2. OBD 样本默认排除 is_demo=True；demo 只能验证管道，不能当实测证据。
3. 先验是论文 5 档速度 × 4 载重带；校准层把载重并成 light(<20t)/heavy(>=20t)，
   共 10 层。每层用经验贝叶斯收缩，样本少时自动退回先验。
4. 输出是“近似 95% 区间”，不是严格贝叶斯后验；文档中必须这样标注。
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import NormalDist
from typing import Any, Dict, Sequence, Tuple

import numpy as np
import pandas as pd

CALIBRATION_VERSION = "OBD-CAL-v1"
DIESEL_KG_CO2_PER_L = 2.63  # TTW，与主账 E0 同口径

SPEED_EDGES: Tuple[float, ...] = (0.0, 20.0, 40.0, 60.0, 80.0, float("inf"))
SPEED_LABELS: Tuple[str, ...] = ("<20", "20-40", "40-60", "60-80", ">=80")
LOAD_GROUP_EDGES: Tuple[float, ...] = (0.0, 20.0, 60.0)
LOAD_GROUP_LABELS: Tuple[str, ...] = ("light", "heavy")

PRIOR_STRENGTH_TRIPS = 30.0
PRIOR_CV = 0.15
MIN_CALIBRATION_TRIPS = 10
MIN_CALIBRATION_KM = 200.0
Z_95 = 1.959963984540054

PRIOR_COLUMNS = (
    "speed_bin", "load_group", "prior_trips", "prior_km",
    "prior_carbon_kg", "prior_ef_g_km",
)
CALIBRATION_COLUMNS = (
    "speed_bin", "load_group", "prior_trips", "prior_km", "prior_ef_g_km",
    "n_obs", "obs_km", "obs_ef_g_km", "obs_sd_g_km", "weight",
    "post_ef_g_km", "ci_low_g_km", "ci_high_g_km", "factor", "status",
)


def _as_bool(series: pd.Series) -> pd.Series:
    """把 True/False、0/1、true/false 字符串统一成 bool，避免 "False" 被当成 True。"""
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False).astype(bool)
    text = series.astype(str).str.strip().str.lower()
    return text.isin({"1", "true", "yes", "y", "t"})


def _bin_index(values: np.ndarray, edges: Sequence[float]) -> np.ndarray:
    inner = np.asarray(edges[1:-1], dtype=float)
    idx = np.searchsorted(inner, np.asarray(values, dtype=float), side="right")
    return np.clip(idx, 0, len(edges) - 2).astype(int)


def _clean_master(master: pd.DataFrame) -> pd.DataFrame:
    need = {"trip_id", "passed", "speed_kmh", "load_t", "distance_km", "carbon_kg"}
    missing = need - set(master.columns)
    if missing:
        raise ValueError("主表缺列：%s" % ", ".join(sorted(missing)))
    frame = master.copy()
    frame["passed"] = _as_bool(frame["passed"])
    for col in ("speed_kmh", "load_t", "distance_km", "carbon_kg"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame[
        frame["passed"]
        & np.isfinite(frame["speed_kmh"])
        & np.isfinite(frame["load_t"])
        & np.isfinite(frame["distance_km"])
        & np.isfinite(frame["carbon_kg"])
        & (frame["distance_km"] > 0)
        & (frame["carbon_kg"] >= 0)
    ].copy()
    return frame


def build_prior_table(master: pd.DataFrame) -> pd.DataFrame:
    """从趟级主表得到 5×2 先验表；EF 为里程加权，不是档位均值。"""
    frame = _clean_master(master)
    if frame.empty:
        raise ValueError("主表没有 passed 且数值有效的行")
    frame["_si"] = _bin_index(frame["speed_kmh"].to_numpy(float), SPEED_EDGES)
    frame["_gi"] = _bin_index(frame["load_t"].to_numpy(float), LOAD_GROUP_EDGES)
    grouped = frame.groupby(["_si", "_gi"], sort=False).agg(
        prior_trips=("trip_id", "size"),
        prior_km=("distance_km", "sum"),
        prior_carbon_kg=("carbon_kg", "sum"),
    )
    rows = []
    for si, speed_bin in enumerate(SPEED_LABELS):
        for gi, load_group in enumerate(LOAD_GROUP_LABELS):
            if (si, gi) in grouped.index:
                item = grouped.loc[(si, gi)]
                trips = int(item["prior_trips"])
                km = float(item["prior_km"])
                carbon = float(item["prior_carbon_kg"])
            else:
                trips, km, carbon = 0, 0.0, 0.0
            ef = carbon / km * 1000.0 if km > 0 else float("nan")
            rows.append({
                "speed_bin": speed_bin,
                "load_group": load_group,
                "prior_trips": trips,
                "prior_km": round(km, 6),
                "prior_carbon_kg": round(carbon, 6),
                "prior_ef_g_km": round(ef, 6) if math.isfinite(ef) else float("nan"),
            })
    return pd.DataFrame(rows, columns=list(PRIOR_COLUMNS))


def load_samples(samples: pd.DataFrame | str | Path) -> pd.DataFrame:
    """接受 DataFrame 或 CSV/Parquet；统一出 carbon_kg 与速度/载重分层列。"""
    if isinstance(samples, (str, Path)):
        path = Path(samples)
        frame = pd.read_parquet(path) if path.suffix.lower() == ".parquet" else pd.read_csv(path)
    else:
        frame = samples.copy()
    if frame.empty:
        frame = pd.DataFrame(columns=["speed_kmh", "load_t", "distance_km", "carbon_kg", "is_demo"])
    for col in ("speed_kmh", "load_t", "distance_km"):
        if col not in frame.columns:
            raise ValueError("OBD 样本缺列：%s" % col)
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    if "carbon_kg" not in frame.columns:
        if "fuel_l" not in frame.columns:
            raise ValueError("OBD 样本需要 carbon_kg 或 fuel_l")
        frame["carbon_kg"] = pd.to_numeric(frame["fuel_l"], errors="coerce") * DIESEL_KG_CO2_PER_L
    frame["carbon_kg"] = pd.to_numeric(frame["carbon_kg"], errors="coerce")
    frame["is_demo"] = _as_bool(frame["is_demo"]) if "is_demo" in frame.columns else False
    frame = frame[
        np.isfinite(frame["speed_kmh"])
        & np.isfinite(frame["load_t"])
        & np.isfinite(frame["distance_km"])
        & np.isfinite(frame["carbon_kg"])
        & (frame["distance_km"] > 0)
        & (frame["carbon_kg"] >= 0)
    ].copy()
    frame["_si"] = _bin_index(frame["speed_kmh"].to_numpy(float), SPEED_EDGES)
    frame["_gi"] = _bin_index(frame["load_t"].to_numpy(float), LOAD_GROUP_EDGES)
    frame["_ef"] = frame["carbon_kg"] / frame["distance_km"] * 1000.0
    return frame


def calibrate(prior: pd.DataFrame, samples: pd.DataFrame | str | Path = None, *,
              prior_strength: float = PRIOR_STRENGTH_TRIPS,
              prior_cv: float = PRIOR_CV,
              min_trips: int = MIN_CALIBRATION_TRIPS,
              min_km: float = MIN_CALIBRATION_KM,
              include_demo: bool = False) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """按 10 层做经验贝叶斯收缩；无样本时因子恒为 1。"""
    if prior_strength <= 0:
        raise ValueError("prior_strength 必须为正")
    if prior_cv <= 0:
        raise ValueError("prior_cv 必须为正")
    table = prior.copy()
    if not set(PRIOR_COLUMNS).issubset(table.columns):
        raise ValueError("prior 表缺列")
    samples = load_samples(samples) if samples is not None else load_samples(pd.DataFrame())
    n_input = int(len(samples))
    n_demo = int(samples["is_demo"].sum()) if n_input else 0
    used = samples if include_demo else samples[~samples["is_demo"]]
    used = used.copy()

    rows = []
    for _, row in table.iterrows():
        si = SPEED_LABELS.index(str(row["speed_bin"]))
        gi = LOAD_GROUP_LABELS.index(str(row["load_group"]))
        prior_ef = float(row["prior_ef_g_km"])
        cell = used[(used["_si"] == si) & (used["_gi"] == gi)]
        n = int(len(cell))
        if n:
            obs_km = float(cell["distance_km"].sum())
            obs_carbon = float(cell["carbon_kg"].sum())
            obs_ef = obs_carbon / obs_km * 1000.0 if obs_km > 0 else float("nan")
            obs_sd = float(cell["_ef"].std(ddof=1)) if n > 1 else float("nan")
        else:
            obs_km, obs_ef, obs_sd = 0.0, float("nan"), float("nan")

        if not math.isfinite(prior_ef):
            post_ef, factor, status = float("nan"), 1.0, "prior_missing"
            ci_low = ci_high = float("nan")
            weight = 0.0
        elif n == 0:
            post_ef, factor, status = prior_ef, 1.0, "prior_only"
            ci_low = prior_ef * (1.0 - Z_95 * prior_cv)
            ci_high = prior_ef * (1.0 + Z_95 * prior_cv)
            weight = 0.0
        else:
            weight = n / (n + prior_strength)
            prior_sd = prior_cv * prior_ef
            obs_se = (obs_sd / math.sqrt(n)) if (n > 1 and math.isfinite(obs_sd)) else prior_sd
            post_ef = weight * obs_ef + (1.0 - weight) * prior_ef
            post_se = math.sqrt((weight * obs_se) ** 2 + ((1.0 - weight) * prior_sd) ** 2)
            ci_low = max(0.0, post_ef - Z_95 * post_se)
            ci_high = post_ef + Z_95 * post_se
            factor = post_ef / prior_ef if prior_ef > 0 else 1.0
            if n < min_trips or obs_km < min_km:
                status = "provisional"
            else:
                status = "calibrated"
            if include_demo and n_demo == n_input and n_input > 0:
                status = "demo_fixture"

        rows.append({
            "speed_bin": row["speed_bin"],
            "load_group": row["load_group"],
            "prior_trips": int(row["prior_trips"]),
            "prior_km": float(row["prior_km"]),
            "prior_ef_g_km": prior_ef,
            "n_obs": n,
            "obs_km": round(obs_km, 6),
            "obs_ef_g_km": obs_ef,
            "obs_sd_g_km": obs_sd,
            "weight": round(weight, 6),
            "post_ef_g_km": post_ef,
            "ci_low_g_km": ci_low,
            "ci_high_g_km": ci_high,
            "factor": round(float(factor), 6),
            "status": status,
        })

    out = pd.DataFrame(rows, columns=list(CALIBRATION_COLUMNS))
    prior_carbon = float(table["prior_carbon_kg"].sum())
    factors = out["factor"].to_numpy(float)
    prior_carbon_values = table["prior_carbon_kg"].to_numpy(float)
    weighted = float(np.nansum(factors * prior_carbon_values) / prior_carbon) if prior_carbon > 0 else 1.0
    audit = {
        "version": CALIBRATION_VERSION,
        "does_not_change_e0": True,
        "include_demo": bool(include_demo),
        "demo_only": bool(n_input > 0 and n_demo == n_input),
        "n_samples_input": n_input,
        "n_samples_used": int(len(used)),
        "n_samples_demo_excluded": 0 if include_demo else n_demo,
        "n_prior_rows": int(len(table)),
        "n_calibrated_cells": int((out["status"] == "calibrated").sum()),
        "n_provisional_cells": int((out["status"] == "provisional").sum()),
        "n_prior_only_cells": int((out["status"].isin(["prior_only", "prior_missing"])).sum()),
        "weighted_factor": round(weighted, 6),
        "notes": [
            "OBD 是验证/校准轨，不是主账前提；carbon_kg 不变。",
            "demo 样本默认剔除，只能验证管道，不能当实测证据。",
            "区间为近似 95%，不是严格贝叶斯后验。",
        ],
    }
    return out, audit


def required_sample_size(cv: float = 0.25, margin: float = 0.10,
                         confidence: float = 0.95) -> int:
    """每层所需样本量 n=(z*cv/margin)^2；cv 是层内 EF 变异系数。"""
    if not (0 < confidence < 1):
        raise ValueError("confidence 必须在 (0,1)")
    if cv <= 0 or margin <= 0:
        raise ValueError("cv/margin 必须为正")
    z = NormalDist().inv_cdf(0.5 + confidence / 2.0)
    return int(math.ceil((z * cv / margin) ** 2))


def allocate_samples(prior: pd.DataFrame, total: int = 250, *,
                     cv: float = 0.25, margin: float = 0.10,
                     mode: str = "balanced") -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """分配试点样本。

    mode="balanced"：10 层均分，250 单=每层 25 单，60 单=每层 6 单；
    mode="carbon"：按先验碳量比例，适合预算受限时优先校准高碳层，
    但低碳层可能只分到 2 单，仍停留在 provisional。
    """
    if total <= 0:
        raise ValueError("total 必须为正")
    if mode not in {"balanced", "carbon"}:
        raise ValueError("mode 只能是 balanced 或 carbon")
    frame = prior.copy()
    if "prior_carbon_kg" not in frame.columns:
        raise ValueError("prior 表缺 prior_carbon_kg")
    n_cells = len(frame)
    if total < n_cells:
        raise ValueError("total 太小，无法覆盖 %d 层" % n_cells)
    carbon = frame["prior_carbon_kg"].to_numpy(float)
    if carbon.sum() <= 0:
        share = np.full(n_cells, 1.0 / n_cells)
    else:
        share = carbon / carbon.sum()
    target = required_sample_size(cv=cv, margin=margin)

    if mode == "balanced":
        base = total // n_cells
        alloc = np.full(n_cells, base, dtype=int)
        remainder = int(total - base * n_cells)
        order = np.argsort(-share) if carbon.sum() > 0 else np.arange(n_cells)
        alloc[order[:remainder]] += 1
        min_each = base
    else:
        min_each = 2 if total >= 2 * n_cells else 1
        raw = share * total
        alloc = np.maximum(min_each, np.floor(raw).astype(int))
        while alloc.sum() < total:
            rem = raw - np.floor(raw)
            order = np.argsort(-rem)
            for idx in order:
                if alloc.sum() >= total:
                    break
                alloc[idx] += 1
        while alloc.sum() > total:
            order = np.argsort(alloc - min_each)[::-1]
            for idx in order:
                if alloc.sum() <= total:
                    break
                if alloc[idx] > min_each:
                    alloc[idx] -= 1

    frame["carbon_share"] = np.round(share, 6)
    frame["target_n"] = target
    frame["allocated_n"] = alloc.astype(int)
    frame["margin_at_alloc"] = [
        Z_95 * cv / math.sqrt(int(n)) if n > 0 else float("nan") for n in alloc
    ]
    audit = {
        "version": CALIBRATION_VERSION,
        "total": int(total),
        "n_cells": n_cells,
        "min_each": min_each,
        "target_per_cell": target,
        "cv": cv,
        "margin_target": margin,
        "allocated": int(alloc.sum()),
        "allocation_mode": mode,
        "notes": [
            "目标样本量按每层 95% 置信、相对误差 margin 估算；25 单对应 CV=25%、margin=10%。",
            "balanced：250 单=每层 25 单；60 单=每层 6 单，只够看方差，不够定稿。",
            "carbon：按先验碳量比例，高碳层优先；低碳层可能只有 2 单，仍属 provisional。",
        ],
    }
    return frame, audit


def apply_calibration(master: pd.DataFrame, table: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """把 10 层因子乘到趟碳上；原 carbon_kg 一字不改。"""
    need = {"trip_id", "passed", "speed_kmh", "load_t", "carbon_kg"}
    missing = need - set(master.columns)
    if missing:
        raise ValueError("主表缺列：%s" % ", ".join(sorted(missing)))
    if not {"speed_bin", "load_group", "factor"}.issubset(table.columns):
        raise ValueError("校准表缺列")
    out = master.copy()
    out["calibration_speed_bin"] = pd.Series([pd.NA] * len(out), dtype="object")
    out["calibration_load_group"] = pd.Series([pd.NA] * len(out), dtype="object")
    out["calibration_factor"] = np.nan
    out["carbon_kg_calibrated"] = np.nan
    out["calibration_status"] = "invalid"

    passed = _as_bool(out["passed"])
    speed = pd.to_numeric(out["speed_kmh"], errors="coerce").to_numpy(float)
    load = pd.to_numeric(out["load_t"], errors="coerce").to_numpy(float)
    carbon = pd.to_numeric(out["carbon_kg"], errors="coerce").to_numpy(float)
    si = _bin_index(np.nan_to_num(speed, nan=0.0), SPEED_EDGES)
    gi = _bin_index(np.nan_to_num(load, nan=0.0), LOAD_GROUP_EDGES)
    speed_labels = np.asarray(SPEED_LABELS, dtype=object)[si]
    load_labels = np.asarray(LOAD_GROUP_LABELS, dtype=object)[gi]
    factor_map = {
        (str(r["speed_bin"]), str(r["load_group"])): float(r["factor"])
        for _, r in table.iterrows() if math.isfinite(float(r["factor"]))
    }
    status_map = {
        (str(r["speed_bin"]), str(r["load_group"])): str(r["status"])
        for _, r in table.iterrows()
    }
    valid = passed & np.isfinite(speed) & np.isfinite(load) & np.isfinite(carbon) & (carbon >= 0)
    for i in np.where(valid)[0]:
        key = (speed_labels[i], load_labels[i])
        factor = factor_map.get(key, 1.0)
        out.at[i, "calibration_speed_bin"] = speed_labels[i]
        out.at[i, "calibration_load_group"] = load_labels[i]
        out.at[i, "calibration_factor"] = factor
        out.at[i, "carbon_kg_calibrated"] = float(carbon[i]) * factor
        out.at[i, "calibration_status"] = status_map.get(key, "prior_only")

    prior_total = float(pd.to_numeric(out.loc[valid, "carbon_kg"], errors="coerce").sum())
    cal_total = float(pd.to_numeric(out.loc[valid, "carbon_kg_calibrated"], errors="coerce").sum())
    delta = cal_total - prior_total
    audit = {
        "version": CALIBRATION_VERSION,
        "does_not_change_e0": True,
        "n_rows": int(len(out)),
        "n_passed": int(valid.sum()),
        "n_invalid": int((~valid).sum()),
        "carbon_prior_kg": round(prior_total, 6),
        "carbon_calibrated_kg": round(cal_total, 6),
        "delta_kg": round(delta, 6),
        "delta_pct": round(delta / prior_total * 100.0, 6) if prior_total > 0 else 0.0,
        "status_counts": out.loc[valid, "calibration_status"].value_counts().to_dict(),
        "notes": [
            "carbon_kg 未改动；carbon_kg_calibrated 只是场景列。",
            "invalid 行不参与校准，状态标 invalid，不写 0。",
        ],
    }
    return out, audit


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OBD 稀疏校准 / 试点样本量规划")
    parser.add_argument("--master", default="data/processed/jd_segment_master.parquet")
    parser.add_argument("--samples", default=None, help="OBD 段级样本 CSV/Parquet；缺省则只做先验与试点规划")
    parser.add_argument("--out", default="outputs/obd_calibration")
    parser.add_argument("--total", type=int, default=250, help="试点总样本量")
    parser.add_argument("--mode", choices=("balanced", "carbon"), default="balanced",
                        help="balanced=每层均分；carbon=按先验碳量比例")
    parser.add_argument("--include-demo", action="store_true", help="把 is_demo=True 也纳入（仅管道验证）")
    args = parser.parse_args(argv)

    master = pd.read_parquet(args.master)
    prior = build_prior_table(master)
    samples = None
    if args.samples:
        samples = load_samples(args.samples)
    table, audit = calibrate(prior, samples, include_demo=args.include_demo)
    plan, plan_audit = allocate_samples(prior, total=args.total, mode=args.mode)
    applied, apply_audit = apply_calibration(master, table)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    prior.to_csv(out / "prior_5x2.csv", index=False, encoding="utf-8-sig")
    table.to_csv(out / "calibration_5x2.csv", index=False, encoding="utf-8-sig")
    plan.to_csv(out / "pilot_allocation.csv", index=False, encoding="utf-8-sig")
    applied[["trip_id", "carbon_kg", "carbon_kg_calibrated", "calibration_factor",
             "calibration_status"]].to_parquet(out / "trip_carbon_calibrated.parquet", index=False)
    payload = {"calibration": audit, "pilot": plan_audit, "apply": apply_audit}
    (out / "audit.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
