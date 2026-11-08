# -*- coding: utf-8 -*-
r"""生成「披露测试集」：带已知答案的模拟数据，测对方碳会计能否正确生成披露。

口径来源（不手抄表头，直接读文件）：
  docs/finance/finance_fields_v2.json        33 列主表
  docs/finance/接口说明.md                    source_mode / verification / deduction / classification
  财务与碳会计功能设计与整改_20261105_v2.md    emission_fact 契约、Scope 规则、15% 双验、幂等
  docs/15_碳会计模块_v2.md:50-62              披露六要素
  config/calc_factors.json                   diesel 2.63 TTW / 0.53 WTT / grid 0.5568 WTW

运行：python tools\gen_disclosure_testset.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "交付_20261108_财务与碳账" / "06_披露测试集"
OUT.mkdir(parents=True, exist_ok=True)

fspec = json.loads((ROOT / "docs" / "finance" / "finance_fields_v2.json").read_text(encoding="utf-8"))
HEADERS = [f["header"] for f in fspec["fields"]]
HDR = {f["field"]: f["header"] for f in fspec["fields"]}
assert len(HEADERS) == 33, len(HEADERS)

fj = json.loads((ROOT / "config" / "calc_factors.json").read_text(encoding="utf-8"))
F = fj["co2_factors"]
DIESEL_TTW = F["diesel_ttw_kg_per_l"]["value"]
DIESEL_WTT = F["diesel_wtt_kg_per_l"]["value"]
GRID_WTW = F["grid_wtw_kg_per_kwh"]["value"]
FACTOR_VERSION = fj["version"]

PERIOD_START, PERIOD_END, PERIOD_DAYS = "2026-10-01", "2026-11-30", 61
ANNUAL_FACTOR = round(365 / PERIOD_DAYS, 4)
QUOTA_T = 1.0
BASE = "2026-11-08T10:00:00+08:00"

# ---------------- 1. emission_fact 用例 ----------------
FACT_COLS = ["fact_id", "trip_id", "plate", "vehicle_id", "period", "scope",
             "activity_type", "emission_kg", "method", "factor_id", "factor_version",
             "computed_at", "evidence_ref"]
facts = [
    ("F-001", "T-2026-1001", "苏A12345", "TRK-LOOP-001", "2026-10", 1, "柴油",
     round(100 * DIESEL_TTW, 3), "legal", "F-DIESEL-TTW", FACTOR_VERSION, BASE, "EV-001|100Lx2.63"),
    ("F-002", "T-2026-1002", "苏A12345", "TRK-LOOP-001", "2026-10", 1, "柴油",
     round(50 * DIESEL_TTW, 3), "legal", "F-DIESEL-TTW", FACTOR_VERSION, BASE, "EV-002|50Lx2.63"),
    ("F-003", "T-2026-1003", "沪A00001", "TRK-EV-001", "2026-10", 2, "电力",
     round(1000 * GRID_WTW, 3), "legal", "F-GRID-WTW", FACTOR_VERSION, BASE, "EV-003|1000kWhx0.5568"),
    ("F-004", "T-2026-1004", "苏A12345", "TRK-LOOP-001", "2026-10", 3, "上游燃料",
     round(100 * DIESEL_WTT, 3), "legal", "F-DIESEL-WTT", FACTOR_VERSION, BASE, "EV-004|100Lx0.53"),
    ("F-005", "T-2026-1101", "苏A12345", "TRK-LOOP-001", "2026-11", 1, "柴油",
     100.000, "legal", "F-DIESEL-TTW", FACTOR_VERSION, BASE, "EV-005|obd=114.9|双验14.9%"),
    ("F-006", "T-2026-1102", "苏A12345", "TRK-LOOP-001", "2026-11", 1, "柴油",
     100.000, "legal", "F-DIESEL-TTW", FACTOR_VERSION, BASE, "EV-006|obd=115.0|双验15.0%"),
    ("F-007", "T-2026-1103", "苏A12345", "TRK-LOOP-001", "2026-11", 1, "柴油",
     100.000, "legal", "F-DIESEL-TTW", FACTOR_VERSION, BASE, "EV-007|obd=115.1|双验15.1%"),
    ("F-007", "T-2026-1103", "苏A12345", "TRK-LOOP-001", "2026-11", 1, "柴油",
     100.000, "legal", "F-DIESEL-TTW", FACTOR_VERSION, "2026-11-08T10:05:00+08:00", "EV-007|同一条重投"),
    ("F-008", "T-2026-1031", "苏A12345", "TRK-LOOP-001", "2026-11", 1, "柴油",
     200.000, "legal", "F-DIESEL-TTW", FACTOR_VERSION, BASE,
     "EV-008|dep=2026-10-31T22:00|arr=2026-11-01T03:00"),
    ("F-012A", "T-2026-1104", "苏A12345", "TRK-LOOP-001", "2026-11", 1, "柴油",
     100.000, "legal", "F-DIESEL-TTW", FACTOR_VERSION, BASE, "EV-012|obd=118.0|双验18.0%"),
    ("F-012B", "T-2026-1104", "苏A12345", "TRK-LOOP-001", "2026-11", 1, "柴油",
     118.000, "obd", "F-DIESEL-TTW", FACTOR_VERSION, BASE, "EV-012|旁证轨，不并入汇总"),
    ("F-009", "T-2026-1109", "苏A12345", "TRK-LOOP-001", "2026-11", 1, "柴油",
     -5.000, "legal", "F-DIESEL-TTW", FACTOR_VERSION, BASE, "EV-009|负值应拒"),
    ("F-010", "T-2026-1110", "苏A12345", "TRK-LOOP-001", "2026-11", 1, "柴油",
     120.000, "vsp", "F-VSP", "", BASE, "EV-010|缺 factor_version 应拒"),
    ("F-011", "T-2026-1111", "苏A12345", "TRK-LOOP-001", "2026-11", 4, "柴油",
     88.000, "legal", "F-DIESEL-TTW", FACTOR_VERSION, BASE, "EV-011|scope=4 应拒"),
]
fact_df = pd.DataFrame(facts, columns=FACT_COLS)
fact_df.to_csv(OUT / "01_emission_fact_测试.csv", index=False, encoding="utf-8-sig")

# ---------------- 2. 期望值（由事实表算） ----------------
ok = fact_df[(fact_df.method == "legal") & fact_df.scope.isin([1, 2, 3])
             & (fact_df.emission_kg >= 0) & (fact_df.factor_version != "")]
ok = ok.drop_duplicates(subset=["fact_id"], keep="first")
s = lambda sc, per: round(float(ok[(ok.scope == sc) & (ok.period == per)].emission_kg.sum()), 3)
sc1_oct, sc2_oct, sc3_oct = s(1, "2026-10"), s(2, "2026-10"), s(3, "2026-10")
sc1_nov = s(1, "2026-11")
TOTAL = round(sc1_oct + sc2_oct + sc3_oct + sc1_nov, 3)
TOTAL_T = round(TOTAL / 1000, 4)
GAP_T = round(TOTAL_T - QUOTA_T, 4)
ANNUALIZED = round(TOTAL * ANNUAL_FACTOR, 1)

expected = {
    "schema": "disclosure_expected_v1",
    "data_source": "simulated",
    "boundary": "自营公路运输（范围一移动源燃烧）+ 外购电（范围二）+ 上游燃料 WTT（范围三）；不含仓储、制冷、空驶调车",
    "factor_source": {
        "factor_version": FACTOR_VERSION,
        "external_factor_version": "JCB2023-BASE-v1",
        "diesel_ttw_kg_per_l": DIESEL_TTW,
        "diesel_wtt_kg_per_l": DIESEL_WTT,
        "grid_wtw_kg_per_kwh": GRID_WTW,
        "note": "柴油走 TTW 2.63；WTW 3.16=2.63+0.53 只用于对外披露口径换算，不得与 2.63 混算",
    },
    "assumptions": [
        "柴油排放按 升 x 2.63 kg/L（TTW）计算；上游 0.53 单列范围三，不并入范围一",
        "电力按 0.5568 kgCO2e/kWh（全国电网平均，WTW）",
        "趟次期间按到达日归月；跨月行程不拆分到两个月份",
        "同一 trip 的 legal 为主账，obd/vsp 为旁证轨，三轨不相加",
        "缺失不做 0 填补：被拒记录整条不入账",
    ],
    "period": {"start": PERIOD_START, "end": PERIOD_END, "days": PERIOD_DAYS},
    "annual_factor": ANNUAL_FACTOR,
    "limitations": [
        "模拟数据（source_mode=simulated），不得用于对外披露或履约",
        "3 条被拒：负值、缺 factor_version、scope 非法",
        "2 组触发双验复核（F-007 15.1%、F-012 18.0%），入账但冻结汇总",
        "跨月行程未按里程拆分（本测试集按到达日整条归月）",
        "不含制冷、空驶调车、仓储与上游燃料以外的范围三",
    ],
    "monthly": {
        "2026-10": {"scope1_kg": sc1_oct, "scope2_kg": sc2_oct, "scope3_kg": sc3_oct},
        "2026-11": {"scope1_kg": sc1_nov, "scope2_kg": 0.0, "scope3_kg": 0.0},
    },
    "totals": {
        "scope1_kg": round(sc1_oct + sc1_nov, 3),
        "scope2_kg": sc2_oct,
        "scope3_kg": sc3_oct,
        "total_kg": TOTAL,
        "total_t": TOTAL_T,
        "annualized_kg": ANNUALIZED,
    },
    "idempotency": {"fact_id": "F-007", "submitted": 2, "counted": 1, "counted_kg": 100.0},
    "review_required": {"groups": 2, "fact_ids": ["F-007", "F-012A", "F-012B"],
                        "frozen": True, "must_still_be_posted": True},
    "rejected": {"count": 3, "fact_ids": ["F-009", "F-010", "F-011"],
                 "reasons": ["emission_kg 为负", "缺 factor_version", "scope 不在 1/2/3"]},
    "quota": {"quota_t": QUOTA_T, "used_t": TOTAL_T, "gap_t": GAP_T,
              "rule": "履约 = Σ已批准 - 配额；不得用已用量反推配额"},
}
(OUT / "03_期望披露_答案.json").write_text(json.dumps(expected, ensure_ascii=False, indent=2),
                                            encoding="utf-8")

# ---------------- 3. 33 列主表 ----------------
PLATE = "苏A12345"


def mk(record_id, cat, item, qty, unit, price, rate, seller, **kw):
    amount = round(qty * price, 2)
    tax = round(amount * rate, 2)
    d = {
        "record_id": record_id, "activity_id": "ACT-" + record_id,
        "source_mode": "simulated", "invoice_month": kw.pop("month", "2026-10"),
        "invoice_date": kw.pop("date", "2026-10-05"),
        "invoice_code": "033001900111",
        "invoice_no": "2526" + record_id.replace("-", "").zfill(8)[-8:],
        "seller_name": seller, "item_name": item, "expense_category": cat,
        "quantity": qty, "unit": unit, "unit_price": price,
        "fuel_liters": round(qty, 3) if cat == "燃料" else None,
        "amount_excluding_tax": amount, "tax_amount": tax,
        "invoice_amount": round(amount + tax, 2), "confirmed_cost": round(amount + tax, 2),
        "plate": PLATE, "waybill_no": "JDV" + record_id.replace("-", ""),
        "trip_id": "T-2026-1001", "run_id": "RUN-SIM-DISC-01",
        "verification_status": None, "posting_allowed": None,
        "reviewer": None, "reviewed_at": None,
        "evidence_ref": "EV-" + record_id, "note": "模拟测试数据，不可报销",
        "invoice_type": "普通发票", "tax_rate": "%d%%" % int(rate * 100),
        "deduction_status": "不可抵扣", "deductible_input_tax": 0.00,
        "deduction_basis": "模拟数据：默认不可抵扣",
    }
    d.update(kw)
    return d


mrows = [
    mk("R-01", "燃料", "*成品油*柴油", 100, "L", 6.46, 0.13, "中国石化销售股份有限公司上海101号加油站",
       verification_status="通过", posting_allowed=True, reviewer="测试员", reviewed_at="2026-10-06 09:00"),
    mk("R-02", "电力", "*供电*电费", 1000, "kWh", 0.80, 0.13, "国网上海市电力公司"),
    mk("R-03", "路桥", "*通行费*高速公路通行费", 1, "次", 500.00, 0.03, "上海城投高速公路有限公司"),
    mk("R-04", "维修", "*劳务*车辆维修费", 1, "次", 620.00, 0.13, "上海路安汽车服务有限公司"),
    mk("R-05", "碳配额购买", "*配额*全国碳市场配额", 1, "tCO2e", 90.00, 0.06, "上海环境能源交易所"),
    mk("R-06", "履约", "*服务*碳履约服务费", 1, "次", 200.00, 0.06, "上海碳资产管理有限公司"),
    mk("R-07", "其他", "*办公用品*打印纸", 1, "批", 120.00, 0.13, "上海文汇办公用品有限公司"),
    mk("R-08", "待分类", "*其他*待归类支出", 1, "批", 300.00, 0.13, "上海某供应商"),
]
master = pd.DataFrame([{HDR[k]: v for k, v in r.items()} for r in mrows], columns=HEADERS)
assert list(master.columns) == HEADERS

# ---------------- 4. 判分表 ----------------
checks = [
    ["J01", "范围分账", "10 月 Scope1=%.1f / Scope2=%.1f / Scope3=%.1f；11 月 Scope1=%.1f"
     % (sc1_oct, sc2_oct, sc3_oct, sc1_nov), "按 scope+period 汇总，逐项比对", "高", "01/03"],
    ["J02", "跨月归属", "F-008 归 2026-11，不落在 2026-10", "按 period 列筛选 F-008", "高", "01/03"],
    ["J03", "期间边界", "period 覆盖 2026-10-01~2026-11-30（61 天）", "披露 period 字段与天数", "中", "03"],
    ["J04", "三轨不混算", "Scope1 汇总不得出现 legal+obd 相加（若得 1112.5 即错）",
     "对比 F-012A 与 F-012B 是否重复计入", "高", "01/03"],
    ["J05", "双验阈值", "14.9% 与 15.0% 不触发；15.1% 与 18.0% 触发 review_required",
     "看复核标记与触发名单", "高", "01/03"],
    ["J06", "冻结语义", "复核行仍入账，但汇总须标「含 2 组待复核冻结」且不得直接出正式披露",
     "披露文本是否有冻结说明", "高", "03/05"],
    ["J07", "幂等", "F-007 投递 2 次只计 1 次（100.0 kg，不是 200.0）",
     "同一 fact_id 出现两次后的合计", "高", "01/03"],
    ["J08", "拒绝", "F-009/F-010/F-011 共 3 条不入账", "拒绝清单条数与 fact_id", "高", "01/03"],
    ["J09", "不填 0", "被拒记录不得以 0 计入，且不得补默认排放", "合计里查 0 值占位", "中", "01/03"],
    ["J10", "总量", "总排放 %.3f kg = %.4f t（容差 ±0.01 kg）" % (TOTAL, TOTAL_T),
     "总量比对", "高", "03"],
    ["J11", "配额缺口", "配额 1.0 t → 缺口 %.4f t；不得用已用量反推配额" % GAP_T,
     "履约表 gap 字段", "高", "03"],
    ["J12", "年化", "annual_factor=%.4f（365/61），年化 %.1f kg" % (ANNUAL_FACTOR, ANNUALIZED),
     "年化字段与乘算", "中", "03"],
    ["J13", "六要素齐全", "boundary/factor_source/assumptions/period/annual_factor/limitations 全部非空",
     "逐项检查空值", "高", "05"],
    ["J14", "因子版本与边界", "须写明 calc-factors-v1 与 JCB2023-BASE-v1；柴油 2.63 标 TTW，0.53 单列范围三",
     "文本检索 2.63 / 0.53 / TTW / 版本号", "高", "05"],
    ["J15", "费用类别", "8 类中仅 燃料/电力 进碳排放；碳配额购买、履约、路桥、维修、其他、待分类只进成本",
     "按 expense_category 对照是否被计入排放", "高", "02"],
    ["J16", "数据性质", "披露须标 simulated（模拟数据不得用于对外披露）", "data_source 字段与声明", "高", "05"],
    ["J17", "单位", "emission_kg 是 kg，不是 t（混用会差 1000 倍）", "数量级检查", "中", "01/03"],
]
wb = Workbook()
ws = wb.active
ws.title = "判分点"
hdr = ["编号", "用例", "期望结果", "判定方法", "权重", "看哪个文件"]
ws.append(hdr)
for r in checks:
    ws.append(r)

ws2 = wb.create_sheet("关键数值")
ws2.append(["项目", "期望值", "容差", "说明"])
for r in [
    ["10月 Scope1 (kg)", sc1_oct, 0.01, "F-001 263.0 + F-002 131.5"],
    ["10月 Scope2 (kg)", sc2_oct, 0.01, "1000 kWh x 0.5568"],
    ["10月 Scope3 (kg)", sc3_oct, 0.01, "100 L x 0.53（上游 WTT）"],
    ["11月 Scope1 (kg)", sc1_nov, 0.01, "F-005/006/007/008/012A 各 100/100/100/200/100"],
    ["总排放 (kg)", TOTAL, 0.01, "四段相加"],
    ["总排放 (t)", TOTAL_T, 0.0001, "kg/1000"],
    ["配额 (t)", QUOTA_T, 0, "测试假设"],
    ["缺口 (t)", GAP_T, 0.0001, "已用 - 配额"],
    ["annual_factor", ANNUAL_FACTOR, 0.0001, "365/61"],
    ["年化 (kg)", ANNUALIZED, 1.0, "总量 x annual_factor"],
    ["拒绝条数", 3, 0, "F-009/010/011"],
    ["待复核组数", 2, 0, "F-007 / F-012"],
]:
    ws2.append(r)

ws3 = wb.create_sheet("已知坑")
ws3.append(["坑", "说明"])
for a, b in [
    ("15.0% 是边界", "规则是严格大于 15% 才触发；等于 15.0% 不触发，四舍五入到 14.9% 会被漏判"),
    ("WTW 陷阱", "柴油 3.16 是 TTW+WTT。用 3.16 算 Scope1 会把上游算进范围一，总量虚高 20.2%"),
    ("三轨陷阱", "legal 100 与 obd 118 是同一趟的两个口径，相加得 218 是重复计碳"),
    ("跨月陷阱", "按开票日归月会把 F-008 错放到 10 月；契约要求按到达日"),
    ("幂等陷阱", "F-007 出现两次是故意的；按行数汇总会多算 100 kg"),
    ("0 值陷阱", "被拒记录如果补 0，披露会少报却看不出异常"),
    ("费用类别陷阱", "碳配额购买/履约是成本，不是排放；把它们计入排放等于重复计碳"),
    ("单位陷阱", "emission_kg 单位是 kg；写成 t 会差 1000 倍"),
]:
    ws3.append([a, b])

WIDTHS = {id(ws): [10, 22, 62, 40, 8, 12], id(ws2): [22, 14, 10, 52], id(ws3): [26, 92]}
for sheet in (ws, ws2, ws3):
    for i, w in enumerate(WIDTHS[id(sheet)], start=1):
        sheet.column_dimensions[get_column_letter(i)].width = w
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2F5597")
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for row_cells in sheet.iter_rows(min_row=2):
        for c in row_cells:
            c.alignment = Alignment(vertical="top", wrap_text=True)
wb.save(OUT / "04_判分表.xlsx")

# ---------------- 5. 33 列主表写出 ----------------
from openpyxl.styles import Font as F2
wb2 = Workbook()
s1 = wb2.active
s1.title = "财务主表_33列"
s1.append(HEADERS)
for r in master.itertuples(index=False):
    s1.append(list(r))
for cell in s1[1]:
    cell.font = F2(bold=True, color="FFFFFF")
    cell.fill = PatternFill("solid", fgColor="1F4E79")
s1.freeze_panes = "A2"
for i in range(1, 34):
    s1.column_dimensions[get_column_letter(i)].width = 16
s2 = wb2.create_sheet("说明")
s2.append(["项", "内容"])
for a, b in [
    ("schema_version", fspec["schema_version"]),
    ("列数", "33（严格按 v2 模板，顺序不可改）"),
    ("source_mode", "simulated（整本工作簿统一，不混 real）"),
    ("行数", "%d 行，覆盖 8 个费用类别各 1 行" % len(master)),
    ("用途", "测「费用类别 -> 是否进碳排放」的归类；只有 燃料/电力 应产生排放"),
    ("验证", "R-01 带双验四字段（通过/true/测试员/2026-10-06 09:00）"),
    ("注意", "金额=数量x单价(2位) 税额=金额x税率(2位) 价税合计=金额+税额"),
]:
    s2.append([a, b])
s2.column_dimensions["A"].width = 18
s2.column_dimensions["B"].width = 90
wb2.save(OUT / "02_财务主表_33列_模拟测试.xlsx")

print("[OK] 输出目录", OUT)
print("  01_emission_fact_测试.csv        %d 行" % len(fact_df))
print("  02_财务主表_33列_模拟测试.xlsx   %d 行 x 33 列" % len(master))
print("  03_期望披露_答案.json")
print("  04_判分表.xlsx                   %d 个判分点" % len(checks))
print("  期望：Scope1 %0.1f(10月) + %0.1f(11月) / Scope2 %0.1f / Scope3 %0.1f" % (sc1_oct, sc1_nov, sc2_oct, sc3_oct))
print("        总 %0.3f kg = %0.4f t | 缺口 %0.4f t | annual_factor %0.4f | 年化 %0.1f kg"
      % (TOTAL, TOTAL_T, GAP_T, ANNUAL_FACTOR, ANNUALIZED))
print("        拒绝 3 / 复核 2 组 / 幂等 F-007 计 1")
