# -*- coding: utf-8 -*-
"""主应用共享状态与展示快照；不修改 src/ 底层核算口径。"""
from __future__ import annotations

from typing import Any, Dict

import streamlit as st

import services

BASE_TOTAL_T = 4502.04  # 仅在结果表完全缺失时兜底


def _base_snapshot() -> tuple[float, str]:
    """优先读 kpi 结果表；缺失时才使用静态演示值。"""
    kpi = services.kpis()
    try:
        gross = float(kpi.get("gross_t"))
        if gross > 0:
            return round(gross, 2), "result"
    except (TypeError, ValueError):
        pass
    return BASE_TOTAL_T, "static"
DEFAULTS = {
    "current_invoice_carbon": 0.0,       # kgCO2
    "current_invoice_fuel": None,        # L
    "current_invoice_estimated": False,
    "invoice_history": [],
    "today_invoice_count": 0,
    "carbon_price": 100.0,
    "carbon_quota_t": BASE_TOTAL_T,
    "disclosure_note": "",
    "disclosure_source": "",
}


def init_state() -> None:
    for key, value in DEFAULTS.items():
        if key not in st.session_state:
            # 列表默认值为每个会话单独创建，避免跨会话共享可变对象。
            st.session_state[key] = list(value) if isinstance(value, list) else value


def _nonnegative(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def carbon_snapshot() -> Dict[str, float]:
    """所有页面共用的当期快照。

    current_invoice_carbon=kg → t，与基准毛口径相加；财务风险只含发票新增配额成本。
    CO₂ 环境传感器通道已删除，不再有「硬件突发成本」这一项。
    """
    invoice_t = _nonnegative(st.session_state.get("current_invoice_carbon")) / 1000.0
    base_t, base_source = _base_snapshot()
    quota_t = _nonnegative(st.session_state.get("carbon_quota_t"))
    price = _nonnegative(st.session_state.get("carbon_price"))
    total_t = base_t + invoice_t
    gap_t = max(0.0, total_t - quota_t)
    invoice_cost = invoice_t * price
    return {
        "base_t": base_t,
        "base_source": base_source,
        "invoice_t": invoice_t,
        "total_t": total_t,
        "quota_t": quota_t,
        "balance_t": quota_t - total_t,
        "gap_t": gap_t,
        "price": price,
        "invoice_cost_yuan": invoice_cost,
        "financial_risk_yuan": invoice_cost,
    }


