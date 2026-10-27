# -*- coding: utf-8 -*-
"""数据层一次准备、脏数据留痕与只读前端边界。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def test_dirty_rows_are_imputed_flagged_and_carbonized():
    from src.preprocess import WIDE_COLUMNS, clean_frame

    raw = pd.DataFrame({
        "waybill_no": ["W1", "W2"],
        "plate": ["沪A12345", ""],
        "vehicle_type": ["9.6米厢式货车", "不存在车型"],
        "load_t": [-5, None],
        "origin": ["上海", ""],
        "dest": ["北京", "杭州"],
        "dist_km": [None, 0],
        "depart_ts": pd.to_datetime(["2026-10-01 10:00", "2026-10-02 12:00"]),
        "arrive_ts": pd.to_datetime(["2026-10-01 09:00", "2026-10-02 11:00"]),
    })
    wide = clean_frame(raw, "test")
    assert list(wide.columns) == WIDE_COLUMNS
    assert (wide["dist_km"] > 0).all()
    assert (wide["load_t"] > 0).all()
    assert wide["flag_estimated"].eq(1).all()
    assert wide["estimate_note"].str.len().gt(0).all()
    for col in ("factor_gkm", "kg_co2", "kg_co2_phys", "factor_version"):
        assert col in wide.columns
    assert (wide["kg_co2"] > 0).all()


def test_real_wide_table_has_carbon_columns():
    path = ROOT / "data" / "processed" / "wide_data.parquet"
    if not path.exists():
        return
    wide = pd.read_parquet(path)
    for col in ("factor_gkm", "kg_co2", "kg_co2_phys",
                "delta_pct", "factor_version", "flag_factor_x"):
        assert col in wide.columns


def test_services_is_read_only_and_live_imports_are_lazy():
    services_src = (APP_DIR / "services.py").read_text(encoding="utf-8")
    live_src = (APP_DIR / "live_services.py").read_text(encoding="utf-8")
    assert "import pymoo" not in services_src
    for name in ("invoice", "sensor", "disclosure", "route_carbon", "evolve_routes"):
        assert "from src import " + name not in services_src
    assert services_src.count("from src import schema") == 1
    # 实时通道的重依赖必须缩进在函数体内，不能在模块导入时加载。
    for line in live_src.splitlines():
        if line.startswith("from src") or line.startswith("import pymoo"):
            raise AssertionError("live_services 顶层不应导入重依赖：" + line)


def test_estimate_audit_renders_yellow_panel(monkeypatch):
    from components import flags

    frame = pd.DataFrame({
        "waybill_no": ["W1"], "vehicle_type": ["9.6米厢式货车"],
        "load_t": [18.0], "dist_km": [300.0], "dist_source": ["industry_default"],
        "speed_method": ["industry_speed_50_assumption"],
        "estimate_note": ["里程缺失→行业均值"], "flag_estimated": [1],
    })
    monkeypatch.setattr(flags.services, "wide", lambda: frame)
    monkeypatch.setattr(flags.services, "flag_counts", lambda: {
        "rows_wide": 2, "rows_dedup": 2, "estimated_rows": 1})
    monkeypatch.setattr(flags.services, "quality", lambda: {
        "estimate_reason_counts": {"里程缺失→行业均值": 1}})
    monkeypatch.setattr(flags.services, "available", lambda: {"quality": True})

    def script():
        from components.flags import render_estimate_audit
        render_estimate_audit()

    at = AppTest.from_function(script, default_timeout=40)
    at.run()
    assert not at.exception
    content = "\n".join(item.value for item in at.markdown)
    assert "估算值留痕与数据质量" in content
    assert "ESTIMATION AUDIT" in content
    assert len(at.dataframe) == 1


def test_estimate_audit_uses_static_fallback(monkeypatch):
    from components import flags

    monkeypatch.setattr(flags.services, "wide", lambda: pd.DataFrame())
    monkeypatch.setattr(flags.services, "flag_counts", lambda: {})
    monkeypatch.setattr(flags.services, "quality", lambda: {})
    monkeypatch.setattr(flags.services, "available", lambda: {"quality": False})

    def script():
        from components.flags import render_estimate_audit
        render_estimate_audit()

    at = AppTest.from_function(script, default_timeout=40)
    at.run()
    assert not at.exception
    content = "\n".join(item.value for item in at.markdown)
    assert "静态演示快照" in content
    assert len(at.dataframe) == 1
