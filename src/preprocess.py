# -*- coding: utf-8 -*-
"""数据层：原始表 → 宽表（全项目唯一的数据准备入口）。

铁律（按工程师踩坑意见落地）：
1. **脏数据绝不抛 Exception**：里程缺失、车型找不到、载重缺失、发票匹配不上、
   时长异常……全部用「同线历史均值 / 行业均值 / 车型额定值」填补，并打 FLAG；
2. **一次做完**：清洗、匹配、口径换算、标记都在本文件；下游只看宽表；
3. **只产出两张文件**：data/processed/wide_data.parquet + data_quality.json；
   碳因子与碳排属于“口径”，按蓝图 §2.2 修正一留在 optimizer.py；
4. **真数据不在就回退演示数据**：data/raw 为空或列名识别失败时，自动生成
   确定性的合成样本（带脏数据），并把来源写进 _source.json，前端明确标注。

FLAG 约定（全项目统一）：
    flag_dup       1 = 重复行（保留在宽表，基准核算剔除）
    flag_anomaly   1 = 时间戳 / 速度异常（保留计碳，只影响速度统计）
    flag_basis     1 = 载重口径换算（总质量 / 准牵引 → 载质量）
    flag_estimated 1 = 有字段被估算填补（前端黄框，estimate_note 写原因）
"""

from __future__ import annotations

import json
import math
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# P0 · 常量
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"

WIDE_PATH = PROCESSED_DIR / "wide_data.parquet"
QUALITY_PATH = PROCESSED_DIR / "data_quality.json"
SOURCE_PATH = PROCESSED_DIR / "_source.json"

DEMO_ROWS = 6_000
DEMO_SEED = 20261018

INDUSTRY_SPEED_KMH = 50.0          # 行业平均门到门速度
INDUSTRY_ROAD_FACTOR = 1.28        # 直线距离 → 公路里程的行业绕行系数
INDUSTRY_LOAD_T = 12.0             # 行业平均载重（兜底）
DEFAULT_DIST_KM = 300.0            # 全样本都算不出里程时的兜底

FLAG_COLS = ["flag_dup", "flag_anomaly", "flag_basis", "flag_estimated"]

# 城市坐标（离线包，不联网）
CITY: Dict[str, Tuple[float, float]] = {
    "上海": (31.23, 121.47), "北京": (39.90, 116.40), "广州": (23.13, 113.26),
    "深圳": (22.55, 114.06), "杭州": (30.27, 120.15), "南京": (32.06, 118.80),
    "苏州": (31.32, 120.62), "成都": (30.57, 104.07), "重庆": (29.56, 106.55),
    "武汉": (30.59, 114.30), "西安": (34.34, 108.94), "郑州": (34.75, 113.63),
    "天津": (39.13, 117.20), "青岛": (36.07, 120.38), "济南": (36.65, 117.00),
    "长沙": (28.23, 112.94), "合肥": (31.86, 117.28), "福州": (26.08, 119.30),
    "厦门": (24.48, 118.09), "昆明": (25.04, 102.71), "贵阳": (26.65, 106.63),
    "南宁": (22.82, 108.32), "沈阳": (41.80, 123.43), "哈尔滨": (45.80, 126.53),
    "长春": (43.82, 125.32), "石家庄": (38.04, 114.51), "太原": (37.87, 112.55),
    "兰州": (36.06, 103.83), "乌鲁木齐": (43.82, 87.62), "拉萨": (29.65, 91.11),
    # 真实订单 TOP 收货城市（离线近似坐标，补全国分布；按订单量排序）
    "无锡": (31.49, 120.31), "宁波": (29.87, 121.55), "常州": (31.81, 119.97),
    "东莞": (23.02, 113.75), "佛山": (23.02, 113.12), "嘉兴": (30.75, 120.76),
    "温州": (27.99, 120.70), "南通": (31.98, 120.89), "绍兴": (30.00, 120.58),
    "珠海": (22.27, 113.58), "台州": (28.66, 121.42), "金华": (29.08, 119.65),
    "扬州": (32.39, 119.41), "泰州": (32.46, 119.92), "江门": (22.58, 113.08),
    "海口": (20.04, 110.20), "惠州": (23.11, 114.42), "中山": (22.52, 113.39),
    "镇江": (32.19, 119.42), "徐州": (34.26, 117.19), "南昌": (28.68, 115.86),
    "湖州": (30.89, 120.09), "湛江": (21.27, 110.36), "汕头": (23.35, 116.68),
    "盐城": (33.35, 120.16), "三亚": (18.25, 109.51), "唐山": (39.63, 118.18),
    "揭阳": (23.55, 116.37), "廊坊": (39.52, 116.70), "秦皇岛": (39.94, 119.60),
    "大连": (38.91, 121.61), "潮州": (23.66, 116.62), "呼和浩特": (40.84, 111.75),
    "临沂": (35.10, 118.36), "宿迁": (33.96, 118.28), "衢州": (28.94, 118.87),
    "芜湖": (31.35, 118.43), "马鞍山": (31.67, 118.51), "舟山": (29.99, 122.21),
    "邯郸": (36.63, 114.54), "泉州": (24.87, 118.68), "烟台": (37.46, 121.45),
    "保定": (38.87, 115.46), "洛阳": (34.62, 112.45), "襄阳": (32.01, 112.12),
    "宜昌": (30.69, 111.29), "岳阳": (29.36, 113.13), "株洲": (27.83, 113.13),
    "柳州": (24.33, 109.41), "桂林": (25.27, 110.29), "绵阳": (31.47, 104.68),
    "宜宾": (28.77, 104.62), "遵义": (27.73, 106.93), "银川": (38.49, 106.23),
    "西宁": (36.62, 101.78), "吉林": (43.84, 126.55), "莆田": (25.45, 119.01),
    "漳州": (24.51, 117.65), "赣州": (25.83, 114.93), "阜阳": (32.89, 115.81),
}

# 车型登记表：名称, 大类(1轻/2中/3重), 额定载重(t), 载重口径, 因子(g/km), 燃料
VEHICLES: List[Tuple[str, int, float, str, float, str]] = [
    ("4.2米厢式货车", 1, 4.5, "载质量", 394.56, "柴油"),
    ("依维柯", 1, 3.0, "载质量", 320.18, "柴油"),
    ("9.6米厢式货车", 2, 18.0, "载质量", 759.24, "柴油"),
    ("7.6米厢式货车", 2, 12.0, "整车总质量", 544.75, "柴油"),
    ("牵引车头", 3, 30.0, "准牵引总质量", 1055.95, "柴油"),
    ("17.5米半挂", 3, 45.0, "载质量", 1581.84, "柴油"),
]
VEHICLE_NAMES = [v[0] for v in VEHICLES]
VEHICLE_BY_NAME = {v[0]: v for v in VEHICLES}
RATED_LOAD = {v[0]: v[2] for v in VEHICLES}
VEHICLE_CLASS = {v[0]: v[1] for v in VEHICLES}
VEHICLE_BASIS = {v[0]: v[3] for v in VEHICLES}
VEHICLE_FUEL = {v[0]: v[5] for v in VEHICLES}
CLASS_REP = {1: "4.2米厢式货车", 2: "9.6米厢式货车", 3: "牵引车头"}

# 真实附件2 的 75 车型 → 因子表“总质量”档位（provisional，待杨按文献表 8 复核）
# 附件2 的“车辆载重”是 载质量/整车总质量/准牵引 混在一起，不能直接当 factor 的 load。
# 规则：车型名包含 key → (load_t, mass_basis, 说明)；key 按从长到短匹配。
REAL_GVW_RULES: List[Tuple[str, float, str, str]] = [
    ("4.2", 4.3, "载质量", "4.2m：登记 2.0 t 货重 + 约 2.3 t 自重 → 4.3 t"),
    ("9.6", 17.5, "载质量", "9.6m：登记 8.6 t 货重 + 约 8.9 t 自重 → 17.5 t"),
    ("7.6", 12.0, "整车总质量", "7.6m：登记 14.4 t 总质量 → 12.0 t 载质量"),
    ("牵引", 30.0, "准牵引总质量", "牵引车头：准牵引 30 t"),
    ("17.5", 45.0, "载质量", "17.5m 半挂：30 t 货重 + 约 15 t 自重 → 45 t"),
    ("16.5", 40.0, "载质量", "16.5m 半挂：30 t 货重 + 约 10 t 自重 → 40 t"),
    ("21米", 49.0, "载质量", "21m 半挂：40 t 货重 + 约 9 t 自重 → 49 t"),
    ("15米", 40.0, "载质量", "15m 半挂：按 40 t 总质量"),
    ("14.5", 40.0, "载质量", "14.5m 半挂：按 40 t 总质量"),
    ("13.75", 40.0, "载质量", "13.75m 半挂：按 40 t 总质量"),
    ("13.7", 40.0, "载质量", "13.7m 半挂：按 40 t 总质量"),
    ("13.5", 40.0, "载质量", "13.5m 半挂：按 40 t 总质量"),
    ("13米", 40.0, "载质量", "13m 半挂/高栏：按 40 t 总质量"),
    ("12.5", 40.0, "载质量", "12.5m 半挂：按 40 t 总质量"),
    ("7.2", 10.0, "载质量", "7.2m：按 10 t 总质量"),
    ("6.8", 10.0, "载质量", "6.8m：按 10 t 总质量"),
    ("8.6", 10.0, "载质量", "8.6m：按 10 t 总质量"),
    ("5.2", 8.0, "载质量", "5.2m：按 8 t 总质量"),
    ("5.6", 6.0, "载质量", "5.6m：按 6 t 总质量"),
    ("5.8", 8.0, "载质量", "5.8m：按 8 t 总质量"),
    ("依维柯", 3.0, "载质量", "依维柯：按 3 t 总质量"),
    ("金杯", 3.0, "载质量", "金杯：按 3 t 总质量"),
    ("大通", 3.0, "载质量", "大通：按 3 t 总质量"),
    ("面包", 3.0, "载质量", "小型面包车：按 3 t 总质量"),
]

# 2027 基准沿用 2026《2023年基准碳排放计算结果.xlsx》的车型→核定载重/类别映射（62 类）。
# 这张表是 E₀ 4,502,042.41 的对拍基准；新车型若无映射，再走 REAL_GVW_RULES 兜底。
VEHICLE_LOAD_CLASS_MAP: Dict[str, Tuple[float, int]] = {
    "小型面包车": (1.0, 1),
    "金杯车": (1.5, 1),
    "依维柯": (2.0, 1),
    "大通": (2.0, 1),
    "(新能源)4.2米厢式货车": (4.5, 1),
    "4.2m冷藏箱式尾板车": (4.5, 1),
    "4.2米冷藏厢式车": (4.5, 1),
    "4.2米厢式货车": (4.5, 1),
    "4.2米厢式货车(共营)": (4.5, 1),
    "4.2米厢式货车尾板车": (4.5, 1),
    "4.2米高栏": (4.5, 1),
    "5.2米厢式货车": (6.0, 1),
    "5.8米厢式货车": (8.0, 1),
    "6.8米厢式货车": (10.0, 2),
    "6.8米高栏": (10.0, 2),
    "7.2米厢式货车": (12.0, 2),
    "7.6米冷藏厢式车": (12.0, 2),
    "7.6米厢式货车": (12.0, 2),
    "7.6米厢式货车(共营)": (12.0, 2),
    "7.6米厢式货车尾板车": (12.0, 2),
    "9.6m冷藏箱式尾板车": (18.0, 2),
    "9.6米冷藏厢式车": (18.0, 2),
    "9.6米厢式货车": (18.0, 2),
    "9.6米厢式货车(共建)": (18.0, 2),
    "9.6米厢式货车(共营)": (18.0, 2),
    "9.6米厢式货车尾板车": (18.0, 2),
    "9.6米厢式货车（60m³固定班次）": (18.0, 2),
    "9.6米厢式货车（60m³）": (18.0, 2),
    "9.6米板车": (18.0, 2),
    "9.6米重型自卸货车": (18.0, 2),
    "9.6米飞翼厢式货车": (18.0, 2),
    "9.6米高栏": (18.0, 2),
    "12.5米厢式货车": (25.0, 2),
    "12.5米重型集装箱半挂车": (30.0, 3),
    "13米厢式货车": (30.0, 3),
    "13米槽罐车": (30.0, 3),
    "牵引车头": (30.0, 3),
    "13.5米厢式货车": (32.0, 3),
    "13米板车": (32.0, 3),
    "13米高栏": (32.0, 3),
    "13米高栏车": (32.0, 3),
    "13.5米重型集装箱半挂车": (35.0, 3),
    "13.75米板车": (35.0, 3),
    "14.5米厢式货车": (35.0, 3),
    "15米冷藏半挂车": (35.0, 3),
    "15米冷藏厢式货车": (35.0, 3),
    "14.5米重型集装箱半挂车": (38.0, 3),
    "16.5米厢式货车": (38.0, 3),
    "16米厢式货车": (38.0, 3),
    "16.5米重型集装箱半挂车": (40.0, 3),
    "16.5米重型集装箱半挂车（130m³固定班次）": (40.0, 3),
    "16.5米重型集装箱半挂车（130m³）": (40.0, 3),
    "17.5米冷藏厢式货车": (45.0, 3),
    "17.5米厢式货车": (45.0, 3),
    "17.5米板车": (45.0, 3),
    "17.5米重型集装箱半挂车": (45.0, 3),
    "17.5米重型集装箱半挂车(共建)": (45.0, 3),
    "17.5米重型集装箱车(150m³)": (45.0, 3),
    "17.5米重型集装箱车(180m³)": (45.0, 3),
    "17.5米高栏": (45.0, 3),
    "17.5米高栏/板车": (45.0, 3),
    "21米重型集装箱半挂车": (49.0, 3),
}


WIDE_COLUMNS: List[str] = [
    "waybill_no", "order_no", "plate", "vehicle_type", "vtype_code", "vclass", "fuel_type",
    "load_t", "load_raw_t", "mass_basis", "basis_note", "load_mapping_method", "flag_load_mapping_fallback",
    "origin", "dest", "od_pair", "od_key", "od_site",
    "origin_lat", "origin_lon", "dest_lat", "dest_lon",
    "dist_km", "dist_source", "month",
    "depart_ts", "arrive_ts", "duration_h", "speed_kmh",
    "dep_site", "arr_site",
    "invoice_no", "invoice_ok", "invoice_amount",
    "cargo_weight_kg", "cargo_volume_cm3", "cargo_pieces", "complete_ts",
    "flag_dup", "flag_anomaly", "flag_basis", "flag_vehicle_conflict", "flag_factor_conflict",
    "flag_estimated", "estimate_note",
    "fill_dist", "fill_vehicle", "fill_load", "fill_invoice", "fill_duration",
    "data_source",
    # 追溯/质量审计字段：只增不改既有列，便于定位原始 Excel 行和验证关联率。
    "order_matched", "vehicle_matched", "source_waybill_row", "speed_method",
    "order_no_fallback", "cargo_weight_missing", "cargo_volume_missing",
    "cargo_pieces_missing", "origin_site_fallback", "dest_site_fallback",
]

# 原始列名别名：真数据列名没冻结前，先尽量认；认不出就回退演示数据
ALIASES: Dict[str, List[str]] = {
    "waybill_no": ["waybill_no", "waybill_number", "运单号", "运单编号", "运单号码"],
    "order_no": ["order_no", "order_number", "订单号", "订单编号"],
    "plate": ["plate", "plate_no", "车牌", "车牌号", "车辆牌照"],
    "vehicle_type": ["vehicle_type", "vehicle_type_name", "车型", "车型名称"],
    "vclass": ["vclass", "vehicle_class", "车型大类"],
    "load_t": ["load_t", "额定载重", "载重", "载质量"],
    "load_raw_t": ["load_raw_t", "登记载重", "总质量", "准牵引总质量"],
    "mass_basis": ["mass_basis", "载重口径", "载重类型"],
    "origin": ["origin", "起点", "发货地", "发货城市", "起运地"],
    "dest": ["dest", "destination", "终点", "收货地", "收货城市", "目的地"],
    "origin_lat": ["origin_lat", "起点纬度"],
    "origin_lon": ["origin_lon", "起点经度"],
    "dest_lat": ["dest_lat", "终点纬度"],
    "dest_lon": ["dest_lon", "终点经度"],
    "dist_km": ["dist_km", "distance_km", "里程", "行驶里程", "实际行驶里程", "距离"],
    "month": ["month", "月份"],
    "depart_ts": ["depart_ts", "发货时间", "封车时间", "起运时间"],
    "arrive_ts": ["arrive_ts", "到达时间", "解封时间", "签收时间"],
    "speed_kmh": ["speed_kmh", "speed", "速度", "平均速度"],
    "invoice_no": ["invoice_no", "invoice_number", "发票号", "发票号码"],
    "invoice_ok": ["invoice_ok", "发票匹配", "发票状态"],
    "invoice_amount": ["invoice_amount", "发票金额", "金额"],
}


# ---------------------------------------------------------------------------
# P1 · 读取层
# ---------------------------------------------------------------------------

class NoRawData(RuntimeError):
    """没有可用的原始数据——由上层回退演示数据，不向外抛。"""


def _as_str(series: Optional[pd.Series], index: pd.Index) -> pd.Series:
    if series is None:
        return pd.Series("", index=index, dtype=object)
    return series.astype("string").fillna("").str.strip().astype(object)


def _num_col(df: pd.DataFrame, name: str) -> pd.Series:
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce")
    return pd.Series(np.nan, index=df.index, dtype=float)


def _to_datetime(series: Optional[pd.Series], index: pd.Index) -> pd.Series:
    if series is None:
        return pd.Series(pd.NaT, index=index, dtype="datetime64[ns]")
    s = series
    if pd.api.types.is_numeric_dtype(s):
        # Excel 序列日期：1899-12-30 起算
        try:
            return pd.to_datetime(s, unit="D", origin="1899-12-30", errors="coerce")
        except Exception:
            return pd.Series(pd.NaT, index=index, dtype="datetime64[ns]")
    try:
        return pd.to_datetime(s, errors="coerce")
    except Exception:
        return pd.Series(pd.NaT, index=index, dtype="datetime64[ns]")


def _haversine(lat1: Any, lon1: Any, lat2: Any, lon2: Any) -> np.ndarray:
    def _arr(x: Any) -> np.ndarray:
        return np.asarray(pd.to_numeric(pd.Series(x), errors="coerce"), dtype=float)

    lat1 = np.radians(_arr(lat1))
    lon1 = np.radians(_arr(lon1))
    lat2 = np.radians(_arr(lat2))
    lon2 = np.radians(_arr(lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    return 2.0 * 6371.0 * np.arcsin(np.sqrt(np.clip(h, 0.0, 1.0)))


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """把原始列名尽量映射到宽表契约列名；认不出的列原样保留。"""
    upper = {str(c).strip().lower(): c for c in df.columns}
    out = pd.DataFrame(index=df.index)
    for target, aliases in ALIASES.items():
        for alias in aliases:
            if alias in df.columns:
                out[target] = df[alias]
                break
            key = alias.strip().lower()
            if key in upper:
                out[target] = df[upper[key]]
                break
    return out


def _read_any(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".csv":
        for enc in ("utf-8-sig", "gbk", "utf-8"):
            try:
                return pd.read_csv(path, encoding=enc)
            except Exception:
                continue
        raise ValueError("CSV 编码无法识别：" + path.name)
    raise ValueError("不支持的文件类型：" + path.name)


def _read_tables(path: Path) -> List[Tuple[str, pd.DataFrame]]:
    """Excel 逐 Sheet 识别（真实附件有隐藏 Sheet1 和封面页，不能只读第一页）。"""
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        book = pd.read_excel(path, sheet_name=None)
        return [(str(name), _normalize_columns(sheet)) for name, sheet in book.items()]
    return [(path.name, _normalize_columns(_read_any(path)))]


def _table_score(df: pd.DataFrame) -> float:
    """给每个 Sheet/文件打分：有运单号 + 有里程/起终点 的才可能是运单表。"""
    cols = set(df.columns)
    if "waybill_no" not in cols:
        return -1.0
    score = 4.0
    if "dist_km" in cols:
        score += 4.0
    if {"origin", "dest"} <= cols or {"origin_lat", "dest_lat"} <= cols:
        score += 2.0
    score += min(len(df) / 10000.0, 3.0)
    return score




def _find_col(df: pd.DataFrame, *needles: str) -> Optional[str]:
    """按列名包含关系找列：兼容 'waybill_number（运单号）' 这类带中文后缀的表头。"""
    for c in df.columns:
        name = str(c)
        if all(n in name for n in needles):
            return c
    return None


def _clean_city(series: Optional[pd.Series], index: pd.Index) -> pd.Series:
    s = _as_str(series, index)
    s = s.str.replace(r"[省市]$", "", regex=True)
    return s.str.strip()


def _site_city(site: Any, city_names: List[str]) -> str:
    s = str(site)
    for name in city_names:
        if name and s.startswith(name):
            return name
    return ""


def _gvw_for_type(vtype: Any, load_raw: Any) -> Tuple[float, str, str]:
    """附件2 的 75 车型 → 因子表总质量档位；provisional，待文献表 8 定稿。"""
    t = str(vtype)
    for key, gvw, basis, note in REAL_GVW_RULES:
        if key in t:
            return gvw, basis, note
    try:
        val = float(load_raw)
    except (TypeError, ValueError):
        val = 12.0
    if not np.isfinite(val) or val <= 0:
        val = 12.0
    return val, "载质量", "按附件2 登记载重"


def _load_competition_workbooks() -> Tuple[pd.DataFrame, str, str]:
    """真实竞赛附件1/附件2：订单 + 运单 + 75 车型表 → 运单级原始表。

    数据事实（2026-10-18 实测）：
      - 订单 12,098 行；运单 36,329 行；订单/运单的 waybill_number 集合完全一致（直连 100%）；
      - 附件2 Sheet1 是 eclp↔tms 桥接，本副本里不是必需；仅作备份；
      - 去重键 = 运单+车牌+里程+封/解车时间 → 33,214 段 / 4,690,435.81 km；
      - 车型 68 种全部能在附件2 Sheet2 的 75 条里匹配到。
    """
    files = [p for p in sorted(RAW_DIR.glob("*"))
             if p.suffix.lower() in (".xlsx", ".xls")]
    f1_candidates = [p for p in files if "附件1" in p.name
                     or ("订单" in p.name and "运单" in p.name)]
    f2 = next((p for p in files if "附件2" in p.name or "运输车辆" in p.name), None)
    if not f1_candidates or not f2:
        raise NoRawData("未找到附件1/附件2 两个工作簿")

    # 若同时有原始附件（无“平均速度”列）和团队生成的平均速度副本，
    # 明确优先选择包含真实速度列的附件，避免仅依赖文件名排序导致输入不确定。
    candidates = []
    for candidate in f1_candidates:
        try:
            candidate_book = pd.read_excel(candidate, sheet_name=None)
            candidate_orders = next((df for df in candidate_book.values()
                                     if _find_col(df, "订单号") and _find_col(df, "运单号")), None)
            candidate_waybills = next((df for df in candidate_book.values()
                                       if _find_col(df, "实际行驶里程") and _find_col(df, "运单号")), None)
            if candidate_orders is None or candidate_waybills is None:
                continue
            candidate_speed_col = (_find_col(candidate_waybills, "平均速度")
                                   or _find_col(candidate_waybills, "speed_kmh"))
            score = (10 if candidate_speed_col else 0)
            score += 2 if ("平均数据" in candidate.name or "副本" in candidate.name) else 0
            # 稳定排序：分数优先；同分时按文件名顺序，确保每次选择一致。
            candidates.append((score, candidate.name, candidate, candidate_book,
                               candidate_orders, candidate_waybills, candidate_speed_col))
        except Exception:
            continue
    if not candidates:
        raise NoRawData("附件1 中未识别到同时包含订单号/运单号和里程的工作簿")
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    _score, _name, f1, book1, orders, waybills, selected_speed_col = candidates[0]

    book2 = pd.read_excel(f2, sheet_name=None)
    vehicles = next((df for df in book2.values()
                     if _find_col(df, "车型种类") and _find_col(df, "车辆载重")), None)
    if vehicles is None:
        raise NoRawData("附件2 的车型 Sheet 结构未识别")

    # ---- 订单：订单号 / 运单号 / 起终点城市 / 重量体积件数 ----
    oi = orders.index
    c_owb = _find_col(orders, "运单号")
    c_ono = _find_col(orders, "订单号")
    c_origin = _find_col(orders, "发货地市") or _find_col(orders, "shipper_city")
    c_dest = (_find_col(orders, "receiver_city") or _find_col(orders, "收货地市")
              or _find_col(orders, "收货地省"))
    c_weight = _find_col(orders, "订单重量")
    c_volume = _find_col(orders, "订单体积")
    c_pieces = _find_col(orders, "总包裹数")
    c_complete = _find_col(orders, "妥投时间")

    o = pd.DataFrame(index=oi)
    o["waybill_no"] = _as_str(orders[c_owb], oi)
    o["order_no"] = _as_str(orders[c_ono], oi) if c_ono else ""
    o["order_no_fallback"] = o["order_no"].eq("").astype(int)
    o["origin"] = _clean_city(orders[c_origin], oi) if c_origin else ""
    o["dest"] = _clean_city(orders[c_dest], oi) if c_dest else ""
    o["origin_site_fallback"] = o["origin"].eq("").astype(int)
    o["dest_site_fallback"] = o["dest"].eq("").astype(int)
    o["cargo_weight_kg"] = pd.to_numeric(orders[c_weight], errors="coerce") if c_weight else np.nan
    o["cargo_volume_cm3"] = pd.to_numeric(orders[c_volume], errors="coerce") if c_volume else np.nan
    o["cargo_pieces"] = pd.to_numeric(orders[c_pieces], errors="coerce") if c_pieces else np.nan
    o["cargo_weight_missing"] = o["cargo_weight_kg"].isna().astype(int)
    o["cargo_volume_missing"] = o["cargo_volume_cm3"].isna().astype(int)
    o["cargo_pieces_missing"] = o["cargo_pieces"].isna().astype(int)
    o["complete_ts"] = _to_datetime(orders[c_complete] if c_complete else None, oi)
    # 运单号只作为缺失订单号的追踪代理；原始缺失状态由 order_no_fallback 保留。
    o["order_no"] = o["order_no"].where(o["order_no"] != "", o["waybill_no"])
    o = o[o["waybill_no"] != ""].drop_duplicates("waybill_no", keep="first")

    # ---- 运单：单号 / 车牌 / 车型 / 里程 / 封解车时间 / 网点 / 速度 ----
    wi = waybills.index
    c_wb = _find_col(waybills, "waybill_number") or _find_col(waybills, "运单号")
    c_plate = _find_col(waybills, "vehicle_number") or _find_col(waybills, "车牌号")
    c_vtype = _find_col(waybills, "车型种类名称") or _find_col(waybills, "vehicle_type_name")
    c_vcode = _find_col(waybills, "车型种类编码")
    c_dist = _find_col(waybills, "实际行驶里程") or _find_col(waybills, "里程")
    c_dep = _find_col(waybills, "封车时间") or _find_col(waybills, "departure_time")
    c_arr = _find_col(waybills, "解封车时间") or _find_col(waybills, "arrival_time")
    c_dep_site = _find_col(waybills, "封车网点")
    c_arr_site = _find_col(waybills, "解封车网点")
    c_speed = (_find_col(waybills, "平均速度")
               or _find_col(waybills, "speed_kmh")
               or _find_col(waybills, "speed"))

    w = pd.DataFrame(index=wi)
    w["source_waybill_row"] = pd.to_numeric(pd.Series(wi, index=wi), errors="coerce").fillna(-1).astype(int) + 2
    w["waybill_no"] = _as_str(waybills[c_wb], wi)
    w["plate"] = _as_str(waybills[c_plate], wi) if c_plate else ""
    w["vehicle_type"] = _as_str(waybills[c_vtype], wi) if c_vtype else "未知车型"
    w["vtype_code"] = (pd.to_numeric(waybills[c_vcode], errors="coerce")
                       if c_vcode else 0)
    w["dist_km"] = pd.to_numeric(waybills[c_dist], errors="coerce") if c_dist else np.nan
    w["depart_ts"] = _to_datetime(waybills[c_dep] if c_dep else None, wi)
    w["arrive_ts"] = _to_datetime(waybills[c_arr] if c_arr else None, wi)
    w["dep_site"] = _as_str(waybills[c_dep_site], wi) if c_dep_site else ""
    w["arr_site"] = _as_str(waybills[c_arr_site], wi) if c_arr_site else ""
    w["speed_kmh"] = (pd.to_numeric(waybills[c_speed], errors="coerce")
                      if c_speed else np.nan)

    # 订单 → 运单：本附件版本的 waybill_number 集合可直接匹配；
    # 关联命中状态必须在回填 order_no 之前记录，不能用非空 order_no 反推匹配成功。
    order_keys = set(o["waybill_no"].astype(str))
    w["order_matched"] = w["waybill_no"].astype(str).isin(order_keys).astype(int)
    order_match = float(w["order_matched"].mean()) if len(w) else 0.0
    w = w.merge(o, on="waybill_no", how="left")

    # ---- 75 车型表：燃油类型 + 登记载重 → 因子表总质量档位 ----
    vi = vehicles.index
    c_vname = _find_col(vehicles, "车型种类")
    c_fuel = _find_col(vehicles, "燃油类型")
    c_load = _find_col(vehicles, "车辆载重")
    v = pd.DataFrame(index=vi)
    v["vehicle_type"] = _as_str(vehicles[c_vname], vi)
    v["fuel_type"] = _as_str(vehicles[c_fuel], vi) if c_fuel else "柴油"
    v["load_raw_t"] = pd.to_numeric(vehicles[c_load], errors="coerce") if c_load else np.nan
    v = v[(v["vehicle_type"] != "")].drop_duplicates("vehicle_type", keep="first")
    vehicle_keys = set(v["vehicle_type"].astype(str))
    w["vehicle_matched"] = w["vehicle_type"].astype(str).isin(vehicle_keys).astype(int)
    w = w.merge(v, on="vehicle_type", how="left")

    gvw: List[float] = []
    basis: List[str] = []
    vcls: List[int] = []
    mapping_method: List[str] = []
    for vtype, raw_load in zip(w["vehicle_type"].astype(str), w["load_raw_t"]):
        profile = VEHICLE_LOAD_CLASS_MAP.get(str(vtype))
        if profile is not None:
            load, cls = profile
            mapping_method.append("baseline_class_map")
            if "7.6" in str(vtype) and "冷藏" not in str(vtype):
                b = "整车总质量"
            elif "牵引" in str(vtype):
                b = "准牵引总质量"
            else:
                b = "载质量"
        else:
            load, b, _note = _gvw_for_type(vtype, raw_load)
            cls = 1 if load <= 5 else (2 if load <= 20 else 3)
            mapping_method.append("fallback_rule")
        gvw.append(load)
        basis.append(b)
        vcls.append(cls)
    w["load_t"] = gvw
    w["mass_basis"] = basis
    w["vclass"] = vcls
    w["load_mapping_method"] = mapping_method
    w["flag_load_mapping_fallback"] = (w["load_mapping_method"] != "baseline_class_map").astype(int)
    # “车型已在 Sheet2 找到”与“载重字段非空”是两件事，不能把后者当车型匹配标志。
    w["vehicle_known"] = w["vehicle_matched"].astype(int)
    w["fuel_type"] = w["fuel_type"].fillna("柴油").replace({"": "柴油"})

    # 订单城市优先；订单缺城市时用网点名前缀兜底。源城市缺失标志保留在宽表中。
    for flag_col in ("order_no_fallback", "cargo_weight_missing", "cargo_volume_missing",
                     "cargo_pieces_missing", "origin_site_fallback", "dest_site_fallback"):
        if flag_col in w.columns:
            w[flag_col] = pd.to_numeric(w[flag_col], errors="coerce").fillna(0).astype(int)
        else:
            w[flag_col] = 0
    city_names = sorted(CITY, key=len, reverse=True)
    for side, site_col in (("origin", "dep_site"), ("dest", "arr_site")):
        w[side] = w[side].fillna("").astype(str)
        miss = w[side] == ""
        if miss.any():
            w.loc[miss, side] = w.loc[miss, site_col].map(
                lambda s: _site_city(s, city_names))

    for col, default in (("order_no", ""), ("cargo_weight_kg", 0.0),
                         ("cargo_volume_cm3", 0.0), ("cargo_pieces", 0.0)):
        if col in w.columns:
            w[col] = w[col].fillna(default)
    w["data_source"] = "real"

    speed_provenance = ("速度取自列“%s”" % selected_speed_col
                        if selected_speed_col else "源文件无平均速度列，按里程/正时长反算速度")
    reason = ("读取 %s / %s（订单 %d 行，运单 %d 行，车型表 %d 条；订单直连率 %.1f%%；%s）"
              % (f1.name, f2.name, len(orders), len(waybills), len(v), order_match * 100,
                 speed_provenance))
    return w.reset_index(drop=True), "real", reason


def _load_raw() -> Tuple[pd.DataFrame, str, str]:
    """读 data/raw：优先按竞赛附件1/附件2 的表结构接，其次通用别名，再回退演示。"""
    if not RAW_DIR.exists():
        raise NoRawData("data/raw 目录不存在")

    try:
        return _load_competition_workbooks()
    except NoRawData:
        pass
    except Exception as exc:
        print("  [P1] 竞赛附件读取失败（%s），尝试通用读取" % type(exc).__name__)

    candidates: List[Path] = []
    for stem in ("waybills", "waybill", "运单", "orders"):
        for ext in (".parquet", ".csv", ".xlsx", ".xls"):
            p = RAW_DIR / (stem + ext)
            if p.exists():
                candidates.append(p)
    if not candidates:
        candidates = [
            p for p in sorted(RAW_DIR.iterdir())
            if p.is_file() and p.suffix.lower() in (".parquet", ".csv", ".xlsx", ".xls")
        ]
    if not candidates:
        raise NoRawData("data/raw 没有 .parquet/.csv/.xlsx 文件")

    best: Optional[pd.DataFrame] = None
    best_score = -1.0
    best_meta = ""
    errors: List[str] = []
    for path in candidates[:3]:
        try:
            for sheet_name, table in _read_tables(path):
                score = _table_score(table)
                if score > best_score:
                    best, best_score = table, score
                    best_meta = path.name if sheet_name == path.name else path.name + " / " + sheet_name
        except Exception as exc:  # 单个文件坏掉不拖垮整条链
            errors.append(path.name + ": " + type(exc).__name__)
            continue

    if best is None or best_score < 0:
        detail = ("；".join(errors) if errors else "未识别到 waybill_no + 里程/起终点 列")
        raise NoRawData("原始文件列名未识别（" + detail + "）")

    raw = best.reset_index(drop=True)
    raw["data_source"] = "real"
    return raw, "real", "读取 %s（%d 行）" % (best_meta, len(raw))


# ---------------------------------------------------------------------------
# P1b · 演示数据（真数据没到时的静态兜底，确定性生成）
# ---------------------------------------------------------------------------

def _make_demo_raw() -> pd.DataFrame:
    rng = np.random.default_rng(DEMO_SEED)
    # 演示只用最初 30 个枢纽城市；CITY 后来补的收货城市只服务真实地图。
    names = list(CITY)[:30]
    coords = np.array([CITY[n] for n in names], dtype=float)
    weights = np.array(
        [14, 8, 9, 7, 6, 4, 3, 3, 2, 3, 2, 3, 3, 2, 2, 2, 2, 2,
         1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1], dtype=float)
    weights = weights / weights.sum()
    n = DEMO_ROWS

    # 70% 走邻省线路、30% 走全国长线，保证既像区域配送又有长途热点
    neighbors = []
    for i in range(len(names)):
        d = np.sqrt((coords[:, 0] - coords[i, 0]) ** 2 + (coords[:, 1] - coords[i, 1]) ** 2)
        neighbors.append(np.argsort(d)[1:7])

    oi = rng.choice(len(names), size=n, p=weights)
    di = rng.choice(len(names), size=n, p=weights)
    near_mask = rng.random(n) < 0.70
    for i in np.where(near_mask)[0]:
        di[i] = int(rng.choice(neighbors[int(oi[i])]))
    far_idx = np.where(~near_mask)[0]
    for i in far_idx:
        for _ in range(8):
            if di[i] != oi[i]:
                break
            di[i] = int(rng.choice(len(names), p=weights))

    veh_idx = rng.choice(len(VEHICLES), size=n, p=[0.44, 0.10, 0.26, 0.08, 0.07, 0.05])
    vehicle_type = np.array([VEHICLES[i][0] for i in veh_idx], dtype=object)
    vclass = np.array([VEHICLES[i][1] for i in veh_idx], dtype=int)
    rated = np.array([VEHICLES[i][2] for i in veh_idx], dtype=float)
    basis = np.array([VEHICLES[i][3] for i in veh_idx], dtype=object)
    fuel = np.array([VEHICLES[i][5] for i in veh_idx], dtype=object)

    road = _haversine(coords[oi, 0], coords[oi, 1], coords[di, 0], coords[di, 1])
    road = np.clip(road * rng.uniform(1.18, 1.42, n), 5.0, None)

    month = rng.choice([1, 2, 11, 12], size=n, p=[0.16, 0.46, 0.16, 0.22])
    day = rng.integers(1, 29, n)
    hour = rng.integers(0, 24, n)
    minute = rng.integers(0, 60, n)
    depart = pd.to_datetime(pd.DataFrame({
        "year": 2023, "month": month, "day": day,
        "hour": hour, "minute": minute,
    }))

    speed = np.clip(rng.lognormal(np.log(52.0), 0.30, n), 15.0, 95.0)
    duration = road / speed
    arrive = depart + pd.to_timedelta(duration, unit="h")

    rate = np.where(vclass == 1, 4.2, np.where(vclass == 2, 5.4, 6.3))
    invoice_amount = road * rate * rng.uniform(0.90, 1.10, n)
    invoice_no = np.array(["FP%012d" % int(x) for x in rng.integers(0, 10 ** 12, n)], dtype=object)

    plate = np.array(
        ["沪" + "ABCDEFG"[int(c)] + "%04d" % int(k)
         for c, k in zip(rng.integers(0, 7, n), rng.integers(1, 9999, n))],
        dtype=object)
    waybill_no = np.array(["JDVB2023%07d" % i for i in range(n)], dtype=object)
    order_no = np.array(["ORD2023%07d" % i for i in range(n)], dtype=object)

    df = pd.DataFrame({
        "waybill_no": waybill_no,
        "order_no": order_no,
        "plate": plate,
        "vehicle_type": vehicle_type,
        "vclass": vclass,
        "load_t": rated,
        "load_raw_t": rated,
        "mass_basis": basis,
        "origin": np.array([names[i] for i in oi], dtype=object),
        "dest": np.array([names[i] for i in di], dtype=object),
        "origin_lat": coords[oi, 0], "origin_lon": coords[oi, 1],
        "dest_lat": coords[di, 0], "dest_lon": coords[di, 1],
        "dist_km": np.round(road, 2),
        "month": month,
        "depart_ts": depart,
        "arrive_ts": arrive,
        "speed_kmh": np.round(speed, 2),
        "invoice_no": invoice_no,
        "invoice_ok": 1,
        "invoice_amount": np.round(invoice_amount, 2),
        "fuel_type": fuel,
    })

    # 7.6 m 的登记载重是“整车总质量”，折算成载质量（12.0 t）
    mask76 = df["vehicle_type"] == "7.6米厢式货车"
    df.loc[mask76, "load_raw_t"] = 14.4

    # ---- 注入真实世界会遇到的脏数据（只用于演示，不影响真数据路径）----
    mask = rng.random(n) < 0.010
    df.loc[mask, "dist_km"] = np.nan

    mask = rng.random(n) < 0.008
    df.loc[mask, "vehicle_type"] = "未知车型"
    df.loc[mask, "vclass"] = np.nan

    mask = rng.random(n) < 0.005
    df.loc[mask, "load_t"] = np.nan
    df.loc[mask, "load_raw_t"] = np.nan

    mask = rng.random(n) < 0.020
    df.loc[mask, "invoice_ok"] = 0
    df.loc[mask, "invoice_no"] = ""
    df.loc[mask, "invoice_amount"] = np.nan

    mask = rng.random(n) < 0.039
    bad_speed = rng.uniform(101.0, 160.0, int(mask.sum()))
    df.loc[mask, "speed_kmh"] = np.round(bad_speed, 2)
    safe_dist = df.loc[mask, "dist_km"].fillna(500.0)
    df.loc[mask, "arrive_ts"] = df.loc[mask, "depart_ts"] + pd.to_timedelta(
        safe_dist.to_numpy() / bad_speed, unit="h")

    # 一条负时长：时间戳异常的真实形态
    df.loc[0, "arrive_ts"] = df.loc[0, "depart_ts"] - pd.Timedelta(hours=2)
    df.loc[0, "speed_kmh"] = np.nan

    # 3% 重复行：同 运单+车牌+里程+起点+终点
    dup = df.sample(frac=0.030, random_state=DEMO_SEED + 1).copy()
    df = pd.concat([df, dup], ignore_index=True)
    df = df.sample(frac=1.0, random_state=DEMO_SEED + 2).reset_index(drop=True)
    df["data_source"] = "demo"
    return df


# ---------------------------------------------------------------------------
# P2/P3/P4 · 清洗、关联、标记
# ---------------------------------------------------------------------------

def _segment_dedupe_key(frame: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """返回段级去重键和键完整标志。

    规则与 docs/21 一致：运单 + 车牌 + 里程(2 位小数) + 封/解车时间(秒)；
    若时间不完整，退回起终点。缺运单号或车牌的记录不自动判重复，避免把不确定记录误合并。
    """
    idx = frame.index
    waybill = _as_str(frame["waybill_no"] if "waybill_no" in frame.columns else None, idx)
    plate = _as_str(frame["plate"] if "plate" in frame.columns else None, idx)
    origin = _as_str(frame["origin"] if "origin" in frame.columns else None, idx)
    dest = _as_str(frame["dest"] if "dest" in frame.columns else None, idx)
    dist = _num_col(frame, "dist_km").round(2).astype(str)
    depart = _to_datetime(frame["depart_ts"] if "depart_ts" in frame.columns else None, idx)
    arrive = _to_datetime(frame["arrive_ts"] if "arrive_ts" in frame.columns else None, idx)
    time_key = (depart.dt.strftime("%Y%m%d%H%M%S").fillna("")
                + "|" + arrive.dt.strftime("%Y%m%d%H%M%S").fillna(""))
    place_key = origin.astype(str) + "|" + dest.astype(str)
    has_time = depart.notna() & arrive.notna()
    row_key = pd.Series(np.where(has_time, time_key, place_key), index=idx).astype(str)
    key = waybill.astype(str) + "|" + plate.astype(str) + "|" + dist + "|" + row_key
    valid = waybill.ne("") & plate.ne("")
    return key, valid


def _duplicate_conflict_summary(wide: pd.DataFrame) -> Dict[str, int]:
    """统计重复路段组的冲突类型；仅输出计数，不改写/挑选原始值。"""
    key, valid = _segment_dedupe_key(wide)
    frame = wide.loc[valid].copy()
    if frame.empty:
        return {
            "duplicate_groups": 0,
            "vehicle_name_conflict_groups": 0,
            "vehicle_code_conflict_groups": 0,
            "emission_factor_input_conflict_groups": 0,
            "fuel_type_conflict_groups": 0,
            "mass_basis_conflict_groups": 0,
        }
    frame["_segment_key"] = key.loc[valid].astype(str)
    sizes = frame.groupby("_segment_key", dropna=False).size()
    repeat_keys = sizes.index[sizes > 1]
    rep = frame[frame["_segment_key"].isin(repeat_keys)]
    if rep.empty:
        return {
            "duplicate_groups": 0,
            "vehicle_name_conflict_groups": 0,
            "vehicle_code_conflict_groups": 0,
            "emission_factor_input_conflict_groups": 0,
            "fuel_type_conflict_groups": 0,
            "mass_basis_conflict_groups": 0,
        }
    grouped = rep.groupby("_segment_key", dropna=False)

    def conflict_count(columns: List[str]) -> int:
        available = [c for c in columns if c in rep.columns]
        if not available:
            return 0
        unique = grouped[available].nunique(dropna=False)
        return int(unique.gt(1).any(axis=1).sum())

    return {
        "duplicate_groups": int(len(repeat_keys)),
        "vehicle_name_conflict_groups": conflict_count(["vehicle_type"]),
        "vehicle_code_conflict_groups": conflict_count(["vtype_code"]),
        # 当前 optimizer 的法定因子查表输入是 (vclass, load_t)；燃料类型单独统计，避免混口径。
        "emission_factor_input_conflict_groups": conflict_count(["vclass", "load_t"]),
        "fuel_type_conflict_groups": conflict_count(["fuel_type"]),
        "mass_basis_conflict_groups": conflict_count(["mass_basis"]),
    }

def _duplicate_conflict_row_flags(frame: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """逐行标记重复路段组中的车型描述冲突与排放因子输入冲突。

    flag_vehicle_conflict：同一段级键组内车型名称/编码、燃料类型、载重口径或载重映射不一致。
    flag_factor_conflict：当前排放因子查表输入 (vclass, load_t) 不一致。
    组内所有行都会标记，包括基准去重时保留的代表行，避免冲突只停留在汇总报告中。
    """
    key, valid = _segment_dedupe_key(frame)
    subset = frame.loc[valid].copy()
    if subset.empty:
        return (pd.Series(0, index=frame.index, dtype=int),
                pd.Series(0, index=frame.index, dtype=int))
    subset["_segment_key"] = key.loc[valid].astype(str)
    grouped = subset.groupby("_segment_key", sort=False, dropna=False)
    sizes = grouped.size()
    repeated_keys = set(sizes[sizes > 1].index.astype(str))
    vehicle_cols = [c for c in ("vehicle_type", "vtype_code", "fuel_type", "load_raw_t",
                                "load_t", "mass_basis", "vclass") if c in subset.columns]
    factor_cols = [c for c in ("vclass", "load_t") if c in subset.columns]

    vehicle_keys: set[str] = set()
    for col in vehicle_cols:
        varying = grouped[col].nunique(dropna=False)
        vehicle_keys.update(str(k) for k in varying[varying > 1].index if str(k) in repeated_keys)

    factor_keys: set[str] = set()
    for col in factor_cols:
        varying = grouped[col].nunique(dropna=False)
        factor_keys.update(str(k) for k in varying[varying > 1].index if str(k) in repeated_keys)

    vehicle_flags = (valid & key.astype(str).isin(vehicle_keys)).astype(int)
    factor_flags = (valid & key.astype(str).isin(factor_keys)).astype(int)
    return vehicle_flags, factor_flags


def _add_note(notes: pd.Series, mask: Any, text: str) -> pd.Series:
    try:
        m = pd.Series(mask, index=notes.index).fillna(False).astype(bool).to_numpy()
    except Exception:
        return notes
    return notes + np.where(m, text + "；", "")


def clean_frame(raw: pd.DataFrame, source: str = "unknown") -> pd.DataFrame:
    """把任意“脏”原始表洗成宽表；所有缺失都填补 + 打标，不抛异常。"""
    work = pd.DataFrame(index=raw.index)

    # ---- 字符串 / 数值 / 时间基础列 ----
    for col in ("waybill_no", "order_no", "plate", "vehicle_type",
                "mass_basis", "origin", "dest", "invoice_no", "dep_site", "arr_site"):
        work[col] = _as_str(raw[col] if col in raw.columns else None, raw.index)

    work["vehicle_type"] = work["vehicle_type"].replace({"": "未知车型"})
    work["mass_basis"] = work["mass_basis"].replace({"": ""})

    work["vclass"] = _num_col(raw, "vclass")
    work["load_t"] = _num_col(raw, "load_t")
    work["load_raw_t"] = _num_col(raw, "load_raw_t")
    if "load_mapping_method" in raw.columns:
        work["load_mapping_method"] = _as_str(raw["load_mapping_method"], raw.index).replace({"": "unknown"})
    else:
        work["load_mapping_method"] = "not_provided"
    if "flag_load_mapping_fallback" in raw.columns:
        work["flag_load_mapping_fallback"] = pd.to_numeric(raw["flag_load_mapping_fallback"], errors="coerce").fillna(0).astype(int)
    else:
        work["flag_load_mapping_fallback"] = 0
    work["dist_km"] = _num_col(raw, "dist_km").where(lambda s: s > 0)
    work["month"] = _num_col(raw, "month")
    work["speed_kmh"] = _num_col(raw, "speed_kmh").where(lambda s: s > 0)
    work["invoice_ok"] = _num_col(raw, "invoice_ok")
    work["invoice_amount"] = _num_col(raw, "invoice_amount").where(lambda s: s >= 0)

    work["vtype_code"] = _num_col(raw, "vtype_code")
    work["source_waybill_row"] = _num_col(raw, "source_waybill_row").fillna(0).astype(int)
    if "order_matched" in raw.columns:
        work["order_matched"] = pd.to_numeric(raw["order_matched"], errors="coerce").fillna(0).astype(int)
    else:
        # 通用/演示输入没有专门匹配标志时，只做保守代理；竞赛附件路径会提供真实关联标志。
        work["order_matched"] = (work["order_no"].astype(str).str.strip() != "").astype(int)
    if "vehicle_matched" in raw.columns:
        work["vehicle_matched"] = pd.to_numeric(raw["vehicle_matched"], errors="coerce").fillna(0).astype(int)
    elif "vehicle_known" in raw.columns:
        work["vehicle_matched"] = pd.to_numeric(raw["vehicle_known"], errors="coerce").fillna(0).astype(int)
    else:
        work["vehicle_matched"] = work["vehicle_type"].isin(VEHICLE_NAMES).astype(int)
    work["cargo_weight_kg"] = _num_col(raw, "cargo_weight_kg").fillna(0.0)
    work["cargo_volume_cm3"] = _num_col(raw, "cargo_volume_cm3").fillna(0.0)
    work["cargo_pieces"] = _num_col(raw, "cargo_pieces").fillna(0.0)
    for flag_col in ("order_no_fallback", "cargo_weight_missing", "cargo_volume_missing",
                     "cargo_pieces_missing", "origin_site_fallback", "dest_site_fallback"):
        work[flag_col] = pd.to_numeric(raw[flag_col], errors="coerce").fillna(0).astype(int) if flag_col in raw.columns else 0

    work["depart_ts"] = _to_datetime(raw["depart_ts"] if "depart_ts" in raw.columns else None, raw.index)
    work["arrive_ts"] = _to_datetime(raw["arrive_ts"] if "arrive_ts" in raw.columns else None, raw.index)
    work["complete_ts"] = _to_datetime(
        raw["complete_ts"] if "complete_ts" in raw.columns else None, raw.index)

    # 坐标：原始优先，缺了按城市名查表
    city = {k: v for k, v in CITY.items()}
    for side in ("origin", "dest"):
        lat_col, lon_col = side + "_lat", side + "_lon"
        lat = _num_col(raw, lat_col) if lat_col in raw.columns else pd.Series(np.nan, index=raw.index)
        lon = _num_col(raw, lon_col) if lon_col in raw.columns else pd.Series(np.nan, index=raw.index)
        work[lat_col] = lat.fillna(work[side].map(lambda c: city.get(c, (np.nan, np.nan))[0]))
        work[lon_col] = lon.fillna(work[side].map(lambda c: city.get(c, (np.nan, np.nan))[1]))

    # 起终点缺失时，用封/解车网点名前缀兜底（真实数据没有城市列时）
    city_names = sorted(CITY, key=len, reverse=True)
    for side, site_col in (("origin", "dep_site"), ("dest", "arr_site")):
        miss = work[side] == ""
        if miss.any():
            work.loc[miss, side] = work.loc[miss, site_col].map(
                lambda s: _site_city(s, city_names))

    # ---- 线路键 ----
    work["od_pair"] = np.where(
        (work["origin"] != "") & (work["dest"] != ""),
        work["origin"].astype(str) + "|" + work["dest"].astype(str), "")
    a = np.minimum(work["origin"].astype(str), work["dest"].astype(str))
    b = np.maximum(work["origin"].astype(str), work["dest"].astype(str))
    work["od_key"] = np.where((work["origin"] != "") & (work["dest"] != ""), a + "|" + b, "")
    # 同线带宽用“起点网点→终点网点”（3000+ 条），比城市级 OD 细；缺网点时退回城市 OD
    work["od_site"] = np.where(
        (work["dep_site"] != "") & (work["arr_site"] != ""),
        work["dep_site"].astype(str) + "|" + work["arr_site"].astype(str),
        work["od_key"])

    # ---- 里程：原值 → 同线历史均值 → 绕行系数 → 全样本兜底 ----
    raw_dist = work["dist_km"].copy()
    dist_source = pd.Series("od", index=work.index, dtype=object)
    dist_source[raw_dist.isna()] = "missing"

    od_mean = work["dist_km"].groupby(work["od_key"]).transform("mean")
    fill = od_mean.where(work["od_key"] != "")
    m = work["dist_km"].isna() & fill.notna()
    work.loc[m, "dist_km"] = fill[m]
    dist_source[m] = "od_mean"

    m = work["dist_km"].isna()
    hav = _haversine(work["origin_lat"], work["origin_lon"],
                     work["dest_lat"], work["dest_lon"]) * INDUSTRY_ROAD_FACTOR
    hav = pd.Series(np.where(np.isfinite(hav) & (hav > 0), hav, np.nan), index=work.index)
    work.loc[m, "dist_km"] = hav[m]
    dist_source[m & hav.notna()] = "haversine"

    m = work["dist_km"].isna() | (work["dist_km"] <= 0)
    work.loc[m, "dist_km"] = DEFAULT_DIST_KM
    dist_source[m] = "global_median"
    work["dist_km"] = work["dist_km"].clip(lower=0.1)
    work["dist_source"] = dist_source

    # ---- 车型 / 载重：车型未知按载重推断；载重缺失按车型额定值/同类别中位 ----
    known_vehicle = work["vehicle_matched"].fillna(0).astype(bool)

    if "fuel_type" in raw.columns:
        work["fuel_type"] = _as_str(raw["fuel_type"], raw.index).replace({"": "柴油"})
    else:
        work["fuel_type"] = work["vehicle_type"].map(VEHICLE_FUEL).fillna("柴油")

    rated = work["vehicle_type"].map(RATED_LOAD)
    vc_guess = np.select(
        [work["load_t"] <= 5, work["load_t"] <= 20, work["load_t"] > 20],
        [1, 2, 3], default=np.nan)
    work["vclass"] = work["vclass"].where(work["vclass"].isin([1, 2, 3]))
    work["vclass"] = work["vclass"].fillna(pd.Series(vc_guess, index=work.index))

    inferred_vehicle = ~known_vehicle
    rep = work["vclass"].map(CLASS_REP)
    work.loc[inferred_vehicle, "vehicle_type"] = rep[inferred_vehicle].fillna(CLASS_REP[2])
    work["vclass"] = work["vclass"].fillna(work["vehicle_type"].map(VEHICLE_CLASS)).fillna(2).astype(int)
    work.loc[inferred_vehicle, "fuel_type"] = (
        work.loc[inferred_vehicle, "vehicle_type"].map(VEHICLE_FUEL).fillna("柴油"))
    rated = work["vehicle_type"].map(RATED_LOAD)

    load_missing_raw = work["load_t"].isna() | (work["load_t"] <= 0)
    work["load_t"] = work["load_t"].where(work["load_t"] > 0)
    # 有车型登记表就用额定载重——这是“匹配”，不是估算，不打 flag_estimated
    work["load_t"] = work["load_t"].fillna(rated)
    raw_load = work["load_raw_t"].where(work["load_raw_t"] > 0)
    load_raw_original = raw_load.copy()
    work["load_t"] = work["load_t"].fillna(raw_load)
    load_still_missing = work["load_t"].isna()
    work["load_t"] = work["load_t"].fillna(work["load_t"].groupby(work["vclass"]).transform("median"))
    work["load_t"] = work["load_t"].fillna(INDUSTRY_LOAD_T).clip(lower=1.0, upper=60.0)
    work["load_raw_t"] = raw_load.fillna(work["load_t"])

    # 载重口径：原始表没给就按车型登记表回填（这正是“匹配 Sheet2”的意义）
    basis_known = work["mass_basis"].isin(["载质量", "整车总质量", "准牵引总质量"])
    basis_from_vehicle = work["vehicle_type"].map(VEHICLE_BASIS).fillna("载质量")
    work["mass_basis"] = work["mass_basis"].where(basis_known, basis_from_vehicle)
    work["mass_basis"] = work["mass_basis"].where(
        work["mass_basis"].isin(["载质量", "整车总质量", "准牵引总质量"]), "载质量")
    work["flag_basis"] = (work["mass_basis"] != "载质量").astype(int)
    work["basis_note"] = np.where(
        work["flag_basis"] == 1,
        np.where(
            load_raw_original.notna(),
            "载重口径：" + work["mass_basis"].astype(str) + " "
            + load_raw_original.round(2).astype(str) + " → 载质量 "
            + work["load_t"].round(2).astype(str),
            "载重口径：" + work["mass_basis"].astype(str)
            + "；按载质量 " + work["load_t"].round(2).astype(str) + " t 计（登记表）",
        ),
        "")

    # ---- 时长 / 速度 ----
    # 明确记录速度来源：原始速度列、里程/时长反算、行业均速假设或时间异常剔除。
    duration = (work["arrive_ts"] - work["depart_ts"]).dt.total_seconds() / 3600.0
    duration = duration.where(np.isfinite(duration))
    bad_time = (duration.notna() & (duration <= 0)) | (
        work["arrive_ts"].notna() & work["depart_ts"].notna()
        & (work["arrive_ts"] < work["depart_ts"]))
    bad_time = bad_time.fillna(False)

    speed = work["speed_kmh"].copy()
    has_source_speed = speed.notna() & np.isfinite(speed) & (speed > 0)
    speed_method = pd.Series("unavailable", index=work.index, dtype=object)
    speed_method[has_source_speed] = "source_speed_column"

    calc_speed = work["dist_km"] / duration.where(duration > 0)
    calc_speed_used = (~has_source_speed) & (~bad_time) & duration.gt(0) & calc_speed.notna() & (calc_speed > 0)
    speed = speed.where(has_source_speed, calc_speed)
    speed_method[calc_speed_used] = "distance_over_duration"

    duration = duration.where(duration > 0)
    duration_from_speed = duration.isna() & speed.notna() & (speed > 0)
    duration = duration.fillna(work["dist_km"] / speed)
    duration_defaulted = duration.isna()
    duration = duration.fillna(work["dist_km"] / INDUSTRY_SPEED_KMH)

    # 负时长：速度不可信，保留 NaN（有效速度样本会少 1 条），时长按行业均速。
    speed = speed.mask(bad_time, np.nan)
    duration = duration.mask(bad_time, work["dist_km"] / INDUSTRY_SPEED_KMH)
    speed_method[bad_time] = "invalid_time_excluded"

    speed_still_missing = speed.isna() & ~bad_time
    calc_speed2 = work["dist_km"] / duration
    calculated_after_fill = speed_still_missing & ~duration_defaulted & calc_speed2.notna() & (calc_speed2 > 0)
    speed = speed.where(~speed_still_missing, calc_speed2)
    speed_method[calculated_after_fill] = "distance_over_duration"
    no_speed_info = speed.isna() & ~bad_time
    speed = speed.where(~no_speed_info, INDUSTRY_SPEED_KMH)
    speed_method[no_speed_info] = "industry_speed_50_assumption"
    # duration_defaulted 但 speed 仍为空时，速度兜底来自行业均速而非真实测量。
    speed = speed.where(np.isfinite(speed) & (speed > 0))
    speed_method[(speed.notna()) & (speed_method == "unavailable")] = "industry_speed_50_assumption"

    work["duration_h"] = duration.round(4)
    work["speed_kmh"] = speed.round(3)
    work["speed_method"] = speed_method
    bad_speed = work["speed_kmh"].notna() & (
        (work["speed_kmh"] > 100) | (work["speed_kmh"] < 1))
    work["flag_anomaly"] = (bad_time | bad_speed).astype(int)

    # ---- 月份 ----
    month = work["month"].where(work["month"].isin([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]))
    month = month.fillna(work["depart_ts"].dt.month.astype(float))
    month = month.fillna(1.0).astype(int)
    work["month"] = month

    # ---- 发票：只有原始输入确实包含发票字段时，才估算“未匹配发票”；
    # 竞赛附件没有发票字段时必须保持 NaN / -1，不能凭空生成金额。
    invoice_source = "invoice_amount" in raw.columns or "invoice_ok" in raw.columns
    if invoice_source:
        if work["invoice_ok"].isna().all():
            work["invoice_ok"] = work["invoice_amount"].notna().astype(int)
        work["invoice_ok"] = work["invoice_ok"].fillna(0).astype(int)
        rate = work["invoice_amount"] / work["dist_km"]
        rate_fill = rate.groupby(work["vclass"]).transform("median").fillna(5.0)
        invoice_missing = work["invoice_amount"].isna() | (work["invoice_ok"] == 0)
        work.loc[invoice_missing, "invoice_amount"] = (
            work.loc[invoice_missing, "dist_km"] * rate_fill[invoice_missing]).round(2)
    else:
        work["invoice_ok"] = -1
        work["invoice_amount"] = np.nan

    # ---- 重复行：保留在宽表，基准核算剔除 ----
    # 真实数据实测：运单+车牌+里程+封/解车时间 → 33,214 段 / 4,690,435.81 km；
    # 只用车牌+里程+起终点会多保留 33 行（4,690,751.88 km），对拍不过。
    key, valid_key = _segment_dedupe_key(work)
    vehicle_conflict_flags, factor_conflict_flags = _duplicate_conflict_row_flags(work)
    work["flag_vehicle_conflict"] = vehicle_conflict_flags
    work["flag_factor_conflict"] = factor_conflict_flags
    dup = pd.Series(False, index=work.index)
    dup.loc[valid_key] = key.loc[valid_key].duplicated(keep="first")
    work["flag_dup"] = dup.astype(int)

    # ---- 估算原因（只写真正被填补过的行）----
    notes = pd.Series("", index=work.index, dtype=object)
    notes = _add_note(notes, work["dist_source"] == "od_mean", "里程缺失→同线历史均值")
    notes = _add_note(notes, work["dist_source"] == "haversine", "里程缺失→行业绕行系数估算")
    notes = _add_note(notes, work["dist_source"] == "global_median", "里程缺失→全样本中位数")
    notes = _add_note(notes, inferred_vehicle, "车型未匹配→按载重推断")
    notes = _add_note(notes, work["flag_load_mapping_fallback"] == 1,
                      "车型未命中62类基准映射→使用补充车型规则/目录载重兜底")
    notes = _add_note(notes, load_still_missing, "载重缺失→按同类别中位/行业均值填补")
    notes = _add_note(notes, work["invoice_ok"] == 0, "发票未匹配→按同类别历史均价估算")
    notes = _add_note(notes, bad_time, "时间戳异常→按行业均速 50 km/h 估算")
    notes = _add_note(notes, no_speed_info, "速度缺失→行业均速填补")
    notes = _add_note(notes, work["order_no_fallback"] == 1, "源订单号缺失→以运单号作为追踪代理")
    notes = _add_note(notes, work["cargo_weight_missing"] == 1, "订单重量缺失→数值列暂填 0，分析时须按缺失处理")
    notes = _add_note(notes, work["cargo_volume_missing"] == 1, "订单体积缺失→数值列暂填 0，分析时须按缺失处理")
    notes = _add_note(notes, work["cargo_pieces_missing"] == 1, "包裹数缺失→数值列暂填 0，分析时须按缺失处理")
    notes = _add_note(notes, (work["origin_site_fallback"] == 1) | (work["dest_site_fallback"] == 1),
                      "订单城市字段缺失→使用网点名称前缀尝试补齐")
    work["estimate_note"] = notes.str.rstrip("；")
    work["flag_estimated"] = (work["estimate_note"] != "").astype(int)

    # ---- 供质量报告直接计数的细分列 ----
    work["fill_dist"] = (work["dist_source"] != "od").astype(int)
    work["fill_vehicle"] = inferred_vehicle.astype(int)
    work["fill_load"] = (load_missing_raw).astype(int)
    work["fill_invoice"] = (work["invoice_ok"] == 0).astype(int)
    work["fill_duration"] = bad_time.astype(int)
    work["data_source"] = source

    # 列顺序按契约冻结
    for col in WIDE_COLUMNS:
        if col not in work.columns:
            work[col] = 0 if col.startswith("flag_") or col.startswith("fill_") else ""
    work = work[WIDE_COLUMNS]
    return work


# ---------------------------------------------------------------------------
# P5 · 宽表输出 + 质量报告
# ---------------------------------------------------------------------------

def _save_parquet(df: pd.DataFrame, path: Path, tries: int = 4) -> None:
    """Windows 上杀毒/索引偶尔锁文件，重试几次再报错（演示现场更稳）。"""
    last: Optional[Exception] = None
    for i in range(tries):
        try:
            df.to_parquet(path, index=False)
            return
        except OSError as exc:
            last = exc
            time.sleep(0.25 * (i + 1))
    raise last if last else RuntimeError("parquet 写入失败：" + str(path))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")


def quality_report(wide: pd.DataFrame, raw_rows: int, source: str, reason: str) -> Dict[str, Any]:
    """生成可解释的关联率与数据质量统计。

    关键定义：
    - order_match_rate：订单运单号是否直接匹配到订单表；
    - vehicle_match_rate：运单车型名称是否精确匹配附件2 Sheet2；
    - match_rate：前两项同时命中的综合关联率；
    - imputation_free_rate：未触发估算填补标记的独立路段比例。
    旧版 match_rate 实际衡量距离/车型填补情况，名称含义不清，现保留字段但明确改为关联率。
    """
    unique = wide[pd.to_numeric(wide["flag_dup"], errors="coerce").fillna(0) == 0].copy()
    total = max(len(unique), 1)
    valid_speed = unique["speed_kmh"].notna() & (pd.to_numeric(unique["speed_kmh"], errors="coerce") > 0)
    speed_num = pd.to_numeric(unique["speed_kmh"], errors="coerce")
    avg_speed = float(speed_num.loc[valid_speed].mean()) if valid_speed.any() else None
    avg_speed_1_100_mask = valid_speed & speed_num.between(1, 100)
    avg_speed_1_100 = float(speed_num.loc[avg_speed_1_100_mask].mean()) if avg_speed_1_100_mask.any() else None

    if "order_matched" in unique.columns:
        order_matched = pd.to_numeric(unique["order_matched"], errors="coerce").fillna(0).eq(1)
    else:
        order_matched = unique["order_no"].astype(str).str.strip().ne("")
    if "vehicle_matched" in unique.columns:
        vehicle_matched = pd.to_numeric(unique["vehicle_matched"], errors="coerce").fillna(0).eq(1)
    else:
        vehicle_matched = pd.to_numeric(unique["fill_vehicle"], errors="coerce").fillna(0).eq(0)

    order_match_rate = float(order_matched.mean()) if len(unique) else None
    vehicle_match_rate = float(vehicle_matched.mean()) if len(unique) else None
    association = order_matched & vehicle_matched
    association_match_rate = float(association.mean()) if len(unique) else None
    imputed = pd.to_numeric(unique["flag_estimated"], errors="coerce").fillna(0).eq(1)
    imputation_free_rate = float(1.0 - imputed.mean()) if len(unique) else None
    estimated_rows_wide = int(pd.to_numeric(wide["flag_estimated"], errors="coerce").fillna(0).eq(1).sum())

    conflicts = _duplicate_conflict_summary(wide)
    canonical_vehicle_conflict_rows = int(pd.to_numeric(unique.get("flag_vehicle_conflict", 0), errors="coerce")
                                          .fillna(0).sum()) if "flag_vehicle_conflict" in unique.columns else 0
    canonical_factor_conflict_rows = int(pd.to_numeric(unique.get("flag_factor_conflict", 0), errors="coerce")
                                         .fillna(0).sum()) if "flag_factor_conflict" in unique.columns else 0
    load_mapping_fallback_rows = int(pd.to_numeric(unique.get("flag_load_mapping_fallback", 0), errors="coerce")
                                     .fillna(0).sum()) if "flag_load_mapping_fallback" in unique.columns else 0
    load_mapping_fallback_raw_rows = int(pd.to_numeric(wide.get("flag_load_mapping_fallback", 0), errors="coerce")
                                         .fillna(0).sum()) if "flag_load_mapping_fallback" in wide.columns else 0
    load_mapping_fallback_duplicate_rows = int((
        pd.to_numeric(wide.get("flag_load_mapping_fallback", 0), errors="coerce").fillna(0).eq(1)
        & pd.to_numeric(wide.get("flag_dup", 0), errors="coerce").fillna(0).eq(1)
    ).sum()) if "flag_load_mapping_fallback" in wide.columns else 0
    load_mapping_fallback_types = (sorted(wide.loc[pd.to_numeric(wide["flag_load_mapping_fallback"], errors="coerce").fillna(0).eq(1),
                                                       "vehicle_type"].astype(str).unique().tolist())
                                   if "flag_load_mapping_fallback" in wide.columns and "vehicle_type" in wide.columns else [])
    time_delta_h = (pd.to_datetime(unique["arrive_ts"], errors="coerce")
                    - pd.to_datetime(unique["depart_ts"], errors="coerce")).dt.total_seconds() / 3600.0
    time_pair_valid = pd.to_datetime(unique["arrive_ts"], errors="coerce").notna() & pd.to_datetime(unique["depart_ts"], errors="coerce").notna()
    negative_duration_rows = int((time_pair_valid & (time_delta_h <= 0)).sum())
    duration_over_24h_rows = int((time_pair_valid & (time_delta_h > 24)).sum())
    duration_over_168h_rows = int((time_pair_valid & (time_delta_h > 168)).sum())
    speed_outlier_rows = int((valid_speed & ((speed_num > 100) | (speed_num < 1))).sum())
    incomplete_key_rows = int((
        unique["waybill_no"].astype(str).str.strip().eq("")
        | unique["plate"].astype(str).str.strip().eq("")
    ).sum())
    origin_still_missing_rows = int(unique["origin"].astype(str).str.strip().eq("").sum())
    dest_still_missing_rows = int(unique["dest"].astype(str).str.strip().eq("").sum())

    notes: List[str] = []
    if source == "demo":
        notes.append(reason)
        notes.append("演示数据为确定性合成样本（seed=%d），只用于前端联调与答辩兜底，不代表真实口径。" % DEMO_SEED)
    else:
        notes.append(reason)
        notes.append("关联率按明确标记统计：订单直连率 %.1f%%，Sheet2 车型名称匹配率 %.1f%%，两者同时命中 %.1f%%。"
                     % ((order_match_rate or 0) * 100, (vehicle_match_rate or 0) * 100,
                        (association_match_rate or 0) * 100))
        notes.append("去重键 = 运单 + 车牌 + 里程(2 位小数) + 封/解车时间(秒)；时间不全时退回起终点。")
    notes.append("综合关联率 match_rate 不再代指缺失填补比例；估算填补独立列出 imputation_free_rate。")
    if origin_still_missing_rows or dest_still_missing_rows:
        notes.append("城市字段经网点前缀尝试补齐后仍为空：起点 %d 行、终点 %d 行；需要后续地名映射或人工复核。"
                     % (origin_still_missing_rows, dest_still_missing_rows))
    if load_mapping_fallback_raw_rows:
        notes.append("宽表中 %d 行、%d 种车型未命中 62 类基准车型映射；其中 %d 行因段级去重未进入基准表，当前代表行中仍使用补充规则的有 %d 行。车型/因子冲突另按行标记，不应只看目录匹配率。"
                     % (load_mapping_fallback_raw_rows, len(load_mapping_fallback_types),
                        load_mapping_fallback_duplicate_rows, load_mapping_fallback_rows))
    if conflicts["duplicate_groups"]:
        notes.append("重复路段 %d 组；车型名称冲突 %d 组、车型编码冲突 %d 组；当前排放因子输入(vclass/load_t)冲突 %d 组。"
                     % (conflicts["duplicate_groups"], conflicts["vehicle_name_conflict_groups"],
                        conflicts["vehicle_code_conflict_groups"], conflicts["emission_factor_input_conflict_groups"]))
        notes.append("已逐行标记去重组中的车型冲突：保留代表行中 %d 行存在车型描述冲突，%d 行存在因子输入冲突；基准计算仍使用现行去重规则保留的代表行，相关结果须视为待复核。"
                     % (canonical_vehicle_conflict_rows, canonical_factor_conflict_rows))
    if negative_duration_rows:
        notes.append("到达时间不晚于发车时间 %d 条：保留原始时间，速度不参与均值，按规则标记。" % negative_duration_rows)
    if speed_outlier_rows:
        notes.append("速度低于 1 或高于 100 km/h 的独立路段 %d 条：保留计碳，单独标记。" % speed_outlier_rows)
    for text, col in (
        ("里程缺失", "fill_dist"), ("车型未匹配", "fill_vehicle"),
        ("发票未匹配", "fill_invoice"), ("时间戳异常", "fill_duration"),
    ):
        n = int(pd.to_numeric(unique[col], errors="coerce").fillna(0).sum())
        if n:
            notes.append("%s %d 行已填补并打标（不删除、不静默覆盖）。" % (text, n))
    n_load = int(pd.to_numeric(unique["fill_load"], errors="coerce").fillna(0).sum())
    if n_load:
        notes.append("载重字段缺失 %d 行：优先按车型登记表额定载重回填（是匹配、不黄框），登记表也没有时才用同类别中位并黄框。" % n_load)

    speed_method_counts: Dict[str, int] = {}
    if "speed_method" in unique.columns:
        speed_method_counts = {str(k): int(v) for k, v in unique["speed_method"].fillna("unknown").value_counts(dropna=False).items()}

    order_unmatched = ~order_matched
    vehicle_unmatched = ~vehicle_matched
    return {
        "source": source,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rows_raw": int(raw_rows),
        "rows_wide": int(len(wide)),
        "rows_dedup": int(len(unique)),
        "dup_rows": int((pd.to_numeric(wide["flag_dup"], errors="coerce").fillna(0) == 1).sum()),
        "duplicate_groups": conflicts["duplicate_groups"],
        "vehicle_name_conflict_groups": conflicts["vehicle_name_conflict_groups"],
        "vehicle_code_conflict_groups": conflicts["vehicle_code_conflict_groups"],
        "emission_factor_input_conflict_groups": conflicts["emission_factor_input_conflict_groups"],
        "canonical_vehicle_conflict_rows": canonical_vehicle_conflict_rows,
        "canonical_factor_conflict_rows": canonical_factor_conflict_rows,
        "load_mapping_fallback_rows": load_mapping_fallback_rows,
        "load_mapping_fallback_raw_rows": load_mapping_fallback_raw_rows,
        "load_mapping_fallback_duplicate_rows": load_mapping_fallback_duplicate_rows,
        "load_mapping_fallback_vehicle_types": load_mapping_fallback_types,
        "fuel_type_conflict_groups": conflicts["fuel_type_conflict_groups"],
        "mass_basis_conflict_groups": conflicts["mass_basis_conflict_groups"],
        "anomaly_rows": int(pd.to_numeric(unique["flag_anomaly"], errors="coerce").fillna(0).sum()),
        "speed_outlier_rows": speed_outlier_rows,
        "negative_duration_rows": negative_duration_rows,
        "duration_over_24h_rows": duration_over_24h_rows,
        "duration_over_168h_rows": duration_over_168h_rows,
        "incomplete_dedupe_key_rows": incomplete_key_rows,
        "origin_still_missing_rows": origin_still_missing_rows,
        "dest_still_missing_rows": dest_still_missing_rows,
        "order_no_fallback_rows": int(pd.to_numeric(unique["order_no_fallback"], errors="coerce").fillna(0).sum()),
        "cargo_weight_missing_rows": int(pd.to_numeric(unique["cargo_weight_missing"], errors="coerce").fillna(0).sum()),
        "cargo_volume_missing_rows": int(pd.to_numeric(unique["cargo_volume_missing"], errors="coerce").fillna(0).sum()),
        "cargo_pieces_missing_rows": int(pd.to_numeric(unique["cargo_pieces_missing"], errors="coerce").fillna(0).sum()),
        "origin_site_fallback_rows": int(pd.to_numeric(unique["origin_site_fallback"], errors="coerce").fillna(0).sum()),
        "dest_site_fallback_rows": int(pd.to_numeric(unique["dest_site_fallback"], errors="coerce").fillna(0).sum()),
        "basis_rows": int(pd.to_numeric(unique["flag_basis"], errors="coerce").fillna(0).sum()),
        "estimated_rows": int(pd.to_numeric(unique["flag_estimated"], errors="coerce").fillna(0).sum()),
        "estimated_rows_wide": estimated_rows_wide,
        "fill_dist": int(pd.to_numeric(unique["fill_dist"], errors="coerce").fillna(0).sum()),
        "fill_vehicle": int(pd.to_numeric(unique["fill_vehicle"], errors="coerce").fillna(0).sum()),
        "fill_load": int(pd.to_numeric(unique["fill_load"], errors="coerce").fillna(0).sum()),
        "fill_invoice": int(pd.to_numeric(unique["fill_invoice"], errors="coerce").fillna(0).sum()),
        "fill_duration": int(pd.to_numeric(unique["fill_duration"], errors="coerce").fillna(0).sum()),
        "valid_speed_rows": int(valid_speed.sum()),
        "avg_speed": None if avg_speed is None else round(avg_speed, 4),
        "avg_speed_1_100": None if avg_speed_1_100 is None else round(avg_speed_1_100, 4),
        "speed_method_counts": speed_method_counts,
        "total_km": round(float(pd.to_numeric(unique["dist_km"], errors="coerce").fillna(0).sum()), 4),
        "order_matched_rows": int(order_matched.sum()),
        "order_unmatched_rows": int(order_unmatched.sum()),
        "vehicle_matched_rows": int(vehicle_matched.sum()),
        "vehicle_unmatched_rows": int(vehicle_unmatched.sum()),
        "match_rate": None if association_match_rate is None else round(association_match_rate, 6),
        "association_match_rate": None if association_match_rate is None else round(association_match_rate, 6),
        "imputation_free_rate": None if imputation_free_rate is None else round(imputation_free_rate, 6),
        "vehicle_match_rate": None if vehicle_match_rate is None else round(vehicle_match_rate, 6),
        "order_match_rate": None if order_match_rate is None else round(order_match_rate, 6),
        "notes": notes,
    }


def _minimal_wide() -> pd.DataFrame:
    """最后一道兜底：三行确定性能跑通宽表契约。"""
    rows = []
    for i, (o, d) in enumerate((("上海", "杭州"), ("北京", "天津"), ("广州", "深圳"))):
        rows.append({
            "waybill_no": "FALLBACK%03d" % i, "order_no": "FALLBACK%03d" % i,
            "plate": "沪A000%d" % i, "vehicle_type": "4.2米厢式货车", "vtype_code": 4,
            "vclass": 1, "fuel_type": "柴油", "load_t": 4.5, "load_raw_t": 4.5,
            "mass_basis": "载质量",
            "basis_note": "", "load_mapping_method": "demo_vehicle_profile", "flag_load_mapping_fallback": 0,
            "origin": o, "dest": d, "od_pair": o + "|" + d,
            "od_key": "|".join(sorted([o, d])), "od_site": "|".join(sorted([o, d])),
            "origin_lat": CITY[o][0], "origin_lon": CITY[o][1],
            "dest_lat": CITY[d][0], "dest_lon": CITY[d][1],
            "dist_km": 180.0 + i * 40, "dist_source": "global_median", "month": 1 + i,
            "depart_ts": pd.Timestamp("2023-01-0%d 08:00" % (i + 1)),
            "arrive_ts": pd.Timestamp("2023-01-0%d 12:00" % (i + 1)),
            "duration_h": 4.0, "speed_kmh": 50.0,
            "dep_site": "", "arr_site": "",
            "invoice_no": "", "invoice_ok": 0, "invoice_amount": 900.0,
            "cargo_weight_kg": 0.0, "cargo_volume_cm3": 0.0, "cargo_pieces": 0.0,
            "complete_ts": pd.NaT,
            "flag_dup": 0, "flag_anomaly": 0, "flag_basis": 0, "flag_vehicle_conflict": 0,
            "flag_factor_conflict": 0, "flag_estimated": 1,
            "estimate_note": "演示兜底→行业均值", "fill_dist": 1, "fill_vehicle": 0,
            "fill_load": 0, "fill_invoice": 1, "fill_duration": 0, "data_source": "demo",
            "order_matched": 1, "vehicle_matched": 1, "source_waybill_row": 0,
            "speed_method": "industry_speed_50_assumption", "order_no_fallback": 0,
            "cargo_weight_missing": 0, "cargo_volume_missing": 0, "cargo_pieces_missing": 0,
            "origin_site_fallback": 0, "dest_site_fallback": 0,
        })
    return pd.DataFrame(rows)[WIDE_COLUMNS]


def build_wide_table() -> pd.DataFrame:
    """P5 唯一出口：宽表 + 质量报告；任何异常都降级，不向上抛脏数据错误。"""
    raw_rows = 0
    reason = ""
    try:
        raw, source, reason = _load_raw()
        raw_rows = len(raw)
        print("  [P1] 原始数据：%s（%d 行）" % (source, raw_rows))
    except Exception as exc:
        source = "demo"
        reason = "原始数据不可用（%s）→ 回退内置演示数据" % type(exc).__name__
        print("  [P1] " + reason)
        raw = _make_demo_raw()
        raw_rows = len(raw)

    try:
        wide = clean_frame(raw, source)
    except Exception:
        traceback.print_exc()
        source = "demo"
        reason = "清洗过程异常→回退演示数据"
        print("  [P2] " + reason)
        try:
            wide = clean_frame(_make_demo_raw(), "demo")
        except Exception:
            traceback.print_exc()
            wide = _minimal_wide()
            reason = "演示数据生成失败→使用三行最小兜底"

    if wide.empty:
        wide = _minimal_wide()
        source, reason = "demo", "清洗结果为空→使用三行最小兜底"

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    _save_parquet(wide, WIDE_PATH)
    report = quality_report(wide, raw_rows, source, reason)
    _write_json(QUALITY_PATH, report)
    _write_json(SOURCE_PATH, {
        "source": source,
        "reason": reason,
        "rows": int(len(wide)),
        "generated_at": report["generated_at"],
    })

    print("  [P5] 宽表落盘：%s（%d 行，去重后 %d 行）"
          % (WIDE_PATH.name, len(wide), report["rows_dedup"]))
    print("       重复 %d 行｜异常 %d 行｜口径换算 %d 行｜估算值 %d 行"
          % (report["dup_rows"], report["anomaly_rows"],
             report["basis_rows"], report["estimated_rows"]))
    print("       总里程 %.2f km｜有效速度 %d 条｜均速 %s km/h"
          % (report["total_km"], report["valid_speed_rows"],
             "—" if report["avg_speed"] is None else "%.2f" % report["avg_speed"]))
    return wide


def self_test_quality_report() -> Dict[str, Any]:
    """最小回归测试：关联率必须由显式匹配标志计算，冲突组必须可被识别。"""
    toy = _minimal_wide().iloc[:3].copy().reset_index(drop=True)
    toy["waybill_no"] = ["T-1", "T-2", "T-3"]
    toy["plate"] = ["P-1", "P-2", "P-3"]
    toy["order_no"] = ["T-1", "T-2", "T-3"]
    toy["order_matched"] = [1, 0, 1]
    toy["vehicle_matched"] = [1, 1, 0]
    toy["flag_dup"] = [0, 0, 0]
    report = quality_report(toy, 3, "real", "self-test")
    assert report["order_match_rate"] == round(2 / 3, 6), report
    assert report["vehicle_match_rate"] == round(2 / 3, 6), report
    assert report["association_match_rate"] == round(1 / 3, 6), report

    conflict = toy.iloc[:2].copy().reset_index(drop=True)
    conflict["waybill_no"] = ["D-1", "D-1"]
    conflict["plate"] = ["P-D", "P-D"]
    conflict["origin"] = ["上海", "上海"]
    conflict["dest"] = ["杭州", "杭州"]
    conflict["dist_km"] = [100.0, 100.0]
    conflict["depart_ts"] = pd.to_datetime(["2023-01-01 08:00:00", "2023-01-01 08:00:00"])
    conflict["arrive_ts"] = pd.to_datetime(["2023-01-01 10:00:00", "2023-01-01 10:00:00"])
    conflict["vehicle_type"] = ["车型 A", "车型 B"]
    conflict["vclass"] = [1, 3]
    conflict["load_t"] = [4.5, 30.0]
    conflict["flag_dup"] = [0, 1]
    conflict_report = _duplicate_conflict_summary(conflict)
    assert conflict_report["duplicate_groups"] == 1, conflict_report
    assert conflict_report["vehicle_name_conflict_groups"] == 1, conflict_report
    assert conflict_report["emission_factor_input_conflict_groups"] == 1, conflict_report
    vehicle_row_flags, factor_row_flags = _duplicate_conflict_row_flags(conflict)
    assert int(vehicle_row_flags.sum()) == 2, vehicle_row_flags.tolist()
    assert int(factor_row_flags.sum()) == 2, factor_row_flags.tolist()

    invoice_free_raw = pd.DataFrame({
        "waybill_no": ["INV-NA"], "order_no": ["O-INV-NA"], "plate": ["TEST"],
        "vehicle_type": ["4.2米厢式货车"], "dist_km": [50.0],
        "depart_ts": [pd.Timestamp("2023-01-01 08:00:00")],
        "arrive_ts": [pd.Timestamp("2023-01-01 09:00:00")],
        "vclass": [1], "load_t": [4.5], "fuel_type": ["柴油"],
        "mass_basis": ["载质量"], "vehicle_known": [1],
    })
    invoice_free_raw["load_mapping_method"] = "fallback_rule"
    invoice_free_raw["flag_load_mapping_fallback"] = 1
    invoice_free_wide = clean_frame(invoice_free_raw, "real")
    assert int(invoice_free_wide.loc[0, "flag_load_mapping_fallback"]) == 1
    assert int(invoice_free_wide.loc[0, "flag_estimated"]) == 1
    assert "62类基准映射" in invoice_free_wide.loc[0, "estimate_note"]
    assert pd.isna(invoice_free_wide.loc[0, "invoice_amount"]), invoice_free_wide[["invoice_amount", "invoice_ok"]]
    assert int(invoice_free_wide.loc[0, "invoice_ok"]) == -1
    assert int(invoice_free_wide.loc[0, "fill_invoice"]) == 0
    return {"association_metric_test": "PASS", "duplicate_conflict_test": "PASS",
            "row_level_conflict_flag_test": "PASS", "load_mapping_fallback_flag_test": "PASS",
            "no_invoice_source_test": "PASS"}


if __name__ == "__main__":
    print("=" * 78)
    print("数据层 preprocess.py · 单跑自测")
    print("质量指标自测：%s" % self_test_quality_report())
    print("=" * 78)
    build_wide_table()
    print("=" * 78)
