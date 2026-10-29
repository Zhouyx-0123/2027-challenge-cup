# -*- coding: utf-8 -*-
"""碳账交付修订项自查（docs/55 · 交付方自检脚本）。

用法::

    python -m tools.verify_carbon_ledger_delivery

检查 ``carbon_ledger/`` 交付物在 docs/55 §3 列出的 **5 条修订项**是否已经改到位。
每条打印 ``OK`` / ``FAIL`` / ``SKIP``，有 FAIL 时退出码 1。

本脚本**只读**交付物，不修改任何文件。
若 ``carbon_ledger/`` 目录不存在（例如交付方只拿到单个工作簿），
全部条目记 ``SKIP`` 并给出提示。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LEDGER = ROOT / "carbon_ledger"
MAIN = LEDGER / "a4_ledger" / "carbon_ledger_main.xlsx"
QUOTA = LEDGER / "a4_ledger" / "quota_ledger.xlsx"
EVIDENCE = LEDGER / "a5_evidence" / "evidence_index.xlsx"
DICTIONARY = LEDGER / "a2_activity_data" / "activity_data_field_dictionary.xlsx"
ACTIVITY_TEMPLATE = LEDGER / "a2_activity_data" / "activity_data_template.xlsx"
LEDGER_README = LEDGER / "README.md"
VALIDATION_DIR = LEDGER / "validation"

OLD_VALIDATION_NAME = "碳账可行性多组数据验证改进版.xlsx"
STALE_TOTAL = "0.5433"
TRUE_TOTAL = "2.439298"


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value != value:
        return ""
    return str(value).strip()


def _read_records(path: Path, sheet: str) -> List[Dict[str, Any]]:
    import openpyxl

    workbook = openpyxl.load_workbook(path, data_only=True)
    worksheet = workbook[sheet]
    rows = [list(item) for item in worksheet.iter_rows(values_only=True)]
    header_index = 0
    for index, row in enumerate(rows):
        if sum(1 for cell in row if cell is not None and str(cell).strip()) >= 2:
            header_index = index
            break
    header = [_text(cell) for cell in rows[header_index]]
    records = []
    for row in rows[header_index + 1:]:
        if not any(cell is not None and str(cell).strip() for cell in row):
            continue
        records.append({header[i]: row[i] if i < len(row) else None
                        for i in range(len(header))})
    return records


def _read_header(path: Path, sheet: str) -> List[str]:
    import openpyxl

    workbook = openpyxl.load_workbook(path, data_only=True)
    worksheet = workbook[sheet]
    return [_text(cell) for cell in next(
        worksheet.iter_rows(min_row=1, max_row=1, values_only=True))]


# ---------------------------------------------------------------------------
# 前置 0 · 公式缓存值必须完好
# ---------------------------------------------------------------------------

def check_formula_cache() -> Tuple[str, str]:
    """工作簿里公式的**缓存值**必须还在。

    用 openpyxl 之类的库打开再保存，公式会保留、但**缓存值会被抹掉**：
    读取端（``data_only=True``）就再也读不到排放量与配额数。
    所以改工作簿必须用 Excel 桌面另存（或任何会重算并写回缓存值的工具）。
    """
    problems: List[str] = []
    if MAIN.exists():
        trips = _read_records(MAIN, "Trip_Detail")
        if trips:
            empty = sum(1 for row in trips
                        if row.get("Emissions_tCO2e") in (None, ""))
            if empty:
                problems.append("主账 %d/%d 行 Emissions_tCO2e 缓存值丢失"
                                % (empty, len(trips)))
        for row in _read_records(MAIN, "Annual_Summary"):
            if row.get("Total_Emissions_tCO2e") in (None, ""):
                problems.append("主账 Annual_Summary 合计缓存值丢失")
    if QUOTA.exists():
        for row in _read_records(QUOTA, "Quota_Ledger"):
            for column in ("Available_Quota_tCO2e", "Closing_Balance_tCO2e",
                           "Shortfall_tCO2e"):
                if row.get(column) in (None, ""):
                    problems.append("配额台账 %s 缓存值丢失" % column)
    if problems:
        return "FAIL", ("；".join(problems)
                        + "（请用 Excel 桌面打开另存一次以刷新公式缓存值）")
    return "OK", "主账与配额台账的公式缓存值完好"


# ---------------------------------------------------------------------------
# 修订 1 · 配额台账 Notes 的陈旧合计
# ---------------------------------------------------------------------------

def check_quota_note() -> Tuple[str, str]:
    if not QUOTA.exists():
        return "SKIP", "配额台账不存在"
    rows = _read_records(QUOTA, "Quota_Ledger")
    if not rows:
        return "FAIL", "Quota_Ledger 读不到数据行"
    row = rows[0]
    notes = _text(row.get("Notes"))
    source = _text(row.get("Source_Document"))
    problems: List[str] = []
    if STALE_TOTAL in notes:
        problems.append("Notes 仍含陈旧合计 %s" % STALE_TOTAL)
    if TRUE_TOTAL not in notes:
        problems.append("Notes 未写出正确合计 %s" % TRUE_TOTAL)
    if TRUE_TOTAL not in source:
        problems.append("Source_Document 未写出 %s" % TRUE_TOTAL)
    # Notes 与 Source_Document 的合计必须一致
    totals = {token for token in (STALE_TOTAL, TRUE_TOTAL)
              if token in notes or token in source}
    if len(totals) > 1:
        problems.append("Notes 与 Source_Document 的合计互相矛盾")
    # 主账实际合计
    if MAIN.exists():
        trips = _read_records(MAIN, "Trip_Detail")
        actual = 0.0
        for item in trips:
            value = item.get("Emissions_tCO2e")
            try:
                actual += float(value)
            except (TypeError, ValueError):
                continue
        if abs(actual - float(TRUE_TOTAL)) > 1e-6:
            problems.append("主账实际合计为 %s，与文档写的 %s 不符"
                            % (round(actual, 6), TRUE_TOTAL))
    if problems:
        return "FAIL", "；".join(problems)
    return "OK", "Notes 与 Source_Document 均为 %s" % TRUE_TOTAL


# ---------------------------------------------------------------------------
# 修订 2 / 5 · README 的行数与规则条数口径
# ---------------------------------------------------------------------------

def check_readme_counts() -> Tuple[str, str]:
    if not LEDGER_README.exists():
        return "SKIP", "carbon_ledger/README.md 不存在"
    text = LEDGER_README.read_text(encoding="utf-8")
    # 只看「看结构」清单里的条目行（以 Trip_Detail / Validation_Rules 开头的行），
    # 不看正文里对这些问题的描述文字。
    entries = [line.strip() for line in text.splitlines()
               if line.strip().startswith("Trip_Detail")
               or line.strip().startswith("Validation_Rules")]
    problems: List[str] = []
    if not entries:
        return "FAIL", "README 的结构清单里找不到 Trip_Detail / Validation_Rules 条目"
    trip_entry = next((line for line in entries if line.startswith("Trip_Detail")), "")
    rule_entry = next((line for line in entries
                       if line.startswith("Validation_Rules")), "")
    # 精确解析「列数 × 行数/条数」，不被说明文字里的行号干扰
    match = re.search(r"×\s*(\d+)\s*[条行]", trip_entry)
    if not match:
        problems.append("结构清单的 Trip_Detail 条目未写出「列数 × 行数」")
    elif int(match.group(1)) != 12:
        problems.append("结构清单仍写 %s 行/条（实际 12 条）" % match.group(1))
    match_rule = re.search(r"(\d+)\s*条", rule_entry)
    if not match_rule:
        problems.append("结构清单的 Validation_Rules 条目未写出条数")
    elif int(match_rule.group(1)) != 14:
        problems.append("结构清单仍写 %s 条控制规则（实际 14 条）"
                        % match_rule.group(1))
    if problems:
        return "FAIL", "；".join(problems)
    return "OK", "行数 12 条 / 控制规则 14 条"


# ---------------------------------------------------------------------------
# 修订 3 · 证据索引状态口径
# ---------------------------------------------------------------------------

def check_evidence_status() -> Tuple[str, str]:
    if not EVIDENCE.exists():
        return "SKIP", "证据索引不存在"
    rows = _read_records(EVIDENCE, "Evidence_Index")
    if not rows:
        return "FAIL", "Evidence_Index 读不到数据行"
    problems: List[str] = []
    blank = 0
    for row in rows:
        status = _text(row.get("Original_Document_Status"))
        if not status:
            blank += 1
        verification = _text(row.get("Verification_Status"))
        if "source file readable" in verification:
            problems.append("Verification_Status 仍称「source file readable」")
    if blank:
        problems.append("有 %d 行 Original_Document_Status 空白" % blank)
    if problems:
        return "FAIL", "；".join(problems)
    return "OK", "%d 行状态字段均非空" % len(rows)


# ---------------------------------------------------------------------------
# 修订 4 · 字段字典表头错位
# ---------------------------------------------------------------------------

def check_dictionary_header() -> Tuple[str, str]:
    if not DICTIONARY.exists():
        return "SKIP", "字段字典不存在"
    header = _read_header(DICTIONARY, "Field_Dictionary")
    problems: List[str] = []
    if len(header) < 3:
        return "FAIL", "字段字典表头不足 3 列"
    if header[2] != "数据类型":
        problems.append("第 3 列是「%s」，应为「数据类型」（表头与数据错位一列）"
                        % header[2])
    if "确认状态" not in header:
        problems.append("表头缺「确认状态」列")
    if "单位" in header:
        problems.append("表头仍保留「单位」列，但数据行没有该列")
    if problems:
        return "FAIL", "；".join(problems)
    # 按修正后的表头读第一行，确认语义归位
    rows = _read_records(DICTIONARY, "Field_Dictionary")
    if rows:
        first = rows[0]
        if _text(first.get("数据类型")) != "文本":
            problems.append("首行「数据类型」= %s，应为「文本」"
                            % _text(first.get("数据类型")))
        if _text(first.get("是否必填")) != "是":
            problems.append("首行「是否必填」= %s，应为「是」"
                            % _text(first.get("是否必填")))
    # 字段清单必须与模板一致
    if ACTIVITY_TEMPLATE.exists():
        template = set(_read_header(ACTIVITY_TEMPLATE, "Activity_Data"))
        names = {_text(row.get("字段名")) for row in rows}
        missing = template - names
        extra = names - template
        if missing:
            problems.append("字典缺模板字段：%s" % "、".join(sorted(missing)[:5]))
        if extra:
            problems.append("字典多出模板没有的字段：%s" % "、".join(sorted(extra)[:5]))
    if problems:
        return "FAIL", "；".join(problems)
    return "OK", "表头与数据对齐，%d 个字段" % len(rows)


# ---------------------------------------------------------------------------
# 修订 6 · 旧版验证工作簿的版本警示
# ---------------------------------------------------------------------------

def check_legacy_validation() -> Tuple[str, str]:
    if not VALIDATION_DIR.exists():
        return "SKIP", "validation 目录不存在"
    legacy = VALIDATION_DIR / OLD_VALIDATION_NAME
    if not legacy.exists():
        others = sorted(p.name for p in VALIDATION_DIR.glob("*.xlsx"))
        if not others:
            return "FAIL", "validation 目录里没有任何工作簿"
        return "OK", "旧版工作簿已删除，现存：%s" % "、".join(others)
    name = legacy.name
    warnings = [token for token in ("旧版", "第一轮", "勿作当前版", "作废")
                if token in name]
    if not warnings:
        return "FAIL", ("文件名仍无版本警示，建议改为 "
                        "「碳账可行性多组数据验证_第一轮旧版_勿作当前版.xlsx」")
    # 文件内还要有一处说明
    try:
        import openpyxl

        workbook = openpyxl.load_workbook(legacy, data_only=True)
    except Exception as exc:  # noqa: BLE001
        return "FAIL", "无法打开旧版工作簿：%s" % exc
    marker = False
    for sheet in workbook.sheetnames:
        worksheet = workbook[sheet]
        for row in worksheet.iter_rows(values_only=True):
            joined = " ".join(_text(cell) for cell in row)
            if any(token in joined for token in
                   ("旧版", "第一轮", "勿作当前版", "不得作为当前版")):
                marker = True
                break
        if marker:
            break
    if not marker:
        return "FAIL", "文件名已有警示，但工作簿内没有版本说明行"
    return "OK", "文件名与工作簿内均已标注版本"


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

CHECKS = [
    ("前置 0 · 工作簿公式缓存值完好", check_formula_cache),
    ("修订 1 · 配额台账 Notes 合计", check_quota_note),
    ("修订 2/5 · README 行数与规则条数口径", check_readme_counts),
    ("修订 3 · 证据索引状态口径", check_evidence_status),
    ("修订 4 · 字段字典表头错位", check_dictionary_header),
    ("修订 6 · 旧版验证工作簿版本警示", check_legacy_validation),
]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="碳账交付修订项自查（docs/55）")
    parser.parse_args(argv)
    print("=" * 74)
    try:
        scope = str(LEDGER.relative_to(ROOT))
    except ValueError:
        scope = str(LEDGER)
    print("碳账交付修订项自查 · docs/55 · %s" % scope)
    print("=" * 74)
    if not LEDGER.exists():
        print("carbon_ledger/ 不存在，全部跳过。")
        return 0
    failed = 0
    for title, func in CHECKS:
        try:
            status, detail = func()
        except Exception as exc:  # noqa: BLE001
            status, detail = "FAIL", "检查脚本异常：%s" % exc
        print("%-5s %s｜%s" % (status, title, detail))
        if status == "FAIL":
            failed += 1
    print("-" * 74)
    if failed:
        print("合计：FAIL %d｜按 docs/55 §2 逐条改完后重跑本脚本" % failed)
        return 1
    print("合计：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
