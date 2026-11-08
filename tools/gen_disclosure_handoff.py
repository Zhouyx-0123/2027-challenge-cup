# -*- coding: utf-8 -*-
r"""生成「披露跑数测试集」：给碳账同学的模拟数据（匹配 33 列 v2 主表 + emission_fact 契约），
让他自己跑披露；期望值另存「评分方留存」，不进发给他的包。

运行：python tools\gen_disclosure_handoff.py
输出：交付_20261108_财务与碳账\07_披露跑数_给碳账同学\
        给碳账同学\   ← 直接发（3 个文件，无答案）
        评分方留存\   ← 自己留着判分（4 个文件）
"""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "交付_20261108_财务与碳账" / "07_披露跑数_给碳账同学"
SEND = BASE / "给碳账同学"
KEEP = BASE / "评分方留存"
for d in (SEND, KEEP):
    d.mkdir(parents=True, exist_ok=True)

SEED = 20261109
rng = random.Random(SEED)

fspec = json.loads((ROOT / "docs" / "finance" / "finance_fields_v2.json").read_text(encoding="utf-8"))
HEADERS = [f["header"] for f in fspec["fields"]]
HDR = {f["field"]: f["header"] for f in fspec["fields"]}
assert len(HEADERS) == 33

fj = json.loads((ROOT / "config" / "calc_factors.json").read_text(encoding="utf-8"))
F = fj["co2_factors"]
D_TTW = F["diesel_ttw_kg_per_l"]["value"]
D_WTT = F["diesel_wtt_kg_per_l"]["value"]
G_WTW = F["grid_wtw_kg_per_kwh"]["value"]
FV = fj["version"]

MONTHS = ["2026-09", "2026-10"]
D0, D1 = date(2026, 9, 1), date(2026, 10, 31)
PERIOD_DAYS = (D1 - D0).days + 1          # 61
ANNUAL_FACTOR = round(365 / PERIOD_DAYS, 4)
QUOTA_T = 90.0

FLEET = [
    ("苏A12345", "TRK-LOOP-001", "31t", 690, 31.0),
    ("苏A12346", "TRK-SH-002", "31t", 690, 31.0),
    ("苏A12347", "TRK-SH-003", "18t", 520, 18.0),
    ("苏A12348", "TRK-SH-004", "18t", 520, 18.0),
    ("苏A12349", "TRK-SH-005", "12t", 430, 12.0),
    ("苏A12350", "TRK-SH-006", "12t", 430, 12.0),
    ("苏A12351", "TRK-SH-007", "4.5t", 300, 4.5),
    ("苏A12352", "TRK-SH-008", "4.5t", 300, 4.5),
]
ROUTES = [
    ("上海", "南京", 300), ("上海", "杭州", 180), ("上海", "合肥", 420), ("上海", "武汉", 830),
    ("上海", "济南", 860), ("苏州", "南昌", 720), ("无锡", "郑州", 900), ("宁波", "福州", 660),
    ("杭州", "厦门", 900), ("南京", "青岛", 600), ("合肥", "长沙", 700), ("上海", "苏州", 100),
]

# ---------------- 1. 趟次 ----------------
trips = []
vi = 0
for i in range((D1 - D0).days + 1):
    day = D0 + timedelta(days=i)
    for k, (plate, vid, cls, ef, cap) in enumerate(FLEET):
        if (day.day + k) % 2:
            continue
        dep, arr, dist = rng.choice(ROUTES)
        km = round(dist * rng.uniform(0.96, 1.06), 1)
        load = round(rng.uniform(0.35, 0.95) * cap, 1)
        hh = rng.choice([6, 7, 8, 9, 13, 14, 20, 22])
        start = datetime(day.year, day.month, day.day, hh, rng.choice([0, 10, 20, 30, 40]))
        end = start + timedelta(minutes=int(km / rng.uniform(48, 62) * 60))
        trips.append({
            "trip_id": "T-%s-%04d" % (day.strftime("%Y%m%d"), k + 1),
            "plate": plate, "vehicle_id": vid, "cls": cls, "ef": ef,
            "date": day, "month": day.strftime("%Y-%m"),
            "dep": dep, "arr": arr, "km": km, "load_t": load,
            "start": start, "end": end,
            "emission": round(km * ef / 1000.0, 3),
        })
print("[1] trips:", len(trips))

# ---------------- 2. emission_fact ----------------
FC = ["fact_id", "trip_id", "plate", "vehicle_id", "period", "scope", "activity_type",
      "emission_kg", "method", "factor_id", "factor_version", "computed_at",
      "evidence_ref", "remark"]
facts, traps = [], []


def add(f, trap=None):
    facts.append(f)
    if trap:
        traps.append((f["fact_id"], trap))


for t in trips:
    add({"fact_id": "EF-%s-A" % t["trip_id"][2:], "trip_id": t["trip_id"], "plate": t["plate"],
         "vehicle_id": t["vehicle_id"], "period": t["month"], "scope": 1, "activity_type": "柴油",
         "emission_kg": t["emission"], "method": "legal", "factor_id": "F-DIESEL-TTW",
         "factor_version": FV, "computed_at": "2026-11-09T09:00:00+08:00",
         "evidence_ref": "EV-%s" % t["trip_id"], "remark": "%s-%s %.1fkm" % (t["dep"], t["arr"], t["km"])})

for t in trips[::3]:
    add({"fact_id": "EF-%s-W" % t["trip_id"][2:], "trip_id": t["trip_id"], "plate": t["plate"],
         "vehicle_id": t["vehicle_id"], "period": t["month"], "scope": 3, "activity_type": "上游燃料",
         "emission_kg": round(t["emission"] / D_TTW * D_WTT, 3), "method": "legal",
         "factor_id": "F-DIESEL-WTT", "factor_version": FV,
         "computed_at": "2026-11-09T09:00:00+08:00",
         "evidence_ref": "EV-%s-W" % t["trip_id"], "remark": "上游 WTT"})

for m in MONTHS:
    add({"fact_id": "EF-GRID-%s" % m.replace("-", ""), "trip_id": "T-GRID-%s" % m.replace("-", ""),
         "plate": "", "vehicle_id": "SITE-SH-01", "period": m, "scope": 2, "activity_type": "电力",
         "emission_kg": round(18000 * G_WTW, 3), "method": "legal", "factor_id": "F-GRID-WTW",
         "factor_version": FV, "computed_at": "2026-11-09T09:00:00+08:00",
         "evidence_ref": "EV-GRID-%s" % m, "remark": "仓库与充电桩用电 18000 kWh"})

# 双验四档（14.9 / 15.0 / 15.1 / 22.0）
review_pairs = []
for idx, pct in enumerate([0.149, 0.150, 0.151, 0.220]):
    t = trips[10 + idx * 17]
    legal = t["emission"]
    obd = round(legal * (1 + pct), 3)
    add({"fact_id": "EF-%s-B" % t["trip_id"][2:], "trip_id": t["trip_id"], "plate": t["plate"],
         "vehicle_id": t["vehicle_id"], "period": t["month"], "scope": 1, "activity_type": "柴油",
         "emission_kg": obd, "method": "obd", "factor_id": "F-DIESEL-TTW", "factor_version": FV,
         "computed_at": "2026-11-09T09:05:00+08:00", "evidence_ref": "EV-%s-OBD" % t["trip_id"],
         "remark": "OBD 实测轨，双验 %.1f%%" % (pct * 100)})
    review_pairs.append({"trip_id": t["trip_id"], "legal": legal, "obd": obd, "pct": pct})

# 幂等：3 条重投
for t in trips[5:8]:
    src = [f for f in facts if f["trip_id"] == t["trip_id"] and f["scope"] == 1 and f["method"] == "legal"][0]
    dup = dict(src)
    dup["computed_at"] = "2026-11-09T10:30:00+08:00"
    dup["remark"] = (src["remark"] or "") + "（重复投递）"
    add(dup, "dup")

# 非法 6 条
bad = [
    (trips[3], {"emission_kg": -12.5}, "负排放"),
    (trips[9], {"emission_kg": -3.2}, "负排放"),
    (trips[21], {"factor_version": ""}, "缺 factor_version"),
    (trips[33], {"factor_version": ""}, "缺 factor_version"),
    (trips[45], {"scope": 4}, "scope 非法"),
    (trips[57], {"scope": 0}, "scope 非法"),
]
for t, patch, why in bad:
    f = {"fact_id": "EF-%s-X" % t["trip_id"][2:], "trip_id": t["trip_id"], "plate": t["plate"],
         "vehicle_id": t["vehicle_id"], "period": t["month"], "scope": 1, "activity_type": "柴油",
         "emission_kg": round(t["emission"] * 0.98, 3), "method": "legal", "factor_id": "F-DIESEL-TTW",
         "factor_version": FV, "computed_at": "2026-11-09T09:00:00+08:00",
         "evidence_ref": "EV-%s-X" % t["trip_id"], "remark": why}
    f.update(patch)
    add(f, why)

# 一条 vsp 合法旁证（不并入 legal 汇总，也不该被拒）
tv = trips[60]
add({"fact_id": "EF-%s-V" % tv["trip_id"][2:], "trip_id": tv["trip_id"], "plate": tv["plate"],
     "vehicle_id": tv["vehicle_id"], "period": tv["month"], "scope": 1, "activity_type": "柴油",
     "emission_kg": round(tv["emission"] * 1.07, 3), "method": "vsp", "factor_id": "F-VSP",
     "factor_version": "vsp-model-v1", "computed_at": "2026-11-09T09:10:00+08:00",
     "evidence_ref": "EV-%s-VSP" % tv["trip_id"], "remark": "VSP 模型轨"}, "vsp_valid")

fact_df = pd.DataFrame(facts, columns=FC).sort_values(["period", "trip_id", "fact_id"]).reset_index(drop=True)
print("[2] facts:", len(fact_df), "| traps:", len(traps))
# ---------------- 3. 财务主表 33 列 ----------------
BUYER = "上海碳感知物流有限公司"
SELLERS = ["中国石化销售股份有限公司上海101号加油站", "中国石化销售股份有限公司上海沪宁高速站",
           "中国石油天然气股份有限公司上海浦东站", "中国石化销售股份有限公司江苏花桥站"]
rows = []
seq = [0]


def mk(cat, item, qty, unit, price, rate, seller, plate, month, dt, trip_id="", waybill="",
       status="一致", allow="是", note="模拟测试数据，不可报销", red=False, reviewer="周涛"):
    seq[0] += 1
    amount = round(qty * price, 2)
    tax = round(amount * rate, 2)
    return {
        "record_id": "INV-%s-%04d" % (month.replace("-", ""), seq[0]),
        "activity_id": "ACT-%s-%04d" % (month.replace("-", ""), seq[0]),
        "source_mode": "模拟",
        "invoice_month": month,
        "invoice_date": dt.strftime("%Y-%m-%d"),
        "invoice_code": "%012d" % rng.randint(310000000000, 319999999999),
        "invoice_no": "%08d" % rng.randint(10000000, 99999999),
        "seller_name": seller,
        "item_name": item,
        "expense_category": cat,
        "quantity": round(qty, 2),
        "unit": unit,
        "unit_price": price,
        "fuel_liters": round(float(qty), 1) if cat == "燃料" else None,
        "amount_excluding_tax": amount,
        "tax_amount": tax,
        "invoice_amount": round(amount + tax, 2),
        "confirmed_cost": round(amount + tax, 2),
        "plate": plate,
        "waybill_no": waybill,
        "trip_id": trip_id,
        "run_id": "RUN-2026%02d" % int(month[-2:]),
        "verification_status": status,
        "posting_allowed": allow,
        "reviewer": reviewer if allow == "是" else None,
        "reviewed_at": (dt + timedelta(days=2)).strftime("%Y-%m-%d %H:%M") if allow == "是" else None,
        "evidence_ref": "EV-" + str(seq[0]),
        "note": note,
        "invoice_type": "增值税专用发票" if cat in ("燃料", "电力") else "普通发票",
        "tax_rate": "%d%%" % int(round(rate * 100)),
        "deduction_status": "已抵扣" if cat in ("燃料", "电力") else "不可抵扣",
        "deductible_input_tax": tax if cat in ("燃料", "电力") else 0.00,
        "deduction_basis": "专票且用于应税项目" if cat in ("燃料", "电力") else "模拟数据：默认不可抵扣",
    }


for t in trips:
    liters = round(t["emission"] / D_TTW, 1)
    price = round(rng.uniform(6.28, 6.72), 2)
    rows.append(mk("燃料", "*成品油*柴油", liters, "L", price, 0.13,
                   rng.choice(SELLERS), t["plate"], t["month"], t["date"],
                   trip_id=t["trip_id"], waybill="JDV" + t["trip_id"].replace("T-", "").replace("-", "")))

for i in range((D1 - D0).days + 1):
    day = D0 + timedelta(days=i)
    rows.append(mk("路桥", "*通行费*高速公路通行费", 1, "次", round(rng.uniform(120, 480), 2), 0.03,
                   "上海城投高速公路有限公司", rng.choice([f[0] for f in FLEET]),
                   day.strftime("%Y-%m"), day))

for i in range(12):
    day = D0 + timedelta(days=rng.randint(0, 60))
    rows.append(mk("维修", rng.choice(["*劳务*车辆维修费", "*劳务*更换轮胎", "*劳务*车辆保养费"]),
                   1, "次", round(rng.uniform(300, 3600), 2), 0.13,
                   "上海路安汽车服务有限公司", rng.choice([f[0] for f in FLEET]),
                   day.strftime("%Y-%m"), day))

for m in MONTHS:
    day = date(int(m[:4]), int(m[5:]), 28)
    rows.append(mk("电力", "*供电*电费", 18000, "kWh", 0.78, 0.13, "国网上海市电力公司",
                   "", m, day, note="仓库与充电桩用电"))

for m in MONTHS:
    day = date(int(m[:4]), int(m[5:]), 15)
    rows.append(mk("碳配额购买", "*配额*全国碳市场配额", 40, "tCO2e", 89.0 if m == "2026-09" else 91.0,
                   0.06, "上海环境能源交易所", "", m, day))
    rows.append(mk("履约", "*服务*碳履约服务费", 1, "次", 200.00, 0.06, "上海碳资产管理有限公司", "", m, day))

for i in range(8):
    day = D0 + timedelta(days=rng.randint(0, 60))
    rows.append(mk("其他", "*办公用品*打印纸/耗材", 1, "批", round(rng.uniform(80, 600), 2), 0.13,
                   "上海文汇办公用品有限公司", "", day.strftime("%Y-%m"), day))

for i in range(6):
    day = D0 + timedelta(days=rng.randint(0, 60))
    rows.append(mk("待分类", "*其他*待归类支出", 1, "批", round(rng.uniform(150, 900), 2), 0.13,
                   "上海某供应商", "", day.strftime("%Y-%m"), day))

# 三行特殊：2 行待核验（posting_allowed = null）、1 行红冲（负数）
d1 = date(2026, 9, 24)
rows.append(mk("燃料", "*成品油*柴油", 118.0, "L", 6.55, 0.13, SELLERS[0], "苏A12347",
               "2026-09", d1, status="待核验", allow="待确认", note="缺 OBD 同期数据，待核验"))
rows.append(mk("维修", "*劳务*发动机大修", 1, "次", 1800.00, 0.13, "上海路安汽车服务有限公司", "苏A12349",
               "2026-10", date(2026, 10, 8), status="待核验", allow="待确认", note="待核验：缺维修明细"))
rows.append(mk("维修", "*劳务*车辆维修费", -300.00 / 1.13, "次", 1.13, 0.13, "上海路安汽车服务有限公司",
               "苏A12350", "2026-10", date(2026, 10, 21), status="异常待复核", allow="待确认",
               note="红冲（负数）发票，按规则转复核", red=True))

# 两张 9 月末票据：财务侧有票、事实侧无对应趟次（对账差异来源，约 195.5 L）
for k in range(2):
    rows.append(mk("燃料", "*成品油*柴油", 96.0 + k * 4, "L", 6.44, 0.13, SELLERS[3],
                   "苏A12351" if k == 0 else "苏A12352", "2026-09", date(2026, 9, 30),
                   note="9 月 30 日开票；无对应 OBD 趟次，对账时按差异解释"))

master = pd.DataFrame([{HDR[k]: v for k, v in r.items()} for r in rows], columns=HEADERS)
master = master.sort_values(["开票月份", "开票日期", "记录ID"]).reset_index(drop=True)
print("[3] master rows:", len(master))

# ---------------- 4. 期望值 ----------------
f = fact_df.drop_duplicates(subset=["fact_id"], keep="first")
dup_ids = sorted(set(fact_df[fact_df.duplicated("fact_id", keep="first")].fact_id))
valid_mask = (f["scope"].isin([1, 2, 3])) & (f["emission_kg"] >= 0) & (f["factor_version"].fillna("") != "")
rejected = f[~valid_mask]
legal = f[valid_mask & (f["method"] == "legal")]
sc = {(p, s): round(float(legal[(legal.period == p) & (legal.scope == s)].emission_kg.sum()), 3)
      for p in MONTHS for s in (1, 2, 3)}
s1 = round(sum(v for (p, ss), v in sc.items() if ss == 1), 3)
s2 = round(sum(v for (p, ss), v in sc.items() if ss == 2), 3)
s3 = round(sum(v for (p, ss), v in sc.items() if ss == 3), 3)
TOTAL = round(s1 + s2 + s3, 3)
TOTAL_T = round(TOTAL / 1000, 4)
GAP = round(TOTAL_T - QUOTA_T, 4)
ANN = round(TOTAL * ANNUAL_FACTOR, 1)

review_hit = [p for p in review_pairs if p["pct"] > 0.15]

# 主表成本（只算 posting_allowed is True）
inc = master[master["是否允许入账"] == "是"]
cost_total = round(float(inc["价税合计（元）"].sum()), 2)
cost_by_cat = {k: round(float(v), 2) for k, v in inc.groupby("费用类别")["价税合计（元）"].sum().items()}
cost_by_month = {k: round(float(v), 2) for k, v in inc.groupby("开票月份")["价税合计（元）"].sum().items()}
fuel_liters = round(float(inc[inc["费用类别"] == "燃料"]["柴油量（L）"].sum()), 1)
fuel_kg_from_invoice = round(fuel_liters * D_TTW, 3)

expected = {
    "schema": "disclosure_expected_v2",
    "dataset": "07_披露跑数_给碳账同学",
    "seed": SEED,
    "data_source": "simulated",
    "period": {"start": "2026-09-01", "end": "2026-10-31", "days": PERIOD_DAYS},
    "annual_factor": ANNUAL_FACTOR,
    "quota_t": QUOTA_T,
    "inputs": {"master_rows": int(len(master)), "fact_rows": int(len(fact_df)),
               "unique_fact_ids": int(fact_df.fact_id.nunique()),
               "trip_count": int(len(trips))},
    "monthly": {p: {"scope1_kg": sc[(p, 1)], "scope2_kg": sc[(p, 2)], "scope3_kg": sc[(p, 3)]} for p in MONTHS},
    "totals": {"scope1_kg": s1, "scope2_kg": s2, "scope3_kg": s3, "total_kg": TOTAL,
               "total_t": TOTAL_T, "annualized_kg": ANN, "gap_t": GAP},
    "counts": {"valid_legal_facts": int(len(legal)), "rejected": int(len(rejected)),
               "duplicate_fact_ids": len(dup_ids), "review_groups": len(review_hit)},
    "rejected_ids": sorted(rejected.fact_id.tolist()),
    "duplicate_ids": dup_ids,
    "review_trip_ids": [p["trip_id"] for p in review_hit],
    "review_all_pairs": [{"trip_id": p["trip_id"], "delta_pct": round(p["pct"] * 100, 2),
                          "hit": p["pct"] > 0.15} for p in review_pairs],
    "master": {"rows": int(len(master)), "included_rows": int(len(inc)),
               "excluded_rows": int(len(master) - len(inc)),
               "cost_total_yuan": cost_total, "cost_by_category": cost_by_cat,
               "cost_by_month": cost_by_month, "fuel_liters": fuel_liters,
               "fuel_kg_ttw_from_invoice": fuel_kg_from_invoice},
    "checks": [
        {"id": "C01", "desc": "总排放 kg", "expect": TOTAL, "tol": 0.5},
        {"id": "C02", "desc": "总排放 t", "expect": TOTAL_T, "tol": 0.0005},
        {"id": "C03", "desc": "Scope1 kg", "expect": s1, "tol": 0.5},
        {"id": "C04", "desc": "Scope2 kg", "expect": s2, "tol": 0.5},
        {"id": "C05", "desc": "Scope3 kg", "expect": s3, "tol": 0.5},
        {"id": "C06", "desc": "9 月 Scope1 kg", "expect": sc[("2026-09", 1)], "tol": 0.5},
        {"id": "C07", "desc": "10 月 Scope1 kg", "expect": sc[("2026-10", 1)], "tol": 0.5},
        {"id": "C08", "desc": "被拒条数", "expect": int(len(rejected)), "tol": 0},
        {"id": "C09", "desc": "幂等命中条数", "expect": len(dup_ids), "tol": 0},
        {"id": "C10", "desc": "双验复核组数", "expect": len(review_hit), "tol": 0},
        {"id": "C11", "desc": "年化 kg", "expect": ANN, "tol": 2.0},
        {"id": "C12", "desc": "配额缺口 t", "expect": GAP, "tol": 0.001},
        {"id": "C13", "desc": "成本合计 元", "expect": cost_total, "tol": 1.0},
        {"id": "C14", "desc": "计入成本行数", "expect": int(len(inc)), "tol": 0},
    ],
}
print("[4] total %.3f kg = %.4f t | S1 %.1f S2 %.1f S3 %.1f | reject %d dup %d review %d"
      % (TOTAL, TOTAL_T, s1, s2, s3, len(rejected), len(dup_ids), len(review_hit)))

# ---------------- 5. 写文件 ----------------
fact_out = fact_df.rename(columns={"remark": "备注"})
fact_out.to_csv(SEND / "02_emission_fact_202609-10.csv", index=False, encoding="utf-8-sig")

wb = Workbook()
ws = wb.active
ws.title = "财务主表"
ws.append(HEADERS)
for r in master.itertuples(index=False):
    ws.append([None if pd.isna(v) else v for v in r])
for c in ws[1]:
    c.font = Font(bold=True, color="FFFFFF")
    c.fill = PatternFill("solid", fgColor="1F4E79")
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
ws.freeze_panes = "A2"
ws.auto_filter.ref = "A1:AG%d" % (len(master) + 1)
for i in range(1, 34):
    ws.column_dimensions[get_column_letter(i)].width = 15
wb.save(SEND / "01_财务主表_33列_202609-10.xlsx")

(KEEP / "01_期望披露_答案.json").write_text(json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8")

cases = []
for fid, why in traps:
    cases.append({"fact_id": fid, "type": why})
for p in review_pairs:
    cases.append({"fact_id": p["trip_id"], "type": "review %.1f%% -> %s" % (p["pct"] * 100, "hit" if p["pct"] > .15 else "miss")})
cases.append({"fact_id": "INV-9月末2行", "type": "财务有票无对应趟次（约 195.5 L / 514 kg）"})
cases.append({"fact_id": "INV-待核验2行+红冲1行", "type": "是否允许入账=待确认，不计成本"})
pd.DataFrame(cases).to_csv(KEEP / "04_用例清单_私用.csv", index=False, encoding="utf-8-sig")
print("[5] files written ->", SEND, "|", KEEP)

