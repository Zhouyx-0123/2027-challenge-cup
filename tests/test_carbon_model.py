from pathlib import Path

import pandas as pd

from src import carbon_model as cm

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "data" / "processed"


def _trips():
    return cm.estimate(pd.read_parquet(P / "baseline_carbon.parquet"))


def test_anchor_self_test():
    s = cm.opt.self_test()
    assert abs(s["er30_vsp2"] - 14.67) < 0.02
    assert abs(s["er30_vsp3"] - 16.53) < 0.02
    assert abs(s["vsp3_dev_pct"] - 12.68) < 0.02


def test_model_card_contract():
    card = cm.summarize(_trips())
    assert card["model_version"] == "CM-2026.10-v1"
    assert card["trips"] == 12167
    assert abs(card["km"] - 2174173.86) < 1.0
    assert abs(card["legal_t"] - 2258.438) < 0.01
    assert abs(card["vsp_t"] - 2324.654) < 0.01
    assert abs(card["vsp_delta_pct"] - 2.932) < 0.01


def test_trip_table_has_both_tracks():
    d = _trips()
    assert len(d) == 12167
    assert {"kg_co2_legal", "kg_co2_vsp", "waybill_associations"} <= set(d.columns)
    assert abs(d["kg_co2_legal"].sum() / 1000 - 2258.438) < 0.01
    assert abs(d["kg_co2_vsp"].sum() / 1000 - 2324.654) < 0.01
