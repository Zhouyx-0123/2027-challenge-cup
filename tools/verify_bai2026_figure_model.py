# -*- coding: utf-8 -*-
"""白孝轩等(2026) 图4/5/7 系数 → 与我们两条轨的逐趟因子对比（同为 g CO2/km，口径可比）。

论文模型（图4a/图5，实测）:  EF(vclass, v, M) = k(vclass,v)·M + b(vclass,v)
我们两条轨（都是 g/km 口径）:
  法定轨  EF_legal = factor_gkm
  物理轨  EF_phys  = factor_gkm × mult(vsp(v_clean)) × 50/v_clean
                      ← ER×时间 折算到里程；**v_clean 为清洗后速度**
                        （v<=1 km/h 按行业均速 50 km/h 兜底，见 src/vsp_segment.clean_speed_for_time）

口径纪律（2026-11-03 起固化）：**禁止用未清洗原始速度重算 EF** —— 那会得到
1,680,957 g/km 的幽灵极值与虚高 1.35% 的碳量。台账真实值见 tools/verify_docs58_numbers.py。

运行：python tools/verify_bai2026_figure_model.py
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

K_V = np.array([30.0, 60.0, 90.0])
K_SLOPE = {1: np.array([58.57, 25.07, 3.10]),
           2: np.array([34.73, 14.94, 1.85]),
           3: np.array([34.39, 14.72, 1.82])}
B_III = {1: np.array([423.85, 273.64, 315.58]),
         2: np.array([370.17, 237.50, 272.04]),
         3: np.array([846.69, 530.21, 578.24])}
VC_NAME = {1: "轻型", 2: "中型", 3: "重型"}


def interp_coef(v, arr):
    return float(np.interp(min(max(v, K_V[0]), K_V[-1]), K_V, arr))


seg = pd.read_parquet(PP / "vsp_seg_trips.parquet")
base = pd.read_parquet(PP / "baseline_carbon.parquet")
led = base.groupby("trip_id").agg(load_t=("load_t", "first"), vclass=("vclass", "first"),
                                   factor_gkm=("factor_gkm", "first"),
                                   trip_km=("trip_km", "first")).reset_index()
d = seg.merge(led, on="trip_id", how="left")
ok = d[d["passed"]].copy()
ok["vclass"] = ok["vclass"].fillna(2).astype(int)

v_clean = clean_speed_for_time(ok["speed_kmh"])
vsp = vsp_from_speed(v_clean)
ok["mult"] = np.interp(vsp, VSP_X, VSP_MULT)
ok["ef_phys"] = ok["factor_gkm"] * ok["mult"] * 50.0 / v_clean
ok["k_paper"] = [interp_coef(v, K_SLOPE[c]) for v, c in zip(ok["speed_kmh"], ok["vclass"])]
ok["b_paper"] = [interp_coef(v, B_III[c]) for v, c in zip(ok["speed_kmh"], ok["vclass"])]
ok["ef_paper"] = ok["k_paper"] * ok["load_t"] + ok["b_paper"]

BIN_EDGES = [0, 20, 40, 60, 80, 110]
BIN_LAB = ["低速档<20", "中低速20-40", "中速40-60", "中高速60-80", "高速≥80"]
ok["speed_bin"] = pd.cut(ok["speed_kmh"], bins=BIN_EDGES, labels=BIN_LAB, right=False)

print("=" * 92)
print("A. 物理轨单位里程碳 EF_phys (g/km) vs 论文实测 EF —— 逐档逐车型")
print("=" * 92)
for vc in (1, 2, 3):
    sub = ok[ok["vclass"] == vc]
    if sub.empty:
        continue
    print(f"\n{VC_NAME[vc]} (vclass={vc}, {len(sub)} 趟)")
    rows = []
    for b in BIN_LAB:
        s2 = sub[sub["speed_bin"] == b]
        if s2.empty:
            continue
        rows.append({
            "速度档": b, "趟数": len(s2),
            "载重均值t": round(s2["load_t"].mean(), 1),
            "我们EF_phys": round(s2["ef_phys"].mean(), 1),
            "论文EF": round(s2["ef_paper"].mean(), 1),
            "Δ": round(s2["ef_paper"].mean() - s2["ef_phys"].mean(), 1),
            "相对差%": round((s2["ef_paper"].mean() / s2["ef_phys"].mean() - 1) * 100, 1),
        })
    print(pd.DataFrame(rows).to_string(index=False))

print()
print("=" * 92)
print("B. 总量口径（物理轨 kg）")
print("=" * 92)
ok["kg_phys_ours"] = ok["ef_phys"] * ok["trip_km"] / 1000.0
ok["kg_paper"] = ok["ef_paper"] * ok["trip_km"] / 1000.0
rows = []
for b in BIN_LAB:
    sub = ok[ok["speed_bin"] == b]
    if sub.empty:
        continue
    rows.append({
        "速度档": b, "趟数": len(sub), "里程万km": round(sub["trip_km"].sum() / 1e4, 1),
        "我们物理t": round(sub["kg_phys_ours"].sum() / 1000, 1),
        "论文t": round(sub["kg_paper"].sum() / 1000, 1),
        "Δt": round((sub["kg_paper"].sum() - sub["kg_phys_ours"].sum()) / 1000, 1),
        "Δ%": round((sub["kg_paper"].sum() / sub["kg_phys_ours"].sum() - 1) * 100, 2),
    })
print(pd.DataFrame(rows).to_string(index=False))
tot_o = ok["kg_phys_ours"].sum()
tot_p = ok["kg_paper"].sum()
print(f"\n合计: 我们物理轨 {tot_o/1000:,.1f} t | 论文口径 {tot_p/1000:,.1f} t | "
      f"Δ {(tot_p-tot_o)/1000:+,.1f} t ({(tot_p/tot_o-1)*100:+.2f}%)")
print()

print("=" * 92)
print("C. 单点验算：重型 30t 趟在几个速度上的 EF")
print("=" * 92)
print(f"{'速度':>6} {'我们factor':>11} {'mult':>7} {'我们EF_phys':>12} {'论文EF':>9} {'相对差':>8}")
for v in (15, 25, 35, 50, 65, 80, 95):
    vsp_v = float(np.clip(1 + (v - 30) / 20, 0, 4))
    mult = float(np.interp(vsp_v, VSP_X, VSP_MULT))
    f = 1055.952
    ours = f * mult * 50 / v
    k = interp_coef(v, K_SLOPE[3])
    b = interp_coef(v, B_III[3])
    paper = k * 30 + b
    print(f"{v:>6} {f:>11.3f} {mult:>7.4f} {ours:>12.1f} {paper:>9.1f} "
          f"{(paper/ours-1)*100:>7.1f}%")

print()
print("=" * 92)
print("D. 温度修正系数 k_T 按月落到我们数据（上海/深圳月均温近似）")
print("=" * 92)
T_MONTH = {"2023-11": 12.0, "2023-12": 8.0, "2024-01": 5.0, "2024-02": 7.0,
           "2024-03": 11.0, "2024-04": 16.0, "2024-05": 21.0, "2024-06": 25.0,
           "2024-07": 29.0, "2024-08": 29.0, "2024-09": 25.0, "2024-10": 20.0}
ok["month"] = ok["month"].astype(str)
rows = []
for mo in sorted(ok["month"].unique()):
    sub = ok[ok["month"] == mo]
    T = T_MONTH.get(mo)
    if T is None:
        continue
    kt = 2.08e-4 * T ** 2 - 0.01 * T + 1.11
    rows.append({"月份": mo, "趟数": len(sub), "里程万km": round(sub["trip_km"].sum() / 1e4, 1),
                 "月均温℃": T, "k_T": round(kt, 4), "Δ%": round((kt - 1) * 100, 2),
                 "碳t": round(sub["kg_phys_ours"].sum() / 1000, 1)})
tm = pd.DataFrame(rows)
print(tm.to_string(index=False))
tot_t = tm["碳t"].sum()
extra = float((tm["k_T"] - 1.0).mul(tm["碳t"]).sum())
print(f"\n按 k_T 修正月度碳量: 净 {extra:+.1f} t ({extra / tot_t * 100:+.2f}%)")
print("（月均温为上海/深圳近似值，实际应按出发城市×月份取气温；海拔 k_A 另算）")
