
# -*- coding: utf-8 -*-
"""生成 07 披露跑数测试集 的说明文档、回填模板、评分说明与判分表（可重跑）。"""
import json
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

ROOT = Path(r"D:\oceanbase02\2027-challenge-cup\交付_20261108_财务与碳账\07_披露跑数_给碳账同学")
GIVE = ROOT / "给碳账同学"
KEEP = ROOT / "评分方留存"
ANS = json.loads((KEEP / "01_期望披露_答案.json").read_text(encoding="utf-8"))

FACT = pd.read_csv(GIVE / "02_emission_fact_202609-10.csv")
MASTER = pd.read_excel(GIVE / "01_财务主表_33列_202609-10.xlsx", sheet_name="财务主表")

_fv = FACT["factor_version"].astype(str).str.strip()
_fv = _fv.where(~_fv.isin(["", "nan", "None"]), None)
legal = FACT[(FACT["method"] == "legal") & (FACT["scope"].isin([1, 2, 3])) & (FACT["emission_kg"] >= 0)]
legal = legal[_fv.reindex(legal.index).notna()]
legal = legal.drop_duplicates("fact_id")
s1 = legal[legal.scope == 1].emission_kg.sum()
s2 = legal[legal.scope == 2].emission_kg.sum()
s3 = legal[legal.scope == 3].emission_kg.sum()
tot = s1 + s2 + s3

obd_all = FACT[FACT["method"].isin(["obd", "vsp"])].emission_kg.clip(lower=0).sum()
dup_extra = FACT[FACT["fact_id"].isin(ANS["duplicate_ids"])].emission_kg.clip(lower=0).sum() / 2.0

wrong = {
    "柴油因子用 3.16（WTW）": round(tot + s1 * (3.16 / 2.63 - 1), 2),
    "legal+obd+vsp 相加": round(tot + obd_all, 2),
    "不做幂等（重复计入）": round(tot + dup_extra, 2),
    "Scope3 并入 Scope1": round(tot, 2),
}
wrong_s1_merged = round(s1 + s3, 2)

HDR = Font(bold=True, size=11, color="FFFFFF")
FILL = PatternFill("solid", fgColor="1F6FEB")
THIN = Side(style="thin", color="BFBFBF")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def style(ws, widths):
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for c in ws[1]:
        c.font = HDR
        c.fill = FILL
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"


# ---------------- 给碳账同学：回填模板 ----------------
wb = Workbook()
ws = wb.active
ws.title = "01_总量与月度"
ws.append(["项目", "口径", "单位", "你填的数值", "备注"])
rows = [
    ("报告期", "2026-09-01 至 2026-10-31", "—", "61 天", "按趟到达日归月"),
    ("总排放", "Scope1+2+3，去重后", "kgCO2e", "", ""),
    ("总排放", "同上", "tCO2e", "", ""),
    ("Scope1", "自有车辆燃料燃烧（TTW）", "kgCO2e", "", ""),
    ("Scope2", "外购电力（location-based）", "kgCO2e", "", ""),
    ("Scope3", "上游燃料 WTT 等", "kgCO2e", "", ""),
    ("2026-09 Scope1", "", "kgCO2e", "", ""),
    ("2026-09 Scope2", "", "kgCO2e", "", ""),
    ("2026-09 Scope3", "", "kgCO2e", "", ""),
    ("2026-10 Scope1", "", "kgCO2e", "", ""),
    ("2026-10 Scope2", "", "kgCO2e", "", ""),
    ("2026-10 Scope3", "", "kgCO2e", "", ""),
    ("年化排放", "按报告期天数折算 365 天", "kgCO2e", "", "需写明折算系数"),
    ("配额", "本期配额", "tCO2e", "90", "给定值"),
    ("配额缺口", "总排放 − 配额", "tCO2e", "", "正数为缺口"),
    ("成本合计", "仅『是否允许入账=是』的行，取确认成本", "元", "", ""),
]
for r in rows:
    ws.append(list(r))
style(ws, [18, 34, 10, 16, 26])

ws2 = wb.create_sheet("02_处理清单")
ws2.append(["清单", "数量", "明细（ID 或趟次，逗号分隔）"])
for r in [("被拒 fact 条数", "", ""), ("幂等命中条数", "", ""), ("双验复核组数", "", ""),
          ("汇总明细行数（参与成本汇总）", "", "")]:
    ws2.append(list(r))
style(ws2, [30, 10, 70])

ws3 = wb.create_sheet("03_成本分类")
ws3.append(["费用类别", "金额（元）", "行数"])
for cat in ["燃料", "电力", "路桥", "维修", "碳配额购买", "履约", "其他", "待分类"]:
    ws3.append([cat, "", ""])
ws3.append(["合计", "", ""])
style(ws3, [16, 16, 10])

ws4 = wb.create_sheet("00_填写说明")
for line in [
    "1. 只填『你填的数值』与明细列，不要改动本文件的表头。",
    "2. Scope 口径：Scope1 只放自有车辆燃料燃烧（TTW）；上游 WTT 单列 Scope3，不得并入 Scope1。",
    "3. 计量轨：method=legal 是主账；method=obd / vsp 是旁证，不得与 legal 相加。",
    "4. 幂等：同一 fact_id 只计一次（数据里有 3 个 fact_id 被重投）。",
    "5. 拒绝：负值、缺 factor_version、scope 不在 1/2/3 的记录不得补 0 计入，需列入被拒清单。",
    "6. 双验：|发票油量 − 实测油量| / 实测油量 严格大于 15% 才进复核；正好 15.0% 不进。",
    "7. 成本：只汇总『是否允许入账=是』的行，取『确认成本（元）』列；不能把不含税/税额/价税合计/确认成本相加。",
    "8. 缺失字段留空，不要用 0 代替未知。",
    "9. 交回本文件 + 一份口径说明（因子来源、年化系数、去重与拒绝规则）。",
]:
    ws4.append([line])
ws4.column_dimensions["A"].width = 110
wb.save(GIVE / "03_回填_披露结果模板.xlsx")

# ---------------- 评分方留存：判分表 ----------------
wb = Workbook()
ws = wb.active
ws.title = "判分点"
ws.append(["编号", "判分点", "标准值", "容差", "权重", "判定", "备注"])
for c in ANS["checks"]:
    w = 2 if c["id"] in ("C01", "C02", "C03", "C04", "C05") else 1
    ws.append([c["id"], c["desc"], c["expect"], c["tol"], w, "", ""])
extra = [
    ("C15", "被拒 fact_id 清单", "、".join(ANS["rejected_ids"]), "完全一致", 2, "", "缺一条或多一条都算错"),
    ("C16", "幂等命中 fact_id 清单", "、".join(ANS["duplicate_ids"]), "完全一致", 2, "", "多计或少计都会改变总量"),
    ("C17", "双验复核趟次", "、".join(ANS["review_trip_ids"]), "完全一致", 2, "", "14.9/15.0 不应进复核"),
    ("C18", "月度矩阵（3 范围 × 2 月）", "见 03_标准答案", "±0.5 kg", 2, "", ""),
    ("C19", "成本按类别 8 类", "见 03_标准答案", "±1 元", 1, "", ""),
    ("C20", "口径说明（因子/年化/去重/拒绝）", "四项都要写", "定性", 1, "", "缺项按比例扣"),
]
for e in extra:
    ws.append(list(e))
style(ws, [8, 30, 40, 12, 8, 10, 34])

ws2 = wb.create_sheet("关键数值")
ws2.append(["项目", "数值", "说明"])
kv = [
    ("总排放 kg", ANS["totals"]["total_kg"], "去重后 legal、scope 1/2/3"),
    ("总排放 t", ANS["totals"]["total_t"], ""),
    ("Scope1 kg", ANS["totals"]["scope1_kg"], "TTW 2.63 kgCO2e/L"),
    ("Scope2 kg", ANS["totals"]["scope2_kg"], "0.5568 kgCO2e/kWh"),
    ("Scope3 kg", ANS["totals"]["scope3_kg"], "上游 WTT 0.53 kgCO2e/L"),
    ("年化系数", ANS["annual_factor"], "365/61"),
    ("年化 kg", ANS["totals"]["annualized_kg"], ""),
    ("配额 t", ANS["quota_t"], "给定"),
    ("缺口 t", ANS["totals"]["gap_t"], "总排放 − 配额"),
    ("有效 legal fact", ANS["counts"]["valid_legal_facts"], ""),
    ("被拒", ANS["counts"]["rejected"], ""),
    ("幂等命中", ANS["counts"]["duplicate_fact_ids"], ""),
    ("复核组", ANS["counts"]["review_groups"], ""),
    ("成本合计 元", ANS["master"]["cost_total_yuan"], "计入 %d 行 / 排除 %d 行" % (ANS["master"]["included_rows"], ANS["master"]["excluded_rows"])),
    ("发票柴油 L", ANS["master"]["fuel_liters"], "比 fact 侧多出 2 张 9 月末票据"),
]
for r in kv:
    ws2.append(list(r))
style(ws2, [26, 18, 40])

ws3 = wb.create_sheet("错法反查")
ws3.append(["错法", "得到的总量 kg", "与正确值的差", "说明"])
for k, v in wrong.items():
    ws3.append([k, v, round(v - tot, 2), "Scope3 并入 Scope1 时 Scope1 变 " + str(wrong_s1_merged) + " kg，总量不变"])
style(ws3, [30, 18, 18, 56])
wb.save(KEEP / "02_判分表.xlsx")

# ---------------- 给碳账同学：README ----------------
monthly = ANS["monthly"]
readme = "# 给碳账同学：一次碳披露跑数测试（模拟数据）\n\n" + f"""这是一套**可以跑通的模拟数据**，目的是验证「拿到财务主表 + 排放事实表后，能不能按既定口径生成一版可交付的碳披露」。
数据是我们造的，**不是真实业务数据**；请当成一次真实作业来做，但不必对外使用。

- 报告期：{ANS['period']['start']} ~ {ANS['period']['end']}（{ANS['period']['days']} 天）
- 车辆：8 台（4 种吨位），趟次：{ANS['inputs']['trip_count']} 趟
- 财务主表：{ANS['inputs']['master_rows']} 行（33 列）
- 排放事实表：{ANS['inputs']['fact_rows']} 行（{ANS['inputs']['unique_fact_ids']} 个唯一 fact_id）

## 一、文件

| 文件 | 说明 |
|---|---|
| 01_财务主表_33列_202609-10.xlsx | sheet 名 财务主表，33 列，{ANS['inputs']['master_rows']} 行；结构与你们 2026-10-29 模板一致（28 列 + v2 的 5 列） |
| 02_emission_fact_202609-10.csv | 排放事实表，14 列（契约 13 列 + 备注） |
| 03_回填_披露结果模板.xlsx | 请把结果填在这里交回 |

## 二、口径（照这个算，别自己改）

**因子**
- 柴油燃烧（TTW）：2.63 kgCO2e/L（factor_id=F-DIESEL-TTW）
- 柴油上游（WTT）：0.53 kgCO2e/L（factor_id=F-DIESEL-WTT，**单列 Scope3**）
- 外购电力：0.5568 kgCO2e/kWh

**范围**
- Scope1 = 自有车辆柴油燃烧（TTW）
- Scope2 = 外购电力
- Scope3 = 上游燃料 WTT 等
- **上游 WTT 不得并入 Scope1**；三轨（legal / obd / vsp）不得相加

**归属期**：按趟到达日归月（period 列已给出），不要用开票日期代替。

**计量轨**：method=legal 是主账；method=obd、method=vsp 是旁证，只做对账，不进总量。

**幂等**：同一 fact_id 只计一次。数据里有重投记录。

**拒绝**：负值、缺 factor_version、scope 不在 1/2/3 的记录，**不得补 0 计入**，列入被拒清单。

**双验**：|发票油量 − 实测油量| / 实测油量 **严格大于 15%** 才进复核；正好 15.0% 不进。

**成本**：只汇总「是否允许入账 = 是」的行，取「确认成本（元）」列。
**不能**把 不含税金额 / 税额 / 价税合计 / 确认成本 相加。

## 三、请交回

1. 03_回填_披露结果模板.xlsx（总量、月度矩阵、处理清单、成本分类）
2. 一页口径说明：因子来源、年化系数怎么算、去重与拒绝怎么判、双验阈值怎么用

## 四、两个格式约定（避免导入报错）

- 「数据性质」一列我们填的是**模拟**（你们模板的写法）。若你们的导入接口要求 simulated，整列替换即可，不影响其他列。
- 「费用类别」按 v2 的 **8 类**（燃料/电力/路桥/维修/碳配额购买/履约/其他/待分类）。若你们的表只有 7 类（无「维修」「待分类」），把这两类并入「其他」并在备注保留原文。

## 五、已知边界

- 财务侧有 2 张 9 月末柴油票在事实表里没有对应趟次（对账差异来源），这是有意设计的，不需要你改数据，请在口径说明里说明你怎么处理。
- 数据里有多商品拆行、红冲、待核验、待分类等真实形态，按表内「备注」和「是否允许入账」处理即可。
"""
(GIVE / "00_README_给碳账同学.md").write_text(readme, encoding="utf-8")

# ---------------- 评分方留存：评分说明 ----------------
cats = "；".join(f"{k} {v:,}" for k, v in sorted(ANS['master']['cost_by_category'].items(), key=lambda x: -x[1]))
score = f"""# 07 评分说明（内部留存，不要发给对方）

数据由 tools/gen_disclosure_handoff.py 生成（seed {ANS['seed']}，可重跑复现）。
答案文件：01_期望披露_答案.json；判分表：02_判分表.xlsx；样例：03_标准答案_披露样例.md。

## 一、标准答案（总量）

| 项目 | 数值 |
|---|---|
| 总排放 | {ANS['totals']['total_kg']:,} kg = **{ANS['totals']['total_t']} t** |
| Scope1 | {ANS['totals']['scope1_kg']:,} kg |
| Scope2 | {ANS['totals']['scope2_kg']:,} kg |
| Scope3 | {ANS['totals']['scope3_kg']:,} kg |
| 年化（×{ANS['annual_factor']}） | {ANS['totals']['annualized_kg']:,} kg |
| 配额 / 缺口 | {ANS['quota_t']} t / **{ANS['totals']['gap_t']} t** |

月度：2026-09 S1 {monthly['2026-09']['scope1_kg']:,} / S2 {monthly['2026-09']['scope2_kg']:,} / S3 {monthly['2026-09']['scope3_kg']:,}；
2026-10 S1 {monthly['2026-10']['scope1_kg']:,} / S2 {monthly['2026-10']['scope2_kg']:,} / S3 {monthly['2026-10']['scope3_kg']:,}。

计数：有效 legal fact {ANS['counts']['valid_legal_facts']} 条；被拒 {ANS['counts']['rejected']} 条；幂等命中 {ANS['counts']['duplicate_fact_ids']} 条；复核 {ANS['counts']['review_groups']} 组。

被拒：{', '.join(ANS['rejected_ids'])}
幂等：{', '.join(ANS['duplicate_ids'])}
复核：{', '.join(ANS['review_trip_ids'])}（14.9% 与 15.0% 两趟**不应**进复核）

## 二、成本

合计 **{ANS['master']['cost_total_yuan']:,} 元**，计入 {ANS['master']['included_rows']} 行，排除 {ANS['master']['excluded_rows']} 行（2 行待核验 + 1 行红冲，是否允许入账 = 待确认）。
按类别：{cats}
按月份：{ANS['master']['cost_by_month']['2026-09']:,} / {ANS['master']['cost_by_month']['2026-10']:,}。
发票柴油合计 {ANS['master']['fuel_liters']:,} L，折 {ANS['master']['fuel_kg_ttw_from_invoice']:,} kg（比 Scope1 多出约 514 kg，即 2 张无对应趟次的 9 月末票据）。

## 三、四条错法反查

| 错法 | 得到 kg | 偏差 |
|---|---|---|
| 柴油用 WTW 3.16 | {wrong['柴油因子用 3.16（WTW）']:,} | +{round(wrong['柴油因子用 3.16（WTW）'] - tot, 2):,} |
| legal + obd + vsp 相加 | {wrong['legal+obd+vsp 相加']:,} | +{round(wrong['legal+obd+vsp 相加'] - tot, 2):,} |
| 不做幂等 | {wrong['不做幂等（重复计入）']:,} | +{round(wrong['不做幂等（重复计入）'] - tot, 2):,} |
| Scope3 并入 Scope1 | {wrong['Scope3 并入 Scope1']:,} | 0（Scope1 变 {wrong_s1_merged:,}） |

## 四、判分

20 个判分点（C01–C20），权重见判分表。C01–C12 是数值，C13/C14 是成本，C15–C17 是清单一致性，C18/C19 是结构，C20 是定性口径说明。
**只看结果不看过程是不行的**：如果总量对但清单错（例如把 15.0% 误判成复核），仍要扣分 —— 那说明规则是碰巧对的。
"""
(KEEP / "00_评分说明.md").write_text(score, encoding="utf-8")

# ---------------- 评分方留存：标准答案样例 ----------------
cost_rows = "\n".join(f"| {k} | {v:,} |" for k, v in sorted(ANS['master']['cost_by_category'].items(), key=lambda x: -x[1]))
ans = f"""# 标准答案：披露样例（内部留存）

## 一、范围与期间

报告期 2026-09-01~2026-10-31（61 天），8 台车、{ANS['inputs']['trip_count']} 趟、{ANS['inputs']['unique_fact_ids']} 个唯一 fact_id。

## 二、排放总量

| 范围 | 9 月 | 10 月 | 合计 |
|---|---|---|---|
| Scope1（柴油 TTW） | {monthly['2026-09']['scope1_kg']:,} kg | {monthly['2026-10']['scope1_kg']:,} kg | **{ANS['totals']['scope1_kg']:,} kg** |
| Scope2（电力） | {monthly['2026-09']['scope2_kg']:,} kg | {monthly['2026-10']['scope2_kg']:,} kg | **{ANS['totals']['scope2_kg']:,} kg** |
| Scope3（上游 WTT） | {monthly['2026-09']['scope3_kg']:,} kg | {monthly['2026-10']['scope3_kg']:,} kg | **{ANS['totals']['scope3_kg']:,} kg** |
| 合计 | | | **{ANS['totals']['total_kg']:,} kg = {ANS['totals']['total_t']} t** |

年化：{ANS['totals']['total_kg']:,} × {ANS['annual_factor']} = {ANS['totals']['annualized_kg']:,} kg。
配额 {ANS['quota_t']} t → 缺口 **{ANS['totals']['gap_t']} t**。

## 三、处理清单

- 有效 legal fact：{ANS['counts']['valid_legal_facts']} 条（去重后）
- 被拒 {ANS['counts']['rejected']} 条：{', '.join(ANS['rejected_ids'])}
- 幂等命中 {ANS['counts']['duplicate_fact_ids']} 条：{', '.join(ANS['duplicate_ids'])}
- 双验复核 {ANS['counts']['review_groups']} 组：{', '.join(ANS['review_trip_ids'])}
- 边界未进：T-20260903-0006（14.9%）、T-20260907-0008（15.0%）

## 四、成本

合计 {ANS['master']['cost_total_yuan']:,} 元（{ANS['master']['included_rows']} 行计入 / {ANS['master']['excluded_rows']} 行排除）。

| 类别 | 金额（元） |
|---|---|
{cost_rows}

## 五、口径说明（应写到的四点）

1. 因子：柴油 TTW 2.63、WTT 0.53（单列 Scope3）、电 0.5568；factor_version=calc-factors-v1。
2. 年化：365 / {ANS['period']['days']} = {ANS['annual_factor']}。
3. 去重与拒绝：fact_id 幂等；负值/缺因子版本/非法 scope 拒绝且不补 0。
4. 双验：严格大于 15% 才复核，边界值 15.0% 不进。
"""
(KEEP / "03_标准答案_披露样例.md").write_text(ans, encoding="utf-8")

print("wrong:", wrong, "| s1+s3:", wrong_s1_merged)
print("docs written")
