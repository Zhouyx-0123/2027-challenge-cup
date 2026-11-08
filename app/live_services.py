# -*- coding: utf-8 -*-
"""实时演示通道：用户触发的 OCR 与路线场景。

与 :mod:`services` 的只读结果层严格分离。本模块不参与 E₀、KPI、Gate，
页面必须明确标注“实时演示”，并且不得把结果伪装成主账。所有重型依赖
均延迟到函数调用时导入，主页启动不需要加载 pymoo 或联网模块。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------- 情景演算 ----------------

def calc_factors() -> Dict[str, Any]:
    from src import scenarios
    return scenarios.load_factors()


def calc_electrification(params: Dict[str, Any]) -> Dict[str, Any]:
    from src import scenarios
    return scenarios.calc_electrification(params)


def calc_fleet_tco(params: Dict[str, Any]) -> Dict[str, Any]:
    from src import scenarios
    return scenarios.calc_fleet_tco(params)


def tonne_km_comparison(our_g_per_tkm: float, **kwargs: Any) -> Dict[str, Any]:
    from src import scenarios
    return scenarios.tonne_km_comparison(our_g_per_tkm, **kwargs)


# ---------------- 发票 ----------------

def invoice_api_key() -> str:
    """读 DeepSeek Key（分层：env → 挂载密钥目录 → config/deepseek_key.txt）。"""
    from src import config as app_config

    return app_config.secret("DEEPSEEK_API_KEY", files=("deepseek_key.txt",))


def extract_invoice(image_bytes: bytes, filename: str = "invoice.jpg") -> Dict[str, Any]:
    from src import invoice
    return invoice.extract_invoice_fields(image_bytes, invoice_api_key(), filename)


def register_finance_invoice(record, image_bytes, filename):
    from src.finance_workflow import register_invoice
    return register_invoice(record, image_bytes, filename)


def finance_download(mode):
    from src.finance_workflow import finance_download as read_file
    return read_file(mode)


def register_transport_invoice(record, image_bytes, filename):
    from src.transport_evidence import register
    from src.finance_workflow import finance_dir
    return register(record, image_bytes, filename, finance_dir())


def transport_evidence_download(mode):
    from src.transport_evidence import read_store, export_excel
    from src.finance_workflow import finance_dir
    return export_excel(read_store(mode, finance_dir())["records"])


def invoice_summary(fuel_liters: Optional[float], distance_km: Optional[float],
                    **kwargs: Any) -> Dict[str, Any]:
    from src import invoice
    return invoice.invoice_summary(fuel_liters, distance_km, **kwargs)


def calc_carbon(load: float, distance: float, truck_type: Any = "重型") -> float:
    from src.invoice import calc_carbon as _calc
    return _calc(load, distance, truck_type=truck_type)


def invoice_fuel_with_estimation(value: Any) -> Tuple[float, bool]:
    from src import invoice
    return invoice.fuel_with_estimation(value)


def invoice_carbon(record: Dict[str, Any]) -> Dict[str, Any]:
    from src import invoice
    return invoice.calculate_invoice_carbon(record)


def invoice_factors() -> Dict[str, Any]:
    from src import invoice
    return invoice.diesel_factors()


def invoice_consumption_table() -> Dict[int, float]:
    from src import invoice
    return dict(invoice.TRUCK_CONSUMPTION)


def invoice_vclass_names() -> Dict[int, str]:
    from src import invoice
    return dict(invoice.VCLASS_NAME)


def invoice_mock_mode() -> bool:
    from src import invoice
    return bool(invoice.MOCK_MODE)


# ---------------- 路线场景 ----------------

def amap_key_available() -> bool:
    from src.routing import amap_client
    return bool(amap_client.load_amap_key())


@st.cache_data(show_spinner=False, max_entries=100)
def _cached_geocode(query: str, city: str) -> Dict[str, Any]:
    from src.routing import geocode
    return geocode.geocode_place(query, city)


def resolve_route_place(query: str, city: str = "") -> Dict[str, Any]:
    return _cached_geocode(str(query).strip(), str(city).strip())


def amap_driving_routes(origin: str, destination: str) -> List[Dict[str, Any]]:
    from src.routing import amap_client
    return amap_client.fetch_driving_routes(origin, destination)


def amap_js_available() -> bool:
    from src.routing import amap_js
    return amap_js.js_available()


def amap_js_credentials() -> Tuple[str, str]:
    from src.routing import amap_js
    return amap_js.load_js_credentials()


def evaluate_route_records(routes: List[Dict[str, Any]], truck_type: str, load: float,
                           carbon_price: float, energy_cost_per_km: float = 0.0) -> List[Dict[str, Any]]:
    from src.routing import route_carbon
    return route_carbon.evaluate_routes(
        routes, truck_type, load, carbon_price, energy_cost_per_km)


def baseline_route_choices(routes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from src.routing import route_carbon
    return route_carbon.as_route_choices(routes)


def evolve_route_options(routes: List[Dict[str, Any]], **kwargs: Any) -> List[Dict[str, Any]]:
    from src.routing import evolve_routes
    return evolve_routes.evolve_routes(routes, **kwargs)


def route_memory() -> pd.DataFrame:
    from src.routing import evolve_routes
    return evolve_routes.load_route_memory()


def save_route_memory(route: Dict[str, Any]) -> Dict[str, Any]:
    from src.routing import evolve_routes
    return evolve_routes.save_route_to_memory(route)


# ---------------- 披露文案 ----------------

def generate_disclosure_note(snapshot: Dict[str, Any]) -> str:
    from src import disclosure
    return disclosure.generate_disclosure_note(snapshot, invoice_api_key())
