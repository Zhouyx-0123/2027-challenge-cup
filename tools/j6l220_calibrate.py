
# -*- coding: utf-8 -*-
"""J6L220 定标体检：把「喷油量×转速积分」与累计计数器 0x0199 对拍。

用途
----
config/did_map_j6l220.json 里 0x0199 的语义（累计油耗 vs 里程）**未定标**，本脚本给出
定标前能拿到的全部证据：

1. 逐段：时长、积分的油耗（L）、0x0199 增量 ⇒ **L/计数**；
2. 若假设 0x0199 = 里程（0.1 km/计数）⇒ 反推车速，用来**否证**里程假设
   （结果会出现 150+ km/h，且与转速无关）；
3. 纯怠速段的 0x0199 增量应当为 0（本次抓包实测为 0），这是"不是纯油耗计数器"的反证，
   需要在 CAL-1（加油定标）里解决。

输出：命令行表格 + 可选 CSV。不写库、不改任何结果表。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import obd_did_j6l220 as dec  # noqa: E402


def counter_scale(doc: dict) -> float:
    """映射表里 0x0199 的刻度（L/计数）；没定标时返回 1.0（=按原始计数算）。"""
    try:
        scale = doc["dids"]["0x0199"].get("scale")
        return float(scale) if scale else 1.0
    except (KeyError, TypeError, ValueError):
        return 1.0


def per_segment(series: pd.DataFrame, fuel: pd.DataFrame, min_dur_s: float = 60.0,
                scale: float = 1.0) -> pd.DataFrame:
    """逐段汇总：积分油耗 vs 计数器增量。

    series 里的 0x0199 已被映射表乘过 scale，这里先换回**原始计数**再求 Δ，
    否则 l_per_count 会变成“L/L”的无意义比值（踩过的坑）。
    """
    odo = series[series["did"] == 0x0199]
    rows = []
    for seg, g in fuel.groupby("seg"):
        if g["dur_s"].iloc[0] < min_dur_s:
            continue
        o = odo[odo["seg"] == seg].sort_values("t_s")
        d0199 = float((o["value"].iloc[-1] - o["value"].iloc[0]) / scale) if len(o) >= 2 else np.nan
        fuel_l = float(g["fuel_l"].iloc[0])
        dur = float(g["dur_s"].iloc[0])
        rows.append({
            "seg": seg, "dur_s": round(dur, 1), "n": int(g["n"].iloc[0]),
            "fuel_l": round(fuel_l, 4),
            "d0199": d0199,
            "l_per_count": round(fuel_l / d0199, 5) if d0199 and d0199 > 0 else np.nan,
            "kmh_if_km": round(d0199 * 0.1 / dur * 3600.0, 1) if np.isfinite(d0199) else np.nan,
            "idle_share": round(float(g["idle_share"].iloc[0]), 2),
        })
    return pd.DataFrame(rows).sort_values("fuel_l", ascending=False)


def verdict(tbl: pd.DataFrame) -> dict:
    drv = tbl[(tbl["d0199"] > 0) & (tbl["idle_share"] < 0.5)]
    lpc = drv["l_per_count"].dropna()
    kmh = drv["kmh_if_km"].dropna()
    return {
        "driving_segments": int(len(drv)),
        "l_per_count_median": round(float(lpc.median()), 4) if len(lpc) else None,
        "l_per_count_mean": round(float(lpc.mean()), 4) if len(lpc) else None,
        "l_per_count_cv": round(float(lpc.std() / lpc.mean()), 3) if len(lpc) else None,
        "kmh_if_km_p50": round(float(kmh.median()), 1) if len(kmh) else None,
        "kmh_if_km_max": round(float(kmh.max()), 1) if len(kmh) else None,
        "kmh_if_km_over_130": int((kmh > 130).sum()) if len(kmh) else 0,
        "note": "若 0x0199 是里程，kmh_if_km 应处处 = 实际车速（16 t 卡车不可能 130+ km/h）；"
                "若 0x0199 是累计油耗，l_per_count 应跨段恒定（实测行驶段集中在 0.019–0.021）。"
                "两个假设都有反例（纯怠速段 0x0199 不增），故只能加油定标（CAL-1）收口。",
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="J6L220 0x0199 定标体检（油耗积分 vs 累计计数器）")
    ap.add_argument("--input", type=Path, required=True, help="含 all_*.csv 的目录")
    ap.add_argument("--out", type=Path, default=None, help="逐段表落盘 CSV")
    ap.add_argument("--json", type=Path, default=None, help="结论落盘 JSON")
    args = ap.parse_args(argv)

    res = dec.decode_directory(args.input)
    doc = dec.load_did_map()
    tbl = per_segment(res["series"], res["fuel"], scale=counter_scale(doc))
    with pd.option_context("display.width", 200, "display.max_rows", 60):
        print(tbl.to_string(index=False))
    v = verdict(tbl)
    v["counter_scale_in_map"] = counter_scale(doc)
    print("\n" + json.dumps(v, ensure_ascii=False, indent=2))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        tbl.to_csv(args.out, index=False, encoding="utf-8-sig")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(v, fh, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
