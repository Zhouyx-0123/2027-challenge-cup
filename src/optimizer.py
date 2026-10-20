# -*- coding: utf-8 -*-
"""引擎层：宽表 → 段级碳排（法定轨 + 物理轨）。

只做一件事：读 wide_data.parquet，写 baseline_carbon.parquet。
不读原始表、不读 data/raw、不碰前端。

口径（docs/08 排放节的最小实现）：
    O1 因子表 FACTOR_MAP：吴睿中等（2024）表 8，按载重锚点线性内插
    O2 载重 → 档位 → 排放率：30 t / VSP=2 → 14.67 g/s；VSP=3 → 16.53 g/s（偏差 12.68%）
    O3 双轨：法定轨 = 里程 × 常因子；物理轨 = VSP × 载重 × 速度 的瞬态排放率积分
    O4 段级碳排 + 双轨偏差
    O5 baseline 汇总（E₀ 由 pipeline 从本表累加，避免二次口径）

为什么因子不放在 preprocess：
    因子是“口径”不是“数据”。改因子只重算碳排、不动宽表，闸门立刻能对拍 14.67/12.68%。
    这是 docs/17 §2.2 修正一，也是本文件与数据层的唯一边界。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = ROOT / "data" / "processed"
WIDE_PATH = PROCESSED_DIR / "wide_data.parquet"
BASELINE_PATH = PROCESSED_DIR / "baseline_carbon.parquet"

FACTOR_VERSION = "JCB2023-BASE-v1"
FACTOR_SOURCE = "吴睿中等（2024）VSP 排放率；v=50 km/h；2023年基准碳排放计算结果.xlsx 因子表"

# O1 · 因子表：载重锚点(t) → 法定因子(g CO₂/km)
# 6 个锚点与附件2 的 6 类登记车型一一对应；30 t 锚点 = 1055.95 g/km，
# 在 50 km/h 下正好是 14.666 g/s（= 1055.95 / 72）。
LOAD_ANCHORS = np.array([3.0, 4.5, 12.0, 18.0, 30.0, 45.0], dtype=float)
FACTOR_ANCHORS = np.array([320.18, 394.56, 544.75, 759.24, 1055.95, 1581.84], dtype=float)

# 2023 基准因子表（来自《2023年基准碳排放计算结果.xlsx》路段明细）：
#   class 1（轻）统一 394.56；class 2 按 10/12/18/25 t；class 3 按 30/32/35/38/40/45/49 t。
# 这是 E₀=4,502,042.41 的对拍口径；factor_lookup 仍保留为未知车型/演示数据的兜底。
FACTOR_BY_CLASS_LOAD: Dict[Tuple[int, float], float] = {
    (1, 1.0): 394.560000,
    (1, 1.5): 394.560000,
    (1, 2.0): 394.560000,
    (1, 4.5): 394.560000,
    (1, 6.0): 394.560000,
    (1, 8.0): 394.560000,
    (2, 10.0): 458.928000,
    (2, 12.0): 544.752000,
    (2, 18.0): 759.240000,
    (2, 25.0): 896.592000,
    (3, 30.0): 1055.952000,
    (3, 32.0): 1119.696000,
    (3, 35.0): 1215.312000,
    (3, 38.0): 1310.928000,
    (3, 40.0): 1374.672000,
    (3, 45.0): 1581.840000,
    (3, 49.0): 1581.840000,
}


# O3 · VSP 修正：以 VSP=2（50 km/h 巡航）为 1.0
VSP_X = np.array([0.0, 1.0, 2.0, 3.0, 4.0], dtype=float)
VSP_MULT = np.array([0.82, 0.92, 1.00, 1.1268, 1.24], dtype=float)

REF_SPEED_KMH = 50.0
SECONDS_PER_HOUR = 3600.0
INDUSTRY_SPEED_KMH = 50.0


# ---------------------------------------------------------------------------
# 因子 / 内插
# ---------------------------------------------------------------------------

def factor_lookup(load_t: Any) -> np.ndarray:
    """载重 → 法定排放因子(g/km)。超出锚点范围自动夹紧（并由 flag_factor_x 标出）。"""
    arr = np.asarray(load_t, dtype=float)
    return np.interp(np.nan_to_num(arr, nan=30.0), LOAD_ANCHORS, FACTOR_ANCHORS)


def er_at(load_t: float, vsp: float = 2.0) -> float:
    """O2：给定载重与 VSP，返回瞬时排放率(g/s)。30 t / VSP=2 → 14.666。"""
    base = float(factor_lookup(load_t)) / 72.0
    mult = float(np.interp(vsp, VSP_X, VSP_MULT))
    return base * mult


def vsp_from_speed(speed_kmh: Any) -> np.ndarray:
    """速度 → VSP 代理：30→1, 50→2, 70→3, 90→4。"""
    arr = np.asarray(speed_kmh, dtype=float)
    return np.clip(1.0 + (arr - 30.0) / 20.0, 0.0, 4.0)


def self_test() -> Dict[str, float]:
    """O7：因子表自测——与数据无关，任何模式下都该全绿。"""
    er2 = er_at(30.0, 2.0)
    er3 = er_at(30.0, 3.0)
    dev = (er3 - er2) / er2 * 100.0
    return {
        "er30_vsp2": round(er2, 4),
        "er30_vsp3": round(er3, 4),
        "vsp3_dev_pct": round(dev, 4),
    }


# ---------------------------------------------------------------------------
# O4 · 段级碳排
# ---------------------------------------------------------------------------

def compute_baseline(wide: pd.DataFrame) -> pd.DataFrame:
    if wide is None or len(wide) == 0:
        raise ValueError("宽表为空，无法计算基准碳排")

    df = wide.copy()
    # 重复行保留在宽表，基准核算剔除（蓝图 §2.3）
    if "flag_dup" in df.columns:
        df = df[pd.to_numeric(df["flag_dup"], errors="coerce").fillna(0) == 0].copy()
    if df.empty:
        raise ValueError("去重后没有有效路段")

    for col in ("dist_km", "load_t", "speed_kmh", "vclass"):
        if col not in df.columns:
            raise ValueError("宽表缺少契约列：" + col)

    dist = pd.to_numeric(df["dist_km"], errors="coerce").fillna(0.1).clip(lower=0.1)
    load_raw = pd.to_numeric(df["load_t"], errors="coerce").fillna(12.0)
    load = load_raw.clip(lower=1.0, upper=60.0)
    speed = pd.to_numeric(df["speed_kmh"], errors="coerce")
    speed = speed.where(speed > 1.0, INDUSTRY_SPEED_KMH).clip(lower=1.0, upper=130.0)

    # 法定轨：优先按 (vclass, load_t) 查 2023 基准因子表；未知车型再走内插兜底
    vclass = pd.to_numeric(df["vclass"], errors="coerce").fillna(2).astype(int).to_numpy()
    keys = list(zip(vclass, np.round(load, 2)))
    factor = np.array([FACTOR_BY_CLASS_LOAD.get((int(c), float(l)), np.nan)
                       for c, l in keys], dtype=float)
    miss = np.isnan(factor)
    if miss.any():
        factor[miss] = factor_lookup(load[miss])
    kg_legal = factor * dist / 1000.0

    # 物理轨：瞬时排放率(g/s) × 行驶秒数
    vsp = vsp_from_speed(speed)
    er = factor / 72.0 * np.interp(vsp, VSP_X, VSP_MULT)
    kg_phys = er * (dist / speed) * SECONDS_PER_HOUR / 1000.0

    delta = np.where(kg_legal > 0, (kg_phys - kg_legal) / kg_legal * 100.0, 0.0)
    out_range = np.array([(int(c), float(l)) not in FACTOR_BY_CLASS_LOAD for c, l in keys])

    df["factor_gkm"] = np.round(factor, 6)
    df["er_gs"] = np.round(er, 6)
    df["kg_co2"] = kg_legal.astype(float)
    df["kg_co2_phys"] = kg_phys.astype(float)
    df["delta_pct"] = np.round(delta, 4)
    df["factor_version"] = FACTOR_VERSION
    df["flag_factor_x"] = out_range.astype(int)

    return df


def _save_parquet(df: pd.DataFrame, path: Path, tries: int = 4) -> None:
    last: Exception | None = None
    for i in range(tries):
        try:
            df.to_parquet(path, index=False)
            return
        except OSError as exc:
            last = exc
            time.sleep(0.25 * (i + 1))
    raise last if last else RuntimeError("parquet 写入失败：" + str(path))


def build_baseline() -> pd.DataFrame:
    if not WIDE_PATH.exists():
        raise FileNotFoundError("缺少 data/processed/wide_data.parquet，请先跑 preprocess")
    wide = pd.read_parquet(WIDE_PATH)
    out = compute_baseline(wide)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    _save_parquet(out, BASELINE_PATH)

    total_t = float(out["kg_co2"].sum()) / 1000.0
    total_km = float(out["dist_km"].sum())
    med = float(out["delta_pct"].median()) if len(out) else 0.0
    print("  [O4] 段级碳排落盘：%s（%d 段）" % (BASELINE_PATH.name, len(out)))
    print("       E₀ = %s tCO₂e｜总里程 = %s km｜双轨偏差中位 = %+.2f%%"
          % (format(total_t, ",.2f"), format(total_km, ",.2f"), med))
    print("       因子表版本 %s（%s）" % (FACTOR_VERSION, FACTOR_SOURCE))

    by_cls = out.groupby("vclass", as_index=False).agg(
        n=("kg_co2", "size"), kg=("kg_co2", "sum"))
    names = {1: "轻型", 2: "中型", 3: "重型"}
    for _, row in by_cls.sort_values("vclass").iterrows():
        share = float(row["kg"]) / max(total_t * 1000.0, 1e-9) * 100.0
        print("       %s车：%d 段，%s t（%.1f%%）"
              % (names.get(int(row["vclass"]), "其他"), int(row["n"]),
                 format(float(row["kg"]) / 1000.0, ",.2f"), share))
    return out


# ---------------------------------------------------------------------------
# O8 · 路线情景（L1/L2）——只读结果表，不动 L0
# ---------------------------------------------------------------------------
#
# 口径分层（docs/23 §6.1）：
#   L0 基准核算 = 宽表实测里程 × 因子        → E₀ = 4,502,042.41 kg，**永不动**
#   L1 路线情景 = 高德路线距离/时长 × 同一因子 → 只做差量分析，不覆盖 E₀
#   L2 λ 决策   = L1 + 运营成本 + λ          → 回答"λ 一动推荐换不换"
#
# 为什么可以放在 optimizer：
#   因子是口径、路线碳排仍然属于"算碳"，所以路线碳核算留在引擎层，
#   保持"核心算法只看结果表"的边界（前端只读 parquet，零计算）。

ROUTE_PATH = PROCESSED_DIR / "route_od.parquet"

# 展示口径常量（不是业务口径——页面上必须标注"展示口径"）
TIME_VALUE_YUAN_PER_HOUR = 200.0   # 司机工时成本假设
ENERGY_PRICE_YUAN_PER_KWH = 0.8    # 电价假设
DIESEL_PRICE_YUAN_PER_L = 7.5      # 柴油价假设


def _route_index(route: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    """od_key → 路线记录（后出现的覆盖先出现的，与 preprocess 的取数口径一致）。

    连接键说明：段表里 `od_key` 是**城市级**（"上海|深圳"），`od_site` 才是**网点级**
    （"上海茸华路营业部|上海车墩集货分拣中心"）。路线表是网点级的，所以这里
    **优先用 od_site 匹配**，od_site 缺失时回退 od_key——保证 310 个热点 OD 全命中。
    """
    index: Dict[str, Dict[str, Any]] = {}
    if route is None or route.empty:
        return index
    for _, r in route.iterrows():
        record = r.to_dict()
        for key_col in ("od_site", "od_key"):
            value = record.get(key_col)
            if value is None or str(value) in ("", "nan"):
                continue
            index[str(value)] = record
    return index


def _segment_join_key(seg: pd.DataFrame) -> pd.Series:
    """段表的路线连接键：优先 od_site（网点级），缺失回退 od_key（城市级）。"""
    if "od_site" in seg.columns:
        keys = seg["od_site"].astype(str)
        if "od_key" in seg.columns:
            fallback = seg["od_key"].astype(str)
            keys = keys.where((keys != "") & (keys != "nan"), fallback)
        return keys
    if "od_key" in seg.columns:
        return seg["od_key"].astype(str)
    return pd.Series([""] * len(seg), index=seg.index)


def route_carbon(seg: pd.DataFrame, route: pd.DataFrame) -> pd.DataFrame:
    """路线情景双轨碳核算（L1）。

    输入：段表 baseline_carbon + 路线表 route_od
    输出：在段表上追加列（**不修改任何原有列**，L0 主口径不动）：
        route_km / route_min      该段所在 OD 的路线距离/时长（缺失回退宽表值 + flag_route_est）
        kg_co2_route_legal        法定轨 = route_km × factor_gkm（常因子，口径不变）
        kg_co2_route_phys         物理轨 = 用路线平均速度 → VSP 档位 → er_gs × 时长积分
        delta_pct_route           路线情景 vs 宽表基准的双轨偏差（正负都保留）
        flag_route_est            1 = 该段路线为离线估算或未命中

    因子仍从 FACTOR_BY_CLASS_LOAD 查——"因子是口径不是数据"的边界不变。
    """
    if seg is None or seg.empty:
        return seg.copy() if seg is not None else pd.DataFrame()

    out = seg.copy()
    index = _route_index(route)

    if "od_key" not in out.columns and "od_site" not in out.columns:
        out["route_km"] = np.nan
        out["route_min"] = np.nan
        out["kg_co2_route_legal"] = np.nan
        out["kg_co2_route_phys"] = np.nan
        out["delta_pct_route"] = np.nan
        out["flag_route_est"] = 1
        return out

    base_km = pd.to_numeric(out.get("dist_km", pd.Series(dtype=float)), errors="coerce")
    factor = pd.to_numeric(out.get("factor_gkm", pd.Series(dtype=float)), errors="coerce")
    speed = pd.to_numeric(out.get("speed_kmh", pd.Series(dtype=float)), errors="coerce")
    load = pd.to_numeric(out.get("load_t", pd.Series(dtype=float)), errors="coerce")

    n = len(out)
    route_km = np.zeros(n, dtype=float)
    route_min = np.zeros(n, dtype=float)
    flag_est = np.ones(n, dtype=int)
    flag_hit = np.zeros(n, dtype=int)

    join_keys = _segment_join_key(out).tolist()
    for i, join_key in enumerate(join_keys):
        rec = index.get(str(join_key))
        if not rec:
            # 未命中路线表：回退宽表实测里程，标为估算
            route_km[i] = float(base_km.iloc[i]) if i < len(base_km) and pd.notna(base_km.iloc[i]) else 0.0
            route_min[i] = 0.0
            continue
        flag_hit[i] = 1
        km = pd.to_numeric(pd.Series([rec.get("distance_km")]), errors="coerce").iloc[0]
        minutes = pd.to_numeric(pd.Series([rec.get("duration_min")]), errors="coerce").iloc[0]
        if pd.isna(km) or km <= 0:
            route_km[i] = float(base_km.iloc[i]) if i < len(base_km) and pd.notna(base_km.iloc[i]) else 0.0
            flag_est[i] = 1
        else:
            route_km[i] = float(km)
        route_min[i] = float(minutes) if (minutes is not None and not pd.isna(minutes)) else 0.0
        # 该 OD 的路线是离线兜底（haversine 估算）→ 本段也是估算
        flag_est[i] = int(rec.get("flag_route_est", 0) or 0)
        if route_min[i] <= 0 and route_km[i] > 0:
            # 时长缺失：按 50 km/h 行业均速反推（与 tools/route_fetch 离线兜底同口径）
            route_min[i] = route_km[i] / INDUSTRY_SPEED_KMH * 60.0
            flag_est[i] = 1

    route_km_arr = np.asarray(route_km, dtype=float)
    route_min_arr = np.asarray(route_min, dtype=float)

    # 法定轨：路线距离 × 同一因子
    kg_legal = factor.fillna(0.0).to_numpy(dtype=float) * route_km_arr / 1000.0

    # 物理轨：路线平均速度 → VSP 档位 → er_gs × 行驶秒数
    route_speed = np.divide(route_km_arr, route_min_arr / 60.0,
                            out=np.full(n, INDUSTRY_SPEED_KMH, dtype=float),
                            where=route_min_arr > 0)
    route_speed = np.clip(route_speed, 1.0, 130.0)
    vsp = np.clip(1.0 + (route_speed - 30.0) / 20.0, 0.0, 4.0)
    er = factor.fillna(0.0).to_numpy(dtype=float) / 72.0 * np.interp(vsp, VSP_X, VSP_MULT)
    kg_phys = er * (route_min_arr / 60.0) * SECONDS_PER_HOUR / 1000.0

    # 与 L0 基准的差量（正负都保留——差值本身是信息，不是误差）
    kg_base = pd.to_numeric(out.get("kg_co2", pd.Series(dtype=float)), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    delta_route = np.where(kg_base > 0, (kg_legal - kg_base) / kg_base * 100.0, 0.0)

    out["route_km"] = np.round(route_km_arr, 4)
    out["route_min"] = np.round(route_min_arr, 2)
    out["kg_co2_route_legal"] = np.round(kg_legal, 6)
    out["kg_co2_route_phys"] = np.round(kg_phys, 6)
    out["delta_pct_route"] = np.round(delta_route, 4)
    out["flag_route_hit"] = flag_hit
    out["flag_route_est"] = flag_est
    return out


def route_select_by_lambda(seg: pd.DataFrame, route: pd.DataFrame,
                           lam: float = 100.0) -> pd.DataFrame:
    """L2 · λ 联合目标：选路线/选车型。

    score = 运营成本(元) + λ × 碳排(t)
      运营成本 = 能耗成本(油/电) + 过路费 + 时间成本(路线时长 × 时薪假设)

    输出每 OD 的最优选择，λ 变则推荐变——这就是首页旋钮的落地点。
    **全部标注为展示口径**：时薪/油价/电价是假设值，不是业务口径。

    车型候选来自段表里实际跑过的车型（同一 OD 的历史车型池），
    碳排按 factor_lookup(载重档) × 路线距离估算，时间成本按路线时长。
    """
    if seg is None or seg.empty or ("od_key" not in seg.columns and "od_site" not in seg.columns):
        return pd.DataFrame()

    index = _route_index(route)
    lam = float(lam or 0.0)

    work = seg.copy()
    work["_route_join_key"] = _segment_join_key(seg)

    rows: List[Dict[str, Any]] = []
    grouped = work.groupby("_route_join_key", sort=False)
    for od_key, sub in grouped:
        rec = index.get(str(od_key))
        if not rec:
            continue
        km = pd.to_numeric(pd.Series([rec.get("distance_km")]), errors="coerce").iloc[0]
        minutes = pd.to_numeric(pd.Series([rec.get("duration_min")]), errors="coerce").iloc[0]
        if pd.isna(km) or km <= 0:
            continue
        if pd.isna(minutes) or minutes <= 0:
            minutes = float(km) / INDUSTRY_SPEED_KMH * 60.0
        toll = float(rec.get("toll_yuan") or 0.0)

        best: Optional[Dict[str, Any]] = None
        candidates = sub.groupby("vehicle_type", sort=False).agg(
            load_t=("load_t", "mean"),
            n=("kg_co2", "size"),
        ).reset_index()
        for _, cand in candidates.iterrows():
            load_v = float(cand["load_t"]) if pd.notna(cand["load_t"]) else 12.0
            factor = float(factor_lookup(load_v))
            kg = factor * float(km) / 1000.0
            # 能耗成本：柴油按里程油耗假设，电车按电耗假设（均为展示口径）
            if "电" in str(cand["vehicle_type"]):
                kwh = float(km) * 0.9
                energy = kwh * ENERGY_PRICE_YUAN_PER_KWH
            else:
                liters = float(km) * 0.35
                energy = liters * DIESEL_PRICE_YUAN_PER_L
            time_cost = float(minutes) / 60.0 * TIME_VALUE_YUAN_PER_HOUR
            opex = energy + toll + time_cost
            carbon_cost = kg / 1000.0 * lam
            score = opex + carbon_cost
            record = {
                "od_key": str(od_key),
                "vehicle_type": str(cand["vehicle_type"]),
                "load_t": round(load_v, 2),
                "route_km": round(float(km), 3),
                "route_min": round(float(minutes), 2),
                "toll_yuan": round(toll, 2),
                "kg_co2_route": round(kg, 3),
                "opex_yuan": round(opex, 2),
                "carbon_cost_yuan": round(carbon_cost, 2),
                "score_yuan": round(score, 2),
                "flag_route_est": int(rec.get("flag_route_est", 0) or 0),
            }
            if best is None or record["score_yuan"] < best["score_yuan"]:
                best = record
        if best is not None:
            best["od_pair"] = str(rec.get("od_pair") or "")
            best["origin"] = str(rec.get("origin") or "")
            best["dest"] = str(rec.get("dest") or "")
            rows.append(best)

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values("score_yuan").reset_index(drop=True)


def route_constraint(route_km: float, available_range_km: float,
                     charging_ok: bool = True) -> Dict[str, Any]:
    """C1/C4 约束校验：路线距离直接挂钩。

    规则文本见 docs/20 §7：
      C1 续航：线路里程 ≤ 可用续航 × 0.8
      C4 补能：线路 30 km 缓冲区内有站（charging_ok 由调用方给，本函数不查站）

    返回结论字段，**不在前端重算**（页面只读结论列）。
    """
    route_km = float(route_km or 0.0)
    available_range_km = float(available_range_km or 0.0)
    usable = available_range_km * 0.8
    c1_ok = route_km <= usable if usable > 0 else False
    gap = max(route_km - usable, 0.0)
    return {
        "c1_range_ok": bool(c1_ok),
        "c1_gap_km": round(gap, 2),
        "c4_charging_ok": bool(charging_ok),
        "constraint_status": "pass" if (c1_ok and charging_ok) else "fail",
        "constraint_note": (
            "C1 续航达标（%.1f ≤ %.1f km）" % (route_km, usable)
            if c1_ok else "C1 续航不达标，缺口 %.1f km" % gap
        ) + ("；C4 补能可达" if charging_ok else "；C4 补能不可达"),
    }


def build_routes() -> pd.DataFrame:
    """O8 落库：读 route_od.parquet + 段表 → route_carbon.parquet + route_select.parquet。

    只在 route_od.parquet 存在时跑；不存在直接返回空表（pipeline 打印 SKIP）。
    """
    if not ROUTE_PATH.exists():
        return pd.DataFrame()
    if not BASELINE_PATH.exists():
        return pd.DataFrame()

    seg = pd.read_parquet(BASELINE_PATH)
    route = pd.read_parquet(ROUTE_PATH)

    enriched = route_carbon(seg, route)
    out_path = PROCESSED_DIR / "route_carbon.parquet"
    _save_parquet(enriched, out_path)

    selected = route_select_by_lambda(enriched, route, lam=100.0)
    sel_path = PROCESSED_DIR / "route_select.parquet"
    if not selected.empty:
        _save_parquet(selected, sel_path)

    n_est = int(enriched["flag_route_est"].sum()) if "flag_route_est" in enriched.columns else 0
    print("  [O8] 路线情景落盘：%s（%d 段，其中估算 %d 段）"
          % (out_path.name, len(enriched), n_est))
    if "route_km" in enriched.columns and "dist_km" in enriched.columns:
        delta = (pd.to_numeric(enriched["route_km"], errors="coerce")
                 - pd.to_numeric(enriched["dist_km"], errors="coerce"))
        base = pd.to_numeric(enriched["dist_km"], errors="coerce")
        valid = base > 0
        if valid.any():
            med = float((delta[valid].abs() / base[valid]).median() * 100.0)
            print("       路线距离 vs 实测里程：中位 |Δ|/km = %.2f%%（闸门 S1 目标 ≤10%%）" % med)
    if not selected.empty:
        print("  [O8] λ=100 推荐落盘：%s（%d 个 OD）" % (sel_path.name, len(selected)))
    return enriched


if __name__ == "__main__":
    print("=" * 78)
    print("引擎层 optimizer.py · 因子表自测")
    print("=" * 78)
    st = self_test()
    print("  30 t / VSP=2 → %.4f g/s（目标 14.67 ±0.01）" % st["er30_vsp2"])
    print("  30 t / VSP=3 → %.4f g/s（目标 16.53 ±0.01）" % st["er30_vsp3"])
    print("  VSP=3 平行校验偏差 → %.4f%%（目标 12.68 ±0.01）" % st["vsp3_dev_pct"])
    print("-" * 78)
    build_baseline()
    build_routes()
    print("=" * 78)
