
# -*- coding: utf-8 -*-
"""J6L220 解码结果 → 项目契约流 → 官方守恒法算碳（端到端联调检查）。

为什么单独一个脚本
------------------
实车抓包**没有绝对时钟**，而 config/obd_gps_contract.json 要求 ts 必填。这里用
"合成时间轴"（每段顺次排布、段间留 120 s 空档）把解码结果喂进 src/obd_gps.py 的
正规校验器与守恒法算碳，用来证明**字段确实合法、碳算得出来**；
合成时间轴只用于联调验证，产物里 qc 会带 synthetic_clock，不能当真实时序入库。

用法
----
python tools/j6l220_contract_check.py --input "<抓包目录>" --out outputs/obd_j6l220/contract_stream.csv
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "src",):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import obd_did_j6l220 as dec  # noqa: E402
import obd_gps  # noqa: E402

SEG_GAP_S = 120.0


def synthetic_clock(points: pd.DataFrame, base: str) -> pd.DataFrame:
    """按段的首次出现顺序排一条单调时间轴（只用于联调）。"""
    df = points.copy()
    order = list(dict.fromkeys(df["seg"].tolist()))
    cursor = pd.Timestamp(base)
    stamps = {}
    for seg in order:
        sub = df[df["seg"] == seg]
        stamps[seg] = cursor
        cursor = cursor + pd.to_timedelta(float(sub["t_s"].max()) + SEG_GAP_S, unit="s")
    df["ts"] = [
        (stamps[seg] + pd.to_timedelta(float(t), unit="s")).isoformat()
        for seg, t in zip(df["seg"], df["t_s"])
    ]
    df["qc"] = df["qc"].fillna("") + ";synthetic_clock"
    return df.drop(columns=["t_s"])


def main() -> int:
    ap = argparse.ArgumentParser(description="J6L220 解码结果 → 契约校验 → 守恒法算碳")
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--vehicle-id", default="J6L220-京A00001")
    ap.add_argument("--ts-base", default="2026-11-11T08:00:00+08:00")
    args = ap.parse_args()

    res = dec.decode_directory(args.input, vehicle_id=args.vehicle_id)
    points = synthetic_clock(res["points"], args.ts_base)
    records = points.rename(columns={"qc": "qc_flags"}).to_dict("records")

    frame, rejects = obd_gps.normalize_records(records)
    frame = obd_gps.attach_carbon(frame)

    fuel_l = float(frame["fuel_l_step"].sum())
    kg = float(frame["kg_co2"].sum())
    qc = obd_gps.qc_report(frame, rejects) if hasattr(obd_gps, "qc_report") else {}
    summary = {
        "records_in": len(records),
        "accepted": int(len(frame)),
        "rejected": int(len(rejects)),
        "reject_reasons": rejects["reason"].value_counts().to_dict() if len(rejects) else {},
        "fuel_l_total": round(fuel_l, 4),
        "kg_co2_total": round(kg, 3),
        "factor_kg_per_l": float(frame["factor_kg_per_l"].iloc[0]) if len(frame) else None,
        "factor_consistency_pct": float(frame["factor_consistency_pct"].iloc[0]) if len(frame) else None,
        "qc": qc,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        cols = [c for c in obd_gps.CANONICAL_FIELDS if c in frame.columns] + ["qc_flags"]
        frame[cols].to_csv(args.out, index=False, encoding="utf-8-sig")
        print(f"\nwrote {args.out} ({len(frame)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
