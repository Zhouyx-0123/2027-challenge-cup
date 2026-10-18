# -*- coding: utf-8 -*-
"""调度 + 闸门 + 台账 / 披露（G1–G5）。

G1 调度器：三段顺序调用，任何一段失败都报“第几段 + 哪一段 + 原始异常”，
          并写 run_log.json；退出码非 0。
G2 闸门：跑完自动核对七个数（+ 结构一致性）；演示数据只跳过“依赖真数据”的
          五项，因子表两项（14.67 / 12.68%）永远校验；--strict 可强制全绿变红。
G3 台账：三行方案 × 六字段（核算日期/边界/排放量/配额缺口/履约成本/附注）。
G4 披露：六要素（边界/因子来源/假设/数据期/年化/局限）。
G5 一键入口：python -m src.pipeline

铁律：前端只读本文件产出的结果表，绝不调用这里任何函数。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import evaluator, optimizer, preprocess  # noqa: E402

PROCESSED = ROOT / "data" / "processed"
WIDE_PATH = PROCESSED / "wide_data.parquet"
SEG_PATH = PROCESSED / "baseline_carbon.parquet"
SCHEMES_PATH = PROCESSED / "final_report.parquet"
HOTSPOTS_PATH = PROCESSED / "hotspots.parquet"
BAND_PATH = PROCESSED / "od_band.parquet"
LEDGER_PATH = PROCESSED / "ledger.parquet"
DISCLOSURE_PATH = PROCESSED / "disclosure.json"
KPI_PATH = PROCESSED / "kpi.json"
QUALITY_PATH = PROCESSED / "data_quality.json"
GATE_PATH = PROCESSED / "gate_report.json"
RUN_LOG_PATH = PROCESSED / "run_log.json"
SOURCE_PATH = PROCESSED / "_source.json"
MOCK_FLAG = PROCESSED / "_mock.flag"

ACCOUNTING_DATE = "2026-11-01"
BOUNDARY = "自营公路运输 · 范围一（移动源燃烧）"
ANNUAL_FACTOR = 3.0417  # 365 / 120
DATA_PERIOD = "2023 年 1/2/11/12 月，共 120 天（另有 10 条 3 月记录，保留计碳）"

SIX_LEDGER = ["accounting_date", "boundary", "emission_t", "quota_gap", "book_cost", "note"]
SIX_DISCLOSURE = ["boundary", "factor_source", "assumptions", "period", "annual_factor", "limitations"]

WIDE_REQUIRED = [
    "waybill_no", "plate", "vehicle_type", "vclass", "load_t", "origin", "dest",
    "od_pair", "dist_km", "month",
    "flag_dup", "flag_anomaly", "flag_basis", "flag_estimated",
]
SEG_REQUIRED = WIDE_REQUIRED + ["factor_gkm", "kg_co2", "kg_co2_phys", "delta_pct"]

# G2 · 七个数（+ 总里程），全部来自 docs/17 §1.5 对拍表
TARGETS: List[Dict[str, Any]] = [
    {"id": "segments", "label": "独立路段数", "expected": 33214.0, "tol": 0.0,
     "stage": "preprocess", "data_dependent": True, "unit": "段"},
    {"id": "total_km", "label": "总里程", "expected": 4690435.81, "tol": 0.01,
     "stage": "preprocess", "data_dependent": True, "unit": "km"},
    {"id": "valid_speed_rows", "label": "有效速度样本", "expected": 33213.0, "tol": 0.0,
     "stage": "preprocess", "data_dependent": True, "unit": "条"},
    {"id": "avg_speed", "label": "门到门均速", "expected": 49.57, "tol": 0.01,
     "stage": "preprocess", "data_dependent": True, "unit": "km/h"},
    {"id": "e0_kg", "label": "基准碳排 E₀", "expected": 4502042.41, "tol": 0.01,
     "stage": "optimizer", "data_dependent": True, "unit": "kg"},
    {"id": "er30_vsp2", "label": "30 t / VSP=2 排放率", "expected": 14.67, "tol": 0.01,
     "stage": "optimizer", "data_dependent": False, "unit": "g/s"},
    {"id": "vsp3_dev_pct", "label": "VSP=3 平行校验偏差", "expected": 12.68, "tol": 0.01,
     "stage": "optimizer", "data_dependent": False, "unit": "%"},
    {"id": "main_reduction_pct", "label": "主方案减排率", "expected": 77.40, "tol": 0.1,
     "stage": "evaluator", "data_dependent": True, "unit": "%"},
]


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")


def _save_parquet(df: pd.DataFrame, path: Path, tries: int = 4) -> None:
    last: Optional[Exception] = None
    for i in range(tries):
        try:
            df.to_parquet(path, index=False)
            return
        except OSError as exc:
            last = exc
            time.sleep(0.25 * (i + 1))
    raise last if last else RuntimeError("parquet 写入失败：" + str(path))


def _read_parquet(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(path)
    except Exception:
        return pd.DataFrame()


def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        f = float(value)
        if f != f:
            return default
        return f
    except (TypeError, ValueError):
        return default


def _pad(text: str, width: int) -> str:
    """按显示宽度补空格：中文算 2 格，CLI 表格才不会歪。"""
    shown = sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)
    return text + " " * max(0, width - shown)


def _fmt(value: Any, unit: str = "") -> str:
    if value is None:
        return "—"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if unit in ("段", "条"):
        return format(f, ",.0f")
    if unit == "kg" or abs(f) >= 1000:
        return format(f, ",.2f")
    return format(f, ".2f")


def _status_icon(status: str) -> str:
    return {"PASS": "✓", "FAIL": "✗", "SKIP": "⏭"}.get(status, "?")


# ---------------------------------------------------------------------------
# G1 · 调度器
# ---------------------------------------------------------------------------

STAGES: List[Dict[str, Any]] = [
    {"key": "preprocess", "name": "① 数据层 preprocess.py", "fn": preprocess.build_wide_table},
    {"key": "optimizer", "name": "② 引擎层 optimizer.py", "fn": optimizer.build_baseline},
    {"key": "evaluator", "name": "③ 分析层 evaluator.py", "fn": evaluator.build_analysis},
]


def _run_stage(index: int, stage: Dict[str, Any], simulate: bool = False) -> Dict[str, Any]:
    print("\n[%d/%d] %s" % (index, len(STAGES) + 1, stage["name"]))
    t0 = time.perf_counter()
    try:
        if simulate:
            raise RuntimeError("验收自测：人为触发第 %d 段故障（--simulate-fail）" % index)
        stage["fn"]()
        seconds = time.perf_counter() - t0
        print("      ✓ %.2f s" % seconds)
        return {"key": stage["key"], "name": stage["name"], "status": "ok",
                "seconds": round(seconds, 3), "error": ""}
    except Exception as exc:
        seconds = time.perf_counter() - t0
        print("      ✗ 第 %d 段失败：%s" % (index, stage["name"]))
        print("        %s: %s" % (type(exc).__name__, exc))
        traceback.print_exc()
        return {"key": stage["key"], "name": stage["name"], "status": "fail",
                "seconds": round(seconds, 3),
                "error": "%s: %s" % (type(exc).__name__, exc)}


def _fail(run_log: Dict[str, Any], stage_name: str, message: str) -> int:
    run_log["exit_code"] = 1
    run_log["total_seconds"] = round(time.perf_counter() - run_log.pop("_t0"), 3)
    _write_json(RUN_LOG_PATH, run_log)
    _write_json(GATE_PATH, {
        "generated_at": _now(),
        "mode": "error",
        "source": run_log.get("source", "unknown"),
        "passed": False,
        "targets": [],
        "structural": [{
            "id": "pipeline", "label": "流水线", "status": "FAIL",
            "detail": stage_name + "：" + message,
        }],
        "counts": {"pass": 0, "fail": 1, "skip": 0},
    })
    print("\n流水线中断：%s" % stage_name)
    print("详见 %s" % RUN_LOG_PATH)
    return 1


# ---------------------------------------------------------------------------
# G3 · 台账
# ---------------------------------------------------------------------------

def build_ledger(schemes: pd.DataFrame, total_kg: float, source: str) -> pd.DataFrame:
    if schemes is None or schemes.empty:
        raise ValueError("方案表为空，无法生成台账")

    s = schemes.copy().sort_values("order").reset_index(drop=True)
    baseline = s[s["reduction_pct"] <= 0]
    base = baseline.iloc[0] if not baseline.empty else s.iloc[0]
    main = s[s["is_main"]]
    main = main.iloc[0] if not main.empty else s.loc[s["reduction_pct"].idxmax()]
    s7 = s[s["scenario"].astype(str).str.contains("S7", na=False)]
    if not s7.empty:
        conservative = s7.iloc[0]
    else:
        rest = s[(s["scenario"] != base["scenario"]) & (s["scenario"] != main["scenario"])]
        conservative = rest.loc[rest["reduction_pct"].idxmax()] if not rest.empty else main

    picks: List[Tuple[pd.Series, bool]] = [(base, True)]
    for row in (main, conservative):
        if all(str(row["scenario"]) != str(existing["scenario"]) for existing, _ in picks):
            picks.append((row, False))
    while len(picks) < 3:
        for _, row in s.iterrows():
            if all(str(row["scenario"]) != str(existing["scenario"]) for existing, _ in picks):
                picks.append((row, False))
                break
        else:
            break

    rows = []
    for row, is_base in picks[:3]:
        emission = float(total_kg) / 1000.0 if is_base else float(row["emission_t"])
        note = "%s；%s；%s" % (row["scenario"], row["basis"], "台账与第 1 页同源")
        rows.append({
            "scheme": str(row["scenario"]),
            "accounting_date": ACCOUNTING_DATE,
            "boundary": BOUNDARY,
            "emission_t": round(emission, 6),
            "quota_gap": "—" if is_base else "未设定（无配额基准）",
            "book_cost": round(float(row["capex_wan"]), 2),
            "note": note,
            "is_baseline": bool(is_base),
            "data_source": source,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# G4 · 披露
# ---------------------------------------------------------------------------

def build_disclosure(kpi: Dict[str, Any], quality: Dict[str, Any], source: str) -> Dict[str, Any]:
    estimated = int(quality.get("estimated_rows", 0) or 0)
    anomaly = int(quality.get("anomaly_rows", 0) or 0)
    basis = int(quality.get("basis_rows", 0) or 0)
    return {
        "boundary": BOUNDARY,
        "factor_source": "%s；因子表版本 %s" % (optimizer.FACTOR_SOURCE, optimizer.FACTOR_VERSION),
        "assumptions": (
            "额定载重按载质量口径；整车总质量/准牵引总质量已换算成载质量并打 flag_basis；"
            "缺失里程按同线历史均值、缺失车型按载重推断、未匹配发票按同类别历史均价，全部打 flag_estimated；"
            "速度 >100 km/h 的时间戳异常段保留计碳，只标记不删除；"
            "当前因子表与 75 车型总质量映射为 provisional，E₀ 与同线 1.50× 待文献表 8 定稿。"
        ),
        "period": DATA_PERIOD + "；年化系数 365/120 = %.4f" % ANNUAL_FACTOR,
        "annual_factor": ANNUAL_FACTOR,
        "limitations": (
            "含 %d 条时间戳异常段（保留计碳）；%d 条口径换算；%d 条估算值（前端黄框）；"
            "未含制冷、空驶调车、仓储与上游燃料生产；方案成本为答辩稿静态标定；"
            "因子表/总质量映射为 provisional，真数据接入后需按实际车队与文献表 8 重算。"
            % (anomaly, basis, estimated)
        ),
        "data_source": source,
        "e0_t": round(float(kpi.get("e0_t") or 0.0), 3),
        "factor_version": optimizer.FACTOR_VERSION,
        "generated_at": _now(),
    }


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------

def build_kpi(seg: pd.DataFrame, wide: pd.DataFrame, schemes: pd.DataFrame,
              band: pd.DataFrame, quality: Dict[str, Any], source: str) -> Dict[str, Any]:
    total_kg = float(pd.to_numeric(seg["kg_co2"], errors="coerce").fillna(0).sum())
    total_km = float(pd.to_numeric(seg["dist_km"], errors="coerce").fillna(0).sum())
    speed = pd.to_numeric(seg["speed_kmh"], errors="coerce") if "speed_kmh" in seg.columns else pd.Series(dtype=float)
    valid = speed.notna() & (speed > 0)
    clean = valid & speed.between(1, 100)
    vclass = pd.to_numeric(seg["vclass"], errors="coerce") if "vclass" in seg.columns else pd.Series(dtype=float)
    heavy = float(pd.to_numeric(seg.loc[vclass == 3, "kg_co2"], errors="coerce").sum()) if len(vclass) else 0.0
    band_ratio = _num(band["ratio"].median()) if not band.empty and "ratio" in band.columns else None
    band_kg = float(pd.to_numeric(band["kg_t"], errors="coerce").sum()) * 1000.0 if not band.empty else 0.0
    main = schemes[schemes["is_main"]] if schemes is not None and not schemes.empty else pd.DataFrame()

    return {
        "source": source,
        "generated_at": _now(),
        "total_kg": total_kg,
        "e0_t": total_kg / 1000.0,
        "total_km": total_km,
        "segments": int(len(seg)),
        "rows_wide": int(len(wide)),
        "valid_speed_rows": int(valid.sum()),
        "avg_speed": None if not valid.any() else float(speed[valid].mean()),
        "avg_speed_clean": None if not clean.any() else float(speed[clean].mean()),
        "heavy_share": (heavy / total_kg) if total_kg > 0 else None,
        "dup_rows": int(quality.get("dup_rows", 0) or 0),
        "anomaly_rows": int(quality.get("anomaly_rows", 0) or 0),
        "basis_rows": int(quality.get("basis_rows", 0) or 0),
        "estimated_rows": int(quality.get("estimated_rows", 0) or 0),
        "dist_filled": int(quality.get("fill_dist", 0) or 0),
        "vehicle_filled": int(quality.get("fill_vehicle", 0) or 0),
        "invoice_missing": int(quality.get("fill_invoice", 0) or 0),
        "od_ratio_median": band_ratio,
        "od_pairs": int(len(band)),
        "od_coverage_pct": (band_kg / total_kg * 100.0) if total_kg > 0 else None,
        "main_reduction_pct": _num(main.iloc[0]["reduction_pct"]) if not main.empty else None,
        "factor_version": optimizer.FACTOR_VERSION,
    }


# ---------------------------------------------------------------------------
# G2 · 闸门
# ---------------------------------------------------------------------------

def _target_items(kpi: Dict[str, Any], schemes: pd.DataFrame, mode: str,
                  strict: bool) -> List[Dict[str, Any]]:
    factor = optimizer.self_test()
    actual: Dict[str, Optional[float]] = {
        "segments": _num(kpi.get("segments")),
        "total_km": _num(kpi.get("total_km")),
        "valid_speed_rows": _num(kpi.get("valid_speed_rows")),
        "avg_speed": _num(kpi.get("avg_speed")),
        "e0_kg": (_num(kpi.get("total_kg")) or 0.0),
        "er30_vsp2": factor["er30_vsp2"],
        "vsp3_dev_pct": factor["vsp3_dev_pct"],
        "main_reduction_pct": _num(kpi.get("main_reduction_pct")),
    }

    items: List[Dict[str, Any]] = []
    for spec in TARGETS:
        got = actual.get(spec["id"])
        expected = float(spec["expected"])
        tol = float(spec["tol"])
        skip = bool(spec["data_dependent"]) and mode == "demo" and not strict
        if skip:
            status = "SKIP"
            note = "演示数据不参与对拍；真数据落盘后自动启用"
        elif got is None:
            status = "FAIL"
            note = "结果缺失"
        else:
            status = "PASS" if abs(float(got) - expected) <= tol else "FAIL"
            note = "" if status == "PASS" else "超出容差 ±%s" % tol
            if status == "FAIL":
                diff = float(got) - expected
                if spec["id"] == "avg_speed":
                    note = ("差 %+.3f km/h；数据版本/去重口径差异，待确认" % diff)
                elif spec["id"] == "e0_kg":
                    note = ("差 %+.0f kg（%+.3f%%）；GVW 映射 provisional，"
                            "待杨按文献表 8 定稿" % (diff, diff / expected * 100.0))
        if spec["id"] == "main_reduction_pct":
            note = "方案表为静态标定（答辩稿表 19–21），闸门只校验它没被改动"
            if skip:
                note += "；演示数据不参与对拍"
        elif not spec["data_dependent"]:
            note = "因子表自测，与数据无关"
        items.append({
            "id": spec["id"], "label": spec["label"], "stage": spec["stage"],
            "expected": expected, "actual": got, "tol": tol, "unit": spec["unit"],
            "status": status, "note": note,
        })
    return items


def _structural_items(wide: pd.DataFrame, seg: pd.DataFrame, schemes: pd.DataFrame,
                      hotspots: pd.DataFrame, band: pd.DataFrame, ledger: pd.DataFrame,
                      disclosure: Dict[str, Any], kpi: Dict[str, Any],
                      quality: Dict[str, Any], source: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    def check(cid: str, label: str, fn: Callable[[], Tuple[bool, str]]) -> None:
        try:
            ok, detail = fn()
            status = "PASS" if ok else "FAIL"
        except Exception as exc:
            status, detail = "FAIL", "%s: %s" % (type(exc).__name__, exc)
        items.append({"id": cid, "label": label, "status": status, "detail": detail})

    def need_cols(df: pd.DataFrame, cols: List[str]) -> Tuple[bool, str]:
        miss = [c for c in cols if c not in df.columns]
        return (not miss), ("缺列：" + "、".join(miss) if miss else "契约齐全")

    check("wide_rows", "宽表非空", lambda: (len(wide) > 0, "宽表 %d 行" % len(wide)))
    check("wide_cols", "宽表字段契约", lambda: need_cols(wide, WIDE_REQUIRED))
    check("seg_rows", "段表非空", lambda: (len(seg) > 0, "段表 %d 行" % len(seg)))
    check("seg_cols", "段表字段契约", lambda: need_cols(seg, SEG_REQUIRED))

    def _dedup() -> Tuple[bool, str]:
        dup = int(quality.get("dup_rows", -1))
        dedup = int(quality.get("rows_dedup", -1))
        return (len(seg) == dedup and len(wide) - len(seg) == dup,
                "宽表 %d − 段表 %d = 重复 %d；质量报告 %d / %d"
                % (len(wide), len(seg), len(wide) - len(seg), dup, dedup))

    check("dedup", "去重口径一致", _dedup)

    def _finite() -> Tuple[bool, str]:
        cols = ["kg_co2", "dist_km", "factor_gkm"]
        miss = [c for c in cols if c not in seg.columns]
        if miss:
            return False, "缺列：" + "、".join(miss)
        bad = int(seg[cols].isna().any(axis=1).sum())
        neg = int((pd.to_numeric(seg["kg_co2"], errors="coerce").fillna(0) < 0).sum())
        zero = int((pd.to_numeric(seg["dist_km"], errors="coerce").fillna(0) <= 0).sum())
        return (bad == 0 and neg == 0 and zero == 0,
                "NaN %d｜负碳 %d｜零里程 %d" % (bad, neg, zero))

    check("finite", "碳排/里程无空值", _finite)

    def _kpi_total() -> Tuple[bool, str]:
        total = float(pd.to_numeric(seg["kg_co2"], errors="coerce").fillna(0).sum())
        return (abs(total - float(kpi.get("total_kg") or 0)) < 1e-6,
                "段表 %.2f kg vs kpi %.2f kg" % (total, float(kpi.get("total_kg") or 0)))

    check("kpi_total", "E₀ 与 kpi 同源", _kpi_total)

    check("ledger_fields", "台账六字段",
          lambda: (all(c in ledger.columns for c in SIX_LEDGER) and len(ledger) >= 3,
                   "字段 %d 个｜%d 行" % (len(ledger.columns), len(ledger))))

    def _ledger_total() -> Tuple[bool, str]:
        base = ledger[ledger.get("is_baseline", False) == True] if "is_baseline" in ledger.columns else ledger.head(1)
        if base.empty:
            return False, "没有基准行"
        emission = float(pd.to_numeric(base.iloc[0]["emission_t"], errors="coerce")) * 1000.0
        total = float(kpi.get("total_kg") or 0)
        return (abs(emission - total) <= 1.0,
                "台账基准 %.2f kg vs 第 1 页 %.2f kg" % (emission, total))

    check("ledger_total", "台账总碳 = 首页总碳", _ledger_total)
    check("disclosure_six", "披露六要素",
          lambda: (all(k in disclosure and str(disclosure[k]).strip() != "" for k in SIX_DISCLOSURE),
                   "命中 %d / 6" % sum(1 for k in SIX_DISCLOSURE
                                       if k in disclosure and str(disclosure[k]).strip() != "")))

    def _schemes() -> Tuple[bool, str]:
        if schemes is None or len(schemes) < 3:
            return False, "方案数不足 3"
        for col in ("reduction_pct", "emission_t", "capex_wan", "payback_years"):
            if col not in schemes.columns:
                return False, "缺列：" + col
        red = pd.to_numeric(schemes["reduction_pct"], errors="coerce")
        em = pd.to_numeric(schemes["emission_t"], errors="coerce")
        pay = pd.to_numeric(schemes["payback_years"], errors="coerce")
        ok = (red.between(0, 100).all() and em.notna().all() and (em >= 0).all()
              and pay.notna().all() and (pay >= 0).all())
        return ok, "减排 %.1f–%.1f%%｜回本 %.1f–%.1f 年" % (red.min(), red.max(), pay.min(), pay.max())

    check("schemes", "方案表可对账", _schemes)

    def _hotspots() -> Tuple[bool, str]:
        if hotspots.empty or "dimension" not in hotspots.columns:
            return False, "热点表为空"
        dims = set(hotspots["dimension"].astype(str))
        need = {"车型", "线路", "月份", "里程段"}
        return (need <= dims), "维度：" + "、".join(sorted(dims))

    check("hotspots", "四维热点齐全", _hotspots)
    check("od_band", "同线带宽非空",
          lambda: (not band.empty and "ratio" in band.columns,
                   "可比较 OD %d 条" % len(band)))
    check("source", "数据来源标记",
          lambda: (source in ("real", "demo"), "source = " + str(source)))

    return items


def run_gate(strict: bool = False) -> Dict[str, Any]:
    source = str(_read_json(SOURCE_PATH).get("source", "unknown"))
    mode = "strict" if strict else ("real" if source == "real" else "demo")
    wide = _read_parquet(WIDE_PATH)
    seg = _read_parquet(SEG_PATH)
    schemes = _read_parquet(SCHEMES_PATH)
    hotspots = _read_parquet(HOTSPOTS_PATH)
    band = _read_parquet(BAND_PATH)
    ledger = _read_parquet(LEDGER_PATH)
    disclosure = _read_json(DISCLOSURE_PATH)
    kpi = _read_json(KPI_PATH)
    quality = _read_json(QUALITY_PATH)

    targets = _target_items(kpi, schemes, "demo" if mode == "strict" and source != "real" else mode, strict)
    structural = _structural_items(wide, seg, schemes, hotspots, band, ledger,
                                   disclosure, kpi, quality, source)
    counts = {
        "pass": sum(1 for x in targets + structural if x["status"] == "PASS"),
        "fail": sum(1 for x in targets + structural if x["status"] == "FAIL"),
        "skip": sum(1 for x in targets + structural if x["status"] == "SKIP"),
    }
    report = {
        "generated_at": _now(),
        "source": source,
        "mode": mode,
        "passed": counts["fail"] == 0,
        "targets": targets,
        "structural": structural,
        "counts": counts,
    }
    _write_json(GATE_PATH, report)
    return report


def _print_gate(report: Dict[str, Any]) -> None:
    mode_label = {"demo": "演示数据（真数据未接入，数据项跳过）",
                  "real": "实测数据（八项全开）",
                  "strict": "严格模式（演示数据也强制对拍）",
                  "error": "流水线中断"}.get(report.get("mode"), str(report.get("mode")))
    print("\n" + "=" * 78)
    print("G2 · 闸门｜模式：%s" % mode_label)
    print("-" * 78)
    for item in report.get("targets", []):
        unit = item.get("unit", "")
        print("  %s %-4s %s 目标 %-16s 实测 %s"
              % (_status_icon(item["status"]), item["status"], _pad(item["label"], 24),
                 _fmt(item["expected"], unit) + " " + unit,
                 _fmt(item["actual"], unit) + " " + unit))
    print("-" * 78)
    bad = [x for x in report.get("structural", []) if x["status"] != "PASS"]
    total = len(report.get("structural", []))
    print("  结构项：%d / %d 通过" % (total - len(bad), total))
    for item in bad:
        print("    ✗ %s：%s" % (item["label"], item.get("detail", "")))
    counts = report.get("counts", {})
    print("  汇总：PASS %s｜FAIL %s｜SKIP %s"
          % (counts.get("pass", 0), counts.get("fail", 0), counts.get("skip", 0)))
    print("  结论：%s" % ("✓ 通过" if report.get("passed") else "✗ 未通过（退出码 1）"))
    print("=" * 78)


# ---------------------------------------------------------------------------
# G5 · 一键入口
# ---------------------------------------------------------------------------

def run_pipeline(strict: bool = False, simulate_fail: Optional[int] = None) -> int:
    run_log: Dict[str, Any] = {
        "generated_at": _now(),
        "source": "unknown",
        "mode": "strict" if strict else "auto",
        "stages": [],
        "exit_code": None,
        "gate_passed": None,
        "_t0": time.perf_counter(),
    }
    print("=" * 78)
    print("物流企业碳感知智能决策系统 · 一键复算")
    print("时间：%s" % run_log["generated_at"])
    print("=" * 78)

    start_index = simulate_fail or -1
    for i, stage in enumerate(STAGES, start=1):
        rec = _run_stage(i, stage, simulate=(start_index == i))
        run_log["stages"].append(rec)
        if rec["status"] != "ok":
            run_log["source"] = str(_read_json(SOURCE_PATH).get("source", "unknown"))
            return _fail(run_log, "第 %d 段 %s" % (i, stage["name"]), rec["error"])

    # 报表段（G3/G4）也算一段：失败要能报出“报表段”
    print("\n[4/4] ④ 报表段（G3 台账 + G4 披露 + kpi）")
    t0 = time.perf_counter()
    try:
        source = str(_read_json(SOURCE_PATH).get("source", "unknown"))
        quality = _read_json(QUALITY_PATH)
        wide = _read_parquet(WIDE_PATH)
        seg = _read_parquet(SEG_PATH)
        schemes = _read_parquet(SCHEMES_PATH)
        band = _read_parquet(BAND_PATH)
        if seg.empty:
            raise ValueError("baseline_carbon.parquet 为空")
        if schemes.empty:
            raise ValueError("final_report.parquet 为空")

        total_kg = float(pd.to_numeric(seg["kg_co2"], errors="coerce").fillna(0).sum())
        kpi = build_kpi(seg, wide, schemes, band, quality, source)
        ledger = build_ledger(schemes, total_kg, source)
        disclosure = build_disclosure(kpi, quality, source)
        _write_json(KPI_PATH, kpi)
        _save_parquet(ledger, LEDGER_PATH)
        _write_json(DISCLOSURE_PATH, disclosure)
        seconds = time.perf_counter() - t0
        print("      ✓ %.2f s｜台账 %d 行｜披露 %d 要素｜kpi %d 项"
              % (seconds, len(ledger), len(SIX_DISCLOSURE), len(kpi)))
        run_log["stages"].append({
            "key": "reports", "name": "④ 报表段（G3 台账 + G4 披露）",
            "status": "ok", "seconds": round(seconds, 3), "error": "",
        })
    except Exception as exc:
        seconds = time.perf_counter() - t0
        print("      ✗ 第 4 段失败：④ 报表段")
        traceback.print_exc()
        run_log["stages"].append({
            "key": "reports", "name": "④ 报表段（G3 台账 + G4 披露）",
            "status": "fail", "seconds": round(seconds, 3),
            "error": "%s: %s" % (type(exc).__name__, exc),
        })
        run_log["source"] = str(_read_json(SOURCE_PATH).get("source", "unknown"))
        return _fail(run_log, "第 4 段 ④ 报表段", "%s: %s" % (type(exc).__name__, exc))

    # 演示标记：前端据此显示“演示数据”
    if source == "real":
        try:
            MOCK_FLAG.unlink(missing_ok=True)
        except Exception:
            pass
    else:
        MOCK_FLAG.write_text("demo", encoding="utf-8")

    try:
        report = run_gate(strict=strict)
    except Exception as exc:
        traceback.print_exc()
        run_log["source"] = source
        return _fail(run_log, "第 4 段 ④ 报表段（闸门）",
                     "%s: %s" % (type(exc).__name__, exc))
    _print_gate(report)

    run_log.update({
        "source": source,
        "exit_code": 0 if report.get("passed") else 1,
        "gate_passed": bool(report.get("passed")),
        "gate_counts": report.get("counts", {}),
        "total_seconds": round(time.perf_counter() - run_log.pop("_t0"), 3),
    })
    _write_json(RUN_LOG_PATH, run_log)
    print("\n本次耗时 %.2f s｜结果目录 %s" % (run_log["total_seconds"], PROCESSED))
    if report.get("passed"):
        print("流水线全部跑通 ✓")
        return 0
    print("流水线跑完，但闸门未通过 ✗（退出码 1）")
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="一键复算：三段调度 + 闸门 + 台账/披露")
    parser.add_argument("--strict", action="store_true",
                        help="严格模式：演示数据也强制对拍七个数（用于验证闸门会红）")
    parser.add_argument("--simulate-fail", type=int, choices=[1, 2, 3], default=None,
                        help="验收自测：模拟第 N 段失败（G1 定位段名）")
    args = parser.parse_args(argv)
    return run_pipeline(strict=args.strict, simulate_fail=args.simulate_fail)


if __name__ == "__main__":
    raise SystemExit(main())
