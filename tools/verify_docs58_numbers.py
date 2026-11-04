# -*- coding: utf-8 -*-
"""docs/58 §3.8 数字 → 台账口径重算并断言一致。

复核对象：docs/58_论文锚定速度分段与KMeans归档.md 的 §3.8 节。

口径纪律（2026-11-03 起固化）：
  · EF / 碳量一律用**台账真实值** kg_co2_phys_gross / trip_km × 1000；
  · 速度用**清洗后** v_clean（v<=1 km/h 按行业均速 50 km/h 兜底，见 src/vsp_segment.clean_speed_for_time）；
  · **禁止**用未清洗速度重算 EF —— 会得到 1,680,957 g/km 的幽灵极值与 +1.35% 的虚高碳量。

运行：python tools/verify_docs58_numbers.py
"""
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO = Path(__file__).resolve().parents[1]
PP = REPO / "data" / "processed"
DOC = REPO / "docs" / "58_论文锚定速度分段与KMeans归档.md"
sys.path.insert(0, str(REPO))
from src.factor_model import VSP_MULT, VSP_X, vsp_from_speed  # noqa: E402

doc = DOC.read_text(encoding="utf-8")
sec = doc[doc.index("## 3.8"):doc.index("## 4. 诚实边界")]

K_V = np.array([30.0, 60.0, 90.0])
K_SLOPE = {1: np.array([58.57, 25.07, 3.10]),
           2: np.array([34.73, 14.94, 1.85]),
           3: np.array([34.39, 14.72, 1.82])}
B_III = {1: np.array([423.85, 273.64, 315.58]),
         2: np.array([370.17, 237.50, 272.04]),
         3: np.array([846.69, 530.21, 578.24])}


def ic(v, a):
    return float(np.interp(min(max(v, K_V[0]), K_V[-1]), K_V, a))


seg = pd.read_parquet(PP / "vsp_seg_trips.parquet")
carb = pd.read_parquet(PP / "vsp_seg_carbon.parquet")
base = pd.read_parquet(PP / "baseline_carbon.parquet")
led = base.groupby("trip_id").agg(load_t=("load_t", "first"), vclass=("vclass", "first"),
                                   factor_gkm=("factor_gkm", "first"),
                                   trip_km=("trip_km", "first"),
                                   kg_phys=("kg_co2_phys_gross", "first")).reset_index()
d = seg.merge(carb[["trip_id", "kg_co2_binned"]], on="trip_id").merge(led, on="trip_id", how="left")
ok = d[d["passed"]].copy()
ok["vclass"] = ok["vclass"].fillna(2).astype(int)
ok["ef_real"] = ok["kg_phys"] / ok["trip_km"] * 1000.0        # 台账真实单位里程碳
ok["ef_paper"] = [ic(v, K_SLOPE[c]) * m + ic(v, B_III[c])
                  for v, c, m in zip(ok["speed_kmh"], ok["vclass"], ok["load_t"])]
ok["kg_paper"] = ok["ef_paper"] * ok["trip_km"] / 1000.0

BIN_E = [0, 20, 40, 60, 80, 110]
BIN_L = ["低速档 <20", "中低速档 20-40", "中速档 40-60", "中高速档 60-80", "高速档 ≥80"]
ok["sb"] = pd.cut(ok["speed_kmh"], bins=BIN_E, labels=BIN_L, right=False)

FA = np.array([394.56, 544.752, 759.24, 1055.952, 1374.672, 1581.84])
LA = np.array([4.5, 12.0, 18.0, 30.0, 40.0, 45.0])
k_our = float((FA[-1] - FA[0]) / (LA[-1] - LA[0]))
v_eq = {vc: float(np.interp(k_our, K_SLOPE[vc][::-1], K_V[::-1])) for vc in (1, 2, 3)}

km = ok["trip_km"].sum()
kg = ok["kg_phys"].sum()
cov = {}
for lo, hi in ((0, 30), (30, 90), (90, 110)):
    s = ok[(ok["speed_kmh"] >= lo) & (ok["speed_kmh"] < hi)]
    cov[(lo, hi)] = (s["trip_km"].sum() / km * 100, s["kg_phys"].sum() / kg * 100)

per_bin = {}
for b in BIN_L:
    s = ok[ok["sb"] == b]
    if len(s) == 0:
        continue
    per_bin[b] = (s["kg_paper"].sum() / s["kg_phys"].sum() - 1) * 100

tot_ours = ok["kg_phys"].sum() / 1000
tot_paper = ok["kg_paper"].sum() / 1000

single = {}
for v in (25, 35, 50, 65, 80, 95):
    f = 1055.952
    ours = f * float(np.interp(float(np.clip(1 + (v - 30) / 20, 0, 4)), VSP_X, VSP_MULT)) * 50 / v
    paper = ic(v, K_SLOPE[3]) * 30 + ic(v, B_III[3])
    single[v] = (paper / ours - 1) * 100

lb = pd.cut(ok["load_t"], bins=[0, 8, 18, 30, 45, 100],
            labels=["<=8t", "8-18t", "18-30t", "30-45t", ">45t"])
piv = ok.pivot_table(index="sb", columns=lb, values="ef_real", aggfunc="mean",
                     observed=False).reindex(BIN_L)
ratio = (piv["30-45t"] / piv["<=8t"]).dropna()

band = {}
for lo, hi in ((1, 3), (3, 5)):
    s = ok[(ok["speed_kmh"] >= lo) & (ok["speed_kmh"] < hi)]
    band[(lo, hi)] = (s["ef_real"].mean(), s["ef_real"].max())

T_MONTH = {"2023-11": 12.0, "2023-12": 8.0, "2024-01": 5.0, "2024-02": 7.0,
           "2024-03": 11.0, "2024-04": 16.0, "2024-05": 21.0, "2024-06": 25.0,
           "2024-07": 29.0, "2024-08": 29.0, "2024-09": 25.0, "2024-10": 20.0}
ok["month"] = ok["month"].astype(str)
tt = 0.0
extra = 0.0
mrows = []
for mo in sorted(ok["month"].unique()):
    T = T_MONTH.get(mo)
    if T is None:
        continue
    s = ok[ok["month"] == mo]
    kt = 2.08e-4 * T ** 2 - 0.01 * T + 1.11
    c = s["kg_phys"].sum() / 1000
    tt += c
    extra += (kt - 1) * c
    mrows.append((mo, T, kt, (kt - 1) * 100, c))

CHECKS = [
    ("因子表斜率 29.32", round(k_our, 2), 29.32),
    ("轻型等效 56 km/h", round(v_eq[1]), 56),
    ("中型等效 38 km/h", round(v_eq[2]), 38),
    ("重型等效 38 km/h", round(v_eq[3]), 38),
    ("<30 里程 9.8%", round(cov[(0, 30)][0], 1), 9.8),
    ("<30 碳 20.1%", round(cov[(0, 30)][1], 1), 20.1),
    ("30-90 里程 86.6%", round(cov[(30, 90)][0], 1), 86.6),
    ("30-90 碳 78.4%", round(cov[(30, 90)][1], 1), 78.4),
    (">90 里程 3.6%", round(cov[(90, 110)][0], 1), 3.6),
    (">90 碳 1.5%", round(cov[(90, 110)][1], 1), 1.5),
    ("低速档 Δ%", round(per_bin["低速档 <20"], 2), -64.37),
    ("中低速档 Δ%", round(per_bin["中低速档 20-40"], 2), -0.07),
    ("中速档 Δ%", round(per_bin["中速档 40-60"], 2), -0.22),
    ("中高速档 Δ%", round(per_bin["中高速档 60-80"], 2), -13.91),
    ("高速档 Δ%", round(per_bin["高速档 ≥80"], 2), -18.24),
    ("总 ours t", round(tot_ours, 1), 2302.2),
    ("总 paper t", round(tot_paper, 1), 1923.0),
    ("总 Δ%", round((tot_paper / tot_ours - 1) * 100, 2), -16.47),
    ("v=25 −0.6%", round(single[25], 1), -0.6),
    ("v=35 +21.8%", round(single[35], 1), 21.8),
    ("v=50 +20.6%", round(single[50], 1), 20.6),
    ("v=65 +2.9%", round(single[65], 1), 2.9),
    ("v=80 −4.5%", round(single[80], 1), -4.5),
    ("v=95 −8.2%", round(single[95], 1), -8.2),
    ("载重倍数下界 3.49", round(float(ratio.min()), 2), 3.49),
    ("载重倍数上界 3.90", round(float(ratio.max()), 2), 3.90),
    ("载重倍数极差 12.0%", round(float(ratio.max() / ratio.min() - 1) * 100, 1), 12.0),
    ("EF_real 极值 46894.8", round(float(ok["ef_real"].max()), 1), 46894.8),
    ("[1,3) EF 均值 10479.4", round(band[(1, 3)][0], 1), 10479.4),
    ("[3,5) EF 均值 4664.5", round(band[(3, 5)][0], 1), 4664.5),
    ("温度净 +35.8 t", round(extra, 1), 35.8),
    ("温度净 +3.42%", round(extra / tt * 100, 2), 3.42),
]

print("=" * 80)
print("docs/58 §3.8 数字 → 台账口径复核")
print("=" * 80)
fails = 0
for name, got, want in CHECKS:
    good = abs(got - want) <= 0.011
    if not good:
        fails += 1
    print(f"  [{'OK ' if good else 'FAIL'}] {name:<26} 实测={got}  文档={want}")
print()
for n, exp in ((20, 5.80), (100, 16.76), (1000, 69.12)):
    v = round(float(ok.nlargest(n, "kg_phys")["kg_phys"].sum() / kg * 100), 2)
    good = abs(v - exp) <= 0.011
    fails += 0 if good else 1
    print(f"  [{'OK ' if good else 'FAIL'}] Top{n:<5}  实测={v}%  文档={exp}%")

print()
print("=" * 80)
print("A. 等效链断言：台账 kg_co2_phys_gross ≡ factor×mult×50/v_clean（清洗后速度）")
print("=" * 80)
from src.vsp_segment import INDUSTRY_SPEED_KMH, clean_speed_for_time  # noqa: E402

v_clean = clean_speed_for_time(ok["speed_kmh"])
mult_c = np.interp(vsp_from_speed(v_clean), VSP_X, VSP_MULT)
kg_formula = ok["factor_gkm"].to_numpy() * mult_c * 50.0 / v_clean * ok["trip_km"].to_numpy() / 1000.0
kg_led = ok["kg_phys"].to_numpy()
rel = np.abs(kg_formula - kg_led) / np.maximum(kg_led, 1e-9)
tol = 1e-3
good = float(rel.max()) <= tol
fails += 0 if good else 1
print(f"  [{'OK ' if good else 'FAIL'}] 等效链最大相对差 {rel.max():.3e} <= {tol:.0e}"
      f"  (公式总量 {kg_formula.sum()/1000:,.4f} t vs 台账 {kg_led.sum()/1000:,.4f} t)")
print(f"         行业均速兜底 INDUSTRY_SPEED_KMH = {INDUSTRY_SPEED_KMH} km/h")

print()
print("=" * 80)
print("B. 幽灵值防回归：未清洗速度路径的产物不得出现在正文")
print("=" * 80)
v_raw = ok["speed_kmh"].to_numpy(float)
mult_raw = np.interp(vsp_from_speed(v_raw), VSP_X, VSP_MULT)
ef_raw = ok["factor_gkm"].to_numpy() * mult_raw * 50.0 / v_raw
kg_raw = ef_raw * ok["trip_km"].to_numpy() / 1000.0
print(f"  [对照] 未清洗原始速度路径: 总量 {kg_raw.sum()/1000:,.1f} t (台账 {kg_led.sum()/1000:,.1f} t)，"
      f"EF 极值 {ef_raw.max():,.1f} g/km -> 文档禁止出现")
for ghost in ("1,680,957", "1680957"):
    good = ghost not in sec
    fails += 0 if good else 1
    print(f"  [{'OK ' if good else 'FAIL'}] 正文不含幽灵值 '{ghost}'")

print()
print("=" * 80)
print("C. 文档正文实际取值断言（直接读 §3.8 文本，非自我循环）")
print("=" * 80)
MUST = [
    ("低速档 Δ%", "−64.37%"),
    ("总计 ours", "2,302.2 t"),
    ("总计 paper", "1,923.0 t"),
    ("总计 Δ%", "−16.47%"),
    ("EF 极值", "46,894.8 g/km"),
    ("EF 极值 trip", "trip 6938"),
    ("v<=1 兜底趟数", "**49 趟**"),
    ("v<=1 兜底区间", "394.6~1,056.0 g/km"),
    ("温度 2023-11", "409.2 t"),
    ("温度 2023-12", "637.9 t"),
    ("温度净增", "+35.8 t（+3.42%）"),
    ("载重倍数极差", "极差 12.0%"),
    ("v<=1 兜底注", "v≤1 km/h 按行业均速 50 km/h 兜底"),
]
for name, needle in MUST:
    good = needle in sec
    fails += 0 if good else 1
    print(f"  [{'OK ' if good else 'FAIL'}] {name:<16} 正文含 '{needle}'")

print()
print("月度温度修正明细:")
for mo, T, kt, dp, c in mrows:
    print(f"   {mo}  {T:>5.1f}℃  k_T={kt:.4f}  Δ={dp:+.2f}%  碳={c:.1f} t")
print()
print("失败项:", fails)
sys.exit(1 if fails else 0)
