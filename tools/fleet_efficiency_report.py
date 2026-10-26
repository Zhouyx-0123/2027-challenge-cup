# -*- coding: utf-8 -*-
"""车辆·路段油耗审计报告（tools/）：读结果表 + 发票台账 → 出可审计报告。

定位：**只读分析工具**，不改 data/processed、不进闸门、不动 kpi。
核心逻辑在 src/fleet_efficiency.py，本文件只负责调度与落盘。

产出三份（默认写 outputs/，已被 .gitignore 挡住，不入公开仓库）：
    outputs/fleet_vehicle_efficiency.csv   车牌 × 月 的油耗标签
    outputs/fleet_route_model.csv          路段油耗系数模型
    outputs/fleet_audit_trail.json         审计线索（含证据链）

用法：
    python -m tools.fleet_efficiency_report
    python -m tools.fleet_efficiency_report --invoices outputs/invoice_ledger.csv
    python -m tools.fleet_efficiency_report --out-dir outputs
    python -m tools.fleet_efficiency_report --limit-vehicles 50   # 只看部分车，便于人工抽查

退出码：0 成功 / 1 系统错误（结果表缺失或读不了）/ 2 输入错误（参数非法）。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import fleet_efficiency as fe  # noqa: E402

PROCESSED = ROOT / "data" / "processed"
DEFAULT_OUT = ROOT / "outputs"
DEFAULT_INVOICES = ROOT / "outputs" / "invoice_ledger.csv"


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

def load_invoices(path: Path) -> List[Dict[str, Any]]:
    """读发票台账 CSV → 记录列表。文件不存在返回空列表（不报错，走无发票模式）。"""
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if not any(str(v).strip() for v in row.values()):
                continue
            rows.append({k: (v if v is not None else "") for k, v in row.items()})
    return rows


def _num(value: Any) -> Optional[float]:
    try:
        text = str(value).strip()
        if not text:
            return None
        f = float(text)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def build_report(segments: pd.DataFrame, routes: pd.DataFrame,
                 invoices: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """跑完整链路，返回 {vehicle_table, route_model, attributed, audit, assumptions}。"""
    # ★ 先落到物理行程口径（与主账 E₀ 同口径），否则理论油耗会被低估 N 倍
    enriched = fe.prepare_segments(segments)
    enriched = fe.attach_route_features(enriched, routes)

    price_history = fe.load_price_history()
    resolved: List[Dict[str, Any]] = []
    for rec in invoices:
        item = dict(rec)
        item["_resolved"] = fe.resolve_fuel_volume(rec, price_history)
        resolved.append(item)

    allocated = fe.allocate_invoice_fuel(resolved, enriched) if resolved else enriched.copy()
    vehicle_table = fe.vehicle_efficiency_table(enriched, allocated)
    route_model = fe.route_efficiency_model(enriched, allocated)
    attributed = fe.attribute_carbon(enriched, route_model)
    audit = fe.build_audit_trail(vehicle_table, route_model)
    gaps = fe.data_gap_summary(vehicle_table)

    assumptions = [
        "理论油耗取法定因子表轨；VSP 轨仅作高速段参考（低速段 VSP/法定可达 4.5 倍，见 docs/45）",
        "油量按油箱周期里程比例分摊；无发票覆盖的段不参与实测口径",
        "置信度按被油量覆盖的里程分档（<50km 不足 / 50-200 低 / 200-1000 中 / ≥1000 高）",
        "路段系数：实测样本段数 ≥%d 用实测中位数，否则用 VSP 先验"
        % fe.ROUTE_FACTOR_MIN_SEGMENTS,
    ]
    return {
        "vehicle_table": vehicle_table,
        "route_model": route_model,
        "attributed": attributed,
        "audit": audit,
        "assumptions": assumptions,
        "invoice_count": len(resolved),
        "invoice_resolved": sum(1 for r in resolved
                                if (r.get("_resolved") or {}).get("status") == "ok"),
        "data_gaps": gaps,
    }


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------

def _write_outputs(out_dir: Path, report: Dict[str, Any]) -> Dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, str] = {}

    vpath = out_dir / "fleet_vehicle_efficiency.csv"
    report["vehicle_table"].to_csv(vpath, index=False, encoding="utf-8-sig")
    paths["vehicle"] = str(vpath)

    rpath = out_dir / "fleet_route_model.csv"
    report["route_model"].to_csv(rpath, index=False, encoding="utf-8-sig")
    paths["route"] = str(rpath)

    apath = out_dir / "fleet_audit_trail.json"
    apath.write_text(json.dumps({
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "scope": "只读分析产出，不进闸门、不进 kpi、不改任何结果表",
        "assumptions": report["assumptions"],
        "counts": {
            "audit_items": len(report["audit"]),
            "invoice_records": report["invoice_count"],
            "invoice_resolved": report["invoice_resolved"],
            "data_gaps": report.get("data_gaps") or {},
        },
        "items": report["audit"],
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    paths["audit"] = str(apath)
    return paths


def _summarize(report: Dict[str, Any]) -> None:
    vt = report["vehicle_table"]
    rm = report["route_model"]
    print("  车 × 月 标签行数：%d" % len(vt))
    if len(vt):
        print("    置信度分布：%s" % vt["confidence"].value_counts().to_dict())
        print("    标签分布　：%s" % vt["label"].value_counts().to_dict())
        measured = vt[vt["measured_l_100km"].notna()]
        print("    有实测油耗的车月数：%d" % len(measured))
        if len(measured):
            print("    实测 L/100km：min=%.2f p50=%.2f max=%.2f"
                  % (measured["measured_l_100km"].min(),
                     measured["measured_l_100km"].median(),
                     measured["measured_l_100km"].max()))
    print("  路段模型行数：%d" % len(rm))
    if len(rm) and "factor_source" in rm.columns:
        print("    系数来源分布：%s" % rm["factor_source"].value_counts().to_dict())
    print("  审计线索条数：%d" % len(report["audit"]))
    gaps = report.get("data_gaps") or {}
    if gaps:
        print("  数据缺口：车月 %d 个，其中 %d 个有发票覆盖、%d 个还没接上发票"
              % (gaps.get("vehicle_months", 0), gaps.get("with_invoice", 0),
                 gaps.get("without_invoice", 0)))
    print("  发票记录：%d 条（成功解析 %d 条）"
          % (report["invoice_count"], report["invoice_resolved"]))


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="车辆·路段油耗审计报告（只读分析，不改结果表）")
    parser.add_argument("--segments", default=str(PROCESSED / "baseline_carbon.parquet"),
                        help="段表（默认 data/processed/baseline_carbon.parquet）")
    parser.add_argument("--routes", default=str(PROCESSED / "route_od.parquet"),
                        help="高德路线表（默认 data/processed/route_od.parquet）")
    parser.add_argument("--invoices", default=str(DEFAULT_INVOICES),
                        help="发票台账 CSV（不存在则走无发票模式）")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--limit-vehicles", type=int, default=0,
                        help="只保留前 N 辆车（0=全部），便于人工抽查")
    args = parser.parse_args(argv)

    seg_path = Path(args.segments)
    if not seg_path.exists():
        print("[FAIL] 段表不存在：%s（先跑 python -m src.pipeline）" % seg_path)
        return 2
    try:
        segments = pd.read_parquet(seg_path)
    except Exception as exc:
        print("[FAIL] 段表读不了：%s: %s" % (type(exc).__name__, exc))
        return 1

    route_path = Path(args.routes)
    routes = pd.DataFrame()
    if route_path.exists():
        try:
            routes = pd.read_parquet(route_path)
        except Exception:
            routes = pd.DataFrame()

    invoices = load_invoices(Path(args.invoices))
    if not invoices:
        print("  [WARN] 没有发票台账（%s），本次只出理论轨与路段先验，无实测油耗"
              % args.invoices)

    try:
        report = build_report(segments, routes, invoices)
    except Exception as exc:
        print("[FAIL] %s: %s" % (type(exc).__name__, exc))
        return 1

    if args.limit_vehicles and args.limit_vehicles > 0:
        keep = sorted(report["vehicle_table"]["plate"].dropna().unique())[:args.limit_vehicles]
        report["vehicle_table"] = report["vehicle_table"][
            report["vehicle_table"]["plate"].isin(keep)].reset_index(drop=True)
        report["audit"] = [a for a in report["audit"] if a.get("plate") in set(keep)]

    print("=" * 70)
    print("车辆·路段油耗审计报告（只读分析）")
    print("=" * 70)
    _summarize(report)
    paths = _write_outputs(Path(args.out_dir), report)
    print()
    for key, path in paths.items():
        print("  %-8s → %s" % (key, path))
    print()
    for text in report["assumptions"]:
        print("  · %s" % text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
