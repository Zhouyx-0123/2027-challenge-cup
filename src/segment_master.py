# -*- coding: utf-8 -*-
"""京东趟级“路段”主表：论文 5 档 + K-means 3 簇并列，供聚合/页面使用。

口径边界
--------
京东运单只有**整趟均速**，没有逐秒 GPS/OBD。因此本表的一行是
"一趟 = 一个 trip_proxy 路段"，不是同一趟内切出的多个真实道路片段。
字段 `grain="trip_proxy"`、`has_subtrip_trajectory=False` 是写死的诚实标记。

碳值口径
--------
`carbon_kg` = `baseline_carbon.kg_co2` 按 trip_id 求和，等于 kpi.json 的
`total_kg`（E0 主账）；`carbon_kg_phys` 只作物理轨对账，**不要**把两者混加。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

MASTER_VERSION = "SEG-MASTER-v1"
GRAIN = "trip_proxy"
SOURCE = "JD_WAYBILL"
RESULT_MASTER = "jd_segment_master.parquet"
RESULT_AUDIT = "jd_segment_master_audit.json"

MASTER_COLUMNS = [
    "segment_id", "trip_id", "grain", "source", "segment_scope",
    "has_subtrip_trajectory", "month", "plate_id", "vehicle_type",
    "vehicle_group", "start_ts", "end_ts", "origin", "n_origins",
    "n_destinations", "distance_km", "duration_h", "speed_kmh", "load_t",
    "paper_bin", "passed", "qc_flags", "cluster_id", "cluster_feature_set",
    "n_waybills", "carbon_kg", "carbon_kg_phys", "ef_g_km", "carbon_basis",
]

LEDGER_REQUIRED = {
    "trip_id", "waybill_no", "depart_ts", "arrive_ts", "origin", "dest",
    "duration_h", "trip_km", "kg_co2", "kg_co2_phys", "vehicle_type",
}
SEG_REQUIRED = {
    "trip_id", "month", "speed_kmh", "distance_km", "speed_bin",
    "speed_flag_seg", "passed", "cluster_id", "vehicle_group",
}
ASSIGN_REQUIRED = {"unit_id", "plate_id", "load_t"}

#: 论文 5 档的展示/聚合顺序（与 vsp_segment.PAPER_BIN_LABELS 一致）
PAPER_BIN_ORDER = [
    "低速档 <20",
    "中低速档 20-40",
    "中速档 40-60",
    "中高速档 60-80",
    "高速档 ≥80",
]


def _require_columns(df: pd.DataFrame, cols: set, name: str) -> None:
    missing = sorted(set(cols) - set(df.columns))
    if missing:
        raise ValueError(f"{name} 缺列: {missing}")


def build_trip_carbon(ledger: pd.DataFrame) -> pd.DataFrame:
    """把运单级主账聚成 trip 级；kg_co2 求和 = E0。"""
    _require_columns(ledger, LEDGER_REQUIRED, "baseline_carbon")
    if ledger["trip_id"].isna().any():
        raise ValueError("baseline_carbon.trip_id 存在空值，不能聚合")
    g = ledger.groupby("trip_id", sort=False)
    out = g.agg(
        start_ts=("depart_ts", "min"),
        end_ts=("arrive_ts", "max"),
        origin=("origin", "first"),
        n_origins=("origin", "nunique"),
        n_destinations=("dest", "nunique"),
        n_waybills=("waybill_no", "size"),
        duration_h=("duration_h", "first"),
        ledger_distance_km=("trip_km", "first"),
        carbon_kg=("kg_co2", "sum"),
        carbon_kg_phys=("kg_co2_phys", "sum"),
        vehicle_type=("vehicle_type", "first"),
    ).reset_index()
    return out


def _qflags(value: Any) -> str:
    if isinstance(value, (list, tuple, np.ndarray)):
        return ";".join(str(v) for v in value if str(v))
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    return str(value)


def _group_rows(df: pd.DataFrame, key: str) -> list:
    """按 key 聚合；只统计通过门禁的趟。"""
    ok = df[df["passed"] & df["distance_km"].notna() & df["carbon_kg"].notna()].copy()
    rows = []
    if ok.empty:
        return rows
    total_kg = float(ok["carbon_kg"].sum())
    for value, g in ok.groupby(key, dropna=False, sort=True):
        distance = float(g["distance_km"].sum())
        duration = float(g["duration_h"].sum())
        kg = float(g["carbon_kg"].sum())
        speed = pd.to_numeric(g["speed_kmh"], errors="coerce")
        load = pd.to_numeric(g["load_t"], errors="coerce")
        smask = speed.notna()
        lmask = load.notna()
        rows.append({
            key: (None if pd.isna(value) else (int(value) if key == "cluster_id" else value)),
            "trips": int(len(g)),
            "waybills": int(g["n_waybills"].sum()),
            "distance_km": round(distance, 6),
            "duration_h": round(duration, 6),
            "carbon_kg": round(kg, 6),
            "carbon_share_pct": round(kg / total_kg * 100.0, 4) if total_kg else None,
            "ef_g_km": round(kg * 1000.0 / distance, 4) if distance > 0 else None,
            # 两个速度口径必须分开：趟均速（与 K-means 簇心同口径）vs 时间加权
            "avg_speed_trip_kmh": round(float(speed.mean()), 4) if speed.notna().any() else None,
            "avg_speed_dist_weighted_kmh": (
                round(float((speed[smask] * g.loc[smask, "distance_km"]).sum())
                      / float(g.loc[smask, "distance_km"].sum()), 4)
                if smask.any() and float(g.loc[smask, "distance_km"].sum()) > 0 else None),
            "avg_speed_kmh": round(distance / duration, 4) if duration > 0 else None,
            # 载重同理：距离加权是“吨公里口径”，趟均值是“每趟平均装载”
            "avg_load_t": (
                round(float((load[lmask] * g.loc[lmask, "distance_km"]).sum())
                      / float(g.loc[lmask, "distance_km"].sum()), 4)
                if lmask.any() and float(g.loc[lmask, "distance_km"].sum()) > 0 else None),
            "avg_load_trip_t": round(float(load.mean()), 4) if load.notna().any() else None,
            "n_plates": int(g["plate_id"].nunique(dropna=True)),
        })
    if key == "paper_bin":
        order = {label: i for i, label in enumerate(PAPER_BIN_ORDER)}
        rows.sort(key=lambda row: order.get(row[key], len(order)))
    return rows


def build_master(
    seg: pd.DataFrame,
    assignments: pd.DataFrame,
    ledger: pd.DataFrame,
    trips: Optional[pd.DataFrame] = None,
    kpi_total_kg: Optional[float] = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """构建一趟一行的 trip_proxy 主表，并返回可核对的审计。"""
    _require_columns(seg, SEG_REQUIRED, "vsp_seg_trips")
    _require_columns(assignments, ASSIGN_REQUIRED, "vsp_seg_clusters")
    if seg["trip_id"].duplicated().any():
        raise ValueError("vsp_seg_trips.trip_id 不唯一")
    if assignments["unit_id"].duplicated().any():
        raise ValueError("vsp_seg_clusters.unit_id 不唯一")

    carbon = build_trip_carbon(ledger)
    assign = assignments.rename(columns={"unit_id": "trip_id"}).copy()
    if "feature_set" not in assign.columns:
        assign["feature_set"] = None
    assign = assign[["trip_id", "plate_id", "load_t", "feature_set"]].rename(
        columns={"feature_set": "cluster_feature_set"})

    master = seg.copy()
    # 主账聚合结果优先；若 seg/trips 已有同名列，先让位，避免 merge 产生 _x/_y
    master = master.drop(columns=[c for c in (
        "start_ts", "end_ts", "origin", "n_origins", "n_destinations",
        "n_waybills", "duration_h", "carbon_kg", "carbon_kg_phys",
        "vehicle_type",
    ) if c in master.columns])
    master = master.merge(carbon, on="trip_id", how="left", validate="one_to_one")
    master = master.merge(assign, on="trip_id", how="left", validate="one_to_one")
    if "vehicle_type" not in master.columns:
        if trips is not None and "vehicle_type" in trips.columns:
            master = master.merge(
                trips[["trip_id", "vehicle_type"]].drop_duplicates("trip_id"),
                on="trip_id", how="left", validate="one_to_one")
        else:
            master["vehicle_type"] = pd.NA

    master["distance_km"] = master["ledger_distance_km"].combine_first(master["distance_km"])
    master["paper_bin"] = master["speed_bin"]
    master["qc_flags"] = master["speed_flag_seg"].apply(_qflags)
    master["segment_id"] = "TRIP-" + master["trip_id"].astype("int64").astype(str).str.zfill(6)
    master["grain"] = GRAIN
    master["source"] = SOURCE
    master["segment_scope"] = "whole_trip"
    master["has_subtrip_trajectory"] = False
    master["carbon_basis"] = "baseline_carbon.kg_co2 summed by trip_id (= kpi total_kg)"
    master["ef_g_km"] = np.where(
        master["distance_km"] > 0,
        master["carbon_kg"] * 1000.0 / master["distance_km"],
        np.nan,
    )
    master["cluster_id"] = master["cluster_id"].astype("Int64")

    # 距离不一致只登记，不悄悄改；主表采用主账 trip_km（与 E0 同源）
    mismatch = int(((master["ledger_distance_km"] - master["distance_km"]).abs() > 1e-9).sum())
    missing_carbon = int(master["carbon_kg"].isna().sum())

    master = master.sort_values("trip_id").reset_index(drop=True)
    master = master[[c for c in MASTER_COLUMNS if c in master.columns]]

    valid = master[master["passed"]]
    invalid = master[~master["passed"]]
    total_kg = float(master["carbon_kg"].sum())
    valid_kg = float(valid["carbon_kg"].sum())
    invalid_kg = float(invalid["carbon_kg"].sum())
    by_bin = _group_rows(master, "paper_bin")
    by_cluster = _group_rows(master, "cluster_id")
    by_month = _group_rows(master, "month")

    recon: Dict[str, Any]
    if kpi_total_kg is None:
        recon = {"status": "not_checked", "reason": "未提供 kpi.json total_kg"}
    else:
        delta = total_kg - float(kpi_total_kg)
        recon = {
            "status": "ok" if abs(delta) <= 1e-6 else "mismatch",
            "kpi_total_kg": round(float(kpi_total_kg), 6),
            "master_total_kg": round(total_kg, 6),
            "delta_kg": round(delta, 6),
            "matches": bool(abs(delta) <= 1e-6),
        }

    audit: Dict[str, Any] = {
        "status": "ok",
        "version": MASTER_VERSION,
        "grain": GRAIN,
        "source": SOURCE,
        "n_rows": int(len(master)),
        "n_passed": int(valid.shape[0]),
        "n_invalid": int(invalid.shape[0]),
        "n_clustered": int(master["cluster_id"].notna().sum()),
        "n_unclustered": int(master["cluster_id"].isna().sum()),
        "n_plates": int(master["plate_id"].nunique(dropna=True)),
        "n_waybills": int(master["n_waybills"].sum()),
        "distance_km": round(float(master["distance_km"].sum()), 6),
        "duration_h": round(float(master["duration_h"].sum()), 6),
        "carbon_kg": round(total_kg, 6),
        "carbon_kg_valid": round(valid_kg, 6),
        "carbon_kg_invalid": round(invalid_kg, 6),
        "carbon_kg_phys": round(float(master["carbon_kg_phys"].sum()), 6),
        "carbon_basis": "carbon_kg = sum(baseline_carbon.kg_co2 by trip_id); carbon_kg_phys 仅对账",
        "reconciliation": recon,
        "by_paper_bin": by_bin,
        "by_cluster": by_cluster,
        "by_month": by_month,
        "checks": {
            "distance_mismatch_trips": mismatch,
            "missing_carbon_trips": missing_carbon,
            "missing_plate_id": int(master["plate_id"].isna().sum()),
            "missing_vehicle_type": int(master["vehicle_type"].isna().sum()),
            "missing_duration": int(master["duration_h"].isna().sum()),
        },
        "limitations": [
            "京东运单只有整趟均速；一行=一趟，不是同趟内多段真实轨迹",
            "没有逐秒 GPS/OBD，无法给 rpm/扭矩/瞬时油耗/怠速",
            "plate_id 为加盐哈希；原始车牌只在 vsp_seg_plate_map",
            "paper_bin 是论文 5 档（碳规则轨）；cluster_id 是速度+里程+载重的运营分层，二者并列不互替",
            "131 条速度门禁未过趟保留在主表，passed=False；聚合时必须显式过滤",
        ],
        "aggregation_contract": {
            "keys": ["trip_id", "plate_id", "month", "paper_bin", "cluster_id"],
            "additive": ["carbon_kg", "carbon_kg_phys", "distance_km", "duration_h", "n_waybills"],
            "non_additive": {
                "ef_g_km": "1000 * sum(carbon_kg) / sum(distance_km)",
                "avg_speed_kmh": "sum(distance_km) / sum(duration_h)  # 时间加权，车队运营口径",
                "avg_speed_trip_kmh": "mean(speed_kmh)  # 趟等权，与 K-means 簇心同口径",
                "avg_speed_dist_weighted_kmh": "sum(speed_kmh * distance_km) / sum(distance_km)",
                "avg_load_t": "sum(load_t * distance_km) / sum(distance_km)  # 吨公里口径",
                "avg_load_trip_t": "mean(load_t)  # 每趟平均装载",
            },
            "do_not": "不要对 ef_g_km 直接求平均；不要把 n_waybills 当碳权重；报告平均速度必须注明是趟等权、距离加权还是时间加权",
        },
    }
    return master, audit


def main() -> None:
    parser = argparse.ArgumentParser(description="京东趟级 trip_proxy 主表 + 聚合审计")
    parser.add_argument("--out", default="data/processed")
    parser.add_argument("--seg", default="data/processed/vsp_seg_trips.parquet")
    parser.add_argument("--clusters", default="data/processed/vsp_seg_clusters.parquet")
    parser.add_argument("--ledger", default="data/processed/baseline_carbon.parquet")
    parser.add_argument("--trips", default="data/processed/vsp_physical_trips.parquet")
    parser.add_argument("--kpi", default="data/processed/kpi.json")
    args = parser.parse_args()

    seg = pd.read_parquet(args.seg)
    clusters = pd.read_parquet(args.clusters)
    ledger = pd.read_parquet(args.ledger)
    trips = pd.read_parquet(args.trips) if Path(args.trips).exists() else None
    kpi_total = None
    if Path(args.kpi).exists():
        payload = json.loads(Path(args.kpi).read_text(encoding="utf-8"))
        kpi_total = payload.get("total_kg")

    master, audit = build_master(seg, clusters, ledger, trips=trips, kpi_total_kg=kpi_total)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    master.to_parquet(out / RESULT_MASTER, index=False)
    (out / RESULT_AUDIT).write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[segment_master] {len(master)} rows -> {out / RESULT_MASTER}")
    print(f"[segment_master] carbon {audit['carbon_kg']:.3f} kg; "
          f"valid {audit['n_passed']}; invalid {audit['n_invalid']}")
    print(f"[segment_master] reconciliation: {audit['reconciliation'].get('status')}")


if __name__ == "__main__":
    main()
