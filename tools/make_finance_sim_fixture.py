"""Generate synthetic finance-transport fixtures for the integration test.

本脚本生成的是仓库内合成测试数据，不是用户原始凭证，也不是真实发票。
所有发票号、金额、车牌、日期与里程均由本文件内的固定规则推导，不读取外部数据，
因此重复运行会得到相同内容的文件（xlsx 压缩包内时间戳除外）。

生成文件（默认写入 tests/fixtures/finance_transport/）：
- invoice_simulated_truth.xlsx：45 票（票级 45 行 / 明细行 48 行）的合成真值工作簿
- invoice_template.xlsx：只有表头的空白票级/明细行导入模板
- simulated_trips.csv：与 39 张柴油票一一对应的 39 趟合成行程（合计 4641 km）

用法：
    python tools/make_finance_sim_fixture.py
    python tools/make_finance_sim_fixture.py --output-dir tests/fixtures/finance_transport
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "tests" / "fixtures" / "finance_transport"

TICKET_HEADERS = [
    "发票代码", "发票号码", "开票日期", "发票类型", "购买方名称", "购买方税号",
    "销售方名称", "销售方税号", "项目名称", "规格型号", "数量", "单位", "单价",
    "金额", "税率", "税额", "价税合计", "价税合计大写", "车牌号", "备注",
    "开票人", "文件",
]
DETAIL_HEADERS = [
    "发票号码", "项目名称", "规格型号", "单位", "数量", "单价", "金额",
    "税率", "税额", "识别置信度",
]

DIESEL_PLATES = ["苏A12345", "苏A12346", "苏A12347", "苏A12348",
                 "苏A12349", "苏A12350", "苏A12351", "苏A12352"]
DIESEL_START = date(2026, 10, 31)
DIESEL_DAYS = 5
DIESEL_CODE = "032002400111"
REPAIR_CODE = "032002400112"
BUYER = "合成物流车队（模拟）"
BUYER_TAX_ID = "91320000MA1SIMBUY01"
DIESEL_SELLER = "合成油品供应商（模拟）"
DIESEL_SELLER_TAX_ID = "91320000MA1SIMSELL01"
REPAIR_SELLER = "合成维修服务商（模拟）"
REPAIR_SELLER_TAX_ID = "91320000MA1SIMSELL02"
SIMULATION_MARK = "模拟样张：仓库合成测试数据，非用户原始凭证"
FIXED_TIMESTAMP = datetime(2026, 1, 1, 0, 0, 0)

# 39 张柴油票 = 8 个车牌 × 5 天，其中苏A12352 少 11-04 一天。
# 每车每天最多一张票，因此每车按日期形成的连续周期恰好 N-1 个，
# 合计 39 - 8 = 31 个 closed cycles；每车最后一张票当天的行程不落入任何周期。
REPAIRS = [
    {
        "invoice_no": "25946248797655020504",
        "invoice_date": "2026-11-02",
        "plate": "苏A12348",
        "file": "FP-REPAIR-003.png",
        "item_name": "多行明细（见明细表）",
        "note": "维修项目详见明细；车牌号：苏A12348；" + SIMULATION_MARK,
        "lines": [
            ("*劳务*更换轮胎", "11R22.5", "条", 2, 990.00, 1980.00, 257.40),
            ("*货物*轮胎", "11R22.5", "条", 2, 235.00, 470.00, 61.10),
        ],
    },
    {
        "invoice_no": "25245078884927233714",
        "invoice_date": "2025-06-01",
        "plate": "苏A12345",
        "file": "FP-REPAIR-001.png",
        "item_name": "多行明细（见明细表）",
        "note": "维修项目详见明细；车牌号：苏A12345；" + SIMULATION_MARK,
        "lines": [
            ("*劳务*维修工时", "", "次", 1, 800.00, 800.00, 104.00),
            ("*货物*机油", "15W-40", "桶", 4, 120.00, 480.00, 62.40),
            ("*货物*滤芯", "", "个", 2, 360.00, 720.00, 93.60),
        ],
    },
    {
        "invoice_no": "25245078884927233715",
        "invoice_date": "2026-10-31",
        "plate": "苏A12346",
        "file": "FP-REPAIR-002.png",
        "item_name": "*劳务*常规保养",
        "note": "常规保养；" + SIMULATION_MARK,
        "lines": [("*劳务*常规保养", "", "次", 1, 500.00, 500.00, 65.00)],
    },
    {
        "invoice_no": "25245078884927233716",
        "invoice_date": "2026-11-01",
        "plate": "苏A12347",
        "file": "FP-REPAIR-004.png",
        "item_name": "*劳务*制动系统检修",
        "note": "制动系统检修；" + SIMULATION_MARK,
        "lines": [("*劳务*制动系统检修", "", "次", 1, 800.00, 800.00, 104.00)],
    },
    {
        "invoice_no": "25245078884927233717",
        "invoice_date": "2026-11-03",
        "plate": "苏A12349",
        "file": "FP-REPAIR-005.png",
        "item_name": "*货物*轮胎",
        "note": "轮胎采购；" + SIMULATION_MARK,
        "lines": [("*货物*轮胎", "11R22.5", "条", 2, 600.00, 1200.00, 156.00)],
    },
    {
        "invoice_no": "25245078884927233718",
        "invoice_date": "2026-11-04",
        "plate": "苏A12350",
        "file": "FP-REPAIR-006.png",
        "item_name": "*劳务*四轮定位",
        "note": "四轮定位；" + SIMULATION_MARK,
        "lines": [("*劳务*四轮定位", "", "次", 1, 300.00, 300.00, 39.00)],
    },
]


def _ticket(values):
    return {name: values.get(name) for name in TICKET_HEADERS}


def _detail(invoice_no, line):
    name, spec, unit, quantity, unit_price, amount, tax = line
    return {
        "发票号码": invoice_no,
        "项目名称": name,
        "规格型号": spec,
        "单位": unit,
        "数量": quantity,
        "单价": unit_price,
        "金额": amount,
        "税率": "13%",
        "税额": tax,
        "识别置信度": 0.99,
    }


def build_dataset():
    """Return (tickets, details, trips) with deterministic synthetic values."""
    tickets, details, trips = [], [], []
    diesel_index = 0
    for day_offset in range(DIESEL_DAYS):
        current = DIESEL_START + timedelta(days=day_offset)
        for plate_index, plate in enumerate(DIESEL_PLATES):
            if day_offset == DIESEL_DAYS - 1 and plate_index == len(DIESEL_PLATES) - 1:
                continue
            quantity = round(30 + 0.3 * diesel_index, 1)
            unit_price = round(7.05 + (diesel_index % 7) * 0.03, 2)
            net = round(quantity * unit_price, 2)
            tax = round(net * 0.13, 2)
            gross = round(net + tax, 2)
            invoice_no = f"2594{diesel_index + 1:016d}"
            date_text = current.strftime("%Y-%m-%d")
            tickets.append(_ticket({
                "发票代码": DIESEL_CODE,
                "发票号码": invoice_no,
                "开票日期": date_text,
                "发票类型": "增值税专用发票",
                "购买方名称": BUYER,
                "购买方税号": BUYER_TAX_ID,
                "销售方名称": DIESEL_SELLER,
                "销售方税号": DIESEL_SELLER_TAX_ID,
                "项目名称": "*柴油*0号车用柴油",
                "规格型号": "0号",
                "数量": quantity,
                "单位": "L",
                "单价": unit_price,
                "金额": net,
                "税率": "13%",
                "税额": tax,
                "价税合计": gross,
                "车牌号": plate,
                "备注": SIMULATION_MARK,
                "开票人": "合成开票员",
                "文件": f"FP-DIESEL-{diesel_index + 1:03d}.png",
            }))
            details.append(_detail(invoice_no, (
                "*柴油*0号车用柴油", "0号", "L", quantity, unit_price, net, tax,
            )))
            trips.append({
                "trip_id": f"T-{plate}-{date_text}",
                "waybill_no": f"WB-{invoice_no}",
                "plate": plate,
                "depart_ts": f"{date_text} 08:00:00",
                "arrive_ts": f"{date_text} 10:00:00",
                "trip_km": 100 + diesel_index,
                "od_site": "A|B",
                "vehicle_type": "牵引车",
                "energy_type": "fuel",
                "source_mode": "simulated",
            })
            diesel_index += 1

    for repair in REPAIRS:
        lines = repair["lines"]
        net = round(sum(line[5] for line in lines), 2)
        tax = round(sum(line[6] for line in lines), 2)
        gross = round(net + tax, 2)
        assert abs(round(net * 0.13, 2) - tax) <= 0.010001
        multi = len(lines) > 1
        tickets.append(_ticket({
            "发票代码": REPAIR_CODE,
            "发票号码": repair["invoice_no"],
            "开票日期": repair["invoice_date"],
            "发票类型": "增值税专用发票",
            "购买方名称": BUYER,
            "购买方税号": BUYER_TAX_ID,
            "销售方名称": REPAIR_SELLER,
            "销售方税号": REPAIR_SELLER_TAX_ID,
            "项目名称": repair["item_name"],
            "规格型号": "" if multi else lines[0][1],
            "数量": None if multi else lines[0][3],
            "单位": "" if multi else lines[0][2],
            "单价": None if multi else lines[0][4],
            "金额": net,
            "税率": "13%",
            "税额": tax,
            "价税合计": gross,
            "车牌号": repair["plate"],
            "备注": repair["note"],
            "开票人": "合成开票员",
            "文件": repair["file"],
        }))
        for line in lines:
            details.append(_detail(repair["invoice_no"], line))

    assert len(tickets) == 45, len(tickets)
    assert len(details) == 48, len(details)
    assert sum(1 for t in tickets if t["单位"] == "L") == 39
    assert len(trips) == 39
    assert len({t["车牌号"] for t in tickets}) == 8
    return tickets, details, trips


def _style_sheet(worksheet):
    worksheet.freeze_panes = "A2"
    for cell in worksheet[1]:
        cell.font = Font(bold=True)
    for index, header in enumerate(worksheet[1], 1):
        width = max(10, min(28, len(str(header.value)) * 2 + 4))
        worksheet.column_dimensions[get_column_letter(index)].width = width


def _new_workbook(title):
    workbook = Workbook()
    workbook.properties.creator = "tools/make_finance_sim_fixture.py"
    workbook.properties.title = title
    workbook.properties.subject = "仓库内合成测试数据，不是用户原始凭证"
    workbook.properties.created = FIXED_TIMESTAMP
    workbook.properties.modified = FIXED_TIMESTAMP
    return workbook


def write_truth_workbook(path, tickets, details):
    workbook = _new_workbook("运输成本合成票面真值（模拟样张）")
    ticket_sheet = workbook.active
    ticket_sheet.title = "票级"
    ticket_sheet.append(TICKET_HEADERS)
    for row in tickets:
        ticket_sheet.append([row[name] for name in TICKET_HEADERS])
    _style_sheet(ticket_sheet)

    detail_sheet = workbook.create_sheet("明细行")
    detail_sheet.append(DETAIL_HEADERS)
    for row in details:
        detail_sheet.append([row[name] for name in DETAIL_HEADERS])
    _style_sheet(detail_sheet)

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


def write_template_workbook(path):
    workbook = _new_workbook("运输成本发票导入模板（空白）")
    ticket_sheet = workbook.active
    ticket_sheet.title = "票级"
    ticket_sheet.append(TICKET_HEADERS)
    _style_sheet(ticket_sheet)

    detail_sheet = workbook.create_sheet("明细行")
    detail_sheet.append(DETAIL_HEADERS)
    _style_sheet(detail_sheet)

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


def write_trips_csv(path, trips):
    fieldnames = [
        "trip_id", "waybill_no", "plate", "depart_ts", "arrive_ts", "trip_km",
        "od_site", "vehicle_type", "energy_type", "source_mode",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(trips)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help="fixture output directory (default: tests/fixtures/finance_transport)")
    args = parser.parse_args(argv)

    tickets, details, trips = build_dataset()
    output_dir = args.output_dir
    truth = output_dir / "invoice_simulated_truth.xlsx"
    template = output_dir / "invoice_template.xlsx"
    trips_csv = output_dir / "simulated_trips.csv"
    write_truth_workbook(truth, tickets, details)
    write_template_workbook(template)
    write_trips_csv(trips_csv, trips)
    print(f"tickets={len(tickets)} details={len(details)} trips={len(trips)} "
          f"total_km={sum(t['trip_km'] for t in trips)}")
    print(f"wrote {truth}")
    print(f"wrote {template}")
    print(f"wrote {trips_csv}")


if __name__ == "__main__":
    main()
