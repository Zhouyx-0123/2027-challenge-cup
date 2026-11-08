"""生成「电子发票（普通发票）」样张 + 识别答案，供财务同学测 OCR → Excel。

为什么不用上一版系统格式：财务那边发给大模型的是**票面图片**，模型只认票面上的字，
所以模拟数据必须长得像票、字段与票面一一对应，而不是先给一张结构化表。

输出目录：交付_20261108_财务与碳账/04_发票识别测试集/
  images/FP_xxx.png        45 张样张（39 张柴油 + 6 张维修费，其中 3 张多行明细）
  发票识别_答案.xlsx        票面字段真值 + 勾稽校验
  发票识别_Excel模板.xlsx   空表头，给「模型输出」直接套用
  README_发票测试集.md      字段格式、勾稽规则、易错点

全部样张带水印「模拟样张 · 仅供测试 · 不可报销」，不构成真实票据。
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pandas as pd
from PIL import Image, ImageDraw, ImageFont
from openpyxl.styles import Alignment, Font, PatternFill

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "交付_20261108_财务与碳账" / "04_发票识别测试集"
IMAGES = OUT / "images"
SIM = ROOT / "data" / "sim" / "excel_10000"

W, H = 2364, 1773
PAPER = (247, 246, 243)
INK = (32, 32, 32)
GREY = (110, 110, 110)
LINE = (60, 60, 60)

F_TITLE = ImageFont.truetype(r"C:\Windows\Fonts\simsun.ttc", 66)
F_SUB = ImageFont.truetype(r"C:\Windows\Fonts\simsun.ttc", 30)
F_LABEL = ImageFont.truetype(r"C:\Windows\Fonts\simsun.ttc", 26)
F_BODY = ImageFont.truetype(r"C:\Windows\Fonts\simsun.ttc", 30)
F_SMALL = ImageFont.truetype(r"C:\Windows\Fonts\simsun.ttc", 24)
F_TH = ImageFont.truetype(r"C:\Windows\Fonts\simsun.ttc", 27)
F_MARK = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 78)

BUYER = "上海碳感知物流有限公司"
BUYER_ID = "91310115MA1K4YQ23R"

DIGITS = "零壹贰叁肆伍陆柒捌玖"
SMALL_UNITS = ["", "拾", "佰", "仟"]
BIG_UNITS = ["", "万", "亿", "万亿"]


def _group_upper(group: int) -> str:
    """四位一组的大写（不含万/亿单位）。"""
    text = ""
    zero = False
    for i in range(4):
        digit = group % 10
        if digit == 0:
            if text:
                zero = True
        else:
            text = DIGITS[digit] + SMALL_UNITS[i] + ("零" if zero else "") + text
            zero = False
        group //= 10
    return text


def rmb_upper(amount: float) -> str:
    """人民币金额大写（票面「价税合计（大写）」）。"""
    value = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    integer = int(value)
    jiao = int(value * 10) % 10
    fen = int(value * 100) % 10
    if integer == 0 and jiao == 0 and fen == 0:
        return "零圆整"

    groups = []
    rest = integer
    while rest > 0:
        groups.append(rest % 10000)
        rest //= 10000
    parts = []
    for gi in range(len(groups) - 1, -1, -1):
        group = groups[gi]
        if group == 0:
            if parts and not parts[-1].endswith("零"):
                parts.append("零")
            continue
        if parts and group < 1000 and not parts[-1].endswith("零"):
            parts.append("零")
        parts.append(_group_upper(group) + BIG_UNITS[gi])
    out = "".join(parts) + "圆" if parts else "零圆"
    if jiao == 0 and fen == 0:
        return out + "整"
    if jiao:
        out += DIGITS[jiao] + "角"
    elif fen:
        out += "零"
    if fen:
        out += DIGITS[fen] + "分"
    elif jiao:
        out += "整"
    return out
def money(value: float) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"


def price_text(value: float) -> str:
    """单价：票面允许很长的小数（数量=1 时尤其明显）。"""
    if abs(value * 100 - round(value * 100)) < 1e-9:
        return f"{value:.2f}"
    text = f"{Decimal(str(value)):.11f}".rstrip("0").rstrip(".")
    return text or "0"


def cn_date(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d.year}年{d.month:02d}月{d.day:02d}日"


def vline(d, x, y0, y1, width=2):
    d.line([(x, y0), (x, y1)], fill=LINE, width=width)


def hline(d, x0, x1, y, width=2):
    d.line([(x0, y), (x1, y)], fill=LINE, width=width)


def text(d, xy, s, font=F_BODY, fill=INK, anchor="la"):
    d.text(xy, s, font=font, fill=fill, anchor=anchor)


def stamp(d, image):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.text((W // 2, H // 2), "模拟样张 · 仅供测试 · 不可报销",
            font=F_MARK, fill=(160, 160, 160, 90), anchor="mm")
    layer = layer.rotate(18, center=(W // 2, H // 2), resample=Image.BICUBIC)
    image.alpha_composite(layer) if image.mode == "RGBA" else image.paste(
        Image.alpha_composite(image.convert("RGBA"), layer).convert("RGB"), (0, 0))


def render(rec: dict, path: Path) -> None:
    img = Image.new("RGBA", (W, H), PAPER + (255,))
    d = ImageDraw.Draw(img)
    d.rectangle([(48, 40), (W - 48, H - 40)], outline=LINE, width=3)

    text(d, (W // 2, 120), "电子发票（普通发票）", F_TITLE, INK, "mm")
    text(d, (W - 90, 250), f"发票号码：{rec['发票号码']}", F_BODY, INK, "ra")
    text(d, (W - 90, 305), f"开票日期：{cn_date(rec['开票日期'])}", F_BODY, INK, "ra")

    rows = rec["明细"]
    party_top = 380
    party_bottom = party_top + 96 + 55 * 4
    hline(d, 48, W - 48, party_top, 3)
    hline(d, 48, W - 48, party_bottom, 2)
    mid = 1150
    vline(d, mid, party_top, party_bottom)

    text(d, (80, party_top + 24), "购买方信息", F_SUB, GREY)
    text(d, (mid + 40, party_top + 24), "销售方信息", F_SUB, GREY)
    left_rows = [("名称：", rec["购买方名称"]), ("统一社会信用代码/纳税人识别号：", rec["购买方税号"]),
                 ("地址、电话：", rec.get("购买方地址电话", "-")), ("开户行及账号：", rec.get("购买方开户行", "-"))]
    right_rows = [("名称：", rec["销售方名称"]), ("统一社会信用代码/纳税人识别号：", rec["销售方税号"]),
                  ("地址、电话：", rec.get("销售方地址电话", "-")), ("开户行及账号：", rec.get("销售方开户行", "-"))]
    for i, (label, value) in enumerate(left_rows):
        text(d, (80, party_top + 66 + i * 55), label, F_LABEL, INK)
        text(d, (80 + d.textlength(label, font=F_LABEL) + 8, party_top + 66 + i * 55), value, F_BODY, INK)
    for i, (label, value) in enumerate(right_rows):
        text(d, (mid + 40, party_top + 66 + i * 55), label, F_LABEL, INK)
        text(d, (mid + 40 + d.textlength(label, font=F_LABEL) + 8, party_top + 66 + i * 55), value, F_BODY, INK)

    table_top = party_bottom + 10
    header_h = 66
    cols = [(60, 640), (640, 900), (900, 1010), (1010, 1200), (1200, 1480), (1480, 1720), (1720, 1900), (1900, 2304)]
    heads = ["项目名称", "规格型号", "单位", "数量", "单价", "金额", "税率/征收率", "税额"]
    hline(d, 48, W - 48, table_top, 3)
    hline(d, 48, W - 48, table_top + header_h, 2)
    for (x0, x1), head in zip(cols, heads):
        text(d, ((x0 + x1) // 2, table_top + header_h // 2), head, F_TH, INK, "mm")
    for x, _ in cols[1:]:
        vline(d, x, table_top, table_top + header_h)

    row_h = 62
    y = table_top + header_h
    for item in rows:
        text(d, (72, y + 10), item["项目名称"], F_BODY, INK)
        text(d, ((cols[1][0] + cols[1][1]) // 2, y + 12), item.get("规格型号", ""), F_BODY, INK, "ma")
        text(d, ((cols[2][0] + cols[2][1]) // 2, y + 12), item["单位"], F_BODY, INK, "ma")
        text(d, ((cols[3][0] + cols[3][1]) // 2, y + 12), item["数量"], F_BODY, INK, "ma")
        text(d, (cols[4][1] - 24, y + 12), item["单价"], F_BODY, INK, "ra")
        text(d, (cols[5][1] - 24, y + 12), item["金额"], F_BODY, INK, "ra")
        text(d, ((cols[6][0] + cols[6][1]) // 2, y + 12), item["税率"], F_BODY, INK, "ma")
        text(d, (cols[7][1] - 24, y + 12), item["税额"], F_BODY, INK, "ra")
        for x, _ in cols[1:]:
            vline(d, x, y, y + row_h, 1)
        y += row_h
        hline(d, 48, W - 48, y, 1)

    # 合计行：与明细区同高，只填金额/税额两列
    text(d, (90, y + 18), "合    计", F_BODY, INK)
    text(d, (cols[5][1] - 24, y + 18), "¥" + money(rec["金额"]), F_BODY, INK, "ra")
    text(d, (cols[7][1] - 24, y + 18), "¥" + money(rec["税额"]), F_BODY, INK, "ra")
    for x, _ in cols[1:]:
        vline(d, x, y, y + row_h, 1)
    y += row_h
    hline(d, 48, W - 48, y, 2)

    text(d, (90, y + 24), "价税合计（大写）", F_BODY, INK)
    text(d, (430, y + 22), "⊗", F_BODY, INK)
    text(d, (490, y + 24), rmb_upper(rec["价税合计"]), F_BODY, INK)
    text(d, (W - 90, y + 24), f"（小写）¥{money(rec['价税合计'])}", F_BODY, INK, "ra")
    y += 86
    hline(d, 48, W - 48, y, 2)

    note_top = y + 20
    text(d, (90, note_top), "备", F_LABEL, GREY)
    text(d, (90, note_top + 40), "注", F_LABEL, GREY)
    vline(d, 160, y, H - 130, 1)
    for i, line in enumerate(rec.get("备注", [])):
        text(d, (190, note_top + i * 44), line, F_BODY, INK)

    hline(d, 48, W - 48, H - 130, 2)
    text(d, (90, H - 108), f"开票人：{rec['开票人']}", F_SMALL, INK)
    text(d, (760, H - 108), f"收款人：{rec['收款人']}", F_SMALL, INK)
    text(d, (1380, H - 108), f"复核人：{rec['复核人']}", F_SMALL, INK)

    stamp(d, img)
    img.convert("RGB").save(path, "PNG")

def build_records() -> list[dict]:
    rng = random.Random(20261108)
    recs = []

    fuel = pd.read_csv(SIM / "fuel_invoices.csv", encoding="utf-8-sig")
    for i, row in fuel.iterrows():
        qty = round(float(row.fuel_liters), 2)
        amount = round(float(row.amount_excluding_tax), 2)
        tax = round(amount * 0.13, 2)
        total = round(amount + tax, 2)
        recs.append({
            "编号": f"FP-FUEL-{i + 1:03d}",
            "发票号码": str(row.invoice_no),
            "开票日期": str(row.invoice_date),
            "发票类型": "电子发票（普通发票）",
            "购买方名称": BUYER, "购买方税号": BUYER_ID,
            "购买方地址电话": "上海市浦东新区张江路 88 号 021-58900000",
            "购买方开户行": "中国银行上海张江支行 4367 0000 1234 5678",
            "销售方名称": row.seller_name,
            "销售方税号": "91310101MA1FL" + f"{rng.randint(10000, 99999)}",
            "销售方地址电话": "上海市辖区 021-6" + f"{rng.randint(1000000, 9999999)}",
            "销售方开户行": "中国石化财务有限责任公司 1001 2000 3000 4000",
            "明细": [{
                "项目名称": "*成品油*柴油",
                "规格型号": "0#",
                "单位": "L",
                "数量": f"{qty:.2f}",
                "单价": price_text(amount / qty),
                "金额": money(amount),
                "税率": "13%",
                "税额": money(tax),
            }],
            "金额": amount, "税额": tax, "价税合计": total,
            "车牌号": row.plate,
            "备注": [f"车牌号：{row.plate}", f"加油日期：{row.invoice_date}", "数量单位：升（L）"],
            "开票人": rng.choice(["王芳", "李强", "张敏"]),
            "收款人": rng.choice(["王芳", "李强", "张敏"]),
            "复核人": rng.choice(["刘洋", "陈静"]),
            "明细行数": 1,
        })

    # (开票日期, 车牌, [(项目名称, 规格型号, 单位, 数量, 金额), ...])
    repair_specs = [
        ("2025-06-01", "苏A12345", [("*劳务*车辆维修费", "", "次", 1, 1180.53)]),
        ("2026-10-28", "苏A12346", [("*劳务*车辆维修费", "", "次", 1, 860.00)]),
        ("2026-10-30", "苏A12348", [("*劳务*更换轮胎", "11R22.5", "条", 2, 1980.00),
                                     ("*货物*轮胎", "11R22.5", "条", 2, 470.00)]),
        ("2026-11-02", "苏A12350", [("*劳务*车辆保养费", "", "次", 1, 620.00)]),
        ("2026-11-04", "苏A12347", [("*劳务*发动机大修", "", "项", 1, 1800.00),
                                     ("*货物*机油", "CI-4 15W-40", "桶", 2, 900.00),
                                     ("*货物*滤芯", "机油滤", "个", 1, 600.00)]),
        ("2026-11-05", "苏A12352", [("*服务*道路救援费", "", "次", 1, 480.00)]),
    ]
    for i, (day, plate, spec_lines) in enumerate(repair_specs):
        amount = round(sum(line[4] for line in spec_lines), 2)
        tax = round(amount * 0.13, 2)
        total = round(amount + tax, 2)
        lines = []
        for name, spec, unit, qty, line_amount in spec_lines:
            lines.append({
                "项目名称": name, "规格型号": spec, "单位": unit,
                "数量": f"{qty:.2f}",
                "单价": price_text(line_amount / qty),
                "金额": money(line_amount), "税率": "13%",
                "税额": money(round(line_amount * 0.13, 2)),
            })
        recs.append({
            "编号": f"FP-REPAIR-{i + 1:03d}",
            "发票号码": "25" + str(rng.randint(10 ** 17, 10 ** 18 - 1)),
            "开票日期": day,
            "发票类型": "电子发票（普通发票）",
            "购买方名称": BUYER, "购买方税号": BUYER_ID,
            "购买方地址电话": "上海市浦东新区张江路 88 号 021-58900000",
            "购买方开户行": "中国银行上海张江支行 4367 0000 1234 5678",
            "销售方名称": rng.choice(["上海顺达汽车维修服务有限公司", "上海沪东汽配维修中心", "上海路安汽车服务有限公司"]),
            "销售方税号": "91310115MA1H" + f"{rng.randint(10000, 99999)}",
            "销售方地址电话": "上海市浦东新区川沙路 500 号 021-5" + f"{rng.randint(1000000, 9999999)}",
            "销售方开户行": "中国工商银行上海川沙支行 1001 2345 6789 0001",
            "明细": lines,
            "金额": amount, "税额": tax, "价税合计": total,
            "车牌号": plate,
            "备注": [f"车牌号：{plate}", "维修项目详见明细", "本发票为模拟样张，仅供测试"],
            "开票人": rng.choice(["周涛", "孙丽", "赵鹏"]),
            "收款人": rng.choice(["周涛", "孙丽"]),
            "复核人": rng.choice(["吴敏", "郑凯"]),
            "明细行数": len(lines),
        })
    return recs


def export(recs: list[dict]) -> None:
    rows, lines = [], []
    for rec in recs:
        first = rec["明细"][0]
        multi = rec["明细行数"] > 1
        rows.append({
            "文件": rec["编号"] + ".png",
            "发票代码": "",
            "发票号码": rec["发票号码"],
            "开票日期": rec["开票日期"],
            "发票类型": rec["发票类型"],
            "购买方名称": rec["购买方名称"],
            "购买方税号": rec["购买方税号"],
            "销售方名称": rec["销售方名称"],
            "销售方税号": rec["销售方税号"],
            "项目名称": "多行明细（见明细表）" if multi else first["项目名称"],
            "规格型号": "" if multi else first["规格型号"],
            "单位": "" if multi else first["单位"],
            "数量": "" if multi else first["数量"],
            "单价": "" if multi else first["单价"],
            "金额": money(rec["金额"]),
            "税率": "13%",
            "税额": money(rec["税额"]),
            "价税合计": money(rec["价税合计"]),
            "价税合计大写": rmb_upper(rec["价税合计"]),
            "车牌号": rec["车牌号"],
            "备注": " / ".join(rec["备注"]),
            "开票人": rec["开票人"],
            "明细行数": rec["明细行数"],
        })
        for j, line in enumerate(rec["明细"], start=1):
            lines.append({
                "发票号码": rec["发票号码"],
                "文件": rec["编号"] + ".png",
                "行号": j,
                **line,
            })
    truth = pd.DataFrame(rows)
    detail = pd.DataFrame(lines)

    def num(series: pd.Series) -> pd.Series:
        return series.astype(str).str.replace(",", "").astype(float)

    check = truth.copy()
    check["金额+税额=价税合计"] = (num(check["金额"]) + num(check["税额"]) - num(check["价税合计"])).abs() < 0.005
    detail_check = detail.copy()
    detail_check["数量×单价=金额"] = (num(detail_check["数量"]) * num(detail_check["单价"])
                                 - num(detail_check["金额"])).abs() < 0.02
    detail_check["金额×13%=税额"] = (num(detail_check["金额"]) * 0.13).round(2).sub(num(detail_check["税额"])).abs() <= 0.011

    out = OUT / "发票识别_答案.xlsx"
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        truth.to_excel(writer, sheet_name="票面字段真值", index=False)
        detail.to_excel(writer, sheet_name="明细行真值", index=False)
        check.to_excel(writer, sheet_name="勾稽校验_票级", index=False)
        detail_check.to_excel(writer, sheet_name="勾稽校验_行级", index=False)
        notes = pd.DataFrame({"说明": [
            "1. 全部 45 张为模拟样张（图片带「模拟样张·仅供测试·不可报销」水印），不是真实发票。",
            "2. 字段顺序＝票面阅读顺序，可直接作为大模型输出的列顺序。",
            "3. 多行明细票（FP-REPAIR-003 两行、FP-REPAIR-005 三行）不得合并成一行："
            "票级表留空项目字段，明细逐行写进「明细行真值」。",
            "4. 单价保留票面原始小数位（柴油票最多 11 位，如 6.46024636058），"
            "不要四舍五入成 2 位再回算金额——回算会对不上分。",
            "5. 开票日期在票面写作「2026年11月04日」，抽取结果要求 YYYY-MM-DD。",
            "6. 价税合计大写与小写必须一致；大写用「圆」不是「元」；角后无分时补「整」。",
            "7. 柴油票的「车牌号」只出现在备注栏，不是独立字段，抽取时须从备注里取。",
            "8. 税率全部 13%（成品油/维修劳务）；「规格型号」多数为空，柴油是 0#、轮胎是 11R22.5。",
        ]})
        notes.to_excel(writer, sheet_name="口径说明", index=False)
        for name in ("票面字段真值", "明细行真值"):
            ws = writer.book[name]
            for cell in ws[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="1F4E79")
                cell.alignment = Alignment(vertical="center", wrap_text=True)
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
        for col, width in zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", [18, 10, 22, 12, 22, 26, 22, 30, 22, 26, 12, 6, 8, 14, 12, 8, 10, 12, 24, 10, 34, 8, 10]):
            writer.book["票面字段真值"].column_dimensions[col].width = width
        for col, width in zip("ABCDEFGHIJKLMNOP", [22, 18, 6, 22, 14, 6, 8, 16, 12, 10, 8, 10]):
            writer.book["明细行真值"].column_dimensions[col].width = width

    with pd.ExcelWriter(OUT / "发票识别_Excel模板.xlsx", engine="openpyxl") as writer:
        pd.DataFrame(columns=list(truth.columns) + ["识别置信度", "人工复核"]).to_excel(
            writer, sheet_name="票级", index=False)
        pd.DataFrame(columns=list(detail.columns) + ["识别置信度"]).to_excel(
            writer, sheet_name="明细行", index=False)

    truth.to_csv(OUT / "发票识别_答案_票级.csv", index=False, encoding="utf-8-sig")
    detail.to_csv(OUT / "发票识别_答案_明细行.csv", index=False, encoding="utf-8-sig")
    bad = int((~check["金额+税额=价税合计"]).sum()) + int((~detail_check["数量×单价=金额"]).sum())
    bad += int((~detail_check["金额×13%=税额"]).sum())
    print("勾稽失败项", bad)

def main() -> None:
    IMAGES.mkdir(parents=True, exist_ok=True)
    recs = build_records()
    for rec in recs:
        render(rec, IMAGES / f"{rec['编号']}.png")
    export(recs)
    print("OK", OUT, len(recs), "images")
    total = sum(r["价税合计"] for r in recs)
    print("合计价税", round(total, 2), "金额", round(sum(r['金额'] for r in recs), 2),
          "税额", round(sum(r['税额'] for r in recs), 2))
    print("大写样例", rmb_upper(1334.00), rmb_upper(1180.53 + 153.47))


if __name__ == "__main__":
    main()
