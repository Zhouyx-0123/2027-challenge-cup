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
    "sensor_penalty": 0.0,               # 元
    "sensor_excess_carbon_t": 0.0,       # tCO2，避免金额与排放量混加
    "sensor_co2": None,
    "sensor_temp": None,
    "sensor_device": "未连接",
    "sensor_updated_at": "—",
    "sensor_is_warning": False,
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

    current_invoice_carbon=kg；sensor_penalty=元；二者不直接相加。
    财务风险 = 硬件突发成本 + 发票新增配额成本，硬件成本不重复计两次。
    """
    invoice_t = _nonnegative(st.session_state.get("current_invoice_carbon")) / 1000.0
    sensor_t = _nonnegative(st.session_state.get("sensor_excess_carbon_t"))
    base_t, base_source = _base_snapshot()
    quota_t = _nonnegative(st.session_state.get("carbon_quota_t"))
    price = _nonnegative(st.session_state.get("carbon_price"))
    total_t = base_t + invoice_t + sensor_t
    gap_t = max(0.0, total_t - quota_t)
    invoice_cost = invoice_t * price
    sensor_cost = _nonnegative(st.session_state.get("sensor_penalty"))
    return {
        "base_t": base_t,
        "base_source": base_source,
        "invoice_t": invoice_t,
        "sensor_t": sensor_t,
        "total_t": total_t,
        "quota_t": quota_t,
        "balance_t": quota_t - total_t,
        "gap_t": gap_t,
        "price": price,
        "invoice_cost_yuan": invoice_cost,
        "sensor_cost_yuan": sensor_cost,
        "financial_risk_yuan": invoice_cost + sensor_cost,
    }


def fallback_disclosure(snapshot: Dict[str, float]) -> str:
    return (
        "本期基准碳排放为 %.2f tCO2e。发票感知新增 %.6f tCO2e，"
        "硬件超标事件新增 %.6f tCO2e，合并后当期总碳排放为 %.6f tCO2e。"
        "按当前配额 %.2f tCO2e 计算，配额缺口为 %.6f tCO2e；"
        "相关财务风险合计 %.2f 元。估算数据已保留标记，正式披露前需人工复核。"
        % (snapshot["base_t"], snapshot["invoice_t"], snapshot["sensor_t"],
           snapshot["total_t"], snapshot["quota_t"], snapshot["gap_t"],
           snapshot["financial_risk_yuan"])
    )
