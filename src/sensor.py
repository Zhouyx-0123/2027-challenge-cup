# -*- coding: utf-8 -*-
"""云边端协同内核：传感器读取与超标碳成本估算，纯函数、可单测。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd

CO2_THRESHOLD_PPM = 1000.0
AIR_DENSITY_KG_M3 = 1.2
CO2_MOLAR_MASS = 44.01
AIR_MOLAR_MASS = 28.97
WARNING_COLUMNS = ["日期", "设备ID", "超标碳排量", "额外碳配额成本(元)", "is_warning"]

#: 读数文件默认位置（仓库根下）；可用环境变量 ``SENSOR_DATA_FILE`` 覆盖
#: （容器部署时指向挂载卷，例如 /app/data/sensor_data.csv）
DEFAULT_SENSOR_DATA_PATH = Path(__file__).resolve().parents[1] / "sensor_data.csv"


def sensor_data_path() -> Path:
    """读数文件路径：环境变量 ``SENSOR_DATA_FILE`` > 默认（仓库根 sensor_data.csv）。

    接收端（``server.py``）与读取端（``app/live_services.py``）**共用这一个函数**，
    避免两边路径写死导致"写了读不到"。
    """
    raw = (os.environ.get("SENSOR_DATA_FILE") or "").strip()
    return Path(raw) if raw else DEFAULT_SENSOR_DATA_PATH


def read_sensor_data(path: Path) -> pd.DataFrame:
    """读取最新 CSV；文件不存在、被占用或损坏时返回空表，前端不红屏。"""
    columns = ["timestamp", "device_id", "co2", "temp"]
    try:
        frame = pd.read_csv(path, encoding="utf-8-sig")
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeError):
        return pd.DataFrame(columns=columns)
    if not set(columns).issubset(frame.columns):
        return pd.DataFrame(columns=columns)
    frame = frame[columns].copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
    frame["co2"] = pd.to_numeric(frame["co2"], errors="coerce")
    frame["temp"] = pd.to_numeric(frame["temp"], errors="coerce")
    return frame.dropna(subset=["timestamp", "co2", "temp"]).reset_index(drop=True)


def build_warning_ledger(frame: pd.DataFrame, room_volume_m3: float = 100.0,
                         carbon_price_yuan_t: float = 100.0,
                         threshold_ppm: float = CO2_THRESHOLD_PPM) -> pd.DataFrame:
    """把每次超标读数转换为一条突发碳成本事件。

    演示估算：ppm 为体积分数；超标 CO2 质量 = 浓度差 × 空气体积 ×
    空气质量密度 × CO2/空气摩尔质量比。结果单位为 kgCO2，不直接等同于
    经核证的碳配额缺口；正式入账前仍需按监测与财务制度复核。
    """
    if frame.empty or room_volume_m3 <= 0 or carbon_price_yuan_t < 0:
        return pd.DataFrame(columns=WARNING_COLUMNS)
    high = frame[frame["co2"] > threshold_ppm].copy()
    if high.empty:
        return pd.DataFrame(columns=WARNING_COLUMNS)

    excess_fraction = (high["co2"] - threshold_ppm) / 1_000_000.0
    excess_kg = (excess_fraction * room_volume_m3 * AIR_DENSITY_KG_M3
                 * CO2_MOLAR_MASS / AIR_MOLAR_MASS)
    result = pd.DataFrame({
        "日期": high["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S"),
        "设备ID": high["device_id"].astype(str),
        "超标碳排量": excess_kg.round(6),
        "额外碳配额成本(元)": (excess_kg / 1000.0 * carbon_price_yuan_t).round(6),
        "is_warning": True,
    })
    return result.reset_index(drop=True)


def highlight_warning(row: pd.Series) -> list[str]:
    """Pandas Styler：预警整行黄色高亮。"""
    color = "background-color: #fef08a; color: #713f12" if bool(row.get("is_warning")) else ""
    return [color] * len(row)


def warning_assumptions(room_volume_m3: float, carbon_price_yuan_t: float,
                        threshold_ppm: float) -> dict[str, Any]:
    return {
        "threshold_ppm": threshold_ppm,
        "room_volume_m3": room_volume_m3,
        "carbon_price_yuan_t": carbon_price_yuan_t,
        "air_density_kg_m3": AIR_DENSITY_KG_M3,
    }
