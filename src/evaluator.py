# -*- coding: utf-8 -*-
"""分析层：段级碳排 → 四维热点 / 同线带宽 / 替代方案。

只读 baseline_carbon.parquet，写三张结果表：
    hotspots.parquet      四维热点（车型 / 线路 / 月份 / 里程段）
    od_band.parquet       同线带宽（同一 OD 不同车型的碳强度倍数）
    final_report.parquet  三方案以上的减排与成本对比

前端只读这三张表 + kpi.json，不再调用这里任何函数。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = ROOT / "data" / "processed"
BASELINE_PATH = PROCESSED_DIR / "baseline_carbon.parquet"
HOTSPOTS_PATH = PROCESSED_DIR / "hotspots.parquet"
OD_BAND_PATH = PROCESSED_DIR / "od_band.parquet"
SCHEMES_PATH = PROCESSED_DIR / "final_report.parquet"
SOURCE_PATH = PROCESSED_DIR / "_source.json"

# 方案表：减排率 / 投资 / 年节省来自答辩稿表 19–21 的静态标定；
# 排放量按本次数据期的 E₀ 等比缩放，保证“同一份数据、同一套账”。
# 单位：capex_wan = 万元；opex_save_wan = 万元/年。
SCENARIOS: List[Dict[str, Any]] = [
    {
        "order": 0, "scenario": "S0 柴油基准（现状）",
        "reduction_pct": 0.0, "capex_wan": 0.0, "opex_save_wan": 0.0,
        "meets_target": False, "is_main": False,
        "basis": "现状基准：与第 1 页 E₀ 同源，不做事后调整",
    },
    {
        "order": 1, "scenario": "S1 全电动",
        "reduction_pct": 31.11, "capex_wan": 17012.3, "opex_save_wan": 3123.0,
        "meets_target": False, "is_main": False,
        "basis": "静态标定（答辩稿表 19–21）：全车队电动化，含补能改造",
    },
    {
        "order": 2, "scenario": "S2 轻中电 + 重灰氢",
        "reduction_pct": 31.80, "capex_wan": 9000.0, "opex_save_wan": 1500.0,
        "meets_target": False, "is_main": False,
        "basis": "静态标定（答辩稿表 19–21）：重卡灰氢，轻中卡电动",
    },
    {
        "order": 3, "scenario": "S3 轻中电 + 重蓝氢",
        "reduction_pct": 56.50, "capex_wan": 9500.0, "opex_save_wan": 1600.0,
        "meets_target": False, "is_main": False,
        "basis": "静态标定（答辩稿表 19–21）：蓝氢带 CCS，减排按使用端计",
    },
    {
        "order": 4, "scenario": "S4 轻中电 + 重绿氢 WTW",
        "reduction_pct": 68.90, "capex_wan": 10500.0, "opex_save_wan": 1700.0,
        "meets_target": False, "is_main": False,
        "basis": "静态标定（答辩稿表 19–21）：绿氢全生命周期口径",
    },
    {
        "order": 5, "scenario": "S5 轻中电 + 重氢（主推）",
        "reduction_pct": 77.40, "capex_wan": 4809.0, "opex_save_wan": 500.8,
        "meets_target": True, "is_main": True,
        "basis": "静态标定（答辩稿表 19–21）：117 辆口径；4,809 万 / 9.6 年",
    },
    {
        "order": 7, "scenario": "S7 绿氢 WTW + 电网清洁化",
        "reduction_pct": 71.50, "capex_wan": 4809.0, "opex_save_wan": 500.8,
        "meets_target": True, "is_main": False,
        "basis": "静态标定（答辩稿表 19–21）：保守口径，用于边界对照",
    },
]

TARGET_REDUCTION_PCT = 70.0
DIST_EDGES = [0.0, 100.0, 300.0, 600.0, 1000.0, float("inf")]
DIST_LABELS = ["0–100 km", "100–300 km", "300–600 km", "600–1000 km", ">1000 km"]


def _save_parquet(df: pd.DataFrame, path: Path, tries: int = 4) -> None:
    last: Exception | None = None
    for i in range(tries):
        try:
            df.to_parquet(path, index=False)
            return
        except OSError as exc:
            last = exc
            time.sleep(0.25 * (i + 1))
    raise last if last else RuntimeError("parquet 写入失败：" + str(path))


def _source() -> str:
    try:
        return json.loads(SOURCE_PATH.read_text(encoding="utf-8")).get("source", "unknown")
    except Exception:
        return "unknown"


def _hot(frame: pd.DataFrame, dimension: str, key: str, top: int | None = None) -> pd.DataFrame:
    g = (frame.groupby(key, dropna=False, as_index=False)
              .agg(metric_kg=("kg_co2", "sum"), n=("kg_co2", "size")))
    g["metric_t"] = g["metric_kg"] / 1000.0
    total = float(g["metric_t"].sum()) or 1.0
    g["share_pct"] = g["metric_t"] / total * 100.0
    g["dimension"] = dimension
    g["value"] = g[key].astype(str)
    g = g.sort_values("metric_t", ascending=False)
    if top:
        g = g.head(top)
    return g[["dimension", "value", "metric_t", "share_pct", "n"]]


def build_hotspots(seg: pd.DataFrame) -> pd.DataFrame:
    frames = [
        _hot(seg, "车型", "vehicle_type", top=20),
        _hot(seg, "线路", "od_pair", top=20),
        _hot(seg, "月份", "month"),
    ]
    dist = pd.to_numeric(seg["dist_km"], errors="coerce").fillna(0.0)
    bins = np.select(
        [(dist <= 100), (dist <= 300), (dist <= 600), (dist <= 1000)],
        DIST_LABELS[:4], default=DIST_LABELS[4])
    seg_dist = seg.assign(_dist_bin=bins)
    frames.append(_hot(seg_dist, "里程段", "_dist_bin"))
    out = pd.concat(frames, ignore_index=True)
    out["metric_t"] = out["metric_t"].round(3)
    out["share_pct"] = out["share_pct"].round(3)
    return out


def build_od_band(seg: pd.DataFrame) -> pd.DataFrame:
    key = "od_key" if "od_key" in seg.columns else "od_pair"
    g = (seg.groupby(key, dropna=False, as_index=False)
            .agg(n=("factor_gkm", "size"),
                 nt=("vehicle_type", "nunique"),
                 fmin=("factor_gkm", "min"),
                 fmax=("factor_gkm", "max"),
                 kg=("kg_co2", "sum"),
                 od_pair=("od_pair", "first")))
    g = g[(g["n"] >= 3) & (g["nt"] >= 2) & (g["fmin"] > 0)].copy()
    if g.empty:
        return pd.DataFrame(columns=[
            "od_key", "od_pair", "n", "nt", "fmin", "fmax", "ratio",
            "kg_t", "coverage_pct"])

    total_kg = float(seg["kg_co2"].sum()) or 1.0
    g["ratio"] = g["fmax"] / g["fmin"]
    g["kg_t"] = g["kg"] / 1000.0
    g["coverage_pct"] = g["kg"] / total_kg * 100.0
    g = g.rename(columns={"kg": "kg_raw"})
    g = g[[key, "od_pair", "n", "nt", "fmin", "fmax", "ratio", "kg_t", "coverage_pct"]]
    g = g.sort_values("ratio", ascending=False).reset_index(drop=True)
    for col in ("fmin", "fmax", "ratio", "kg_t", "coverage_pct"):
        g[col] = g[col].round(4)
    return g


def build_schemes(seg: pd.DataFrame, source: str) -> pd.DataFrame:
    e0_t = float(seg["kg_co2"].sum()) / 1000.0
    rows = []
    for spec in SCENARIOS:
        reduction = float(spec["reduction_pct"])
        emission = e0_t * (1.0 - reduction / 100.0)
        capex = float(spec["capex_wan"])
        opex = float(spec["opex_save_wan"])
        saving = e0_t - emission
        payback = (capex / opex) if opex > 0 else 0.0
        unit = (capex * 10000.0 / saving) if saving > 0 else 0.0
        rows.append({
            "order": int(spec["order"]),
            "scenario": spec["scenario"],
            "emission_t": round(emission, 3),
            "reduction_pct": reduction,
            "carbon_saving_t": round(saving, 3),
            "capex_wan": round(capex, 2),
            "opex_save_wan": round(opex, 2),
            "payback_years": round(payback, 3),
            "unit_cost_yuan_t": round(unit, 2),
            "meets_target": bool(reduction >= TARGET_REDUCTION_PCT),
            "is_main": bool(spec["is_main"]),
            "basis": spec["basis"],
            "data_source": source,
        })
    out = pd.DataFrame(rows).sort_values("order").reset_index(drop=True)
    out["e0_t"] = round(e0_t, 3)
    return out


def build_analysis() -> Dict[str, Any]:
    if not BASELINE_PATH.exists():
        raise FileNotFoundError("缺少 data/processed/baseline_carbon.parquet，请先跑 optimizer")
    seg = pd.read_parquet(BASELINE_PATH)
    if seg.empty:
        raise ValueError("段级碳排为空，无法做分析")
    for col in ("kg_co2", "factor_gkm", "vehicle_type", "od_pair", "dist_km"):
        if col not in seg.columns:
            raise ValueError("段级碳排缺少契约列：" + col)

    source = _source()
    hotspots = build_hotspots(seg)
    band = build_od_band(seg)
    schemes = build_schemes(seg, source)

    _save_parquet(hotspots, HOTSPOTS_PATH)
    _save_parquet(band, OD_BAND_PATH)
    _save_parquet(schemes, SCHEMES_PATH)

    median_ratio = float(band["ratio"].median()) if not band.empty else float("nan")
    coverage = float(band["kg_t"].sum() * 1000.0 / max(float(seg["kg_co2"].sum()), 1e-9) * 100.0)
    print("  [E1] 四维热点落盘：%s（%d 行）" % (HOTSPOTS_PATH.name, len(hotspots)))
    print("  [E2] 同线带宽落盘：%s（%d 条可比较 OD）" % (OD_BAND_PATH.name, len(band)))
    print("       碳强度差中位 = %.2f×｜碳排覆盖率 = %.1f%%" % (median_ratio, coverage))
    print("  [E4] 方案对比落盘：%s（%d 个方案）" % (SCHEMES_PATH.name, len(schemes)))
    main = schemes[schemes["is_main"]]
    if not main.empty:
        row = main.iloc[0]
        print("       主推 %s：减排 %.1f%%｜%s t｜%.0f 万｜%.1f 年"
              % (row["scenario"], row["reduction_pct"], format(row["emission_t"], ",.2f"),
                 row["capex_wan"], row["payback_years"]))

    return {
        "od_ratio_median": median_ratio,
        "od_pairs": int(len(band)),
        "od_coverage_pct": coverage,
        "e0_t": float(seg["kg_co2"].sum()) / 1000.0,
        "main_reduction_pct": float(main.iloc[0]["reduction_pct"]) if not main.empty else None,
        "scenarios": int(len(schemes)),
    }


if __name__ == "__main__":
    print("=" * 78)
    print("分析层 evaluator.py · 单跑自测")
    print("=" * 78)
    summary = build_analysis()
    print("-" * 78)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    print("=" * 78)
