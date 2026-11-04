# -*- coding: utf-8 -*-
"""低速段异常排查：EF_phys = factor × mult(v) × 50/v 在极低速的表现、谁在主导碳量。

口径纪律（2026-11-03 起固化）：
  · 速度用**清洗后** v_clean（v<=1 km/h 按行业均速 50 km/h 兜底，见 src/vsp_segment.clean_speed_for_time）；
  · EF / 碳量一律用**台账真实值** kg_co2_phys_gross / trip_km × 1000；
  · **禁止**用未清洗速度重算 EF —— 会得到 1,680,957 g/km 的幽灵极值。

运行：python tools/verify_lowspeed_carbon.py
"""
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO = Path(__file__).resolve().parents[1]
PP = REPO / "data" / "processed"
sys.path.insert(0, str(REPO))
from src.factor_model import VSP_MULT, VSP_X, vsp_from_speed  # noqa: E402
from src.vsp_segment import clean_speed_for_time  # noqa: E402

seg = pd.read_parquet(PP / "vsp_seg_trips.parquet")
carb = pd.read_parquet(PP / "vsp_seg_carbon.parquet")
base = pd.read_parquet(PP / "baseline_carbon.parquet")

led = base.groupby("trip_id").agg(load_t=("load_t", "first"), vclass=("vclass", "first"),
                                   factor_gkm=("factor_gkm", "first"),
                                   trip_km=("trip_km", "first"),
                                   kg_phys=("kg_co2_phys_gross", "first"),
                                   speed_led=("speed_kmh", "first"),
                                   depart=("depart_ts", "first")).reset_index()
d = seg.merge(carb[["trip_id", "kg_co2_binned"]], on="trip_id").merge(led, on="trip_id", how="left")
ok = d[d["passed"]].copy()

v_clean = clean_speed_for_time(ok["speed_kmh"])
vsp = vsp_from_speed(v_clean)
ok["mult"] = np.interp(vsp, VSP_X, VSP_MULT)
ok["ef_phys"] = ok["factor_gkm"] * ok["mult"] * 50.0 / v_clean   # 清洗后速度口径
ok["ef_ledger"] = ok["kg_phys"] / ok["trip_km"] * 1000.0          # 台账真实值（交叉校验）

print("=" * 88)
print("A. 速度分布尾部")
print("=" * 88)
print("速度分位 (km/h):", [round(float(ok["speed_kmh"].quantile(x)), 3) for x in (0, .01, .05, .25, .5, .75, .95, .99)])
print()
for lo, hi in [(0, 1), (1, 3), (3, 5), (5, 10), (10, 20), (20, 40), (40, 60), (60, 80), (80, 110)]:
    s = ok[(ok["speed_kmh"] >= lo) & (ok["speed_kmh"] < hi)]
    if len(s) == 0:
        continue
    print(f"  速度 [{lo:>3},{hi:>3}) : {len(s):>5} 趟 | 里程 {s['trip_km'].sum()/1e4:>7.1f} 万km "
          f"| 碳 {s['kg_phys'].sum()/1000:>9.1f} t | 占碳 {s['kg_phys'].sum()/ok['kg_phys'].sum()*100:>5.2f}% "
          f"| EF 均值 {s['ef_phys'].mean():>10.1f} g/km | 最大 {s['ef_phys'].max():>12.1f}")

print()
print("=" * 88)
print("B. 碳量最集中的趟（Top 20）")
print("=" * 88)
top = ok.nlargest(20, "kg_phys")[
    ["trip_id", "speed_kmh", "speed_led", "trip_km", "factor_gkm", "mult", "ef_phys", "kg_phys", "load_t"]]
top["kg_pct"] = top["kg_phys"] / ok["kg_phys"].sum() * 100
print(top.to_string(index=False))
print(f"\nTop20 合计 {top['kg_phys'].sum()/1000:,.1f} t = 总碳的 {top['kg_phys'].sum()/ok['kg_phys'].sum()*100:.2f}%")
print(f"Top 100 占 {ok.nlargest(100, 'kg_phys')['kg_phys'].sum()/ok['kg_phys'].sum()*100:.2f}%")
print(f"Top 1000 占 {ok.nlargest(1000, 'kg_phys')['kg_phys'].sum()/ok['kg_phys'].sum()*100:.2f}%")
print()

print("=" * 88)
print("C. 极低速趟的物理合理性")
print("=" * 88)
slow = ok[ok["speed_kmh"] < 5].copy()
print(f"速度 <5 km/h: {len(slow)} 趟, 里程 {slow['trip_km'].sum():,.1f} km, "
      f"碳 {slow['kg_phys'].sum()/1000:,.1f} t ({slow['kg_phys'].sum()/ok['kg_phys'].sum()*100:.2f}%)")
print(f"  其中速度 <1 km/h: {(slow['speed_kmh'] < 1).sum()} 趟, "
      f"碳 {slow[slow['speed_kmh'] < 1]['kg_phys'].sum()/1000:,.1f} t")
print()
print("低速趟的样本（前 12 行）:")
cols = ["trip_id", "speed_kmh", "speed_led", "trip_km", "factor_gkm", "mult", "ef_phys", "kg_phys", "depart"]
print(slow.nsmallest(12, "speed_kmh")[cols].to_string(index=False))
print()
print("速度<1 的趟：耗时/里程是否合理（速度=里程/时长）")
s1 = ok[ok["speed_kmh"] < 1]
print(s1[["trip_id", "speed_kmh", "speed_led", "trip_km", "kg_phys", "depart"]].head(15).to_string(index=False))
print()

print("=" * 88)
print("D. 口径交叉校验：清洗后公式 vs 台账真实值")
print("=" * 88)
rel = (ok["ef_phys"] - ok["ef_ledger"]).abs() / ok["ef_ledger"].clip(lower=1e-9)
print(f"  最大相对差 {rel.max():.3e} | 中位 {rel.median():.3e}")
i_max = int(ok["ef_ledger"].to_numpy().argmax())
r_max = ok.iloc[i_max]
print(f"  台账 EF 极值 {ok['ef_ledger'].max():,.1f} g/km  "
      f"(trip {r_max['trip_id']}, 速度 {r_max['speed_kmh']:.3f} km/h, "
      f"里程 {r_max['trip_km']:.2f} km, 碳 {r_max['kg_phys']:.2f} kg)")
v_le1 = ok[ok["speed_kmh"] <= 1.0]
print(f"  v<=1 km/h 的 {len(v_le1)} 趟（50 km/h 兜底）: EF {v_le1['ef_ledger'].min():,.1f}"
      f" ~ {v_le1['ef_ledger'].max():,.1f} g/km, 合计碳 {v_le1['kg_phys'].sum():,.1f} kg")
