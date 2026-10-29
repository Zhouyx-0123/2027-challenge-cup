# -*- coding: utf-8 -*-
"""OBD + GPS 实测碳排流水线：原始流 → 车级碳排结果表。

**与主链路的关系（重要）**
    主链路 ``src/pipeline.py`` 管的是「发票 + 运单 → E₀ 主口径」，那 28 条闸门
    对拍判据是冻结的。本模块是**另一条独立轨道**：OBD 实测油耗 → 守恒法 → 每车碳排。

    因此本模块：
      · **不改** src/pipeline.py 的任何口径、闸门目标与对拍值；
      · **不写** main pipeline 的结果表（不碰 wide_data / baseline_carbon / final_report）；
      · 结果表单独落在 data/processed/ 下，前端只读；
      · 失败**不阻断**主链路（降级纪律，与可选段 T7 一致）。

用法::

    python -m src.obd_pipeline                 # 读 data/raw/obd_gps/obd_stream.csv
    python -m src.obd_pipeline --demo          # 无原始流时用确定性演示序列
    python -m src.obd_pipeline --from-csv X.csv

产出：

    obd_points.parquet        逐点工况 + 碳排（前端画曲线用）
    obd_vehicle_carbon.parquet 每车聚合（**每台车/每台发动机的碳排放**）
    obd_trips.parquet         行程级聚合
    obd_summary.json          汇总与数据质量
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import obd_gps, schema  # noqa: E402

PROCESSED = ROOT / "data" / "processed"

POINTS_PATH = PROCESSED / schema.RESULT_FILES["obd_points"]
VEHICLE_PATH = PROCESSED / schema.RESULT_FILES["obd_vehicle_carbon"]
TRIPS_PATH = PROCESSED / schema.RESULT_FILES["obd_trips"]
SUMMARY_PATH = PROCESSED / schema.RESULT_FILES["obd_summary"]


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _save_parquet(df: pd.DataFrame, path: Path, tries: int = 4) -> None:
    """落 parquet（带重试：Windows 上文件被占用时偶发失败）。"""
    import time

    path.parent.mkdir(parents=True, exist_ok=True)
    last: Optional[Exception] = None
    for attempt in range(tries):
        try:
            df.to_parquet(path, index=False)
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.4 * (attempt + 1))
    raise RuntimeError("写 parquet 失败 %s：%s" % (path.name, last))


def _flatten_flags(frame: pd.DataFrame) -> pd.DataFrame:
    """把 qc_flags 列表列压成分号分隔字符串（parquet 与 CSV 才好往返）。"""
    if frame is None or frame.empty or "qc_flags" not in frame.columns:
        return frame
    out = frame.copy()
    out["qc_flags"] = out["qc_flags"].apply(
        lambda item: ";".join(str(v) for v in (item or [])) if isinstance(item, (list, tuple)) else (
            "" if item is None or (isinstance(item, float) and pd.isna(item)) else str(item)))
    return out


def load_records(csv_path: Optional[Path] = None,
                 use_demo: bool = False) -> tuple[List[Dict[str, Any]], str]:
    """读原始流 → 记录列表。返回 ``(records, 来源说明)``。

    优先读 CSV（设备直传或文件回传）；CSV 不存在且 ``use_demo`` 为真时用确定性演示序列。
    """
    path = Path(csv_path) if csv_path else obd_gps.stream_path()
    frame = obd_gps.read_stream(path)
    if not frame.empty:
        records = frame.to_dict(orient="records")
        return records, "file:%s" % path
    if use_demo:
        demo = obd_gps.demo_stream()
        return demo.to_dict(orient="records"), "demo:demo_stream"
    return [], "empty:%s" % path


def run_obd_pipeline(csv_path: Optional[Path] = None,
                     use_demo: bool = False,
                     write: bool = True) -> Dict[str, Any]:
    """跑一遍 OBD 实测碳排流水线，返回报告 dict。

    步骤：原始记录 → 归一校验 → 守恒法算碳 → 分车聚合 → 落表。
    **任何一步都不改主口径结果表。**
    """
    started = _now()
    records, source = load_records(csv_path, use_demo=use_demo)
    if not records:
        return {
            "status": "skipped",
            "reason": "没有原始流：%s" % source,
            "generated_at": started,
            "n_records": 0,
        }

    normalized, rejects = obd_gps.normalize_records(records)
    if normalized.empty:
        report = {
            "status": "failed",
            "reason": "归一后没有可用记录（全部缺必填字段）",
            "generated_at": started,
            "n_records": len(records),
            "n_accepted": 0,
            "n_rejected": int(len(rejects)),
            "reject_reasons": {str(k): int(v) for k, v in
                               rejects["reason"].value_counts().items()} if not rejects.empty else {},
        }
        if write:
            _write_summary(report)
        return report

    carbon = obd_gps.attach_carbon(normalized)
    vehicles = obd_gps.summarize_vehicles(carbon)
    trips = obd_gps.summarize_trips(carbon)
    quality = obd_gps.qc_report(carbon, rejects)

    total_kg = float(pd.to_numeric(vehicles.get("kg_co2"), errors="coerce").fillna(0).sum())
    total_dist = float(pd.to_numeric(vehicles.get("dist_km"), errors="coerce").fillna(0).sum())
    total_fuel = float(pd.to_numeric(vehicles.get("fuel_l"), errors="coerce").fillna(0).sum())

    factors = obd_gps.load_factors()
    calc = obd_gps.load_calc_factors()
    factor_note = ""
    if not vehicles.empty:
        first = vehicles.iloc[0]
        factor_note = str(first.get("factor_source") or "")
        consistency = first.get("factor_consistency_pct")
        if consistency is not None and pd.notna(consistency):
            factor_note += "｜守恒推导对拍偏差 %s%%" % round(float(consistency), 3)

    report: Dict[str, Any] = {
        "status": "ok",
        "generated_at": started,
        "source": source,
        "is_demo": source.startswith("demo"),
        "n_records": int(len(records)),
        "n_accepted": quality.get("n_accepted"),
        "n_rejected": quality.get("n_rejected"),
        "n_vehicles": int(len(vehicles)),
        "n_trips": int(len(trips)),
        "total_kg_co2": round(total_kg, 4),
        "total_t_co2e": round(total_kg / 1000.0, 6),
        "total_dist_km": round(total_dist, 4),
        "total_fuel_l": round(total_fuel, 4),
        "fleet_co2_gkm": round(total_kg * 1000.0 / total_dist, 3) if total_dist > 0 else None,
        "factor_source": factor_note,
        "contract_version": (obd_gps.load_contract().get("version") or ""),
        "factor_version": (obd_gps.load_factors().get("version") or ""),
        "calc_factor_version": (calc.get("version") or ""),
        "qc_flags": quality.get("qc_flags"),
        "reject_reasons": quality.get("reject_reasons"),
        "accept_rate": quality.get("accept_rate"),
    }

    if write:
        _save_parquet(_flatten_flags(carbon), POINTS_PATH)
        _save_parquet(vehicles, VEHICLE_PATH)
        _save_parquet(trips, TRIPS_PATH)
        _write_summary(report)

    report["outputs"] = {
        "points": str(POINTS_PATH),
        "vehicles": str(VEHICLE_PATH),
        "trips": str(TRIPS_PATH),
        "summary": str(SUMMARY_PATH),
    }
    return report


def _write_summary(report: Dict[str, Any]) -> None:
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def print_report(report: Dict[str, Any]) -> None:
    """把报告打成给人看的样子（中文，控制台友好）。"""
    print("=" * 78)
    print("OBD + GPS 实测碳排流水线")
    print("=" * 78)
    if report.get("status") != "ok":
        print("[%s] %s" % (str(report.get("status", "?")).upper(), report.get("reason", "")))
        return
    print("来源      ：%s%s" % (report.get("source", ""),
                                 "（演示数据）" if report.get("is_demo") else ""))
    print("原始记录  ：%d 条（可用 %s / 不可用 %s）"
          % (report.get("n_records", 0), report.get("n_accepted", 0),
             report.get("n_rejected", 0)))
    print("车辆 / 行程：%d 台 / %d 段" % (report.get("n_vehicles", 0), report.get("n_trips", 0)))
    print("总碳排    ：%.4f kgCO₂e（%.6f tCO₂e）"
          % (report.get("total_kg_co2", 0.0), report.get("total_t_co2e", 0.0)))
    print("总里程    ：%.2f km｜总油耗 %.3f L" % (report.get("total_dist_km", 0.0),
                                                  report.get("total_fuel_l", 0.0)))
    gkm = report.get("fleet_co2_gkm")
    if gkm:
        print("车队碳强度：%.2f gCO₂/km" % gkm)
    print("因子      ：%s" % (report.get("factor_source") or "—"))
    flags = report.get("qc_flags") or {}
    if flags:
        print("质控标记  ：%s" % "、".join("%s×%d" % (k, v) for k, v in
                                          sorted(flags.items(), key=lambda kv: -kv[1])))
    reasons = report.get("reject_reasons") or {}
    if reasons:
        print("不可用原因：%s" % "、".join("%s×%d" % (k, v) for k, v in reasons.items()))
    print("-" * 78)
    print("结果表：%s" % "、".join(Path(p).name for p in (report.get("outputs") or {}).values()))
    print("提示：实测碳轨**不参与**主闸门对拍，也不写入 E₀ 主口径。")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="OBD + GPS 实测碳排流水线（独立轨道，不改主口径与闸门）")
    parser.add_argument("--from-csv", default="", help="指定原始流 CSV 路径")
    parser.add_argument("--demo", action="store_true",
                        help="没有原始流时用确定性演示序列")
    parser.add_argument("--no-write", action="store_true", help="只算不落表（自测用）")
    args = parser.parse_args(argv)
    csv_path = Path(args.from_csv) if args.from_csv else None
    try:
        report = run_obd_pipeline(csv_path=csv_path, use_demo=args.demo,
                                  write=not args.no_write)
    except Exception as exc:  # noqa: BLE001
        print("[FAIL] OBD 流水线异常：%s: %s" % (type(exc).__name__, exc))
        traceback.print_exc()
        return 1
    print_report(report)
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
