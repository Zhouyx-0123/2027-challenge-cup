#!/usr/bin/env python3
"""Build the HYC physical-trip audit layer from D:\\vsp_delivery.

This is an AUDIT layer, not the main E0. It never overwrites baseline_carbon.parquet.
Run:
    python -m tools.build_vsp_audit --src D:\\vsp_delivery
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
BASELINE = PROCESSED / "baseline_carbon.parquet"
DEFAULT_SRC = Path(r"D:\vsp_delivery")

HYC_COLS = [
    "trip_id", "waybill", "customer", "plate", "vtype", "vtype_name", "km",
    "dep", "arr", "o", "d", "n_assoc", "vtype_mode", "fuel", "group", "ef",
    "e_kg", "hours", "speed", "speed_flag", "band", "month", "e_t",
]


def _physical_key(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["plate_s"] = df["plate"].astype(str).str.strip()
    df["dist_s"] = pd.to_numeric(df["km"], errors="coerce").round(2)
    df["dep_s"] = pd.to_datetime(df["dep"], errors="coerce").astype("string")
    df["arr_s"] = pd.to_datetime(df["arr"], errors="coerce").astype("string")
    df["o_s"] = df["o"].astype(str).str.strip()
    df["d_s"] = df["d"].astype(str).str.strip()
    return df


def _baseline_key(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["plate_s"] = df["plate"].astype(str).str.strip()
    df["dist_s"] = pd.to_numeric(df["dist_km"], errors="coerce").round(2)
    df["dep_s"] = pd.to_datetime(df["depart_ts"], errors="coerce").astype("string")
    df["arr_s"] = pd.to_datetime(df["arrive_ts"], errors="coerce").astype("string")
    df["o_s"] = df["dep_site"].astype(str).str.strip()
    df["d_s"] = df["arr_site"].astype(str).str.strip()
    return df


KEYS = ["plate_s", "dist_s", "dep_s", "arr_s", "o_s", "d_s"]


def build(src: Path = DEFAULT_SRC, out: Path = PROCESSED) -> dict:
    src = Path(src)
    model = src / "improved_model.xlsx"
    if not model.exists():
        raise FileNotFoundError(f"missing {model}")
    if not BASELINE.exists():
        raise FileNotFoundError(f"missing {BASELINE}")

    b = _baseline_key(pd.read_parquet(BASELINE))
    h = pd.read_excel(model, sheet_name=0)
    if len(h.columns) != len(HYC_COLS):
        raise ValueError(f"unexpected HYC columns: {len(h.columns)}")
    h.columns = HYC_COLS
    h = _physical_key(h)

    # Current waybill-association口径
    current_rows = int(len(b))
    current_km = float(b["dist_km"].sum())
    current_t = float(b["kg_co2"].sum() / 1000.0)

    # Current physical-trip口径: first row per physical key, current factors
    first = b.drop_duplicates(KEYS, keep="first").copy()
    physical_trips = int(len(first))
    physical_km = float(first["dist_km"].sum())
    physical_t = float(first["kg_co2"].sum() / 1000.0)

    # Our own VSP physical track (same physical trips; no HYC factors)
    vsp_t = float(first["kg_co2_phys"].sum() / 1000.0)
    vsp_all_t = float(b["kg_co2_phys"].sum() / 1000.0)

    # HYC physical口径
    hyc_trips = int(len(h))
    hyc_km = float(h["km"].sum())
    hyc_t = float(h["e_kg"].sum() / 1000.0)

    # HYC factors applied to current association rows (auxiliary)
    hf = h[KEYS + ["ef"]].copy()
    aux = b.merge(hf, on=KEYS, how="left")
    if aux["ef"].isna().any():
        raise ValueError(f"unmatched HYC factors: {int(aux['ef'].isna().sum())}")
    hyc_aux_t = float((pd.to_numeric(aux["dist_km"], errors="coerce") * pd.to_numeric(aux["ef"], errors="coerce") / 1_000_000.0).sum())

    duplicate_effect = current_t - physical_t
    factor_effect = physical_t - hyc_t
    net_delta = current_t - hyc_t
    vsp_effect = vsp_t - physical_t

    # group summary from HYC physical trips
    g = h.groupby("group", dropna=False).agg(
        trips=("trip_id", "count"),
        km=("km", "sum"),
        e_kg=("e_kg", "sum"),
        ef_mean=("ef", "mean"),
    ).reset_index()
    g["share_pct"] = g["e_kg"] / g["e_kg"].sum() * 100.0
    g = g.rename(columns={"group": "车型组", "trips": "物理行程", "km": "里程_km",
                          "e_kg": "碳排_kg", "ef_mean": "平均因子_g_km",
                          "share_pct": "碳排占比_pct"})

    # month reconciliation
    month_rows = []
    for m in [1, 2, 3, 11, 12]:
        cm = b[b["month"] == m]
        pm = first[first["month"] == m]
        hm = h[h["month"] == f"2023-{m:02d}"]
        month_rows.append({
            "月份": f"2023-{m:02d}",
            "当前关联行": int(len(cm)),
            "物理行程": int(len(pm)),
            "当前关联_t": round(float(cm["kg_co2"].sum() / 1000.0), 2),
            "物理口径_t": round(float(pm["kg_co2"].sum() / 1000.0), 2),
            "HYC_t": round(float(hm["e_kg"].sum() / 1000.0), 2),
        })

    # compact trip layer; no plate / waybill / customer / site names
    trips = h[["trip_id", "month", "km", "ef", "e_kg", "group", "vtype_name",
               "speed", "speed_flag", "band", "n_assoc"]].copy()
    trips = trips.rename(columns={
        "trip_id": "trip_id", "month": "month", "km": "distance_km",
        "ef": "ef_g_km", "e_kg": "carbon_kg", "group": "vehicle_group",
        "vtype_name": "vehicle_type", "speed": "speed_kmh",
        "speed_flag": "speed_flag", "band": "distance_band",
        "n_assoc": "waybill_associations",
    })
    trips["carbon_t"] = trips["carbon_kg"] / 1000.0

    # duplicate distribution
    dup = b.groupby(KEYS, dropna=False).size().value_counts().sort_index()
    dup_rows = [{"关联数": int(k), "物理行程数": int(v)} for k, v in dup.items()]

    out.mkdir(parents=True, exist_ok=True)
    trips.to_parquet(out / "vsp_physical_trips.parquet", index=False)
    g.to_parquet(out / "vsp_vehicle_groups.parquet", index=False)

    summary = {
        "source": str(src),
        "source_file": model.name,
        "source_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "mode": "audit_only",
        "note": "物理行程审计口径，不替代当前 E0；HYC 是代理因子映射，不是 VSP。",
        "current": {"rows": current_rows, "km": round(current_km, 2), "t": round(current_t, 3)},
        "physical_current": {"trips": physical_trips, "km": round(physical_km, 2), "t": round(physical_t, 3)},
        "vsp": {"trips": physical_trips, "km": round(physical_km, 2), "t": round(vsp_t, 3),
                "all_rows_t": round(vsp_all_t, 3),
                "ef_gkm": round(vsp_t * 1_000_000.0 / physical_km, 3)},
        "hyc": {"trips": hyc_trips, "km": round(hyc_km, 2), "t": round(hyc_t, 3)},
        "hyc_aux": {"rows": current_rows, "km": round(current_km, 2), "t": round(hyc_aux_t, 3)},
        "duplicate_effect_t": round(duplicate_effect, 3),
        "factor_effect_t": round(factor_effect, 3),
        "net_delta_t": round(net_delta, 3),
        "vsp_effect_t": round(vsp_effect, 3),
        "ratios": {
            "associations_per_trip": round(current_rows / physical_trips, 3),
            "current_to_physical_km": round(current_km / physical_km, 4),
            "current_to_hyc_t": round(current_t / hyc_t, 4),
            "vsp_to_legal_pct": round((vsp_t / physical_t - 1.0) * 100.0, 3),
            "hyc_to_vsp_pct": round((hyc_t / vsp_t - 1.0) * 100.0, 3),
        },
        "reconciliation": [
            {"口径": "当前 E0（运单—车辆关联）", "记录数": current_rows, "里程_km": round(current_km, 2),
             "碳排_t": round(current_t, 3), "说明": "逐条关联记录求和；同一物理行程可能重复"},
            {"口径": "当前因子 + 物理行程去重", "记录数": physical_trips, "里程_km": round(physical_km, 2),
             "碳排_t": round(physical_t, 3), "说明": "法定轨：里程 × 当前六档因子；非 VSP"},
            {"口径": "我们的 VSP 物理轨 + 物理行程", "记录数": physical_trips, "里程_km": round(physical_km, 2),
             "碳排_t": round(vsp_t, 3), "说明": "物理轨：速度 → VSP → 排放率积分；我们的模型"},
            {"口径": "HYC 代理因子 + 物理行程", "记录数": hyc_trips, "里程_km": round(hyc_km, 2),
             "碳排_t": round(hyc_t, 3), "说明": "HYC 车型代理映射；不是 VSP"},
            {"口径": "HYC 因子 + 当前关联行", "记录数": current_rows, "里程_km": round(current_km, 2),
             "碳排_t": round(hyc_aux_t, 3), "说明": "仅用于拆因子影响"},
        ],
        "months": month_rows,
        "duplicate_distribution": dup_rows,
    }
    (out / "vsp_audit.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(DEFAULT_SRC))
    ap.add_argument("--out", default=str(PROCESSED))
    args = ap.parse_args()
    s = build(Path(args.src), Path(args.out))
    print(json.dumps({k: s[k] for k in ("current", "physical_current", "hyc", "hyc_aux",
                                       "duplicate_effect_t", "factor_effect_t", "net_delta_t")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
