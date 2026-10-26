# -*- coding: utf-8 -*-
"""车辆·路段油耗与碳核算模块（碳计算层）。

一句话：把**发票实测油量**、**高德路网实距**、**VSP 模型**三样东西拧成一把尺子，
给每辆车、每条路一个可审计的油耗标签，并把碳摊回路段与车辆。

定位与纪律（照 src/scenarios.py，docs/20 §11）：
    1. 纯函数、可单测；只读结果表与 config/ 口径文件；
       **不写 data/processed、不改闸门、不进 kpi**——本模块是分析层，不是对拍链路；
    2. 输出必带 ``assumptions`` 列表，页面/报告据此标注假设值；
    3. 口径唯一：柴油因子只从 config/calc_factors.json 读，油价只从
       config/fuel_price_history.json 读（默认空，见下），**不硬编码、不用无来源均值**。

四条轨道与它们的关系（这是本模块的核心）::

    发票(金额/油量/单价) ──► 油量 ──┬──► 油量直算碳排        （实测）
                                  └──► 与该车行程配对 ──► 实测百公里油耗
    里程(运单) + 车型 + 因子表 ───────────────────────────► 理论百公里油耗（法定轨）
    里程 + VSP 模型(er_gs) ──────────────────────────────► 理论百公里油耗（VSP 轨）
    高德路线(实距/时长/拥堵灯) ─────────────────────────► 路段特征

    实测 vs 理论 = 油耗偏差 = 车牌标签 + 审计线索
    多车样本在同一条路上的偏差中位数 = 路段系数 → 碳摊回路段与车辆

**为什么要有「油价」这一环**：
    有些发票只给金额、不给加注数量。要把金额变成油量，必须有当时的油价：
        油量 = 金额 ÷ 油价
    油价有三级来源，优先级从高到低：
        ① 票面单价（unit_price）—— 最强，自证；
        ② 金额 ÷ 油量 —— 自洽反推，可与①互相校验；
        ③ 外部历史油价表 config/fuel_price_history.json —— **默认空**。
    ③ 默认空是刻意的（照 config/invoice_spend_factors.json 的纪律）：
    没有可核查来源的油价，宁可不换算、标 needs_review，也不塞一个行业均值糊过去。

一个必须知道的物理事实（实测得出，见 docs/45）：
    VSP 轨的百公里油耗在**低速段不可用**。因为 VSP 修正的含义是
    「瞬时排放率随速度偏离 50 km/h 而变」，而 factor 表隐含的是 50 km/h 基准：
        VSP/法定 = mult(VSP) × 50 ÷ 速度
    实测：速度 ≤20 km/h 时该比值达 4.51 倍，>80 km/h 时只有 0.60 倍；
    逐段差异 p99 = +1130%，但按里程汇总仍只有 +2.93%（与 README 主账一致）。
    所以本模块对 VSP 轨一律标注速度档位，低速段标 ``vsp_applicable=False``，
    **理论基准取法定轨，VSP 轨只作高速段参考**。

口径单位：油量＝L；里程＝km；载重＝t；油耗＝L/100km；碳排＝kg CO₂ 与 tCO₂e；金额＝元。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FACTORS_PATH = ROOT / "config" / "calc_factors.json"
PRICE_PATH = ROOT / "config" / "fuel_price_history.json"

#: VSP 修正的基准速度（km/h）：mult(VSP) 是相对 50 km/h 的倍率（src/optimizer.py）
VSP_REF_SPEED_KMH = 50.0

#: 速度档位（km/h）。VSP 轨的适用性按档位判定，不按单段速度硬切。
SPEED_BANDS: List[Tuple[str, float, float]] = [
    ("≤20", 0.0, 20.0),
    ("20-30", 20.0, 30.0),
    ("30-40", 30.0, 40.0),
    ("40-50", 40.0, 50.0),
    ("50-60", 50.0, 60.0),
    ("60-80", 60.0, 80.0),
    (">80", 80.0, float("inf")),
]

#: VSP 轨适用的速度下限（km/h）。低于此值 VSP/法定比值失控，不作为理论值。
VSP_APPLICABLE_MIN_KMH = 30.0

#: 车速偏离阈值：|实测 − 理论| / 理论 超过此值判"偏差"，再分档
DEVIATION_NOTICE_PCT = 10.0
DEVIATION_ANOMALY_PCT = 25.0

#: 置信度分档（按该车该月被油量覆盖的里程 km）。**经验阈值，可调，需在报告里标注**。
CONFIDENCE_KM = (
    ("insufficient", 0.0),      # 无覆盖或 <50 km
    ("low", 50.0),              # 50–200 km
    ("medium", 200.0),          # 200–1000 km
    ("high", 1000.0),           # ≥1000 km
)
CONFIDENCE_KM_LOW = 50.0
CONFIDENCE_KM_MEDIUM = 200.0
CONFIDENCE_KM_HIGH = 1000.0

#: 路段系数标定所需的最少样本段数（低于此值用先验，不标定）
ROUTE_FACTOR_MIN_SEGMENTS = 5


# ===========================================================================
# 口径读取
# ===========================================================================

def load_factors() -> Dict[str, Any]:
    """读口径配置 config/calc_factors.json（柴油因子唯一来源）。"""
    try:
        return json.loads(FACTORS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def diesel_kg_per_l(factors: Optional[Dict[str, Any]] = None) -> Optional[float]:
    """柴油碳因子合计（kg CO₂/L）：TTW + WTT。读不到返回 None，绝不用默认值顶替。"""
    data = factors if factors is not None else load_factors()
    co2 = (data or {}).get("co2_factors") or {}
    ttw = (co2.get("diesel_ttw_kg_per_l") or {}).get("value")
    wtt = (co2.get("diesel_wtt_kg_per_l") or {}).get("value")
    try:
        if ttw is None or wtt is None:
            return None
        return float(ttw) + float(wtt)
    except (TypeError, ValueError):
        return None


def load_price_history() -> Dict[str, Any]:
    """读外部油价表 config/fuel_price_history.json。默认空（无可核查来源）。"""
    try:
        return json.loads(PRICE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ===========================================================================
# 口径对齐：运单行 → 物理行程
# ===========================================================================

#: 物理行程口径下需要**求和**的碳排列（运单行上都是分摊值）
_SUM_CARBON_COLS = ("kg_co2", "kg_co2_phys")
#: 物理行程口径下取**首行**即可的列
_FIRST_COLS = (
    "plate", "month", "od_site", "od_key", "od_pair", "vehicle_type", "vclass",
    "fuel_type", "load_t", "load_raw_t", "mass_basis", "speed_kmh", "duration_h",
    "factor_gkm", "er_gs", "kg_co2_gross", "kg_co2_phys_gross", "trip_km",
    "dist_km", "waybill_assoc_count", "allocation_share", "baseline_mode",
    "depart_ts", "arrive_ts", "origin", "dest", "dep_site", "arr_site",
)


def to_physical_trips(segments: pd.DataFrame) -> pd.DataFrame:
    """运单行 → 物理行程（按 trip_id 去重）。

    **为什么必须先做这一步**（这是本模块最容易算错的地方）：
    段表里 ``kg_co2`` 是**分摊后**的值（``kg_co2 = kg_co2_gross × allocation_share``），
    而 ``trip_km`` 在该行程的**每一个运单行上都是整趟里程**。
    所以直接在运单行上算 g/km 会把里程算重 N 倍（N = waybill_assoc_count），
    表现为理论百公里油耗被严重低估、油耗偏差虚高。

    主账 E₀ 用的就是物理行程口径（``src/pipeline.py`` 的 ``build_kpi`` 对 ``trip_id``
    去重后取 ``trip_km``），**本模块必须同口径**，否则两边对不上账。

    口径：
      - 碳排列取求和（各运单行的分摊值之和 = 该趟总碳排）；
      - 其余列取首行（同一趟内这些值相同）。
    """
    if segments is None or len(segments) == 0:
        return pd.DataFrame()
    if "trip_id" not in segments.columns:
        return segments.copy()

    frame = segments.copy()
    # 碳排：按行程求和（分摊值之和 = 整趟碳排）
    carbon = frame.groupby("trip_id", as_index=False)[[c for c in _SUM_CARBON_COLS
                                                        if c in frame.columns]].sum()
    carbon = carbon.rename(columns={c: c + "_trip" for c in carbon.columns
                                    if c != "trip_id"})
    # 其余：取首行
    first_cols = [c for c in _FIRST_COLS if c in frame.columns]
    first = (frame.sort_values("trip_id")
             .groupby("trip_id", as_index=False)[first_cols].first())
    out = first.merge(carbon, on="trip_id", how="left")
    for c in _SUM_CARBON_COLS:
        src = c + "_trip"
        if src in out.columns:
            if c in out.columns:
                out = out.drop(columns=[c])
            out = out.rename(columns={src: c})
    out.attrs["unit_note"] = "物理行程口径：碳排已按行程汇总，里程不重复计"
    return out


# ===========================================================================
# 理论轨：段级百公里油耗
# ===========================================================================

def speed_band(speed_kmh: Optional[float]) -> str:
    """速度档位标签。"""
    if speed_kmh is None or (isinstance(speed_kmh, float) and math.isnan(speed_kmh)):
        return "未知"
    for label, lo, hi in SPEED_BANDS:
        if lo < float(speed_kmh) <= hi:
            return label
    return SPEED_BANDS[0][0]


def _g_per_km_to_l_100km(g_per_km: Any, kg_per_l: Optional[float]) -> Optional[float]:
    """g CO₂/km → L/100km。换算成立的物理依据：燃油消耗率 = 排放率 ÷ 排放因子。"""
    if g_per_km is None or kg_per_l is None:
        return None
    try:
        g = float(g_per_km)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(g) or g <= 0:
        return None
    return (g / 1000.0) / float(kg_per_l) * 100.0


def prepare_segments(segments: pd.DataFrame,
                    factors: Optional[Dict[str, Any]] = None,
                    dedupe: bool = True) -> pd.DataFrame:
    """**标准入口**：运单行 → 物理行程 → 理论轨。

    下游所有函数（分摊 / 车牌标签 / 路段模型 / 碳分摊）都假定输入已是物理行程口径，
    调用方请统一走这个函数，别直接喂 ``baseline_carbon.parquet`` 的运单行。

    dedupe=False 仅用于已经按行程去重好的表。
    """
    frame = to_physical_trips(segments) if dedupe else segments.copy()
    return theoretical_fuel_table(frame, factors)


def theoretical_fuel_table(segments: pd.DataFrame,
                           factors: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
    """段级理论轨：法定轨 / VSP 轨 的 g·km⁻¹ 与 L·100km⁻¹。

    输入需含：trip_id, plate, month, od_site, trip_km, speed_kmh,
              factor_gkm, er_gs, kg_co2, kg_co2_phys（缺列按 NaN 处理）。
    输出在原表上追加：
        legal_g_km, legal_l_100km, vsp_g_km, vsp_l_100km,
        speed_band, vsp_applicable, vsp_over_legal
    """
    if segments is None or len(segments) == 0:
        return pd.DataFrame()
    out = segments.copy()
    kg_per_l = diesel_kg_per_l(factors)

    trip_km = pd.to_numeric(out.get("trip_km"), errors="coerce") if "trip_km" in out else pd.Series(np.nan, index=out.index)
    legal = pd.to_numeric(out.get("kg_co2"), errors="coerce") if "kg_co2" in out else pd.Series(np.nan, index=out.index)
    vsp = pd.to_numeric(out.get("kg_co2_phys"), errors="coerce") if "kg_co2_phys" in out else pd.Series(np.nan, index=out.index)

    with np.errstate(divide="ignore", invalid="ignore"):
        out["legal_g_km"] = (legal / trip_km * 1000.0).replace([np.inf, -np.inf], np.nan)
        out["vsp_g_km"] = (vsp / trip_km * 1000.0).replace([np.inf, -np.inf], np.nan)

    out["legal_l_100km"] = out["legal_g_km"].map(lambda g: _g_per_km_to_l_100km(g, kg_per_l))
    out["vsp_l_100km"] = out["vsp_g_km"].map(lambda g: _g_per_km_to_l_100km(g, kg_per_l))

    speed = pd.to_numeric(out.get("speed_kmh"), errors="coerce") if "speed_kmh" in out else pd.Series(np.nan, index=out.index)
    out["speed_band"] = speed.map(speed_band)
    out["vsp_applicable"] = speed.ge(VSP_APPLICABLE_MIN_KMH).fillna(False)
    with np.errstate(divide="ignore", invalid="ignore"):
        out["vsp_over_legal"] = (out["vsp_g_km"] / out["legal_g_km"]).replace([np.inf, -np.inf], np.nan)
    out.attrs["kg_co2_per_l"] = kg_per_l
    return out


# ===========================================================================
# 路段特征（高德嵌入模型侧）
# ===========================================================================

def route_feature_table(routes: pd.DataFrame) -> pd.DataFrame:
    """高德路线的路段级物理特征。

    输入需含：od_site, distance_km, duration_min, traffic_lights（缺列按 0/NaN 处理）。
    输出：
        route_km, route_min, route_avg_kmh（高德实均速）,
        lights_per_100km（拥堵灯密度）, toll_yuan, route_source
    **不编造任何修正系数** —— 特征只做刻画，修正由 VSP 轨（物理依据）或实测标定给出。
    """
    if routes is None or len(routes) == 0:
        return pd.DataFrame()
    out = routes.copy()
    km = pd.to_numeric(out.get("distance_km"), errors="coerce") if "distance_km" in out else pd.Series(np.nan, index=out.index)
    minutes = pd.to_numeric(out.get("duration_min"), errors="coerce") if "duration_min" in out else pd.Series(np.nan, index=out.index)
    lights = pd.to_numeric(out.get("traffic_lights"), errors="coerce") if "traffic_lights" in out else pd.Series(0.0, index=out.index)

    out["route_km"] = km
    out["route_min"] = minutes
    with np.errstate(divide="ignore", invalid="ignore"):
        out["route_avg_kmh"] = (km / (minutes / 60.0)).replace([np.inf, -np.inf], np.nan)
        out["lights_per_100km"] = (lights / km * 100.0).replace([np.inf, -np.inf], np.nan)
    if "toll_yuan" not in out.columns:
        out["toll_yuan"] = np.nan
    if "source" not in out.columns:
        out["route_source"] = "unknown"
    else:
        out["route_source"] = out["source"]
    return out


def attach_route_features(segments: pd.DataFrame, routes: pd.DataFrame) -> pd.DataFrame:
    """把高德路段特征并到段表（按 od_site 关联）。

    **关联不上是常态**（高德只预取了热点 OD），命中不了的段落 route_km = NaN，
    不做任何填补 —— 后续里程一致性判定只对命中段生效。
    """
    if segments is None or len(segments) == 0:
        return pd.DataFrame()
    out = segments.copy()
    feats = route_feature_table(routes)
    if feats.empty or "od_site" not in feats.columns or "od_site" not in out.columns:
        for col in ("route_km", "route_min", "route_avg_kmh", "lights_per_100km",
                    "toll_yuan", "route_source"):
            out[col] = np.nan
        return out
    keep = ["od_site", "route_km", "route_min", "route_avg_kmh",
            "lights_per_100km", "toll_yuan", "route_source"]
    right = feats[[c for c in keep if c in feats.columns]].drop_duplicates("od_site")
    out = out.merge(right, on="od_site", how="left")
    trip_km = pd.to_numeric(out.get("trip_km"), errors="coerce") if "trip_km" in out else pd.Series(np.nan, index=out.index)
    with np.errstate(divide="ignore", invalid="ignore"):
        out["route_km_ratio"] = (trip_km / pd.to_numeric(out["route_km"], errors="coerce")).replace([np.inf, -np.inf], np.nan)
    return out


# ===========================================================================
# 油价桥梁：金额 → 油量
# ===========================================================================

def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return f if math.isfinite(f) else None
    text = str(value).strip().replace(",", "").replace("¥", "").replace("￥", "")
    for unit in ("升", "公升", "元", "L", "l"):
        text = text.replace(unit, "")
    text = text.strip()
    if not text:
        return None
    try:
        f = float(text)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def resolve_fuel_volume(record: Dict[str, Any],
                        price_history: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把一条发票解析成「油量 + 油价 + 来源」。

    油价三级来源（优先级从高到低）：
        ① 票面单价 unit_price —— 最强，自证；
        ② 金额 ÷ 油量 —— 自洽反推（并与①互相校验，偏差过大判 needs_review）；
        ③ 外部历史油价表（按日期查 config/fuel_price_history.json）。

    任何一级都拿不到 → status="needs_review"，**不编造油量**。
    """
    out: Dict[str, Any] = {
        "fuel_liters": None,
        "price_yuan_per_l": None,
        "price_source": "",
        "status": "needs_review",
        "reason_code": "",
        "note": "",
    }
    amount = _num(record.get("invoice_amount"))
    liters = _num(record.get("fuel_liters"))
    if liters is None:
        liters = _num(record.get("quantity")) if str(record.get("unit") or "").strip() in ("L", "升", "l") else None
    unit_price = _num(record.get("unit_price"))

    if liters is not None and liters > 0:
        out["fuel_liters"] = liters
        if unit_price is not None and unit_price > 0:
            out["price_yuan_per_l"] = unit_price
            out["price_source"] = "invoice_unit_price"
            if amount is not None and amount > 0:
                implied = amount / liters
                if abs(implied - unit_price) / unit_price > 0.05:
                    out["status"] = "needs_review"
                    out["reason_code"] = "price_amount_mismatch"
                    out["note"] = ("票面单价 %.3f 与金额/油量 %.3f 相差 >5%%，需人工复核"
                                   % (unit_price, implied))
                else:
                    out["status"] = "ok"
            else:
                out["status"] = "ok"
        elif amount is not None and amount > 0:
            implied = amount / liters
            out["price_yuan_per_l"] = round(implied, 4)
            out["price_source"] = "amount_over_liters"
            out["status"] = "ok"
            out["note"] = "单价由金额÷油量反推"
        else:
            out["status"] = "ok"
            out["note"] = "票面有油量，无金额也无单价"
        return out

    # 到这里说明没有可用油量 → 只能用金额 ÷ 油价
    history = price_history if price_history is not None else load_price_history()
    price = _lookup_price(history, record.get("invoice_date"))
    if amount is None or amount <= 0:
        out["reason_code"] = "missing_amount_and_liters"
        out["note"] = "既无油量也无金额，无法换算"
        return out
    if price is None:
        out["reason_code"] = "missing_price_source"
        out["note"] = ("有金额但拿不到可核查的油价（票面无单价、无法反推、"
                       "config/fuel_price_history.json 未登记该日期）——不编造油量")
        return out
    out["fuel_liters"] = round(amount / float(price), 3)
    out["price_yuan_per_l"] = float(price)
    out["price_source"] = "price_history"
    out["status"] = "ok"
    out["note"] = "油量由金额÷历史油价换算"
    return out


def _lookup_price(history: Dict[str, Any], date: Any) -> Optional[float]:
    """按日期查外部油价表。表为空或没有该日期 → None（不猜、不插值）。"""
    if not history:
        return None
    series = history.get("prices")
    if not isinstance(series, list) or not series:
        return None
    if not date:
        return None
    key = str(date).strip()[:10]
    for item in series:
        if not isinstance(item, dict):
            continue
        if str(item.get("date", "")).strip()[:10] == key:
            value = _num(item.get("price_yuan_per_l"))
            if value is not None and value > 0:
                return value
    return None


# ===========================================================================
# 油箱周期：把发票油量分摊到该车的行程段
# ===========================================================================

def allocate_invoice_fuel(invoices: Sequence[Dict[str, Any]],
                          segments: pd.DataFrame) -> pd.DataFrame:
    """按「油箱周期」把发票油量分摊到段。

    油箱周期定义：同一车牌的相邻两次加油之间，是一箱油覆盖的行程。
    某张发票的油量覆盖 [该发票日期, 下一张发票日期) 内该车的全部行程；
    周期内油量按**里程比例**分摊到各段。

    为什么按里程分摊：油耗率是里程的函数（L/100km），按里程分摊等价于给每段一个
    恒定的等效油耗率，符合"一箱油走完全程"的物理直觉，且可复算、可审计。

    输出（段级，一行一段）：
        trip_id, plate, month, od_site, trip_km, allocated_liters,
        cycle_id, cycle_fuel_liters, cycle_km, cycle_invoice_no,
        cycle_invoice_date, allocation_status
    没有发票覆盖的段：allocated_liters = NaN，allocation_status = "no_invoice_cover"。
    """
    if segments is None or len(segments) == 0:
        return pd.DataFrame()
    base = segments.copy()
    # 数值列初始化 NaN，文本列初始化空串 —— 统一用 NaN 会让 pandas 把列锁成 float64，
    # 后续写入发票号这类字符串会直接抛 TypeError。
    for col in ("allocated_liters", "cycle_fuel_liters", "cycle_km"):
        base[col] = np.nan
    for col in ("cycle_id", "cycle_invoice_no", "cycle_invoice_date"):
        base[col] = ""
    base["allocation_status"] = "no_invoice_cover"
    if not invoices:
        return base

    # 段的时间键：优先 depart_ts，退化到 month
    time_key = None
    if "depart_ts" in base.columns:
        cand = pd.to_datetime(base["depart_ts"], errors="coerce")
        if cand.notna().any():
            time_key = cand
    if time_key is None and "month" in base.columns:
        time_key = pd.to_datetime(base["month"].astype(str) + "-15", errors="coerce")
    if time_key is None:
        return base

    base["_t"] = time_key
    plate_col = "plate" if "plate" in base.columns else None
    if plate_col is None:
        return base

    # 发票预处理
    inv_rows: List[Dict[str, Any]] = []
    for rec in invoices:
        resolved = rec.get("_resolved") or resolve_fuel_volume(rec)
        plate = str(rec.get("plate") or "").strip()
        liters = resolved.get("fuel_liters")
        date = rec.get("invoice_date")
        if not plate or liters is None or not date:
            continue
        ts = pd.to_datetime(str(date), errors="coerce")
        if pd.isna(ts):
            continue
        inv_rows.append({
            "plate": plate,
            "ts": ts,
            "liters": float(liters),
            "invoice_no": str(rec.get("invoice_no") or ""),
            "date": str(date)[:10],
            "price": resolved.get("price_yuan_per_l"),
            "price_source": resolved.get("price_source"),
            "status": resolved.get("status"),
        })
    if not inv_rows:
        return base
    inv = pd.DataFrame(inv_rows).sort_values(["plate", "ts"])

    trip_id_col = "trip_id" if "trip_id" in base.columns else None

    for plate, grp in base.groupby(plate_col, sort=False):
        sub_inv = inv[inv["plate"] == plate]
        if sub_inv.empty:
            continue
        g = grp.sort_values("_t")
        for i, row in enumerate(sub_inv.itertuples()):
            start = row.ts
            end = sub_inv.iloc[i + 1].ts if i + 1 < len(sub_inv) else None
            mask = g["_t"] >= start
            if end is not None:
                mask &= g["_t"] < end
            window = g[mask]
            if window.empty:
                continue
            km = pd.to_numeric(window["trip_km"], errors="coerce").fillna(0.0)
            total_km = float(km.sum())
            if total_km <= 0:
                continue
            alloc = row.liters * (km / total_km)
            idx = window.index
            base.loc[idx, "allocated_liters"] = alloc
            base.loc[idx, "cycle_id"] = "%s@%s" % (plate, row.date)
            base.loc[idx, "cycle_fuel_liters"] = row.liters
            base.loc[idx, "cycle_km"] = total_km
            base.loc[idx, "cycle_invoice_no"] = row.invoice_no
            base.loc[idx, "cycle_invoice_date"] = row.date
            base.loc[idx, "allocation_status"] = "allocated"

    base = base.drop(columns=["_t"], errors="ignore")
    return base


# ===========================================================================
# 车辆标签
# ===========================================================================

def _confidence(covered_km: float) -> str:
    if covered_km is None or not math.isfinite(covered_km) or covered_km < CONFIDENCE_KM_LOW:
        return "insufficient"
    if covered_km < CONFIDENCE_KM_MEDIUM:
        return "low"
    if covered_km < CONFIDENCE_KM_HIGH:
        return "medium"
    return "high"


def _label(deviation_pct: Optional[float], confidence: str) -> str:
    if deviation_pct is None:
        return "无判定（缺实测或理论值）"
    if confidence == "insufficient":
        return "样本不足，不判定"
    if abs(deviation_pct) <= DEVIATION_NOTICE_PCT:
        return "正常"
    if abs(deviation_pct) <= DEVIATION_ANOMALY_PCT:
        return "偏高（关注）" if deviation_pct > 0 else "偏低（关注）"
    return "异常·偏高" if deviation_pct > 0 else "异常·偏低"


def vehicle_efficiency_table(segments: pd.DataFrame,
                              allocated: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """车牌 × 月 的油耗标签表。

    口径：
        实测百公里油耗 = Σ分摊油量 ÷ Σ里程 × 100     （仅统计被油量覆盖的段）
        理论百公里油耗 = Σ法定轨碳排 ÷ Σ里程 × 100     （同口径段，取法定轨）
        偏差 = (实测 − 理论) ÷ 理论

    置信度按**被油量覆盖的里程**分档（样本不足时明确写 insufficient，不硬判）。
    输出同时给出审计所需的证据字段（发票号、覆盖段数、覆盖里程、理论来源）。
    """
    if segments is None or len(segments) == 0:
        return pd.DataFrame()
    df = allocated if allocated is not None and len(allocated) > 0 else segments.copy()
    if "allocated_liters" not in df.columns:
        df = df.copy()
        df["allocated_liters"] = np.nan

    kg_per_l = diesel_kg_per_l()
    df["_km"] = pd.to_numeric(df.get("trip_km"), errors="coerce")
    df["_alloc"] = pd.to_numeric(df["allocated_liters"], errors="coerce")
    df["_legal_kg"] = pd.to_numeric(df.get("kg_co2"), errors="coerce")
    df["_plate"] = df.get("plate")
    df["_month"] = df.get("month")

    rows: List[Dict[str, Any]] = []
    for (plate, month), g in df.groupby(["_plate", "_month"], dropna=False, sort=True):
        km_all = float(g["_km"].fillna(0.0).sum())
        covered = g[g["_alloc"].notna()]
        km_cov = float(covered["_km"].fillna(0.0).sum())
        alloc_l = float(covered["_alloc"].fillna(0.0).sum())

        # 理论值：与实测同口径段（只算被覆盖的段），避免拿没油量的段拉偏
        legal_kg = float(covered["_legal_kg"].fillna(0.0).sum()) if len(covered) else 0.0
        theo_l100 = _g_per_km_to_l_100km(
            (legal_kg / km_cov * 1000.0) if km_cov > 0 else None, kg_per_l)
        meas_l100 = (alloc_l / km_cov * 100.0) if km_cov > 0 and alloc_l > 0 else None

        deviation = None
        if meas_l100 is not None and theo_l100:
            deviation = (meas_l100 - theo_l100) / theo_l100 * 100.0

        conf = _confidence(km_cov)
        invoices = sorted({str(v) for v in covered.get("cycle_invoice_no", pd.Series(dtype=object)).dropna().tolist() if str(v)})

        rows.append({
            "plate": plate,
            "month": month,
            "segments_total": int(len(g)),
            "segments_covered": int(len(covered)),
            "km_total": round(km_all, 2),
            "km_covered": round(km_cov, 2),
            "fuel_liters_covered": round(alloc_l, 3),
            "measured_l_100km": round(meas_l100, 3) if meas_l100 is not None else None,
            "theoretical_l_100km": round(theo_l100, 3) if theo_l100 is not None else None,
            "deviation_pct": round(deviation, 2) if deviation is not None else None,
            "label": _label(deviation, conf),
            "confidence": conf,
            "invoice_nos": "; ".join(invoices),
            "theoretical_source": "legal_factor_table",
        })

    out = pd.DataFrame(rows)
    # 注意：assumptions 是标量广播到每一行（赋 list 会被 pandas 当成行赋值而报错）
    out["assumptions"] = (
        "理论油耗取法定因子表轨（VSP 轨仅作高速段参考，低速段 VSP/法定可达 4.5 倍）；"
        "油量按油箱周期里程比例分摊，分摊法假设一箱油走完周期内全程；"
        "置信度按被油量覆盖的里程分档：<50km 不足 / 50-200 低 / 200-1000 中 / ≥1000 高（经验阈值）"
    )
    return out


# ===========================================================================
# 路段模型
# ===========================================================================

def route_efficiency_model(segments: pd.DataFrame,
                           allocated: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """路段油耗系数模型。

    一级（先验，有物理依据）：该路段 VSP 轨 g/km ÷ 法定轨 g/km 的里程加权比。
        它来自 VSP 模型本身（速度对排放的修正），**不是拍脑袋的系数**。
        低速路段比值大 → 说明该路段慢、排放高 → 系数 >1。
    二级（实测标定）：该路段上所有车的「实测百公里油耗 ÷ 同段理论百公里油耗」的中位数。
        样本段数 ≥ ROUTE_FACTOR_MIN_SEGMENTS 才采用，否则用一级先验并标注来源。

    输出（每 od_site 一行）：
        od_site, segments, km, vsp_prior_factor, measured_factor,
        route_factor, factor_source, sample_segments, confidence
    """
    if segments is None or len(segments) == 0:
        return pd.DataFrame()
    df = allocated if allocated is not None and len(allocated) > 0 else segments.copy()
    df = df.copy()
    if "allocated_liters" not in df.columns:
        df["allocated_liters"] = np.nan

    df["_km"] = pd.to_numeric(df.get("trip_km"), errors="coerce")
    df["_legal_gkm"] = pd.to_numeric(df.get("legal_g_km"), errors="coerce")
    df["_vsp_gkm"] = pd.to_numeric(df.get("vsp_g_km"), errors="coerce")
    df["_alloc"] = pd.to_numeric(df["allocated_liters"], errors="coerce")
    df["_legal_l100"] = pd.to_numeric(df.get("legal_l_100km"), errors="coerce")

    rows: List[Dict[str, Any]] = []
    for od_site, g in df.groupby("od_site", dropna=False, sort=True):
        km = float(g["_km"].fillna(0.0).sum())
        legal_g = float(g["_legal_gkm"].fillna(0.0).sum())
        vsp_g = float(g["_vsp_gkm"].fillna(0.0).sum())
        prior = (vsp_g / legal_g) if legal_g > 0 else None

        meas = g[g["_alloc"].notna() & g["_legal_l100"].notna() & (g["_legal_l100"] > 0)].copy()
        # 向量化算比值：注意不能用 itertuples 读下划线开头的列 ——
        # namedtuple 不允许 _ 开头，pandas 会把列名改成 _1/_2…，getattr 永远取不到值。
        seg_km = pd.to_numeric(meas["_km"], errors="coerce")
        alloc = pd.to_numeric(meas["_alloc"], errors="coerce")
        legal_l = pd.to_numeric(meas["_legal_l100"], errors="coerce")
        valid = (seg_km > 0) & (alloc > 0) & (legal_l > 0)
        ratios = (alloc / seg_km * 100.0 / legal_l)[valid].tolist()

        measured_factor = float(np.median(ratios)) if ratios else None
        n_sample = len(ratios)
        if measured_factor is not None and n_sample >= ROUTE_FACTOR_MIN_SEGMENTS:
            factor = measured_factor
            source = "measured"
            conf = _confidence(float(meas["_km"].fillna(0.0).sum()))
        else:
            factor = prior
            source = "vsp_prior" if prior is not None else "none"
            conf = "insufficient"

        rows.append({
            "od_site": od_site,
            "segments": int(len(g)),
            "km": round(km, 2),
            "vsp_prior_factor": round(prior, 4) if prior is not None else None,
            "measured_factor": round(measured_factor, 4) if measured_factor is not None else None,
            "route_factor": round(factor, 4) if factor is not None else None,
            "factor_source": source,
            "sample_segments": n_sample,
            "confidence": conf,
        })
    out = pd.DataFrame(rows)
    out["assumptions"] = (
        "一级先验＝该路段 VSP 轨 g/km ÷ 法定轨 g/km 的里程加权比（物理依据：VSP 速度修正）；"
        "二级实测＝该路段各车「实测百公里油耗 ÷ 同段理论百公里油耗」的中位数；"
        "实测样本段数 <%d 时不标定，采用一级先验并标注来源" % ROUTE_FACTOR_MIN_SEGMENTS
    )
    return out


def attribute_carbon(segments: pd.DataFrame,
                     route_model: pd.DataFrame) -> pd.DataFrame:
    """把路段系数作用到段级碳排 → 得到「摊回路段/车辆」的碳。

    co2_attributed_kg = kg_co2 × route_factor
    factor 缺失 → 不修正（原值），并标 factor_applied=False。
    """
    if segments is None or len(segments) == 0:
        return pd.DataFrame()
    out = segments.copy()
    if route_model is not None and len(route_model) > 0 and "od_site" in route_model.columns:
        cols = [c for c in ("od_site", "route_factor", "factor_source") if c in route_model.columns]
        out = out.merge(route_model[cols].drop_duplicates("od_site"), on="od_site", how="left")
    else:
        out["route_factor"] = np.nan
        out["factor_source"] = "none"

    kg = pd.to_numeric(out.get("kg_co2"), errors="coerce") if "kg_co2" in out else pd.Series(np.nan, index=out.index)
    factor = pd.to_numeric(out.get("route_factor"), errors="coerce")
    applied = factor.notna() & (factor > 0)
    out["factor_applied"] = applied
    out["co2_attributed_kg"] = np.where(applied, kg * factor.fillna(1.0), kg)
    return out


# ===========================================================================
# 审计线索
# ===========================================================================

def build_audit_trail(vehicle_table: pd.DataFrame,
                      route_model: pd.DataFrame) -> List[Dict[str, Any]]:
    """生成可审计清单：每条异常都带完整证据链。

    证据链字段：车、月、实测/理论油耗、偏差、样本量与覆盖里程、置信度、
    关联发票号、判定规则、路段系数来源。

    **只列需要人看的条目**：
      - 有发票证据（覆盖里程 > 0）但偏差超阈值 → 真线索，列；
      - 有发票证据但覆盖里程不足 → 需要人补数据，列；
      - **完全没有发票覆盖（覆盖里程 = 0）→ 不是线索，是数据缺口**，不列进清单
        （否则没有发票时整张表都会变成"待办"，把真线索淹掉）。
        这类车月由调用方按 ``data_gap`` 口径单独统计。
    """
    trail: List[Dict[str, Any]] = []
    if vehicle_table is None or len(vehicle_table) == 0:
        return trail
    route_src = {}
    if route_model is not None and len(route_model) > 0 and "od_site" in route_model.columns:
        for row in route_model.itertuples():
            route_src[str(getattr(row, "od_site", ""))] = {
                "route_factor": getattr(row, "route_factor", None),
                "factor_source": getattr(row, "factor_source", ""),
            }

    for row in vehicle_table.itertuples():
        deviation = getattr(row, "deviation_pct", None)
        confidence = str(getattr(row, "confidence", ""))
        label = str(getattr(row, "label", ""))
        covered_km = float(getattr(row, "km_covered", 0.0) or 0.0)
        if covered_km <= 0:
            continue          # 无发票证据 → 数据缺口，不是审计线索
        needs = (deviation is not None and abs(float(deviation)) > DEVIATION_NOTICE_PCT) \
            or confidence == "insufficient"
        if not needs:
            continue
        trail.append({
            "plate": getattr(row, "plate", ""),
            "month": getattr(row, "month", ""),
            "label": label,
            "confidence": confidence,
            "measured_l_100km": getattr(row, "measured_l_100km", None),
            "theoretical_l_100km": getattr(row, "theoretical_l_100km", None),
            "deviation_pct": deviation,
            "segments_covered": getattr(row, "segments_covered", 0),
            "km_covered": getattr(row, "km_covered", 0.0),
            "invoice_nos": getattr(row, "invoice_nos", ""),
            "theoretical_source": getattr(row, "theoretical_source", ""),
            "rule": ("偏差 >%.0f%%" % DEVIATION_NOTICE_PCT) if deviation is not None else "覆盖里程不足",
        })
    return trail


def data_gap_summary(vehicle_table: pd.DataFrame) -> Dict[str, Any]:
    """数据缺口统计：完全没有发票覆盖的车月（不是审计线索，是"还没接上发票"）。"""
    if vehicle_table is None or len(vehicle_table) == 0:
        return {"vehicle_months": 0, "with_invoice": 0, "without_invoice": 0}
    covered = pd.to_numeric(vehicle_table.get("km_covered"), errors="coerce").fillna(0.0)
    total = int(len(vehicle_table))
    without = int((covered <= 0).sum())
    return {
        "vehicle_months": total,
        "with_invoice": total - without,
        "without_invoice": without,
    }


# ===========================================================================
# 自测
# ===========================================================================

def self_test() -> Dict[str, Any]:
    """内核自测（给 CI 与 tools/ 冒烟用）。"""
    kg_per_l = diesel_kg_per_l()
    out: Dict[str, Any] = {
        "kg_co2_per_l": kg_per_l,
        "speed_band_15": speed_band(15.0),
        "speed_band_55": speed_band(55.0),
        "speed_band_none": speed_band(None),
    }
    out["g_km_to_l100km_1055_952"] = _g_per_km_to_l_100km(1055.952, kg_per_l)
    out["g_km_to_l100km_394_56"] = _g_per_km_to_l_100km(394.56, kg_per_l)
    out["confidence_0"] = _confidence(0.0)
    out["confidence_600"] = _confidence(600.0)
    out["confidence_2000"] = _confidence(2000.0)
    out["label_ok"] = _label(3.0, "high")
    out["label_bad"] = _label(40.0, "high")
    return out


if __name__ == "__main__":
    print(json.dumps(self_test(), ensure_ascii=False, indent=2))
