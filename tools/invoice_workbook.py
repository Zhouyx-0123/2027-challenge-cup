"""Import OCR workbooks using the project's 票级 / 明细行 contract.

Supports both the clean template (票级, 明细行) and the supplied simulation
truth workbook (票面字段真值, 明细行真值). It does not run OCR or fabricate
missing source images; source_image remains the filename printed in the workbook.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook


def _number(value):
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "").replace("¥", "").replace("￥", "").strip())
    except (TypeError, ValueError):
        return None


def _date(value):
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return text


def _rows(ws):
    iterator = ws.iter_rows(values_only=True)
    headers = next(iterator, None)
    if not headers:
        return []
    headers = [str(x).strip() if x is not None else "" for x in headers]
    out = []
    for values in iterator:
        if not any(v is not None and str(v).strip() for v in values):
            continue
        out.append({headers[i]: values[i] for i in range(min(len(headers), len(values))) if headers[i]})
    return out


def _pick_sheet(wb, names):
    for name in names:
        if name in wb.sheetnames:
            return wb[name]
    return None


def _plate_from_note(note):
    match = re.search(r"(?:车牌号|车牌)\s*[:：]\s*([A-Za-z0-9\u4e00-\u9fff]{6,10})", str(note or ""), re.I)
    return match.group(1) if match else ""


def read_invoice_workbook(path, source_mode="simulated"):
    if source_mode not in {"real", "simulated"}:
        raise ValueError("source_mode 必须是 real 或 simulated")
    path = Path(path)
    wb = load_workbook(path, data_only=True, read_only=True)
    ticket_ws = _pick_sheet(wb, ("票级", "票面字段真值", "勾稽校验_票级"))
    detail_ws = _pick_sheet(wb, ("明细行", "明细行真值", "勾稽校验_行级"))
    if ticket_ws is None:
        raise ValueError("工作簿缺少票级或票面字段真值工作表")

    ticket_rows = _rows(ticket_ws)
    detail_rows = _rows(detail_ws) if detail_ws else []
    if source_mode == "real":
        simulation_markers = ("模拟样张", "仅供测试", "模拟数据")
        marked = [row for row in ticket_rows
                  if any(marker in str(row.get("备注") or "") or marker in str(row.get("文件") or "")
                         for marker in simulation_markers)]
        if marked:
            raise ValueError("工作簿包含模拟样张标记，不能以 real 数据性质导入")
    by_invoice = {}
    for row in detail_rows:
        no = str(row.get("发票号码") or "").strip()
        if not no:
            continue
        by_invoice.setdefault(no, []).append({
            "item_name": str(row.get("项目名称") or "").strip(),
            "specification": str(row.get("规格型号") or "").strip(),
            "unit": str(row.get("单位") or "").strip(),
            "quantity": _number(row.get("数量")),
            "unit_price": _number(row.get("单价")),
            "amount": _number(row.get("金额")),
            "tax_rate": str(row.get("税率") or "").strip(),
            "tax_amount": _number(row.get("税额")),
            "confidence": row.get("识别置信度"),
        })

    records = []
    for index, row in enumerate(ticket_rows, 2):
        invoice_no = str(row.get("发票号码") or "").strip()
        invoice_code = str(row.get("发票代码") or "").strip()
        filename = str(row.get("文件") or "").strip() or f"{path.stem}_row_{index}"
        note = str(row.get("备注") or "").strip()
        plate = str(row.get("车牌号") or "").strip() or _plate_from_note(note)
        items = by_invoice.get(invoice_no, [])
        if not items and row.get("项目名称"):
            items = [{
                "item_name": str(row.get("项目名称") or "").strip(),
                "specification": str(row.get("规格型号") or "").strip(),
                "unit": str(row.get("单位") or "").strip(),
                "quantity": _number(row.get("数量")),
                "unit_price": _number(row.get("单价")),
                "amount": _number(row.get("金额")),
                "tax_rate": str(row.get("税率") or "").strip(),
                "tax_amount": _number(row.get("税额")),
                "confidence": row.get("识别置信度"),
            }]
        multiple = len(items) > 1 or str(row.get("项目名称") or "").strip() in {"多行明细", "多行明细（见明细表）"}
        item_name = str(row.get("项目名称") or "").strip()
        if multiple and not item_name:
            item_name = "多行明细（见明细表）"
        quantity = _number(row.get("数量"))
        unit = str(row.get("单位") or "").strip()
        unit_price = _number(row.get("单价"))
        if multiple:
            quantity, unit, unit_price = None, "", None
        elif items:
            quantity = items[0]["quantity"] if items[0]["quantity"] is not None else quantity
            unit = items[0]["unit"] or unit
            unit_price = items[0]["unit_price"] if items[0]["unit_price"] is not None else unit_price
        amount = _number(row.get("价税合计"))
        net_amount = _number(row.get("金额"))
        tax_amount = _number(row.get("税额"))
        identity = invoice_code + ":" + invoice_no if invoice_no else filename
        record = {
            "record_id": "INV-" + __import__("hashlib").sha256(identity.encode("utf-8")).hexdigest()[:24],
            "invoice_code": invoice_code,
            "invoice_no": invoice_no,
            "invoice_date": _date(row.get("开票日期")),
            "invoice_type": str(row.get("发票类型") or "").strip(),
            "buyer_name": str(row.get("购买方名称") or "").strip(),
            "buyer_tax_id": str(row.get("购买方税号") or "").strip(),
            "seller_name": str(row.get("销售方名称") or "").strip(),
            "seller_tax_id": str(row.get("销售方税号") or "").strip(),
            "item_name": item_name,
            "specification": str(row.get("规格型号") or "").strip(),
            "quantity": quantity,
            "unit": unit,
            "unit_price": unit_price,
            "amount_excluding_tax": net_amount,
            "tax_rate": str(row.get("税率") or "").strip(),
            "tax_amount": tax_amount,
            "invoice_amount": amount,
            "invoice_amount_upper": str(row.get("价税合计大写") or "").strip(),
            "plate": plate,
            "note": note,
            "issuer": str(row.get("开票人") or "").strip(),
            "items": items,
            "multiple_items": multiple,
            "source_image": filename,
            "source_mode": source_mode,
            "mock": source_mode == "simulated",
        }
        records.append(record)
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-mode", choices=("real", "simulated"), default="simulated")
    parser.add_argument("--image-dir", type=Path, help="原始发票图片目录；用于报告缺失图片，不会伪造图片")
    args = parser.parse_args(argv)
    records = read_invoice_workbook(args.input, args.source_mode)
    image_dir = args.image_dir or args.input.parent
    missing_images = sorted({r["source_image"] for r in records
                             if r.get("source_image") and not (image_dir / r["source_image"]).is_file()})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"records": len(records), "source_mode": args.source_mode,
                      "multi_line": sum(bool(r["multiple_items"]) for r in records),
                      "missing_source_images": len(missing_images),
                      "missing_image_files": missing_images,
                      "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
