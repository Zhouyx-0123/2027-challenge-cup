# -*- coding: utf-8 -*-
"""车辆·路段油耗模块（src/fleet_efficiency.py）测试。

守五条：
  1. **换算物理正确** —— g CO₂/km ÷ 柴油因子 → L/100km，30 t 重卡应落在真实区间；
  2. **VSP 轨有适用边界** —— 低速段 VSP/法定可达 4.5 倍，必须标注不适用；
  3. **不编造数据** —— 拿不到可核查油价/油量就 needs_review，绝不填数；
  4. **油箱周期分摊正确** —— 相邻两次加油之间是一箱油，按里程比例分摊；
  5. **口径与纪律** —— 因子只从 config/ 读；src/fleet_efficiency 不 import
     preprocess/evaluator/pipeline；输出必带 assumptions。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

from src import fleet_efficiency as fe

ROOT = Path(__file__).resolve().parents[1]

#: 30 t 重卡在 50 km/h 的因子（g CO₂/km），来自 2023 基准因子表
FACTOR_HEAVY = 1055.952
KG_PER_L = 3.16   # TTW 2.63 + WTT 0.53


# ---------------------------------------------------------------------------
# 造数
# ---------------------------------------------------------------------------

def make_segment(plate: str, ts: str, trip_km: float, od_site: str = "O-A|B",
                 speed: float = 50.0, factor: float = FACTOR_HEAVY,
                 vclass: int = 3, load_t: float = 30.0, month: int = 1,
                 vsp_ratio: float = 1.0) -> Dict:
    """造一段：kg_co2 由因子×里程推出，保证与因子表自洽。

    vsp_ratio：VSP 轨相对法定轨的倍数（低速段应 >1，高速段 <1）。
    """
    kg = factor * trip_km / 1000.0
    return {
        "trip_id": "%s@%s@%s" % (plate, ts, od_site),
        "plate": plate, "month": month, "od_site": od_site,
        "trip_km": trip_km, "dist_km": trip_km,
        "speed_kmh": speed, "vclass": vclass, "load_t": load_t,
        "factor_gkm": factor, "er_gs": factor / 72.0,
        "kg_co2": kg, "kg_co2_phys": kg * vsp_ratio,
        "depart_ts": ts, "arrive_ts": ts,
    }


def make_invoice(plate: str, date: str, liters: float, amount: float,
                 unit_price: Optional[float] = 7.28,
                 invoice_no: str = "INV-1") -> Dict:
    rec = {
        "invoice_no": invoice_no, "plate": plate, "invoice_date": date,
        "invoice_amount": amount, "fuel_liters": liters, "unit": "L",
    }
    if unit_price is not None:
        rec["unit_price"] = unit_price
    return rec


# ---------------------------------------------------------------------------
# 0. 物理行程口径（最容易算错的地方）
# ---------------------------------------------------------------------------

def test_physical_trips_dedupe_waybill_rows():
    """一个行程 6 个运单：碳排求和、里程只算一次。

    段表里 kg_co2 是分摊值（kg_co2_gross × allocation_share），
    而 trip_km 在每个运单行上都是整趟里程 —— 不去重会把里程算重 6 倍，
    理论百公里油耗被低估 6 倍。
    """
    rows = []
    for i in range(6):
        seg = make_segment("P1", "2026-01-20 08:00", 120.0)
        seg["trip_id"] = "TRIP-1"
        seg["kg_co2"] = seg["kg_co2_gross"] / 6.0 if "kg_co2_gross" in seg else seg["kg_co2"] / 6.0
        seg["kg_co2_phys"] = seg["kg_co2_phys"] / 6.0
        seg["kg_co2_gross"] = seg["kg_co2"] * 6.0
        seg["waybill_assoc_count"] = 6
        rows.append(seg)
    raw = pd.DataFrame(rows)
    assert len(raw) == 6

    trips = fe.to_physical_trips(raw)
    assert len(trips) == 1, "6 个运单行应折叠成 1 个物理行程"
    assert trips.iloc[0]["trip_km"] == pytest.approx(120.0), "里程不能算重"
    assert trips.iloc[0]["kg_co2"] == pytest.approx(raw["kg_co2"].sum()), "碳排应等于分摊值之和"

    # 走标准入口后，理论百公里油耗应等于 因子×里程 的口径（不被低估）
    out = fe.prepare_segments(raw)
    assert out.iloc[0]["legal_l_100km"] == pytest.approx(
        fe._g_per_km_to_l_100km(FACTOR_HEAVY, KG_PER_L), abs=0.01)


def test_prepare_segments_is_idempotent_on_trip_level():
    """已是物理行程口径的表，再过一次不应改变结果。"""
    seg = pd.DataFrame([make_segment("P1", "2026-01-20 08:00", 120.0)])
    once = fe.prepare_segments(seg)
    twice = fe.prepare_segments(once)
    assert len(once) == len(twice) == 1
    assert once.iloc[0]["kg_co2"] == pytest.approx(twice.iloc[0]["kg_co2"])


# ---------------------------------------------------------------------------
# 1. 换算物理正确
# ---------------------------------------------------------------------------

def test_diesel_factor_read_from_config():
    """柴油因子只从 config/calc_factors.json 读，不硬编码。"""
    assert fe.diesel_kg_per_l() == pytest.approx(KG_PER_L, abs=1e-9)
    src = (ROOT / "src" / "fleet_efficiency.py").read_text(encoding="utf-8")
    assert "2.63" not in src, "柴油因子不得硬编码"
    assert "0.53" not in src
    assert "3.16" not in src


def test_g_per_km_to_l_100km_lands_in_real_truck_range():
    """30 t 重卡 1055.952 g/km → 约 33.4 L/100km（真实重卡满载区间内）。"""
    got = fe._g_per_km_to_l_100km(FACTOR_HEAVY, KG_PER_L)
    assert got == pytest.approx(33.416, abs=0.01)
    assert 25.0 < got < 40.0


def test_g_per_km_to_l_100km_refuses_without_factor():
    """没有因子就返回 None —— 不拿默认值顶替。"""
    assert fe._g_per_km_to_l_100km(1055.952, None) is None
    assert fe._g_per_km_to_l_100km(None, KG_PER_L) is None
    assert fe._g_per_km_to_l_100km(0, KG_PER_L) is None


# ---------------------------------------------------------------------------
# 2. VSP 轨适用边界
# ---------------------------------------------------------------------------

def test_speed_bands():
    assert fe.speed_band(15.0) == "≤20"
    assert fe.speed_band(25.0) == "20-30"
    assert fe.speed_band(45.0) == "40-50"
    assert fe.speed_band(55.0) == "50-60"
    assert fe.speed_band(90.0) == ">80"
    assert fe.speed_band(None) == "未知"


def test_theoretical_table_marks_vsp_inapplicable_at_low_speed():
    """低速段 VSP/法定可达 4.5 倍 —— 必须标 vsp_applicable=False，不能当理论值用。"""
    slow = make_segment("SLOW", "2026-01-10 08:00", 100.0, speed=15.0)
    fast = make_segment("FAST", "2026-01-10 08:00", 100.0, speed=70.0)
    seg = pd.DataFrame([slow, fast])
    out = fe.theoretical_fuel_table(seg)
    assert bool(out.loc[0, "vsp_applicable"]) is False
    assert bool(out.loc[1, "vsp_applicable"]) is True
    assert out.loc[0, "speed_band"] == "≤20"
    assert out.loc[0, "legal_l_100km"] == pytest.approx(33.416, abs=0.01)


def test_vsp_over_legal_ratio_matches_speed_formula():
    """VSP/法定 = mult(VSP) × 50 ÷ 速度（mult 随速度变化）。低速必然 >1。"""
    seg = pd.DataFrame([
        make_segment("A", "2026-01-10 08:00", 100.0, speed=15.0, vsp_ratio=4.0),
        make_segment("B", "2026-01-10 08:00", 100.0, speed=70.0, vsp_ratio=0.7),
    ])
    out = fe.theoretical_fuel_table(seg)
    assert out.loc[0, "vsp_over_legal"] == pytest.approx(4.0, abs=1e-6)
    assert out.loc[0, "vsp_over_legal"] > 1.0      # 低速：VSP 轨更高
    assert out.loc[1, "vsp_over_legal"] < 1.0      # 高速：VSP 轨更低


def test_theoretical_table_vsp_g_km_uses_phys_track():
    """VSP 轨的 g/km 必须由 kg_co2_phys 推，不是把法定轨换个名字。"""
    seg = pd.DataFrame([make_segment("A", "2026-01-10 08:00", 100.0, vsp_ratio=2.0)])
    out = fe.theoretical_fuel_table(seg)
    assert out.iloc[0]["vsp_g_km"] == pytest.approx(2.0 * out.iloc[0]["legal_g_km"], abs=1e-6)
    assert out.iloc[0]["vsp_l_100km"] == pytest.approx(
        2.0 * out.iloc[0]["legal_l_100km"], abs=1e-6)


# ---------------------------------------------------------------------------
# 3. 油价桥梁：不编造
# ---------------------------------------------------------------------------

def test_resolve_fuel_volume_prefers_invoice_unit_price():
    rec = make_invoice("P1", "2026-01-10", 65.5, 476.84, unit_price=7.28)
    out = fe.resolve_fuel_volume(rec)
    assert out["status"] == "ok"
    assert out["fuel_liters"] == 65.5
    assert out["price_yuan_per_l"] == pytest.approx(7.28)
    assert out["price_source"] == "invoice_unit_price"


def test_resolve_fuel_volume_derives_price_from_amount_over_liters():
    rec = make_invoice("P1", "2026-01-10", 65.5, 476.84, unit_price=None)
    out = fe.resolve_fuel_volume(rec)
    assert out["status"] == "ok"
    assert out["price_source"] == "amount_over_liters"
    assert out["price_yuan_per_l"] == pytest.approx(476.84 / 65.5, abs=1e-3)


def test_resolve_fuel_volume_flags_price_amount_mismatch():
    """票面单价 7.28 与 金额/油量 10.00 差 >5% → 需人工复核。"""
    rec = make_invoice("P1", "2026-01-10", 65.5, 655.0, unit_price=7.28)
    out = fe.resolve_fuel_volume(rec)
    assert out["status"] == "needs_review"
    assert out["reason_code"] == "price_amount_mismatch"


def test_resolve_fuel_volume_uses_price_history_only_as_last_resort():
    history = {"prices": [{"date": "2026-01-10", "price_yuan_per_l": 7.28,
                           "source": "TEST"}]}
    rec = {"plate": "P1", "invoice_date": "2026-01-10", "invoice_amount": 476.84}
    out = fe.resolve_fuel_volume(rec, history)
    assert out["status"] == "ok"
    assert out["price_source"] == "price_history"
    assert out["fuel_liters"] == pytest.approx(476.84 / 7.28, abs=0.01)


def test_resolve_fuel_volume_refuses_to_invent_without_price():
    """有金额但拿不到可核查油价 → needs_review，绝不编油量。"""
    rec = {"plate": "P1", "invoice_date": "2026-01-10", "invoice_amount": 476.84}
    out = fe.resolve_fuel_volume(rec, {"prices": []})
    assert out["status"] == "needs_review"
    assert out["reason_code"] == "missing_price_source"
    assert out["fuel_liters"] is None


def test_price_history_lookup_does_not_interpolate():
    history = {"prices": [{"date": "2026-01-10", "price_yuan_per_l": 7.28}]}
    assert fe._lookup_price(history, "2026-01-11") is None
    assert fe._lookup_price({}, "2026-01-10") is None


# ---------------------------------------------------------------------------
# 4. 油箱周期分摊
# ---------------------------------------------------------------------------

def test_allocation_splits_fuel_by_distance_in_a_cycle():
    """一箱油覆盖周期内全程，按里程比例分摊。"""
    seg = pd.DataFrame([
        make_segment("P1", "2026-01-20 08:00", 200.0),
        make_segment("P1", "2026-01-21 08:00", 100.0),
    ])
    inv = make_invoice("P1", "2026-01-15", 65.5, 476.84)
    out = fe.allocate_invoice_fuel([inv], seg)
    assert out["allocated_liters"].sum() == pytest.approx(65.5, abs=1e-6)
    assert out.loc[0, "allocated_liters"] == pytest.approx(65.5 * 200 / 300, abs=1e-6)
    assert out.loc[1, "allocated_liters"] == pytest.approx(65.5 * 100 / 300, abs=1e-6)
    assert set(out["allocation_status"]) == {"allocated"}


def test_allocation_cuts_cycles_at_the_next_invoice():
    """相邻两次加油之间才是一箱油：第二张票不覆盖第一张之后的行程。"""
    seg = pd.DataFrame([
        make_segment("P1", "2026-01-05 08:00", 100.0),
        make_segment("P1", "2026-01-20 08:00", 100.0),
        make_segment("P1", "2026-01-25 08:00", 100.0),
    ])
    invs = [
        make_invoice("P1", "2026-01-10", 60.0, 436.8, invoice_no="INV-A"),
        make_invoice("P1", "2026-01-22", 40.0, 291.2, invoice_no="INV-B"),
    ]
    out = fe.allocate_invoice_fuel(invs, seg)
    by_id = out.set_index("trip_id")
    # 01-05 在第一张票之前 → 无覆盖
    assert by_id.loc["P1@2026-01-05 08:00@O-A|B", "allocation_status"] == "no_invoice_cover"
    assert pd.isna(by_id.loc["P1@2026-01-05 08:00@O-A|B", "allocated_liters"])
    # 01-20 在 [01-10, 01-22) → 归 INV-A
    assert by_id.loc["P1@2026-01-20 08:00@O-A|B", "cycle_invoice_no"] == "INV-A"
    # 01-25 在 [01-22, ∞) → 归 INV-B
    assert by_id.loc["P1@2026-01-25 08:00@O-A|B", "cycle_invoice_no"] == "INV-B"


def test_allocation_without_invoice_leaves_nan():
    seg = pd.DataFrame([make_segment("P1", "2026-01-20 08:00", 200.0)])
    out = fe.allocate_invoice_fuel([], seg)
    assert out["allocated_liters"].isna().all()
    assert set(out["allocation_status"]) == {"no_invoice_cover"}


def test_allocation_ignores_invoice_without_plate_or_date():
    seg = pd.DataFrame([make_segment("P1", "2026-01-20 08:00", 200.0)])
    bad = {"plate": "", "invoice_date": "2026-01-10", "fuel_liters": 65.5,
           "invoice_amount": 476.84}
    out = fe.allocate_invoice_fuel([bad], seg)
    assert out["allocated_liters"].isna().all()


# ---------------------------------------------------------------------------
# 5. 车辆标签
# ---------------------------------------------------------------------------

def test_vehicle_label_flags_high_consumption():
    """油耗明显偏高 → 异常·偏高；正常 → 正常。"""
    seg = pd.DataFrame([make_segment("P1", "2026-01-20 08:00", 200.0)])
    normal = fe.allocate_invoice_fuel(
        [make_invoice("P1", "2026-01-15", 66.8, 486.3)], seg)
    table = fe.vehicle_efficiency_table(normal, normal)
    row = table.iloc[0]
    assert row["measured_l_100km"] == pytest.approx(33.4, abs=0.2)
    assert row["theoretical_l_100km"] == pytest.approx(33.416, abs=0.01)
    assert abs(row["deviation_pct"]) < fe.DEVIATION_NOTICE_PCT
    assert row["label"] == "正常"

    heavy = fe.allocate_invoice_fuel(
        [make_invoice("P1", "2026-01-15", 100.0, 728.0)], seg)
    table2 = fe.vehicle_efficiency_table(heavy, heavy)
    row2 = table2.iloc[0]
    assert row2["deviation_pct"] > fe.DEVIATION_ANOMALY_PCT
    assert row2["label"] == "异常·偏高"


def test_vehicle_confidence_grades_by_covered_km():
    assert fe._confidence(0.0) == "insufficient"
    assert fe._confidence(30.0) == "insufficient"
    assert fe._confidence(100.0) == "low"
    assert fe._confidence(500.0) == "medium"
    assert fe._confidence(1500.0) == "high"


def test_vehicle_label_refuses_to_judge_when_sample_insufficient():
    """覆盖里程不足 → 明确写"样本不足，不判定"，不硬给标签。"""
    seg = pd.DataFrame([make_segment("P1", "2026-01-20 08:00", 30.0)])
    alloc = fe.allocate_invoice_fuel(
        [make_invoice("P1", "2026-01-15", 10.0, 72.8)], seg)
    table = fe.vehicle_efficiency_table(alloc, alloc)
    assert table.iloc[0]["confidence"] == "insufficient"
    assert table.iloc[0]["label"] == "样本不足，不判定"


def test_vehicle_table_carries_audit_evidence():
    seg = pd.DataFrame([make_segment("P1", "2026-01-20 08:00", 200.0)])
    alloc = fe.allocate_invoice_fuel(
        [make_invoice("P1", "2026-01-15", 66.8, 486.3, invoice_no="INV-X")], seg)
    table = fe.vehicle_efficiency_table(alloc, alloc)
    row = table.iloc[0]
    assert row["invoice_nos"] == "INV-X"
    assert row["segments_covered"] == 1
    assert row["km_covered"] == pytest.approx(200.0)
    assert "assumptions" in row and row["assumptions"]


# ---------------------------------------------------------------------------
# 6. 路段模型与碳分摊
# ---------------------------------------------------------------------------

def test_route_model_uses_vsp_prior_without_samples():
    seg = pd.DataFrame([
        make_segment("P1", "2026-01-20 08:00", 200.0, speed=15.0, vsp_ratio=4.0),
    ])
    enriched = fe.theoretical_fuel_table(seg)
    rm = fe.route_efficiency_model(enriched, enriched)
    assert rm.iloc[0]["factor_source"] == "vsp_prior"
    assert rm.iloc[0]["sample_segments"] == 0
    assert rm.iloc[0]["route_factor"] == pytest.approx(4.0, abs=1e-3)   # 低速路段 VSP 轨 4 倍


def test_route_model_switches_to_measured_with_enough_samples():
    """同一路段多车样本 ≥5 段 → 用实测中位数标定。"""
    segs = []
    for i in range(5):
        segs.append(make_segment("P%d" % i, "2026-01-20 08:00", 200.0))
    seg = pd.DataFrame(segs)
    invs = [make_invoice("P%d" % i, "2026-01-15", 66.8, 486.3,
                          invoice_no="INV-%d" % i) for i in range(5)]
    enriched = fe.theoretical_fuel_table(seg)
    alloc = fe.allocate_invoice_fuel(invs, enriched)
    rm = fe.route_efficiency_model(enriched, alloc)
    assert rm.iloc[0]["factor_source"] == "measured"
    assert rm.iloc[0]["sample_segments"] == 5
    assert rm.iloc[0]["measured_factor"] == pytest.approx(1.0, abs=0.02)


def test_attribute_carbon_applies_route_factor():
    seg = pd.DataFrame([make_segment("P1", "2026-01-20 08:00", 200.0)])
    enriched = fe.theoretical_fuel_table(seg)
    model = pd.DataFrame([{"od_site": "O-A|B", "route_factor": 1.25,
                           "factor_source": "vsp_prior"}])
    out = fe.attribute_carbon(enriched, model)
    assert bool(out.iloc[0]["factor_applied"]) is True
    assert out.iloc[0]["co2_attributed_kg"] == pytest.approx(
        enriched.iloc[0]["kg_co2"] * 1.25)


def test_attribute_carbon_without_factor_keeps_original():
    seg = pd.DataFrame([make_segment("P1", "2026-01-20 08:00", 200.0)])
    enriched = fe.theoretical_fuel_table(seg)
    out = fe.attribute_carbon(enriched, pd.DataFrame())
    assert bool(out.iloc[0]["factor_applied"]) is False
    assert out.iloc[0]["co2_attributed_kg"] == pytest.approx(enriched.iloc[0]["kg_co2"])


# ---------------------------------------------------------------------------
# 7. 审计清单
# ---------------------------------------------------------------------------

def test_audit_trail_only_lists_items_needing_review():
    seg = pd.DataFrame([
        make_segment("GOOD", "2026-01-20 08:00", 200.0),
        make_segment("BAD", "2026-01-20 08:00", 200.0),
    ])
    invs = [
        make_invoice("GOOD", "2026-01-15", 66.8, 486.3, invoice_no="INV-G"),
        make_invoice("BAD", "2026-01-15", 120.0, 873.6, invoice_no="INV-B"),
    ]
    enriched = fe.theoretical_fuel_table(seg)
    alloc = fe.allocate_invoice_fuel(invs, enriched)
    table = fe.vehicle_efficiency_table(enriched, alloc)
    trail = fe.build_audit_trail(table, pd.DataFrame())
    plates = {a["plate"] for a in trail}
    assert "BAD" in plates
    assert "GOOD" not in plates
    for item in trail:
        assert item["rule"]
        assert "invoice_nos" in item
        assert "confidence" in item


def test_audit_trail_separates_data_gap_from_real_clues():
    """完全没有发票覆盖 = 数据缺口，不是审计线索，不能淹掉真线索。"""
    seg = pd.DataFrame([
        make_segment("GOOD", "2026-01-20 08:00", 200.0),
        make_segment("BAD", "2026-01-20 08:00", 200.0),
        make_segment("NOCOVER", "2026-01-20 08:00", 200.0),
    ])
    invs = [
        make_invoice("GOOD", "2026-01-15", 66.8, 486.3, invoice_no="INV-G"),
        make_invoice("BAD", "2026-01-15", 120.0, 873.6, invoice_no="INV-B"),
    ]
    enriched = fe.theoretical_fuel_table(seg)
    alloc = fe.allocate_invoice_fuel(invs, enriched)
    table = fe.vehicle_efficiency_table(enriched, alloc)
    trail = fe.build_audit_trail(table, pd.DataFrame())
    plates = {a["plate"] for a in trail}
    assert "BAD" in plates
    assert "GOOD" not in plates
    assert "NOCOVER" not in plates, "无发票覆盖是数据缺口，不该进审计清单"

    gaps = fe.data_gap_summary(table)
    assert gaps["without_invoice"] == 1
    assert gaps["with_invoice"] == 2


# ---------------------------------------------------------------------------
# 8. 纪律
# ---------------------------------------------------------------------------

def test_module_does_not_import_pipeline_layers():
    """src/fleet_efficiency 是分析层：不 import preprocess/evaluator/pipeline。"""
    tree = ast.parse((ROOT / "src" / "fleet_efficiency.py").read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(("." * node.level) + (node.module or ""))
    offenders = [n for n in imported
                 if n.endswith("preprocess") or n.endswith("evaluator")
                 or n.endswith("pipeline") or n.endswith("invoice")]
    assert not offenders, "分析层不得 import %s" % offenders


def test_module_does_not_write_processed():
    """分析层不得写 data/processed（只读结果表）。"""
    src = (ROOT / "src" / "fleet_efficiency.py").read_text(encoding="utf-8")
    assert "to_parquet" not in src
    assert "to_csv" not in src
    assert "PROCESSED" not in src


def test_self_test_runs():
    out = fe.self_test()
    assert out["kg_co2_per_l"] == pytest.approx(3.16, abs=1e-9)
    assert out["g_km_to_l100km_1055_952"] == pytest.approx(33.416, abs=0.01)
    assert out["speed_band_55"] == "50-60"
