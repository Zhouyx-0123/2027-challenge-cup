# -*- coding: utf-8 -*-
"""发票智能录入 CLI（tools/）：发票图片 → 发票台账 + 报告。

定位：**CLI 包装层**。核心逻辑（识别、JSON 抽取、字段归一、碳排口径）全部在
`src/invoice.py`，本文件只负责批量调度、幂等、台账与报告落盘、退出码。
这样「同一套口径只有一份」，CLI 与页面（app/main_views.py）共用同一个内核。

**它不做什么（重要）**
    1. 不改宽表、不写 data/processed、不进闸门、不动 kpi —— 对拍链路一字未动；
    2. **不编造数据** —— 离线/失败时油量留空并标状态，绝不填假数充数
       （docs/42 §3.1「不得为填满字段而编造发票、客户或业务记录」）。

**输出契约（后续接 preprocess 的接口，现在就定死）**
    outputs/invoice_ledger.csv
        invoice_no, invoice_code, seller_name, invoice_ok, invoice_amount,
        amount_excluding_tax, tax_amount, item_name, quantity, unit,
        fuel_liters, fuel_source, plate, invoice_date, source_image,
        confidence, status, note, carbon_* / co2e_kg / included_in_total
        其中 invoice_no / invoice_ok / invoice_amount 三列**直接对齐**
        src/preprocess.py 已冻结的宽表发票列（docs/07 §2.6）——
        接进去时 preprocess 的 invoice_source 分支会自动激活。

    outputs/invoice_report.json
        docs/42 §7 的报告格式：status / errors[] / warnings[]，区分真实与演示。

四条纪律（照抄 tools/route_fetch.py）：
    1. 失败不抛异常——单张失败 → 记 error 并继续；
    2. key 只在服务端读，永不进 git、永不进前端；
    3. 幂等——已识别（台账里有该 source_image）直接跳过，第二次运行零 API 调用；
    4. 离线可演——--offline 不联网，只标状态，不造假数。

用法：
    python -m tools.invoice_ocr --input data/raw/发票
    python -m tools.invoice_ocr --input data/raw/发票 --limit 20      # 先小样本试跑
    python -m tools.invoice_ocr --input data/raw/发票 --refresh       # 忽略缓存重抽
    python -m tools.invoice_ocr --input data/raw/发票 --offline       # 无 key/断网演练
    python -m tools.invoice_ocr --input data/raw/发票 --report outputs/invoice_report.json

退出码：0 成功（可含 WARN）/ 1 系统错误（缺依赖、目录不可读）/ 2 输入错误（目录不存在、无图片）。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import invoice as kernel  # noqa: E402  ← 核心逻辑只有一份

KEY_PATH = ROOT / "config" / "deepseek_key.txt"

DEFAULT_INPUT = ROOT / "data" / "raw" / "发票"
DEFAULT_LEDGER = ROOT / "outputs" / "invoice_ledger.csv"
DEFAULT_REPORT = ROOT / "outputs" / "invoice_report.json"

#: 台账列（顺序即 CSV 列顺序）——对齐 preprocess 宽表的 invoice_* 三列
LEDGER_COLUMNS: List[str] = [
    "invoice_no", "invoice_code", "seller_name", "invoice_ok", "invoice_amount",
    "amount_excluding_tax", "tax_amount", "item_name", "quantity", "unit",
    "unit_price",
    "fuel_liters", "fuel_source", "plate", "invoice_date", "source_image",
    "confidence", "status", "note", "carbon_status", "carbon_method",
    "emission_category", "activity_quantity", "activity_unit", "co2e_kg",
    "carbon_scope", "included_in_total", "carbon_note",
]


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_api_key() -> str:
    """读 key：环境变量 → config/deepseek_key.txt。永不入库、只在本机读。"""
    key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if key:
        return key
    if KEY_PATH.exists():
        text = KEY_PATH.read_text(encoding="utf-8", errors="ignore").strip()
        if text.upper().startswith("DEEPSEEK_API_KEY="):
            text = text.split("=", 1)[1].strip()
        if text and not text.startswith("请至"):
            return text
    return ""


def read_ledger(path: Path) -> Dict[str, Dict[str, str]]:
    """读已有台账（幂等用）：source_image → 行。"""
    out: Dict[str, Dict[str, str]] = {}
    if not path.exists():
        return out
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                key = (row.get("source_image") or "").strip()
                if key:
                    out[key] = row
    except Exception:
        return {}
    return out


def write_ledger(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=LEDGER_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in LEDGER_COLUMNS})


def collect_images(folder: Path) -> List[Path]:
    if not folder.exists():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in kernel.ALLOWED_EXT)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run(input_dir: Path, ledger_path: Path, report_path: Path,
        limit: int = 0, refresh: bool = False, offline: bool = False) -> Dict[str, Any]:
    """跑一轮识别，返回报告 dict。

    纪律：单张失败只记 error 并继续；离线模式不联网且**不造假数**。
    """
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []
    counts = {"images": 0, "recognized": 0, "partial": 0,
              "failed": 0, "skipped": 0}

    images = collect_images(input_dir)
    if limit and limit > 0:
        images = images[:limit]
    counts["images"] = len(images)

    existing = {} if refresh else read_ledger(ledger_path)
    api_key = "" if offline else load_api_key()
    if not offline and not api_key:
        warnings.append({"code": "no_api_key",
                         "message": "未找到 DeepSeek API key（config/deepseek_key.txt 或环境变量 "
                                    "DEEPSEEK_API_KEY）；本轮按离线处理，不联网、不造假数。"})

    factors = kernel.load_factors()
    rows: List[Dict[str, Any]] = []

    for image in images:
        source_image = image.name
        if source_image in existing:
            row = dict(existing[source_image])
            row["status"] = row.get("status") or "ok"
            rows.append(row)
            counts["skipped"] += 1
            continue

        base: Dict[str, Any] = {
            "invoice_code": "", "invoice_no": "", "seller_name": "",
            "invoice_ok": 0, "invoice_amount": "", "amount_excluding_tax": "",
            "tax_amount": "", "item_name": "", "quantity": "", "unit": "",
            "fuel_liters": "", "fuel_source": "", "plate": "",
            "invoice_date": "", "source_image": source_image,
            "confidence": "", "status": "", "note": "",
        }

        if offline or not api_key:
            base["status"] = "offline"
            base["note"] = "离线模式：未识别（不编造发票数据）"
            rows.append(base)
            counts["failed"] += 1
            warnings.append({"file": source_image, "code": "offline",
                             "message": "离线模式未识别（不编造发票数据）"})
            continue

        parsed = kernel.extract_invoice_fields(
            image.read_bytes(), api_key, filename=image.name)

        for field in ("invoice_code", "invoice_no", "seller_name", "item_name",
                      "unit", "plate", "invoice_date"):
            base[field] = parsed.get(field, "")
        amount = parsed.get("invoice_amount")
        liters = parsed.get("fuel_liters")
        quantity = parsed.get("quantity")
        for field in ("invoice_amount", "amount_excluding_tax", "tax_amount",
                      "quantity", "fuel_liters", "unit_price"):
            value = parsed.get(field)
            base[field] = value if value is not None else ""
        base["fuel_source"] = "deepseek_flash_vision" if liters is not None else ""
        base["status"] = parsed.get("status", "failed")
        base["note"] = parsed.get("note", "")

        # invoice_ok 对齐 preprocess 宽表口径：1=有有效发票，0=未匹配/不适用
        base["invoice_ok"] = 1 if (amount is not None or liters is not None
                                   or quantity is not None) else 0

        # 碳排交叉校验列：因子来自 config/calc_factors.json，不在工具里维护第二套口径
        if liters is not None and factors:
            fuel_track = kernel.calc_fuel_carbon(liters, factors)
            if fuel_track.get("ok"):
                base["note"] = (base["note"] + "；" if base["note"] else "") + \
                    "碳排交叉校验 %.2f kg（TTW %.2f + WTT %.2f）" % (
                        fuel_track["co2_kg"], fuel_track["ttw_kg"], fuel_track["wtt_kg"])

        status = base["status"]
        if status == "ok":
            counts["recognized"] += 1
        elif status == "partial":
            counts["partial"] += 1
            warnings.append({"file": source_image, "code": "partial",
                             "message": base["note"] or "仅识别出部分字段"})
        else:
            counts["failed"] += 1
            errors.append({"file": source_image, "code": "extract",
                           "message": base["note"] or "未识别出油量与金额"})
        rows.append(base)

    # 通用财务发票核算与 OCR 解耦：缓存命中行也会按当前因子重算，因子更新无需重调 API。
    carbon_batch = kernel.calculate_invoice_batch(rows, factors)
    for row, carbon in zip(rows, carbon_batch["results"]):
        row["carbon_status"] = carbon["status"]
        row["carbon_method"] = carbon.get("method") or ""
        row["emission_category"] = carbon.get("emission_category") or ""
        row["activity_quantity"] = carbon.get("activity_quantity")
        if row["activity_quantity"] is None:
            row["activity_quantity"] = ""
        row["activity_unit"] = carbon.get("activity_unit") or ""
        row["co2e_kg"] = carbon.get("emissions_kg_co2e")
        if row["co2e_kg"] is None:
            row["co2e_kg"] = ""
        row["carbon_scope"] = carbon.get("scope") or ""
        row["included_in_total"] = 1 if carbon.get("included_in_total") else 0
        row["carbon_note"] = carbon.get("reason") or "；".join(carbon.get("warnings") or [])

    write_ledger(ledger_path, rows)

    # 状态判定：一张都没识别出来且有硬错误 → fail；有警告/有未识别 → warn；全成功 → pass
    status = "fail" if errors and counts["recognized"] == 0 else \
             ("warn" if errors or warnings or counts["failed"] else "pass")
    report = {
        "status": status,
        "source": "real",
        "generated_at": _now(),
        "api": {
            "provider": "deepseek",
            "model": kernel.DEEPSEEK_VISION_MODEL,
            "endpoint": kernel.DEEPSEEK_CHAT_URL,
            "mode": "offline" if (offline or not api_key) else "online",
            "key_present": bool(api_key),
        },
        "factor_source": "config/calc_factors.json（活动因子）+ config/invoice_spend_factors.json（支出因子）"
                         if factors else "未读到活动因子",
        "carbon_summary": carbon_batch["summary"],
        "carbon_warnings": carbon_batch["warnings"],
        "counts": counts,
        "input_dir": str(input_dir),
        "ledger": str(ledger_path),
        "errors": errors,
        "warnings": warnings,
        "note": "本报告不含车牌/发票号/金额等可识别业务明细；明细见本地台账，不提交公开仓库。",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="发票智能录入：图片 → DeepSeek M3 抽取 → 发票台账 + 报告")
    parser.add_argument("--input", default=str(DEFAULT_INPUT),
                        help="发票图片目录（默认 data/raw/发票）")
    parser.add_argument("--out", default=str(DEFAULT_LEDGER),
                        help="台账 CSV 输出路径")
    parser.add_argument("--report", default=str(DEFAULT_REPORT),
                        help="报告 JSON 输出路径")
    parser.add_argument("--limit", type=int, default=0,
                        help="只处理前 N 张（0 = 全部）")
    parser.add_argument("--refresh", action="store_true",
                        help="忽略已有台账，强制重抽")
    parser.add_argument("--offline", action="store_true",
                        help="离线演练：不联网、不编造数据")
    args = parser.parse_args(argv)

    input_dir = Path(args.input)
    if not input_dir.exists():
        print("[FAIL] 输入目录不存在：%s" % input_dir)
        return 2

    try:
        report = run(input_dir, Path(args.out), Path(args.report),
                     limit=args.limit, refresh=args.refresh, offline=args.offline)
    except Exception as exc:
        print("[FAIL] %s: %s" % (type(exc).__name__, exc))
        return 1

    counts = report["counts"]
    print("=" * 68)
    print("发票智能录入：%s" % report["api"]["mode"])
    print("=" * 68)
    print("  图片 %d 张｜识别 %d｜部分 %d｜失败 %d｜跳过 %d"
          % (counts["images"], counts["recognized"], counts["partial"],
             counts["failed"], counts["skipped"]))
    print("  台账：%s" % report["ledger"])
    print("  报告：%s" % args.report)
    for item in report["warnings"]:
        print("  [WARN] %s" % item.get("message", ""))
    for item in report["errors"][:10]:
        print("  [FAIL] %s：%s" % (item.get("file", ""), item.get("message", "")))
    if len(report["errors"]) > 10:
        print("  [FAIL] ……另有 %d 条错误，详见报告" % (len(report["errors"]) - 10))
    print("  状态：%s" % report["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
