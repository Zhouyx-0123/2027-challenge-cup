# -*- coding: utf-8 -*-
"""实时演示通道：用户触发的 OCR、传感器和路线场景。

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


# ---------------- 传感器 ----------------
#: 传感器演示模式：默认开启 —— 没有真实 sensor_data.csv 时用一段可复现的
#: 演示序列填充界面；设置 SENSOR_MOCK=0 可关闭演示（页面回到等待读数状态）。
#: 纪律（对齐 src/invoice.py 的 MOCK 纪律）：演示数据必须带 mock 标记，
#: 页面必须显式标注「演示数据，非真实传感器读数」，不能让人误认成真实读数。
def sensor_mock_mode() -> bool:
    flag = (os.environ.get("SENSOR_MOCK", "") or "").strip().lower()
    return flag not in ("0", "false", "no", "off")


def _demo_sensor_frame(points: int = 240) -> pd.DataFrame:
    """确定性演示序列：日内周期 + 偶发超标尖峰 + 轻微噪声。

    固定随机种子，保证每次刷新形状一致，评审时不会"每刷新一次就变"。
    """
    import math
    import random

    rnd = random.Random(20270421)
    end = pd.Timestamp.now().floor("s")
    rows = []
    for i in range(points):
        ts = end - pd.Timedelta(seconds=60 * (points - 1 - i))
        phase = 2 * math.pi * (i / points)
        base = 430 + 120 * math.sin(phase)          # 日内潮汐
        noise = rnd.uniform(-16, 16)
        spike = 0.0
        for centre in (0.18, 0.47, 0.79):            # 三次超标事件
            d = abs(i / points - centre)
            if d < 0.012:
                spike = (1 - d / 0.012) * rnd.uniform(520, 760)
        co2 = max(380.0, base + noise + spike)
        temp = 22.5 + 3.2 * math.sin(phase + 1.1) + rnd.uniform(-0.5, 0.5)
        rows.append({
            "timestamp": ts,
            "device_id": "ESP32-DEMO-01",
            "co2": round(co2, 1),
            "temp": round(temp, 2),
        })
    return pd.DataFrame(rows)


def sensor_data() -> pd.DataFrame:
    """真实读数；没有真实数据且开启演示模式时回退到确定性演示序列。"""
    from src import sensor
    frame = sensor.read_sensor_data(sensor.sensor_data_path())
    if frame.empty and sensor_mock_mode():
        return _demo_sensor_frame()
    return frame


def sensor_is_mock() -> bool:
    """当前读数是否为演示序列（页面据此显式标注，绝不冒充真实读数）。"""
    from src import sensor
    return sensor.read_sensor_data(sensor.sensor_data_path()).empty and sensor_mock_mode()


def sensor_warning_ledger(frame: pd.DataFrame, **kwargs: Any) -> pd.DataFrame:
    from src import sensor
    return sensor.build_warning_ledger(frame, **kwargs)


def highlight_sensor_warning(row: pd.Series) -> List[str]:
    from src import sensor
    return sensor.highlight_warning(row)


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
