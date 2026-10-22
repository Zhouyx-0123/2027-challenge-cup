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
ROUTE_CANDIDATES_PATH = PROCESSED_DIR / "route_candidates.parquet"
ROUTE_CANDIDATE_CARBON_PATH = PROCESSED_DIR / "route_candidate_carbon.parquet"

FACTOR_VERSION = "JCB2023-BASE-v1"
FACTOR_SOURCE = "吴睿中等（2024）VSP 排放率；v=50 km/h；2023年基准碳排放计算结果.xlsx 因子表"

# O1 · 因子表：载重锚点(t) → 法定因子(g CO₂/km)
# 兜底内插的 6 个锚点**全部**取自下方 2023 基准因子表（FACTOR_BY_CLASS_LOAD）：
# 4.5 t → (1, 4.5) 394.56｜12 t → (2, 12) 544.752｜18 t → (2, 18) 759.24
# 30 t → (3, 30) 1055.952（50 km/h 下 14.666 g/s）｜40 t → (3, 40) 1374.672｜45 t → (3, 45) 1581.84
# 旧版首锚点 3.0 t → 320.18 在因子表里没有任何出处（轻型 1.0–8.0 t 统一 394.56），已移除；
# 载重 < 4.5 t 时 np.interp 自动夹紧到 394.56，与轻型统一口径一致。
LOAD_ANCHORS = np.array([4.5, 12.0, 18.0, 30.0, 40.0, 45.0], dtype=float)
FACTOR_ANCHORS = np.array([394.56, 544.752, 759.24, 1055.952, 1374.672, 1581.84], dtype=float)

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


def _flag_int(value: Any) -> int:
    """把可能缺失 / NaN / 字符串的标记列安全转成 0/1。

    原写法 ``int(rec.get("flag_route_est", 0) or 0)`` 在值为 NaN 时会抛
    ``ValueError: cannot convert float NaN to integer``——NaN 是 truthy，``or`` 不兜底。
    路线表来自外部抓取（tools/route_fetch.py），缺列与 NaN 都常见，这里必须挡住。
    """
    if value is None:
        return 0
    try:
        if pd.isna(value):
            return 0
    except (TypeError, ValueError):
        pass
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


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

    base_km = pd.to_numeric(out.get("dist_km", pd.Series(np.nan, index=out.index)),
                            errors="coerce")
    factor = pd.to_numeric(out.get("factor_gkm", pd.Series(np.nan, index=out.index)),
                           errors="coerce")

    n = len(out)
    keys = _segment_join_key(out)

    # 一次性把路线表摊平成 4 张 map（键 → 值），再整列映射进来。
    # 原实现每行构造 3 个单元素 pd.Series（33k 段 × 3 次 ≈ 数秒），
    # 这里改成 O(n) 次字典查找，行为与逐行版**逐位一致**（见 docs/28 §4 的改法）。
    km_map = {k: rec.get("distance_km") for k, rec in index.items()}
    min_map = {k: rec.get("duration_min") for k, rec in index.items()}
    est_map = {k: rec.get("flag_route_est") for k, rec in index.items()}
    hit_map = {k: 1 for k in index}

    hit = keys.map(hit_map).notna().to_numpy()
    route_km_s = pd.to_numeric(keys.map(km_map), errors="coerce")
    route_min_s = pd.to_numeric(keys.map(min_map), errors="coerce")
    est_s = keys.map(est_map).map(_flag_int)
    valid_km = (route_km_s.notna() & (route_km_s > 0)).to_numpy()

    base_arr = base_km.fillna(0.0).to_numpy(dtype=float)
    # 命中但距离非法 → 回退宽表实测里程；未命中同样回退（与逐行版一致）
    route_km_arr = np.where(valid_km, route_km_s.fillna(0.0).to_numpy(dtype=float), base_arr)
    # 命中即用路线时长（缺失记 0）；未命中记 0
    route_min_arr = np.where(hit, route_min_s.fillna(0.0).to_numpy(dtype=float), 0.0)
    # 命中行的估算标记取路线表原值；未命中一律标估算
    flag_est = np.where(hit, est_s.fillna(0).to_numpy(dtype=int), 1)
    flag_hit = hit.astype(int)

    # 时长缺失：按 50 km/h 行业均速反推（与 tools/route_fetch 离线兜底同口径）
    # 只对**命中**的行生效——未命中行沿用逐行版语义：里程回退宽表、时长为 0。
    need_fill = hit & (route_min_arr <= 0) & (route_km_arr > 0)
    route_min_arr = np.where(need_fill, route_km_arr / INDUSTRY_SPEED_KMH * 60.0, route_min_arr)
    flag_est = np.where(need_fill, 1, flag_est)

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


def _best_vehicle_for_route(sub: pd.DataFrame, rec: Dict[str, Any],
                            lam: float) -> Optional[Dict[str, Any]]:
    """给定一个 OD 的段子集与一条路线记录，选该路线下综合得分最低的车型。

    口径与 route_select_by_lambda 完全一致：碳排按 factor_lookup(载重) × 路线距离，
    运营成本 = 能耗 + 过路费 + 时间成本，score = opex + λ × 碳排。全部是展示口径。
    """
    if sub is None or sub.empty or "vehicle_type" not in sub.columns:
        return None
    km = pd.to_numeric(pd.Series([rec.get("distance_km")]), errors="coerce").iloc[0]
    minutes = pd.to_numeric(pd.Series([rec.get("duration_min")]), errors="coerce").iloc[0]
    if pd.isna(km) or km <= 0:
        return None
    if pd.isna(minutes) or minutes <= 0:
        minutes = float(km) / INDUSTRY_SPEED_KMH * 60.0
    toll = float(rec.get("toll_yuan") or 0.0)

    candidates = sub.groupby("vehicle_type", sort=False).agg(
        load_t=("load_t", "mean"),
        n=("kg_co2", "size"),
    ).reset_index()
    best: Optional[Dict[str, Any]] = None
    for _, cand in candidates.iterrows():
        load_v = float(cand["load_t"]) if pd.notna(cand["load_t"]) else 12.0
        factor = float(factor_lookup(load_v))
        kg = factor * float(km) / 1000.0
        if "电" in str(cand["vehicle_type"]):
            energy = float(km) * 0.9 * ENERGY_PRICE_YUAN_PER_KWH
        else:
            energy = float(km) * 0.35 * DIESEL_PRICE_YUAN_PER_L
        time_cost = float(minutes) / 60.0 * TIME_VALUE_YUAN_PER_HOUR
        opex = energy + toll + time_cost
        carbon_cost = kg / 1000.0 * float(lam)
        score = opex + carbon_cost
        record = {
            "od_key": str(rec.get("od_site") or rec.get("od_key") or ""),
            "vehicle_type": str(cand["vehicle_type"]),
            "load_t": round(load_v, 2),
            "route_km": round(float(km), 3),
            "route_min": round(float(minutes), 2),
            "toll_yuan": round(toll, 2),
            "kg_co2_route": round(kg, 3),
            "opex_yuan": round(opex, 2),
            "carbon_cost_yuan": round(carbon_cost, 2),
            "score_yuan": round(score, 2),
            "flag_route_est": _flag_int(rec.get("flag_route_est")),
        }
        if best is None or record["score_yuan"] < best["score_yuan"]:
            best = record
    return best


def route_select_by_lambda(seg: pd.DataFrame, route: pd.DataFrame,
                           lam: float = 100.0) -> pd.DataFrame:
    """L2 · λ 联合目标：选路线/选车型（每个 OD 一条默认路线）。

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
    for od_key, sub in work.groupby("_route_join_key", sort=False):
        rec = index.get(str(od_key))
        if not rec:
            continue
        best = _best_vehicle_for_route(sub, rec, lam)
        if best is not None:
            best["od_pair"] = str(rec.get("od_pair") or "")
            best["origin"] = str(rec.get("origin") or "")
            best["dest"] = str(rec.get("dest") or "")
            rows.append(best)

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values("score_yuan").reset_index(drop=True)


def route_candidate_select(seg: pd.DataFrame, candidates: pd.DataFrame,
                           lam: float = 100.0) -> pd.DataFrame:
    """候选路线 × 车型的 L2 展示口径：每个 OD × 策略一行。

    输入 candidates 来自 route_candidates.parquet（高德 REST 0/4/1/6 策略），
    输出列在 route_select 基础上增加 strategy / strategy_name / source。
    只做展示口径，不进 L0 对拍；候选缺失/距离无效的行直接跳过。
    """
    if seg is None or seg.empty or candidates is None or candidates.empty:
        return pd.DataFrame()
    work = seg.copy()
    work["_route_join_key"] = _segment_join_key(seg)
    grouped = {str(k): sub for k, sub in work.groupby("_route_join_key", sort=False)}

    rows: List[Dict[str, Any]] = []
    for _, cand in candidates.iterrows():
        sub = None
        for col in ("od_site", "od_key"):
            value = cand.get(col)
            if value is not None and str(value) not in ("", "nan"):
                sub = grouped.get(str(value))
                if sub is not None:
                    break
        if sub is None or sub.empty:
            continue
        best = _best_vehicle_for_route(sub, cand.to_dict(), float(lam or 0.0))
        if best is None:
            continue
        best["strategy"] = _flag_int(cand.get("strategy"))
        best["strategy_name"] = str(cand.get("strategy_name") or "")
        best["source"] = str(cand.get("source") or "")
        best["od_pair"] = str(cand.get("od_pair") or "")
        best["origin"] = str(cand.get("origin") or "")
        best["dest"] = str(cand.get("dest") or "")
        rows.append(best)

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["od_key", "strategy", "score_yuan"]).reset_index(drop=True)


CONSTRAINT_BASIS: Dict[str, Dict[str, str]] = {
    "C1": {"level": "团队约定",
           "text": "可用续航 = 标称 ×(1 − 2%/年 × 车龄) × 0.8（SOC 安全余量 20%，docs/20 §7）；"
                   "0.8 与 2%/年 均为工程取值，无国标强制值"},
    "C2": {"level": "法规 + 团队约定",
           "text": "「连续驾驶 4 h 须停车休息 ≥20 min」见《道路交通安全法实施条例》第六十二条；"
                   "「单日 14 h 窗口」无对应法条，为团队口径（含装卸/补能）"},
    "C3": {"level": "团队约定",
           "text": "额定载重 ≥ 最大单票载重 × 1.05，5% 安全系数为团队取值"},
    "C4": {"level": "团队约定",
           "text": "线路 30 km 缓冲区内须有补能站，缓冲距离为团队取值"},
    "C5": {"level": "假设（待实测）",
           "text": "−10 ℃ 续航 ×0.7 取磷酸铁锂容量保持率区间下沿，需本车型实测；"
                   "「坡度 >4% 能耗 +10%」是简化假设——按纵向动力学粗算（30 t、50 km/h、4% 坡）"
                   "坡道功率约 163 kW，远高于平路约 39 kW，+10% 明显偏乐观，"
                   "上线前应改为「坡度→降速」模型或实测标定"},
}


#: 赛题表 1 给定的车型参数（docs/08 §8）。续航/载重按区间给，判据取保守端：
#: 续航取**下界**（C1 更容易不过），额定载重取**上界**（C3 不冤枉车）。
VEHICLE_SPECS: Dict[str, Dict[str, Any]] = {
    "五菱电卡/瑞驰 EC35": dict(fuel="纯电动", range_km=280.0, payload_t=3.0,
                            price_wan=12.0, energy_yuan_km=0.25, maint_yuan_year=8000, vclass=1),
    "比亚迪T5/吉利远程 E200": dict(fuel="纯电动", range_km=250.0, payload_t=5.0,
                                price_wan=18.0, energy_yuan_km=0.35, maint_yuan_year=12000, vclass=1),
    "南京金龙 D10/东风凯普特 e星": dict(fuel="纯电动", range_km=280.0, payload_t=10.0,
                                    price_wan=28.0, energy_yuan_km=0.48, maint_yuan_year=18000, vclass=2),
    "比亚迪Q3/开沃创源": dict(fuel="纯电动", range_km=200.0, payload_t=15.0,
                          price_wan=42.0, energy_yuan_km=0.62, maint_yuan_year=25000, vclass=2),
    "佛山飞驰氢能厢货": dict(fuel="氢燃料", range_km=400.0, payload_t=10.0,
                         price_wan=65.0, energy_yuan_km=0.85, maint_yuan_year=22000, vclass=2),
    "上汽红岩氢燃料重卡": dict(fuel="氢燃料", range_km=450.0, payload_t=15.0,
                          price_wan=95.0, energy_yuan_km=1.05, maint_yuan_year=35000, vclass=3),
    "现有柴油车": dict(fuel="柴油", range_km=float("inf"), payload_t=45.0,
                    price_wan=0.0, energy_yuan_km=0.98, maint_yuan_year=27000, vclass=3),
}


def route_constraint(
    route_km: float,
    available_range_km: float,
    charging_ok: bool = True,
    *,
    cargo_t: Optional[float] = None,
    payload_t: Optional[float] = None,
    avg_speed_kmh: float = INDUSTRY_SPEED_KMH,
    season: str = "summer",
    grade_pct: Optional[float] = None,
    charge_stop_h: float = 1.0,
    driver_window_h: float = 14.0,
    rest_every_h: float = 4.0,
    rest_min: float = 20.0,
    winter_derate: float = 0.30,
    grade_energy_uplift: float = 0.10,
    cargo_safety: float = 1.05,
    vehicle_age_yr: float = 0.0,
    age_degrade_per_yr: float = 0.02,
) -> Dict[str, Any]:
    """O6 · C1–C5 五条运营约束逐条判定（规则文本见 docs/20 §7、docs/08 §8）。

    五条约束**互不覆盖**，各自给结论；任何一条不过，``constraint_status`` 即 ``fail``。
    不通过时必须给出"卡在哪"（缺口 km / 超时 h / 差 t），不能只给 False。

    C1 续航：``route_km ≤ 可用续航 × 0.8``，其中
             可用续航 = 标称 × (1 − 2%/年 × 车龄)（常温、不含冬季折减）
    C2 14 h：``行驶 + 补能 + 强制休息 ≤ 14 h``；每 4 h 行驶强制休息 20 min
    C3 运力：``额定载重 ≥ 历史最大单票载重 × 1.05``
    C4 补能：线路 30 km 缓冲区内有站（可用性由调用方传入，本函数不查站）
    C5 冬季/山区：气温 < −10 ℃ 续航 ×0.7；平均坡度 > 4% 能耗 +10% → 续航 ÷1.1

    **依据等级**见 :data:`CONSTRAINT_BASIS`：只有 C2 的"4 h / 20 min"有法规出处，
    其余是团队约定或待实测假设——答辩时按等级说，不要把假设说成标准。

    说明：C5 是在 C1 的"常温结论"之上再做一次**修正后复检**——所以冬天可能出现
    "C1 通过、C5 不通过"（常温能跑、冬天跑不了），这正是要展示的信息。

    Returns:
        含 ``c1_range_ok … c5_winter_ok``、``constraints``（逐条明细，供表格渲染）
        与 ``constraint_status`` / ``constraint_note`` 的字典。
        ``c3_capacity_ok`` 在调用方未给载重时为 ``None``（未评估，不计入 fail）。
    """
    route_km = max(float(route_km or 0.0), 0.0)
    available_range_km = max(float(available_range_km or 0.0), 0.0)
    speed = float(avg_speed_kmh or INDUSTRY_SPEED_KMH)
    if speed <= 0:
        speed = INDUSTRY_SPEED_KMH

    # ---------------- C1 续航（常温 + 车龄衰减） ----------------
    age = max(float(vehicle_age_yr or 0.0), 0.0)
    base_range = available_range_km * max(1.0 - float(age_degrade_per_yr) * age, 0.0)
    usable = base_range * 0.8
    c1_ok = bool(usable > 0 and route_km <= usable)
    c1_gap = max(route_km - usable, 0.0)

    # ---------------- C2 14 h 作业窗口 ----------------
    drive_h = route_km / speed
    stops = int(np.ceil(route_km / usable) - 1) if usable > 0 else 0
    stops = max(stops, 0)
    charge_h = stops * float(charge_stop_h)
    rest_h = float(np.floor(drive_h / rest_every_h)) * rest_min / 60.0 if rest_every_h > 0 else 0.0
    total_h = drive_h + charge_h + rest_h
    c2_ok = bool(total_h <= driver_window_h)
    over_h = max(total_h - driver_window_h, 0.0)

    # ---------------- C3 运力匹配 ----------------
    c3_ok: Optional[bool] = None
    c3_gap_t: Optional[float] = None
    if cargo_t is not None and payload_t is not None:
        need_t = float(cargo_t) * float(cargo_safety)
        c3_ok = bool(float(payload_t) >= need_t)
        c3_gap_t = round(float(payload_t) - need_t, 2)

    # ---------------- C4 补能可达 ----------------
    c4_ok = bool((stops == 0) or charging_ok)

    # ---------------- C5 冬季 / 山区修正 ----------------
    derate = float(winter_derate) if season == "winter" else 0.0
    range_corr = base_range * (1.0 - derate)
    if grade_pct is not None and float(grade_pct) > 4.0:
        range_corr = range_corr / (1.0 + float(grade_energy_uplift))
    usable_corr = range_corr * 0.8
    c5_ok = bool(usable_corr > 0 and route_km <= usable_corr)
    c5_gap = max(route_km - usable_corr, 0.0)

    # ---------------- 逐条明细（表格渲染用） ----------------
    age_note = ("，车龄 %.0f 年" % age) if age > 0 else ""
    rows = [
        {"code": "C1", "name": "续航", "passed": c1_ok,
         "value": round(route_km, 1), "limit": round(usable, 1), "unit": "km",
         "basis": CONSTRAINT_BASIS["C1"]["level"],
         "note": ("达标（%.1f ≤ %.1f km%s）" % (route_km, usable, age_note)) if c1_ok
                 else ("不达标，缺口 %.1f km%s" % (c1_gap, age_note))},
        {"code": "C2", "name": "14 h 窗口", "passed": c2_ok,
         "value": round(total_h, 2), "limit": round(driver_window_h, 2), "unit": "h",
         "basis": CONSTRAINT_BASIS["C2"]["level"],
         "note": ("行驶 %.1f h + 补能 %.1f h + 休息 %.1f h" % (drive_h, charge_h, rest_h))
                 + ("" if c2_ok else ("，超时 %.0f min" % (over_h * 60.0)))},
        {"code": "C3", "name": "运力匹配",
         "passed": c3_ok,
         "value": (round(float(payload_t), 1) if payload_t is not None else None),
         "limit": (round(float(cargo_t) * cargo_safety, 1) if cargo_t is not None else None),
         "unit": "t",
         "basis": CONSTRAINT_BASIS["C3"]["level"],
         "note": ("未提供载重/额定，未评估" if c3_ok is None else
                  ("达标（%.1f ≥ %.1f t）" % (payload_t, float(cargo_t) * cargo_safety)
                   if c3_ok else ("超载，差 %.1f t" % abs(c3_gap_t or 0.0))))},
        {"code": "C4", "name": "补能可达", "passed": c4_ok,
         "value": stops, "limit": 0, "unit": "次",
         "basis": CONSTRAINT_BASIS["C4"]["level"],
         "note": ("无需途中补能" if stops == 0 else
                  ("沿线有站" if charging_ok else "沿线无可用补能站"))},
        {"code": "C5", "name": "冬季/山区", "passed": c5_ok,
         "value": round(route_km, 1), "limit": round(usable_corr, 1), "unit": "km",
         "basis": CONSTRAINT_BASIS["C5"]["level"],
         "note": (("无修正，结论同 C1" + ("" if c5_ok else ("（缺口 %.1f km）" % c5_gap)))
                  if (derate == 0 and (grade_pct is None or grade_pct <= 4))
                  else ("修正后%s（折减 %.0f%%）"
                        % ("达标" if c5_ok else ("不达标，缺口 %.1f km" % c5_gap),
                           derate * 100)))},
    ]

    evaluated = [r for r in rows if r["passed"] is not None]
    failed = [r for r in evaluated if not r["passed"]]
    note = "；".join("%s %s" % (r["code"], r["note"]) for r in failed)
    if not note:
        note = "C1–C5 全部通过" + ("（C3 未评估）" if c3_ok is None else "")

    return {
        # 兼容旧字段（调用方已在用，不能删）
        "c1_range_ok": c1_ok,
        "c1_gap_km": round(c1_gap, 2),
        "c4_charging_ok": c4_ok,
        "constraint_status": "fail" if failed else "pass",
        "constraint_note": note,
        # 新增逐条结论（与 app/services.CONSTRAINT_COLUMNS 对齐）
        "c2_window_ok": c2_ok,
        "c2_total_h": round(total_h, 3),
        "c3_capacity_ok": c3_ok,
        "c3_gap_t": c3_gap_t,
        "c5_winter_ok": c5_ok,
        "c5_derate_pct": round(derate * 100.0, 1),
        "constraint_failed": [r["code"] for r in failed],
        "constraints": rows,
        # 依据等级：答辩时按这个说，别把假设说成标准
        "constraint_basis": CONSTRAINT_BASIS,
        "c1_base_range_km": round(base_range, 2),
        "c1_age_degrade_pct": round((1.0 - base_range / available_range_km) * 100.0, 2)
        if available_range_km > 0 else 0.0,
    }


def constraint_self_test() -> List[Tuple[str, bool, str]]:
    """O7 · C1–C5 边界自测：六个用例，覆盖通过/不通过两侧。"""
    cases: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = [
        # (名称, 入参, 期望关键字段)
        ("C1 通过：200 km / 280 km 续航", dict(route_km=200, available_range_km=280),
         {"c1_range_ok": True}),
        ("C1 不通过：300 km / 280 km", dict(route_km=300, available_range_km=280),
         {"c1_range_ok": False, "c1_gap_km": 76.0}),
        # 车龄 5 年、2%/年 → 续航 280×0.9=252，可用 201.6；210 km 常温能跑、车龄后跑不了
        ("C1 车龄衰减：5 年车跑 210 km", dict(route_km=210, available_range_km=280,
                                          vehicle_age_yr=5),
         {"c1_range_ok": False, "c1_base_range_km": 252.0}),
        ("C2 不通过：700 km 单程", dict(route_km=700, available_range_km=450),
         {"c2_window_ok": False}),
        ("C3 不通过：12 t 货 / 10 t 额定", dict(route_km=100, available_range_km=280,
                                            cargo_t=12, payload_t=10),
         {"c3_capacity_ok": False, "c3_gap_t": -2.6}),
        ("C3 通过：9 t 货 / 10 t 额定", dict(route_km=100, available_range_km=280,
                                          cargo_t=9, payload_t=10),
         {"c3_capacity_ok": True}),
        ("C5 不通过：冬季 200 km / 280 km", dict(route_km=200, available_range_km=280,
                                              season="winter"),
         {"c1_range_ok": True, "c5_winter_ok": False}),
        ("C4 不通过：需补能但无站", dict(route_km=600, available_range_km=280,
                                     charging_ok=False),
         {"c4_charging_ok": False}),
    ]
    out: List[Tuple[str, bool, str]] = []
    for name, kwargs, expect in cases:
        result = route_constraint(**kwargs)
        bad = [k for k, v in expect.items() if result.get(k) != v]
        out.append((name, not bad, "OK" if not bad else "期望 %s，实得 %s"
                    % (expect, {k: result.get(k) for k in expect})))
    return out


def build_route_carbon_od(enriched: pd.DataFrame) -> pd.DataFrame:
    """O8 附产：OD 级路线口径碳合计（D4 前端零计算的最后一公里）。

    decision_panel 过去在浏览器端按 od_site 对 kg_co2_route_legal 求和，
    再当"路线碳排"指标卡展示——那是碳口径求和，违反前端只读铁律。
    这里按 OD 预计算落表，前端只做行选取。

    行 = od_site；列 = 段数 + 路线口径碳合计（legal/phys）+ 路线里程合计。
    """
    cols = ["od_site", "segments", "kg_co2_route_legal",
            "kg_co2_route_phys", "route_km"]
    if enriched is None or enriched.empty or "od_site" not in enriched.columns:
        return pd.DataFrame(columns=cols)
    frame = enriched.copy()
    for col in ("kg_co2_route_legal", "kg_co2_route_phys", "route_km"):
        if col not in frame.columns:
            frame[col] = 0.0
        frame[col] = pd.to_numeric(frame[col], errors="coerce").fillna(0.0)
    out = (frame.groupby(frame["od_site"].astype(str), as_index=False)
           .agg(segments=("kg_co2_route_legal", "size"),
                kg_co2_route_legal=("kg_co2_route_legal", "sum"),
                kg_co2_route_phys=("kg_co2_route_phys", "sum"),
                route_km=("route_km", "sum")))
    return out[cols]


def route_carbon_od_self_test() -> List[Tuple[str, bool, str]]:
    """D4 自测：OD 级路线碳合计——纯合成样例，不碰项目数据。"""
    frame = pd.DataFrame({
        "od_site": ["A|B", "A|B", "C|D", "C|D"],
        "kg_co2_route_legal": [10.0, 20.5, 4.25, float("nan")],
        "kg_co2_route_phys": [9.0, 18.0, 4.0, float("nan")],
        "route_km": [100.0, 120.0, 80.0, 0.0],
    })
    out = build_route_carbon_od(frame)
    a = out[out["od_site"] == "A|B"]
    c = out[out["od_site"] == "C|D"]
    return [
        ("每 OD 一行", len(out) == 2, "得到 %d 行" % len(out)),
        ("OD 合计对回（A|B）",
         len(a) == 1 and abs(float(a.iloc[0]["kg_co2_route_legal"]) - 30.5) < 1e-9,
         "期望 30.5，实得 %s" % (a.iloc[0]["kg_co2_route_legal"] if len(a) else "—")),
        ("NaN 段计 0 不计 NaN（C|D）",
         len(c) == 1 and abs(float(c.iloc[0]["kg_co2_route_legal"]) - 4.25) < 1e-9,
         "期望 4.25，实得 %s" % (c.iloc[0]["kg_co2_route_legal"] if len(c) else "—")),
        ("段数按行计（C|D = 2）",
         len(c) == 1 and int(c.iloc[0]["segments"]) == 2,
         "期望 2，实得 %s" % (c.iloc[0]["segments"] if len(c) else "—")),
        ("空表不炸", build_route_carbon_od(pd.DataFrame()).empty, "空入空出"),
    ]


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

    # OD 级路线口径碳合计（D4）：decision_panel 原来在浏览器端按 od_site
    # sum kg_co2_route_legal——那是碳口径求和，必须落表，前端只读行。
    od_carbon = build_route_carbon_od(enriched)
    od_path = PROCESSED_DIR / "route_carbon_od.parquet"
    if not od_carbon.empty:
        _save_parquet(od_carbon, od_path)
        print("  [D4] OD 路线碳合计落盘：%s（%d 个 OD）" % (od_path.name, len(od_carbon)))

    # 候选路线 × 策略的碳账（可选；route_candidates.parquet 存在才跑）
    if ROUTE_CANDIDATES_PATH.exists():
        try:
            candidates = pd.read_parquet(ROUTE_CANDIDATES_PATH)
            cand_sel = route_candidate_select(enriched, candidates, lam=100.0)
            if not cand_sel.empty:
                _save_parquet(cand_sel, ROUTE_CANDIDATE_CARBON_PATH)
                print("  [O8] 候选路线碳账落盘：%s（%d 行，%d 策略）"
                      % (ROUTE_CANDIDATE_CARBON_PATH.name, len(cand_sel),
                         int(cand_sel["strategy"].nunique())))
        except Exception as exc:
            print("  [O8] 候选路线碳账跳过：%s" % exc)

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
    print("O6 · C1–C5 运营约束自测")
    failed = 0
    for name, ok, detail in constraint_self_test():
        print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                               "" if ok else "  ← " + detail))
        failed += 0 if ok else 1
    print("  约束用例 %d/%d 通过" % (len(constraint_self_test()) - failed,
                                     len(constraint_self_test())))

    print("-" * 78)
    print("D4 · OD 路线碳合计自测")
    failed = 0
    for name, ok, detail in route_carbon_od_self_test():
        print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                               "" if ok else "  ← " + detail))
        failed += 0 if ok else 1
    print("  用例 %d/%d 通过" % (len(route_carbon_od_self_test()) - failed,
                                len(route_carbon_od_self_test())))

    print("-" * 78)
    build_baseline()
    build_routes()
    print("=" * 78)
