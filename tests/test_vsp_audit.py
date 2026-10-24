from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "data" / "processed"


def test_vsp_audit_contract():
    audit = json.loads((P / "vsp_audit.json").read_text(encoding="utf-8"))
    assert audit["current"]["rows"] == 33214
    assert audit["physical_current"]["trips"] == 12167
    assert audit["hyc"]["trips"] == 12167
    assert abs(audit["current"]["t"] - 4502.042) < 0.01
    assert abs(audit["physical_current"]["t"] - 2258.438) < 0.01
    assert abs(audit["vsp"]["t"] - 2324.654) < 0.01
    assert abs(audit["vsp"]["all_rows_t"] - 4400.866) < 0.01
    assert abs(audit["vsp_effect_t"] - 66.216) < 0.01
    assert abs(audit["hyc"]["t"] - 2338.932) < 0.01
    assert abs(audit["hyc_aux"]["t"] - 4649.104) < 0.01
    assert abs(audit["ratios"]["vsp_to_legal_pct"] - 2.932) < 0.01
    assert abs(audit["ratios"]["hyc_to_vsp_pct"] - 0.614) < 0.01
    assert abs(audit["duplicate_effect_t"] - 2243.604) < 0.01
    assert abs(audit["factor_effect_t"] + 80.493) < 0.01
    assert abs(audit["net_delta_t"] - 2163.111) < 0.01
    assert audit["mode"] == "audit_only"


def test_vsp_audit_tables():
    import pandas as pd
    trips = pd.read_parquet(P / "vsp_physical_trips.parquet")
    groups = pd.read_parquet(P / "vsp_vehicle_groups.parquet")
    assert len(trips) == 12167
    assert len(groups) == 6
    assert "carbon_kg" in trips.columns
    assert "碳排_kg" in groups.columns
