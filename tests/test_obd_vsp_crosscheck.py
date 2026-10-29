# -*- coding: utf-8 -*-
"""VSP 模型 × OBD 碳平衡实测 对照验证轨的测试（docs/57）。

覆盖四条线：
  1. **口径** —— 实测轨取守恒法既有结果，模型轨用 factor_model 的 VSP，不自造因子；
  2. **载重** —— 有 load_kg 用实测载重，没有就扫描锚点档并标注 scan，不做假设；
  3. **判据** —— 偏差分档与「对得上 / 对不上」的结论；
  4. **演示数据一致性** —— 里程必须与报表速度同口径（回归防护）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]

from src import factor_model, obd_gps, obd_vsp_crosscheck as xcheck  # noqa: E402

PANEL_PATH = ROOT / "app" / "components" / "obd_panel.py"
SERVICES_PATH = ROOT / "app" / "services.py"


# ---------------------------------------------------------------------------
# 1. 口径
# ---------------------------------------------------------------------------

def test_crosscheck_uses_the_authoritative_factor_table():
    """模型因子必须来自 factor_model（唯一真源），不自造。"""
    assert xcheck.factor_model is factor_model
    assert factor_model.FACTOR_VERSION == "JCB2023-BASE-v1"
    # 因子来源是可核查的文献，不是自制 PPT
    assert "VSP" in factor_model.FACTOR_SOURCE


def test_measured_track_is_the_existing_conservation_result():
    """实测轨必须是守恒法既有结果，不能另算一套。"""
    points = pd.read_parquet(xcheck.POINTS_PATH)
    trips, _points = xcheck.build_crosscheck(points)
    best = trips.iloc[trips["delta_pct_profile"].abs().argmin()]
    measured_total = float(
        pd.to_numeric(points.get("kg_co2"), errors="coerce").fillna(0.0).sum())
    subset = points[points["vehicle_id"].astype(str) == str(best["vehicle_id"])]
    assert abs(float(best["kg_measured"])
               - float(pd.to_numeric(subset["kg_co2"], errors="coerce").sum())) < 1e-3
    assert measured_total > 0


def test_profile_and_average_speed_are_different_estimates():
    """速度剖面积分与平均速度法必须给出不同结果（否则剖面积分没生效）。"""
    points = pd.read_parquet(xcheck.POINTS_PATH)
    trips, _ = xcheck.build_crosscheck(points)
    assert (trips["kg_vsp_profile"] != trips["kg_vsp_avg"]).any(), \
        "速度剖面积分与平均速度法结果完全相同，说明积分口径没生效"


def test_profile_integration_matches_hand_calculation():
    """剖面积分公式可手工复算：kg = Σ (factor/72)·mult(vsp(v))·dt / 1000。"""
    points = pd.read_parquet(xcheck.POINTS_PATH)
    sub = points[points["vehicle_id"].astype(str) == "TRK-GZ-001"].copy()
    factor = 1055.952
    dt = pd.to_numeric(sub["dt_s"], errors="coerce").fillna(0.0).to_numpy()
    speed = pd.to_numeric(sub["vehicle_speed_kmh"], errors="coerce").fillna(0.0)
    v = speed.where(speed > 1.0, 1.0).clip(1.0, 130.0).to_numpy()
    mult = np.interp(factor_model.vsp_from_speed(v),
                      factor_model.VSP_X, factor_model.VSP_MULT)
    expected = float((factor / 72.0 * mult * dt).sum() / 1000.0)
    got = xcheck.vsp_profile_kg(sub, factor)["kg"]
    assert abs(got - expected) < 1e-6


def test_average_speed_matches_engineering_pipeline_formula():
    """平均速度法必须与工程侧现行口径（factor_model.attach_carbon_columns）同式。"""
    points = pd.read_parquet(xcheck.POINTS_PATH)
    sub = points[points["vehicle_id"].astype(str) == "TRK-GZ-001"].copy()
    factor = 1055.952
    got = xcheck.vsp_average_kg(sub, factor)
    dt = pd.to_numeric(sub["dt_s"], errors="coerce").fillna(0.0)
    dist = pd.to_numeric(sub["dist_step_km"], errors="coerce").fillna(0.0)
    v_bar = float(dist.sum()) / (float(dt[dt > 0].sum()) / 3600.0)
    vsp = float(factor_model.vsp_from_speed(v_bar))
    mult = float(np.interp(vsp, factor_model.VSP_X, factor_model.VSP_MULT))
    expected = factor / 72.0 * mult * (float(dist.sum()) / v_bar) * 3600.0 / 1000.0
    assert abs(got["kg"] - expected) < 1e-6


# ---------------------------------------------------------------------------
# 2. 载重
# ---------------------------------------------------------------------------

def test_missing_load_falls_back_to_scan_and_is_labelled():
    """没有载重输入时不许悄悄假设：必须扫描锚点档并标 scan。"""
    points = pd.read_parquet(xcheck.POINTS_PATH)
    trips, _ = xcheck.build_crosscheck(points)
    assert (trips["load_basis"] == "scan").all()
    # 扫描档数 = 模型因子表的载重锚点数
    assert len(trips) == len(factor_model.LOAD_ANCHORS) * points[
        "vehicle_id"].nunique()


def test_measured_load_switches_basis_to_measured():
    """有点位载重时按实测载重取因子，basis 变 measured。"""
    points = pd.read_parquet(xcheck.POINTS_PATH).copy()
    points["load_kg"] = 30000.0
    trips, _ = xcheck.build_crosscheck(points)
    assert (trips["load_basis"] == "measured").all()
    assert trips["load_t"].iloc[0] == pytest.approx(30.0)


def test_scan_result_is_not_claimed_as_a_verdict():
    """扫描出来的最佳档不得被表述成「可用于兜底」的最终结论。"""
    summary = xcheck.build_summary(
        xcheck.build_crosscheck(pd.read_parquet(xcheck.POINTS_PATH))[0], True)
    assert summary["load_basis"] == "scan"
    assert "不构成" in summary["load_basis_note"]


# ---------------------------------------------------------------------------
# 3. 判据
# ---------------------------------------------------------------------------

def test_verdict_thresholds():
    summary = xcheck.build_summary(
        xcheck.build_crosscheck(pd.read_parquet(xcheck.POINTS_PATH))[0], True)
    delta = abs(float(summary["totals"]["delta_pct_profile"]))
    ok = float(summary["tolerance_pct"]["ok"])
    warn = float(summary["tolerance_pct"]["warn"])
    expected = ("可用于兜底" if delta <= ok
                else "需修正" if delta <= warn else "不可用")
    assert summary["verdict"] == expected
    assert summary["criterion"].startswith("「你测的和这些估的能不能对得上")


def test_summary_is_self_consistent():
    trips = xcheck.build_crosscheck(pd.read_parquet(xcheck.POINTS_PATH))[0]
    summary = xcheck.build_summary(trips, True)
    totals = summary["totals"]
    assert totals["kg_measured"] > 0
    expected = (totals["kg_vsp_profile"] - totals["kg_measured"]) \
        / totals["kg_measured"] * 100.0
    assert abs(totals["delta_pct_profile"] - expected) < 1e-3


def test_crosscheck_does_not_touch_other_result_tables(tmp_path):
    """对拍是独立轨道，不得改动 OBD 主结果表。"""
    import hashlib

    guarded = ["obd_points.parquet", "obd_vehicle_carbon.parquet",
               "obd_trips.parquet", "obd_summary.json"]
    before = {}
    for name in guarded:
        path = xcheck.PROCESSED / name
        if path.exists():
            before[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    xcheck.run(out_dir=tmp_path, write=True)
    for name in guarded:
        path = xcheck.PROCESSED / name
        if name in before:
            after = hashlib.sha256(path.read_bytes()).hexdigest()
            assert after == before[name], "对拍改动了 OBD 主结果表：%s" % name


# ---------------------------------------------------------------------------
# 4. 演示数据一致性（回归防护）
# ---------------------------------------------------------------------------

def test_demo_distance_matches_reported_speed():
    """演示数据的里程必须与**报表速度**同口径。

    回归防护：曾经 `demo_stream` 用未缩放的原始速度累计里程，
    而报表速度/油耗都乘了 scale —— 轻卡里程虚高 61%、碳强度被低估 38%。
    """
    frame = obd_gps.demo_stream(anchor=pd.Timestamp("2026-10-29T08:00:00+08:00"))
    normalized, _ = obd_gps.normalize_records(frame.to_dict(orient="records"))
    carbon = obd_gps.attach_carbon(normalized)
    for _vid, sub in carbon.groupby("vehicle_id", sort=True):
        dt = pd.to_numeric(sub["dt_s"], errors="coerce").fillna(0.0)
        speed = pd.to_numeric(sub["vehicle_speed_kmh"], errors="coerce").fillna(0.0)
        dist = pd.to_numeric(sub["dist_step_km"], errors="coerce").fillna(0.0)
        dist_by_speed = float((speed * dt / 3600.0).sum())
        assert dist_by_speed > 0
        assert abs(float(dist.sum()) - dist_by_speed) / dist_by_speed < 0.02, \
            "演示里程与报表速度不同口径（scale 未同步）"


def test_demo_vehicles_carbon_intensity_agree():
    """同工况不同车速的两台车，碳强度应落在同一量级（演示自洽）。"""
    frame = obd_gps.demo_stream(anchor=pd.Timestamp("2026-10-29T08:00:00+08:00"))
    normalized, _ = obd_gps.normalize_records(frame.to_dict(orient="records"))
    carbon = obd_gps.attach_carbon(normalized)
    summary = obd_gps.summarize_vehicles(carbon)
    values = [float(v) for v in summary["co2_gkm"]]
    assert len(values) == 2
    assert abs(values[0] - values[1]) / max(values) < 0.10, \
        "两台演示车的碳强度量级不一致：%s" % values


# ---------------------------------------------------------------------------
# 5. 前端守卫
# ---------------------------------------------------------------------------

def test_panel_does_not_import_the_crosscheck_module():
    """前端只读结果表：只允许经 services 取数，不许 import 算法模块。"""
    tree = ast.parse(PANEL_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("src")
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("src")
    # 所有对拍取数都必须是 services.xxx 的只读函数
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute)
                and node.attr.startswith("obd_vsp_crosscheck")):
            assert isinstance(node.value, ast.Name) and node.value.id == "services", \
                "对拍取数必须走 services 只读层：%s" % node.attr


def test_services_reads_only_from_processed():
    """只读层不碰算法模块，也不自己算。"""
    tree = ast.parse(SERVICES_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "obd_vsp_crosscheck" not in alias.name
        if isinstance(node, ast.ImportFrom):
            assert "obd_vsp_crosscheck" not in (node.module or "")
    source = SERVICES_PATH.read_text(encoding="utf-8")
    for name in ("obd_vsp_crosscheck", "obd_vsp_crosscheck_points",
                 "obd_vsp_crosscheck_summary"):
        assert name in source


def _code_only(path: Path) -> str:
    import io
    import tokenize

    source = path.read_text(encoding="utf-8")
    tokens = [t for t in tokenize.generate_tokens(io.StringIO(source).readline)
              if t.type != tokenize.COMMENT]
    tree = ast.parse(tokenize.untokenize(tokens))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body[0].value.value = ""
    return ast.unparse(tree)
