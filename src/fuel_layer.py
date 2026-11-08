"""Fuel allocation layer between transport evidence and finance reporting.

One row per allocated trip (not one row per invoice): the transport evidence
cycle already decides how one refuel is shared across trips.  This module keeps
that decision visible, adds plate/month rollups, and records trips that could
not be allocated.  It deliberately has no carbon columns and never writes
`baseline_carbon`, kpi or the 28-gate report.

The finance master is not modified automatically.  The v3 master requires a
human-confirmed cost before an allocation can be posted; this layer is the
reviewable bridge that carries `invoice_no`/`record_id` back to that decision.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd

LAYER_VERSION = "FUEL-LAYER-v1"
ALLOCATION_SHEET = "01_趟级油耗"
PLATE_SHEET = "02_按车牌汇总"
MONTH_SHEET = "03_按月份汇总"
UNCOVERED_SHEET = "04_未覆盖行程"
EXCLUDED_SHEET = "05_已排除非能源票"
GAP_SHEET = "06_数据缺口"
META_SHEET = "00_说明"

ALLOCATION_COLUMNS = [
    "batch_id", "source_mode", "period_start", "period_end",
    "trip_id", "waybill_no", "plate", "od_site", "depart_ts", "month",
    "vehicle_type", "invoice_record_id", "invoice_no", "invoice_date",
    "next_invoice_record_id", "next_invoice_no", "cycle_id",
    "cycle_km", "trip_km", "share", "allocated_liters", "l_per_100km",
    "allocated_cost", "yuan_per_km", "cost_basis",
    "theory_liters", "deviation", "theory_factor_id", "measurement",
    "status", "source_image", "input_sha256",
]
PLATE_COLUMNS = [
    "plate", "vehicle_type", "allocated_trips", "distance_km",
    "allocated_liters", "l_per_100km", "allocated_cost", "cost_per_km",
    "invoice_count",
]
MONTH_COLUMNS = [
    "month", "allocated_trips", "distance_km", "allocated_liters",
    "l_per_100km", "allocated_cost", "cost_per_km", "invoice_count",
]
UNCOVERED_COLUMNS = [
    "trip_id", "waybill_no", "plate", "od_site", "depart_ts", "month",
    "trip_km", "reason", "status",
]
EXCLUDED_COLUMNS = [
    "record_id", "invoice_no", "plate", "invoice_date", "item_name",
    "invoice_amount", "energy_type", "reason",
]


def _number(value):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _frame(rows, columns):
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows).reindex(columns=columns)


def _snapshot(result):
    return (result.get("run") or {}).get("input_snapshot") or {}


def _trip_rows(result):
    trips = _snapshot(result).get("trips") or []
    rows = []
    for trip in trips:
        row = dict(trip)
        trip_id = str(row.get("trip_id") or "")
        if trip_id:
            row["trip_id"] = trip_id
        rows.append(row)
    return rows


def _trip_index(result):
    rows = _trip_rows(result)
    return {row["trip_id"]: row for row in rows if row.get("trip_id")}


def _record_index(result):
    records = _snapshot(result).get("records") or []
    return {str(record.get("record_id")): record for record in records if record.get("record_id")}


def _month(value):
    text = str(value or "")
    if len(text) >= 7 and text[4] == "-" and text[:4].isdigit():
        return text[:7]
    return ""


def _waybills(value):
    if isinstance(value, (list, tuple)):
        return "、".join(str(item) for item in value if item)
    return str(value or "")


def _factor_id(item):
    factor = item.get("factor") or {}
    selection = item.get("theory_factor_selection") or {}
    return (factor.get("factor_id") or selection.get("factor_id")
            or selection.get("candidate_factor_ids") and "|".join(selection["candidate_factor_ids"])
            or "")


def _serialize(value):
    if isinstance(value, (bool, int, str)) or value is None:
        return value
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if hasattr(value, "item"):
        return _serialize(value.item())
    return str(value)


def _total(frame, column):
    if frame.empty or column not in frame:
        return 0.0
    return float(pd.to_numeric(frame[column], errors="coerce").fillna(0).sum())


def build_fuel_layer(result):
    """Return the trip-level fuel layer for one transport analysis batch."""
    run = result.get("run") or {}
    batch_id = str(run.get("batch_id") or "")
    source_mode = str(run.get("source_mode") or "")
    scope = run.get("scope") or {}
    period_start = scope.get("period_start") or ""
    period_end = scope.get("period_end") or ""
    input_sha256 = str(run.get("input_sha256") or "")
    trips = _trip_index(result)
    records = _record_index(result)
    rows = []
    for item in result.get("allocations") or []:
        trip_id = str(item.get("trip_id") or "")
        trip = trips.get(trip_id) or {}
        source = records.get(str(item.get("record_id"))) or {}
        next_source = records.get(str(item.get("end_record_id"))) or {}
        depart_ts = trip.get("depart_ts") or trip.get("start") or ""
        trip_km = _number(item.get("trip_km"))
        liters = _number(item.get("allocated_liters"))
        cost = _number(item.get("allocated_cost"))
        share = _number(item.get("share"))
        cycle_km = _number(item.get("cycle_km"))
        if cycle_km is None and trip_km and share:
            cycle_km = trip_km / share
        row = {
            "batch_id": batch_id,
            "source_mode": source_mode,
            "period_start": period_start,
            "period_end": period_end,
            "trip_id": trip_id,
            "waybill_no": _waybills(item.get("waybills") if item.get("waybills") is not None else trip.get("waybill_no")),
            "plate": str(item.get("plate") or trip.get("plate") or ""),
            "od_site": str(item.get("od_site") or trip.get("od_site") or ""),
            "depart_ts": str(depart_ts or ""),
            "month": _month(depart_ts),
            "vehicle_type": str(trip.get("vehicle_type") or ""),
            "invoice_record_id": str(item.get("record_id") or ""),
            "invoice_no": str(source.get("invoice_no") or ""),
            "invoice_date": str(source.get("invoice_date") or ""),
            "next_invoice_record_id": str(item.get("end_record_id") or ""),
            "next_invoice_no": str(next_source.get("invoice_no") or ""),
            "cycle_id": str(item.get("cycle_id") or ""),
            "cycle_km": cycle_km,
            "trip_km": trip_km,
            "share": share,
            "allocated_liters": liters,
            "l_per_100km": _number(item.get("l_per_100km")),
            "allocated_cost": cost,
            "yuan_per_km": _number(item.get("yuan_per_km")),
            "cost_basis": str(item.get("cost_basis") or ""),
            "theory_liters": _number(item.get("theory_liters")),
            "deviation": _number(item.get("deviation")),
            "theory_factor_id": str(_factor_id(item)),
            "measurement": str(item.get("measurement") or ""),
            "status": "allocated",
            "source_image": str(item.get("source_image") or ""),
            "input_sha256": input_sha256,
        }
        rows.append(row)
    return _frame(rows, ALLOCATION_COLUMNS)


def build_uncovered(result):
    """Trips in the saved snapshot that have no allocation, with a useful reason."""
    trips = _trip_rows(result)
    allocated = {str(item.get("trip_id")) for item in result.get("allocations") or []}
    reasons = {}
    for gap in result.get("gaps") or []:
        trip_id = str(gap.get("trip_id") or "")
        if trip_id:
            reasons.setdefault(trip_id, []).append(str(gap.get("reason") or "gap"))
    rows = []
    for trip in trips:
        trip_id = str(trip.get("trip_id") or "")
        if not trip_id or trip_id in allocated:
            continue
        rows.append({
            "trip_id": trip_id,
            "waybill_no": str(trip.get("waybill_no") or ""),
            "plate": str(trip.get("plate") or ""),
            "od_site": str(trip.get("od_site") or ""),
            "depart_ts": str(trip.get("depart_ts") or trip.get("start") or ""),
            "month": _month(trip.get("depart_ts") or trip.get("start")),
            "trip_km": _number(trip.get("trip_km")),
            "reason": "、".join(dict.fromkeys(reasons.get(trip_id, []))) or "no_valid_cycle_cover",
            "status": "uncovered",
        })
    return _frame(rows, UNCOVERED_COLUMNS)


def build_excluded(result):
    rows = []
    for item in result.get("excluded") or []:
        rows.append({column: item.get(column) for column in EXCLUDED_COLUMNS})
    return _frame(rows, EXCLUDED_COLUMNS)


def _summary(layer, keys, columns):
    if layer.empty:
        return pd.DataFrame(columns=columns)
    frame = layer.copy()
    for column in ("trip_km", "allocated_liters", "allocated_cost"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    frame["plate"] = frame["plate"].fillna("")
    frame["month"] = frame["month"].fillna("")
    grouped = frame.groupby(keys, dropna=False)
    rows = []
    for key, group in grouped:
        key_values = key if isinstance(key, tuple) else (key,)
        row = dict(zip(keys, key_values))
        km = float(group["trip_km"].sum())
        liters = float(group["allocated_liters"].sum())
        cost = float(group["allocated_cost"].sum())
        row.update({
            "allocated_trips": int(len(group)),
            "distance_km": km,
            "allocated_liters": liters,
            "l_per_100km": 100.0 * liters / km if km else None,
            "allocated_cost": cost,
            "cost_per_km": cost / km if km else None,
            "invoice_count": int(group["invoice_record_id"].nunique()),
        })
        if "plate" in keys:
            row["vehicle_type"] = next((x for x in group["vehicle_type"] if x), "")
        rows.append(row)
    result = pd.DataFrame(rows).reindex(columns=columns)
    return result.sort_values(keys, kind="stable").reset_index(drop=True)


def build_by_plate(layer):
    return _summary(layer, ["plate"], PLATE_COLUMNS)


def build_by_month(layer):
    return _summary(layer, ["month"], MONTH_COLUMNS)


def build_meta(result, layer, uncovered, excluded):
    run = result.get("run") or {}
    scope = run.get("scope") or {}
    coverage = result.get("coverage") or {}
    total_km = _total(layer, "trip_km")
    liters = _total(layer, "allocated_liters")
    cost = _total(layer, "allocated_cost")
    total_all_km = _number(coverage.get("total_km")) or 0.0
    return {
        "schema": LAYER_VERSION,
        "batch_id": str(run.get("batch_id") or ""),
        "source_mode": str(run.get("source_mode") or ""),
        "period_start": str(scope.get("period_start") or ""),
        "period_end": str(scope.get("period_end") or ""),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rule": "docs45-section5：同一车牌相邻两次补能之间为一个周期，按周期内行程里程分摊；不是标准满箱法。",
        "evidence_invoice_count": int(scope.get("evidence_invoice_count") or 0),
        "accounting_invoice_count": int(scope.get("invoice_count") or 0),
        "excluded_invoice_count": int(scope.get("excluded_invoice_count") or len(excluded)),
        "trip_count": int(scope.get("trip_count") or 0),
        "allocated_trips": int(len(layer)),
        "uncovered_trips": int(len(uncovered)),
        "excluded_rows": int(len(excluded)),
        "allocated_distance_km": total_km,
        "total_distance_km": total_all_km,
        "allocation_rate": coverage.get("allocation_rate"),
        "allocated_liters": liters,
        "allocated_cost": cost,
        "l_per_100km": 100.0 * liters / total_km if total_km else None,
        "cost_per_km": cost / total_km if total_km else None,
        "cost_basis": str(scope.get("cost_basis") or (run.get("references_snapshot") or {}).get("cost_basis") or ""),
        "input_sha256": str(run.get("input_sha256") or ""),
        "carbon_boundary": "本层不写碳列，不改 baseline_carbon、kpi.json 或 28 项闸门；碳账仍以主账 E0 为准。",
        "posting_boundary": "不自动写财务主表分摊明细。财务主表 v3 要求人工确认成本后才允许分摊；本层供复核、汇总和回链。",
        "sources": "src/transport_evidence.py allocations + run.input_snapshot（票据与行程快照）",
    }


def build_payload(result):
    layer = build_fuel_layer(result)
    uncovered = build_uncovered(result)
    excluded = build_excluded(result)
    gaps = pd.DataFrame([_serialize(item) for item in (result.get("gaps") or [])])
    meta = build_meta(result, layer, uncovered, excluded)
    return {
        "meta": meta,
        "allocations": layer,
        "by_plate": build_by_plate(layer),
        "by_month": build_by_month(layer),
        "uncovered": uncovered,
        "excluded": excluded,
        "gaps": gaps,
    }


def _excel_safe(frame):
    frame = frame.copy()
    for column in frame.columns:
        if frame[column].dtype == object:
            frame[column] = frame[column].map(lambda value: _serialize(value)
                                              if isinstance(value, (dict, list, tuple)) else value)
    return frame


def _cell(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(_serialize(value), ensure_ascii=False)
    if hasattr(value, "item"):
        return value.item()
    return value


def _write_sheet(workbook, name, frame):
    from openpyxl.styles import Font
    frame = _excel_safe(frame)
    sheet = workbook.create_sheet(name)
    sheet.append([str(column) for column in frame.columns])
    for row in frame.itertuples(index=False, name=None):
        sheet.append([_cell(value) for value in row])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"
    for index, column in enumerate(frame.columns, start=1):
        width = max(10, min(42, len(str(column)) + 2))
        if not frame.empty:
            observed = frame[column].astype(str).str.len().max()
            if pd.notna(observed):
                width = max(width, min(42, int(observed) + 2))
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width


def write_fuel_layer(result, directory=None, *, stem=None, write_json=True):
    """Write the layer workbook atomically; return paths and headline totals."""
    from src.finance_workflow import finance_dir
    target = Path(directory) if directory else finance_dir()
    target.mkdir(parents=True, exist_ok=True)
    mode = str((result.get("run") or {}).get("source_mode") or "simulated")
    stem = stem or f"fuel_layer_{mode}"
    payload = build_payload(result)
    meta_frame = pd.DataFrame([{"项目": key, "值": value} for key, value in payload["meta"].items()])
    from openpyxl import Workbook
    fd, temp_name = tempfile.mkstemp(prefix=stem + "_", suffix=".xlsx", dir=target)
    os.close(fd)
    tmp_xlsx = Path(temp_name)
    try:
        workbook = Workbook()
        workbook.remove(workbook.active)
        _write_sheet(workbook, META_SHEET, meta_frame)
        _write_sheet(workbook, ALLOCATION_SHEET, payload["allocations"])
        _write_sheet(workbook, PLATE_SHEET, payload["by_plate"])
        _write_sheet(workbook, MONTH_SHEET, payload["by_month"])
        _write_sheet(workbook, UNCOVERED_SHEET, payload["uncovered"])
        _write_sheet(workbook, EXCLUDED_SHEET, payload["excluded"])
        _write_sheet(workbook, GAP_SHEET, payload["gaps"])
        workbook.save(tmp_xlsx)
        xlsx_path = target / (stem + ".xlsx")
        os.replace(tmp_xlsx, xlsx_path)
    finally:
        if tmp_xlsx.exists():
            try:
                tmp_xlsx.unlink()
            except OSError:
                pass
    json_path = None
    if write_json:
        json_path = target / (stem + ".json")
        serializable = {
            key: (value if not isinstance(value, pd.DataFrame) else value.to_dict(orient="records"))
            for key, value in payload.items()
        }
        json_fd, json_name = tempfile.mkstemp(prefix=stem + "_", suffix=".json", dir=target)
        os.close(json_fd)
        tmp_json = Path(json_name)
        try:
            tmp_json.write_text(json.dumps(_serialize(serializable), ensure_ascii=False, indent=2),
                                encoding="utf-8")
            os.replace(tmp_json, json_path)
        finally:
            if tmp_json.exists():
                try:
                    tmp_json.unlink()
                except OSError:
                    pass
    return {"xlsx": str(xlsx_path), "json": str(json_path) if json_path else None,
            "meta": payload["meta"], "rows": len(payload["allocations"])}


def load_fuel_layer(path):
    """Read all sheets from a written layer (for the finance panel).

    Excel/Arrow cannot carry 20-digit invoice numbers as int64; pandas may
    infer them back as Python ints on read.  Keep every object-column integer
    (invoice numbers, IDs) as text so the Streamlit table and JSON stay exact.
    """
    sheets = pd.read_excel(path, sheet_name=None)
    for name, frame in sheets.items():
        if name == META_SHEET:
            continue
        for column in frame.columns:
            if frame[column].dtype == object:
                frame[column] = frame[column].map(
                    lambda value: str(value) if isinstance(value, int) and not isinstance(value, bool) else value
                )
    return sheets


def main(argv=None):
    parser = argparse.ArgumentParser(description="从运输证据批次生成油耗中间层")
    parser.add_argument("--run", required=True, help="transport run JSON 路径")
    parser.add_argument("--out", default=None, help="输出目录，默认 finance_dir()")
    parser.add_argument("--stem", default=None, help="输出文件前缀，默认 fuel_layer_<mode>")
    args = parser.parse_args(argv)
    payload = json.loads(Path(args.run).read_text(encoding="utf-8"))
    report = write_fuel_layer(payload, args.out, stem=args.stem)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
