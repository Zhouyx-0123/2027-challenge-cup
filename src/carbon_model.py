# -*- coding: utf-8 -*-
"""Our carbon model v1: physical trips + legal factor track + VSP track.

This module is the single source of truth for the *model* (not the page).
It deliberately does not write the main E0; it produces a versioned model card
that can be compared with the HYC proxy package.

Run:
    python -m src.carbon_model --out data/processed
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd

from src import optimizer as opt

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
BASELINE = PROCESSED / "baseline_carbon.parquet"

MODEL_VERSION = "CM-2026.10-v1"
FACTOR_VERSION = opt.FACTOR_VERSION
VSP_VERSION = "VSP-PROXY-v1"
SPEED_POLICY = "door_to_door; <=1 km/h -> 50; clip 1..130"

KEYS = ["plate", "dist_km", "depart_ts", "arrive_ts", "dep_site", "arr_site"]


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["plate_s"] = d["plate"].astype(str).str.strip()
    d["dist_s"] = pd.to_numeric(d["dist_km"], errors="coerce").round(2)
    d["dep_s"] = pd.to_datetime(d["depart_ts"], errors="coerce").astype("string")
    d["arr_s"] = pd.to_datetime(d["arrive_ts"], errors="coerce").astype("string")
    d["o_s"] = d["dep_site"].astype(str).str.strip()
    d["d_s"] = d["arr_site"].astype(str).str.strip()
    return d


def physical_trips(df: pd.DataFrame) -> pd.DataFrame:
    """Deduplicate waybill-linked rows to one row per physical trip."""
    d = _prep(df)
    key = ["plate_s", "dist_s", "dep_s", "arr_s", "o_s", "d_s"]
    counts = d.groupby(key, dropna=False).size().rename("waybill_associations").reset_index()
    first = d.drop_duplicates(key, keep="first").merge(counts, on=key, how="left")
    return first


def _factor(df: pd.DataFrame) -> np.ndarray:
    """Legal factor: exact 2023 table by (vclass, load), fallback interpolation."""
    vclass = pd.to_numeric(df["vclass"], errors="coerce").fillna(2).astype(int).to_numpy()
    load = pd.to_numeric(df["load_t"], errors="coerce").fillna(12.0).clip(1.0, 60.0).to_numpy()
    keys = list(zip(vclass, np.round(load, 2)))
    factor = np.array([opt.FACTOR_BY_CLASS_LOAD.get((int(c), float(l)), np.nan) for c, l in keys], dtype=float)
    miss = np.isnan(factor)
    if miss.any():
        factor[miss] = opt.factor_lookup(load[miss])
    return factor


def legal_emissions(df: pd.DataFrame) -> pd.Series:
    """Legal track: km x representative factor / 1000 (kg)."""
    factor = _factor(df)
    kg = factor * pd.to_numeric(df["dist_km"], errors="coerce").fillna(0.1).clip(lower=0.1) / 1000.0
    return pd.Series(kg, index=df.index, name="kg_co2_legal")


def vsp_emissions(df: pd.DataFrame) -> pd.Series:
    """VSP track: speed -> VSP -> ER(g/s) x travel seconds / 1000 (kg).

    Speed policy intentionally mirrors the current project baseline:
    door-to-door speed; <=1 km/h replaced by 50; clipped 1..130.
    It is a documented approximation, not a tachograph measurement.
    """
    dist = pd.to_numeric(df["dist_km"], errors="coerce").fillna(0.1).clip(lower=0.1)
    speed = pd.to_numeric(df["speed_kmh"], errors="coerce")
    speed = speed.where(speed > 1.0, opt.INDUSTRY_SPEED_KMH).clip(lower=1.0, upper=130.0)
    factor = _factor(df)
    vsp = opt.vsp_from_speed(speed)
    er = factor / 72.0 * np.interp(vsp, opt.VSP_X, opt.VSP_MULT)
    kg = er * (dist / speed) * opt.SECONDS_PER_HOUR / 1000.0
    return pd.Series(kg, index=df.index, name="kg_co2_vsp")


def estimate(df: pd.DataFrame) -> pd.DataFrame:
    """Return physical-trip table with legal/VSP tracks and flags."""
    trips = physical_trips(df)
    trips["factor_gkm"] = _factor(trips)
    trips["kg_co2_legal"] = legal_emissions(trips)
    trips["kg_co2_vsp"] = vsp_emissions(trips)
    trips["vsp_delta_pct"] = np.where(
        trips["kg_co2_legal"] > 0,
        (trips["kg_co2_vsp"] - trips["kg_co2_legal"]) / trips["kg_co2_legal"] * 100.0,
        0.0,
    )
    return trips


def summarize(trips: pd.DataFrame) -> Dict[str, Any]:
    legal_t = float(trips["kg_co2_legal"].sum() / 1000.0)
    vsp_t = float(trips["kg_co2_vsp"].sum() / 1000.0)
    km = float(trips["dist_km"].sum())
    return {
        "model_version": MODEL_VERSION,
        "factor_version": FACTOR_VERSION,
        "vsp_version": VSP_VERSION,
        "speed_policy": SPEED_POLICY,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "trips": int(len(trips)),
        "km": round(km, 2),
        "legal_t": round(legal_t, 3),
        "vsp_t": round(vsp_t, 3),
        "vsp_delta_t": round(vsp_t - legal_t, 3),
        "vsp_delta_pct": round((vsp_t / legal_t - 1.0) * 100.0, 3),
        "factors": [
            {"load_t": float(a), "g_per_km": float(b)}
            for a, b in zip(opt.LOAD_ANCHORS, opt.FACTOR_ANCHORS)
        ],
        "anchors": opt.self_test(),
        "vsp_curve": [
            {"vsp": float(x), "multiplier": float(y)}
            for x, y in zip(opt.VSP_X, opt.VSP_MULT)
        ],
        "notes": [
            "legal_t = physical trips x representative factor / 1000",
            "vsp_t = speed -> VSP -> ER x travel time; not fuel measurement",
            "speed is door-to-door and includes waiting/loading; see speed_policy",
            "HYC 2,338.93 t is a proxy-factor package, not this model",
        ],
    }


def build(baseline: Path = BASELINE, out: Path = PROCESSED) -> Dict[str, Any]:
    if not baseline.exists():
        raise FileNotFoundError(baseline)
    raw = pd.read_parquet(baseline)
    trips = estimate(raw)
    card = summarize(trips)
    out.mkdir(parents=True, exist_ok=True)
    trips.to_parquet(out / "our_carbon_trips.parquet", index=False)
    (out / "our_carbon_model.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    return card


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default=str(BASELINE))
    ap.add_argument("--out", default=str(PROCESSED))
    args = ap.parse_args()
    card = build(Path(args.baseline), Path(args.out))
    print(json.dumps({k: card[k] for k in ("trips", "km", "legal_t", "vsp_t", "vsp_delta_pct")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
