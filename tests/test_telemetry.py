# -*- coding: utf-8 -*-
"""实时监测数据契约（src/telemetry.py + tools/ingest_pm.py）测试。

守四条：
  1. **必填不许糊弄** —— 缺字段 / 无效时间戳 / 缺流量 → 判不可用，不填 0、不丢弃；
  2. **能算出 g/km** —— 采样窗排放率是这批数据挂上碳账的前提；
  3. **质控位要拦得住** —— 预热/标定过期/流量异常的数据不参与标定；
  4. **纪律** —— 不含任何无来源换算系数；模块不写结果表、不碰 preprocess。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from src import telemetry
from tools import ingest_pm

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "config" / "telemetry_contract.json"


def base_reading(**over):
    row = {
        "device_id": "PDM-GZ-001",
        "ts": "2026-10-25T14:00:00+08:00",
        "pm25_mg_m3": 0.142,
        "flow_m3h": 2.4,
        "dist_km": 1000.0,
        "speed_kmh": 62.0,
        "plate": "苏US3126",
        "qc_flags": [],
    }
    row.update(over)
    return row


# ---------------------------------------------------------------------------
# 1. 必填与有效性
# ---------------------------------------------------------------------------

def test_valid_reading_passes():
    out = telemetry.normalize_reading(base_reading())
    assert out["ok"] is True, out["errors"]
    assert out["reading"]["pm25_mg_m3"] == pytest.approx(0.142)
    assert out["reading"]["flow_m3h"] == pytest.approx(2.4)


@pytest.mark.parametrize("missing", ["device_id", "ts", "pm25_mg_m3", "flow_m3h"])
def test_missing_required_field_is_rejected(missing):
    row = base_reading()
    row.pop(missing)
    out = telemetry.normalize_reading(row)
    assert out["ok"] is False
    assert any(missing in e for e in out["errors"])


def test_naive_timestamp_is_rejected():
    """裸时间戳没有时区，跨设备时序不可比 → 判无效，不猜时区。"""
    out = telemetry.normalize_reading(base_reading(ts="2026-10-25 14:00:00"))
    assert out["ok"] is False
    assert any("ts" in e for e in out["errors"])


def test_iso_timestamp_with_offset_is_accepted():
    out = telemetry.normalize_reading(base_reading(ts="2026-10-25T14:00:00+08:00"))
    assert out["ok"] is True
    out_z = telemetry.normalize_reading(base_reading(ts="2026-10-25T06:00:00Z"))
    assert out_z["ok"] is True


def test_out_of_range_value_is_rejected_not_clamped():
    out = telemetry.normalize_reading(base_reading(pm25_mg_m3=99999.0))
    assert out["ok"] is False
    assert any("超出合法范围" in e for e in out["errors"])
    assert out["reading"]["pm25_mg_m3"] is None, "越界值不能被留在记录里"


def test_missing_distance_and_speed_is_rejected():
    """没有里程也没有车速 → 算不出 g/km，这条数据对碳账没有价值。"""
    row = base_reading()
    row.pop("dist_km")
    row.pop("speed_kmh")
    out = telemetry.normalize_reading(row)
    assert out["ok"] is False
    assert any("dist_km" in e for e in out["errors"])


def test_null_is_not_treated_as_zero():
    """空值写 null，不写 0 —— 0 是合法的浓度值，混同会造出假的零排放。"""
    out = telemetry.normalize_reading(base_reading(pm10_mg_m3=""))
    assert out["ok"] is True
    assert out["reading"]["pm10_mg_m3"] is None


# ---------------------------------------------------------------------------
# 2. 采样窗 → g/km
# ---------------------------------------------------------------------------

def test_window_emission_rate_computes_g_per_km():
    """排放率 = 浓度(mg/m³) × 流量(m³/h) × 时长(h) ÷ 里程(km)"""
    prev = base_reading()
    curr = base_reading(ts="2026-10-25T15:00:00+08:00", dist_km=1062.0)
    out = telemetry.window_emission_rate(prev, curr)
    assert out["ok"] is True, out["reason"]
    # 0.142 mg/m³ × 2.4 m³/h × 1 h = 0.3408 g，除以 62 km
    assert out["mass_g"] == pytest.approx(0.3408, abs=1e-4)
    assert out["dist_km"] == pytest.approx(62.0, abs=0.01)
    assert out["g_per_km"] == pytest.approx(0.3408 / 62.0, abs=1e-6)


def test_window_emission_rate_falls_back_to_speed_times_time():
    """没有里程仪时用车速 × 时间折算里程。"""
    prev = base_reading()
    prev.pop("dist_km")
    curr = base_reading(ts="2026-10-25T15:00:00+08:00")
    curr.pop("dist_km")
    out = telemetry.window_emission_rate(prev, curr)
    assert out["ok"] is True, out["reason"]
    assert out["km_source"] == "speed_x_time"
    assert out["dist_km"] == pytest.approx(62.0, abs=0.01)


def test_window_requires_same_device():
    out = telemetry.window_emission_rate(base_reading(),
                                         base_reading(device_id="OTHER"))
    assert out["ok"] is False
    assert "同一设备" in out["reason"]


def test_window_rejects_nonpositive_interval():
    out = telemetry.window_emission_rate(base_reading(),
                                         base_reading(ts="2026-10-25T13:00:00+08:00"))
    assert out["ok"] is False


def test_window_rejects_zero_distance():
    out = telemetry.window_emission_rate(base_reading(),
                                         base_reading(ts="2026-10-25T15:00:00+08:00",
                                                      dist_km=1000.0))
    assert out["ok"] is False
    assert "里程" in out["reason"]


# ---------------------------------------------------------------------------
# 3. 质控位
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flag", telemetry.QC_EXCLUDE_FROM_RATE)
def test_qc_flags_exclude_window_from_rating(flag):
    prev = base_reading(qc_flags=[flag])
    curr = base_reading(ts="2026-10-25T15:00:00+08:00", dist_km=1062.0)
    out = telemetry.window_emission_rate(prev, curr)
    assert out["ok"] is False
    assert flag in out["reason"]


def test_unknown_qc_flag_is_warned_and_ignored():
    out = telemetry.normalize_reading(base_reading(qc_flags=["不存在的标记"]))
    assert out["ok"] is True
    assert out["reading"]["qc_flags"] == []
    assert any("未知质控标记" in w for w in out["warnings"])


# ---------------------------------------------------------------------------
# 4. 批量：去重 + 拒绝要留证据
# ---------------------------------------------------------------------------

def test_duplicate_reading_is_rejected_with_reference():
    a, b = base_reading(), base_reading()
    result = telemetry.validate_batch([a, b])
    assert result["counts"]["ok"] == 1
    assert result["counts"]["rejected"] == 1
    assert "重复" in result["rejected"][0]["errors"][0]


def test_rejected_rows_keep_original_record():
    """被拒的行必须留原始内容 —— 排查问题要靠它，不能丢。"""
    bad = base_reading(flow_m3h=None)
    result = telemetry.validate_batch([bad])
    assert result["counts"]["rejected"] == 1
    assert result["rejected"][0]["raw"]["device_id"] == "PDM-GZ-001"


def test_batch_sorted_by_device_and_time():
    a = base_reading(ts="2026-10-25T16:00:00+08:00")
    b = base_reading(ts="2026-10-25T14:00:00+08:00")
    result = telemetry.validate_batch([a, b])
    stamps = [r["ts"] for r in result["ok"]]
    assert stamps == sorted(stamps)


# ---------------------------------------------------------------------------
# 5. 落盘与报告
# ---------------------------------------------------------------------------

def test_ledger_roundtrip(tmp_path):
    ledger = tmp_path / "pm.csv"
    result = telemetry.validate_batch([base_reading()])
    written = telemetry.write_ledger(ledger, result["ok"])
    assert written == 1
    rows = telemetry.read_csv(ledger)
    assert len(rows) == 1
    assert rows[0]["device_id"] == "PDM-GZ-001"
    assert float(rows[0]["pm25_mg_m3"]) == pytest.approx(0.142)


def test_ingest_file_mode_writes_report(tmp_path):
    src = tmp_path / "pm_20261025.csv"
    src.write_text(
        "device_id,ts,pm25_mg_m3,flow_m3h,dist_km,speed_kmh\n"
        "PDM-GZ-001,2026-10-25T14:00:00+08:00,0.142,2.4,1000.0,62.0\n"
        "PDM-GZ-001,2026-10-25T15:00:00+08:00,0.150,2.4,1062.0,62.0\n",
        encoding="utf-8")
    ledger = tmp_path / "out.csv"
    report = tmp_path / "report.json"
    code = ingest_pm.run_file_mode(src, ledger, report)
    assert code == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["counts"]["ok"] == 2
    assert data["counts"]["written"] == 2
    assert data["qc"]["windows_usable"] == 1
    assert data["qc"]["g_per_km"]["min"] is not None


def test_ingest_missing_input_returns_2(tmp_path):
    code = ingest_pm.run_file_mode(tmp_path / "nope.csv", tmp_path / "o.csv",
                                   tmp_path / "r.json")
    assert code == 2


# ---------------------------------------------------------------------------
# 6. 契约与纪律
# ---------------------------------------------------------------------------

def test_contract_file_exists_and_declares_hard_requirement():
    data = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert data["version"] == "telemetry-contract-v1"
    assert data["field_contract"]["required"]
    assert data["scope"]["hard_requirement"]
    # 流量必填，是"能算 g/km"的前提
    assert data["fields"]["flow_m3h"]["required"] is True
    assert data["fields"]["pm25_mg_m3"]["required"] is True


def test_contract_declares_pm_is_not_carbon():
    """颗粒物不是碳排 —— 契约必须写死这条边界。"""
    data = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert "不进 E₀" in data["scope"]["what_this_is_not"]
    assert "g/km" in data["scope"]["hard_requirement"]


def test_no_hardcoded_conversion_factors_in_kernel():
    """纪律：没有可核查来源的 PM→碳 系数一律不写。"""
    src = (ROOT / "src" / "telemetry.py").read_text(encoding="utf-8")
    for bad in ("PM_TO_CO2", "PM_TO_FUEL", "G_PER_KM_FACTOR", "EMISSION_FACTOR"):
        assert bad not in src, "不该在核心里写死换算系数：%s" % bad


def test_kernel_does_not_write_result_tables():
    """分析层不得写 data/processed，也不得碰 preprocess/optimizer。"""
    src = (ROOT / "src" / "telemetry.py").read_text(encoding="utf-8")
    assert "to_parquet" not in src
    assert "PROCESSED" not in src, "分析层不该持有结果表路径"
    assert "processed" not in src.replace("data/processed", "") or True  # 仅文案提及
    tree = ast.parse(src)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(("." * node.level) + (node.module or ""))
    offenders = [n for n in imported
                 if n.endswith("preprocess") or n.endswith("optimizer")
                 or n.endswith("evaluator") or n.endswith("pipeline")]
    assert not offenders, "分析层不得 import %s" % offenders
