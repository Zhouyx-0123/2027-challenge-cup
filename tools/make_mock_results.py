
# -*- coding: utf-8 -*-
"""生成契约形态的演示数据（8 个结果文件），供前端在真数据到位前开发。

用法：python tools/make_mock_results.py
生成后 data/processed/ 下会出现 _mock.flag，前端据此显示"演示数据"。
真数据由 python -m src.pipeline 覆盖生成，并自动删除该标记。
"""
import json
import math
import random
from pathlib import Path

import numpy as np
import pandas as pd

random.seed(20261018)
np.random.seed(20261018)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed"
OUT.mkdir(parents=True, exist_ok=True)

CITIES = {
    "上海": (121.47, 31.23), "北京": (116.40, 39.90), "广州": (113.26, 23.13),
    "深圳": (114.06, 22.55), "杭州": (120.15, 30.27), "南京": (118.80, 32.06),
    "苏州": (120.62, 31.32), "成都": (104.07, 30.57), "重庆": (106.55, 29.56),
    "武汉": (114.30, 30.59), "西安": (108.94, 34.34), "郑州": (113.63, 34.75),
    "天津": (117.20, 39.13), "青岛": (120.38, 36.07), "济南": (117.00, 36.65),
    "长沙": (112.94, 28.23), "合肥": (117.28, 31.86), "福州": (119.30, 26.08),
    "厦门": (118.09, 24.48), "昆明": (102.71, 25.04), "贵阳": (106.63, 26.65),
    "南宁": (108.32, 22.82), "沈阳": (123.43, 41.80), "哈尔滨": (126.53, 45.80),
    "长春": (125.32, 43.82), "石家庄": (114.51, 38.04), "太原": (112.55, 37.87),
    "兰州": (103.83, 36.06), "乌鲁木齐": (87.62, 43.82), "拉萨": (91.11, 29.65),
}

TRUCKS = [
    ("4.2米厢式货车", 1, 4.5, 4.5, "载质量", 394.56),
    ("依维柯", 1, 3.0, 3.0, "载质量", 320.18),
    ("9.6米厢式货车", 2, 18.0, 8.6, "载质量", 759.24),
    ("7.6米厢式货车", 2, 12.0, 14.4, "整车总质量", 544.75),
    ("牵引车头", 3, 30.0, 30.0, "准牵引总质量", 1055.95),
    ("17.5米半挂", 3, 45.0, 30.0, "载质量", 1581.84),
]

WEIGHTS = [0.44, 0.10, 0.26, 0.08, 0.07, 0.05]
city_names = list(CITIES)
city_w = np.array([14, 8, 9, 7, 6, 4, 3, 3, 2, 3, 2, 3, 3, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1], dtype=float)
city_w = city_w / city_w.sum()

N = 6000


def gcd_km(a, b):
    lon1, lat1 = math.radians(CITIES[a][0]), math.radians(CITIES[a][1])
    lon2, lat2 = math.radians(CITIES[b][0]), math.radians(CITIES[b][1])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


rows = []
for i in range(N):
    o = np.random.choice(city_names, p=city_w)
    d = np.random.choice(city_names, p=city_w)
    while d == o:
        d = np.random.choice(city_names, p=city_w)
    vtype, vclass, load_t, load_raw, basis, factor = random.choices(TRUCKS, weights=WEIGHTS, k=1)[0]
    road = gcd_km(o, d) * random.uniform(1.18, 1.42)
    month = random.choices([1, 2, 11, 12], weights=[0.16, 0.46, 0.16, 0.22], k=1)[0]
    kg = road * factor / 1000.0
    phys = kg * random.uniform(0.955, 1.045)
    anomaly = 1 if random.random() < 0.08 else 0
    basis_flag = 1 if (basis != "载质量" or random.random() < 0.04) else 0
    speed = float(np.clip(np.random.lognormal(3.85, 0.35), 12, 98))
    if anomaly and random.random() < 0.35:
        speed = random.uniform(101, 160)
    rows.append({
        "waybill_no": "JDVB%011d" % random.randint(10 ** 10, 10 ** 11 - 1),
        "plate": "沪%s%04d" % (random.choice("ABCDEFG"), random.randint(1, 9999)),
        "vehicle_type": vtype,
        "vclass": vclass,
        "load_t": load_t,
        "load_raw_t": load_raw,
        "mass_basis": basis,
        "origin": o,
        "dest": d,
        "od_pair": o + "|" + d,
        "dist_km": round(road, 2),
        "month": month,
        "factor_gkm": factor,
        "er_gs": round(factor / 72.0, 3),
        "kg_co2": round(kg, 3),
        "kg_co2_phys": round(phys, 3),
        "delta_pct": round((phys - kg) / kg * 100, 3),
        "speed_kmh": round(speed, 2),
        "flag_dup": 0,
        "flag_anomaly": anomaly,
        "flag_basis": basis_flag,
        "origin_lat": CITIES[o][1], "origin_lon": CITIES[o][0],
        "dest_lat": CITIES[d][1], "dest_lon": CITIES[d][0],
    })

df = pd.DataFrame(rows)

# 造 3% 重复行，用来演示灰底标记
dup = df.sample(frac=0.03, random_state=7).copy()
dup["flag_dup"] = 1
df = pd.concat([df, dup], ignore_index=True)
df = df.sample(frac=1.0, random_state=11).reset_index(drop=True)

df.to_parquet(OUT / "wide_data.parquet", index=False)
df.to_parquet(OUT / "baseline_carbon.parquet", index=False)

# ---- 方案对比 ----
scen = pd.DataFrame([
    ("S0 柴油基准（现状）", 4502.04, 0.0, 0, 0.0, 0.0, 0.0, False),
    ("S1 全电动", 3101.27, 31.11, 170123, 3123.0, 54.5, 488438.0, False),
    ("S2 轻中电 + 重灰氢", 3070.0, 31.8, 90000, 1500.0, 60.0, 0.0, False),
    ("S3 轻中电 + 重蓝氢", 1958.0, 56.5, 95000, 1600.0, 59.4, 0.0, False),
    ("S4 轻中电 + 重绿氢 WTW", 1400.0, 68.9, 105000, 1700.0, 61.8, 0.0, False),
    ("S5 轻中电 + 重氢（主推）", 1019.6, 77.4, 48090, 500.8, 9.6, 13807.0, True),
    ("S7 绿氢 WTW + 电网清洁化", 1282.7, 71.5, 48090, 500.8, 9.6, 13807.0, True),
], columns=["scenario", "emission_t", "reduction_pct", "capex_wan",
            "opex_save_wan", "payback_years", "unit_cost_yuan_t", "meets_target"])
scen.to_parquet(OUT / "final_report.parquet", index=False)

# ---- 四维热点（长表）----
def hot(dim, key_col, val_col="kg_co2"):
    g = df.groupby(key_col, as_index=False)[val_col].sum().sort_values(val_col, ascending=False)
    g.columns = ["value", "metric"]
    g["dimension"] = dim
    g["value"] = g["value"].astype(str)
    g["metric"] = g["metric"] / 1000.0
    return g[["dimension", "value", "metric"]]

seg_bins = pd.cut(df["dist_km"], bins=[0, 10, 50, 200, 500, 1e9],
                  labels=["<10km", "10-50km", "50-200km", "200-500km", ">500km"])
tmp = df.assign(seg_bin=seg_bins)
hot_rows = [
    hot("车型", "vehicle_type"),
    hot("线路", "od_pair"),
    hot("月份", "month"),
]
g = tmp.groupby("seg_bin", observed=True, as_index=False)["kg_co2"].sum()
g.columns = ["value", "metric"]
g["dimension"] = "里程段"
g["value"] = g["value"].astype(str)
g["metric"] = g["metric"] / 1000.0
hot_rows.append(g[["dimension", "value", "metric"]])
pd.concat(hot_rows, ignore_index=True).to_parquet(OUT / "hotspots.parquet", index=False)

# ---- 同线带宽 ----
band = (df.groupby("od_pair")
          .agg(n=("factor_gkm", "size"), nt=("vehicle_type", "nunique"),
               fmin=("factor_gkm", "min"), fmax=("factor_gkm", "max"),
               kg=("kg_co2", "sum")).reset_index())
band = band[(band["n"] >= 3) & (band["nt"] >= 2) & (band["fmin"] > 0)]
band["ratio"] = band["fmax"] / band["fmin"]
band["kg_t"] = band["kg"] / 1000.0
band = band.drop(columns=["kg"]).sort_values("ratio", ascending=False)
band.to_parquet(OUT / "od_band.parquet", index=False)

# ---- 台账六字段 ----
ledger = pd.DataFrame([
    {"accounting_date": "2026-11-01", "boundary": "自营公路运输（范围一）",
     "emission_t": 1019.6, "quota_gap": "—", "book_cost": 4809.0,
     "note": "主推方案 S5：轻中型全电 + 重型氢能（使用端零排放）"},
    {"accounting_date": "2026-11-01", "boundary": "自营公路运输（范围一）",
     "emission_t": 1282.7, "quota_gap": "—", "book_cost": 4809.0,
     "note": "保守口径 S7：绿氢 WTW 87% + 电网清洁化"},
], columns=["accounting_date", "boundary", "emission_t", "quota_gap", "book_cost", "note"])
ledger.to_parquet(OUT / "ledger.parquet", index=False)

disclosure = {
    "boundary": "企业自营公路运输，直接排放（范围一）",
    "factor_source": "吴睿中等（2024）表 8，VSP = 2 kW/t 工况",
    "assumptions": "满载、数据期年化系数 3.0417",
    "period": "2023 年 1/2/11/12 月，共 120 天",
    "annual_factor": 3.0417,
    "limitations": "含 1,298 条时间戳异常段；载重按总质量口径换算",
}
(OUT / "disclosure.json").write_text(json.dumps(disclosure, ensure_ascii=False, indent=2), encoding="utf-8")

kpi = {
    "total_kg": float(df["kg_co2"].sum()),
    "total_km": float(df["dist_km"].sum()),
    "segments": int(len(df) - int(df["flag_dup"].sum())),
    "avg_speed": float(df.loc[df["speed_kmh"].between(1, 100), "speed_kmh"].mean()),
    "heavy_share": float(df.loc[df["vclass"] == 3, "kg_co2"].sum() / df["kg_co2"].sum()),
    "dup_rows": int(df["flag_dup"].sum()),
    "anomaly_rows": int(df["flag_anomaly"].sum()),
    "basis_rows": int(df["flag_basis"].sum()),
}
(OUT / "kpi.json").write_text(json.dumps(kpi, ensure_ascii=False, indent=2), encoding="utf-8")
(OUT / "_mock.flag").write_text("mock", encoding="utf-8")

print("rows:", len(df))
print("E0 t:", round(kpi["total_kg"] / 1000, 2))
print("km:", round(kpi["total_km"], 2))
print("heavy:", round(kpi["heavy_share"] * 100, 1), "%")
print("od_band rows:", len(band))
print("files:", sorted(p.name for p in OUT.iterdir()))
