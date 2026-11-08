# -*- coding: utf-8 -*-
"""论文锚定速度分段 + K-means 归档轨（并列轨，不改 E₀ 主账）。

背景（吴老师 2026-11-02 指导 + 两篇工科论文）：
    老师要求把一段运输"聚成多段、分别计算"，且分段标准**锚定工科论文的
    实测档位**——"他们已经测好了，我们就在这儿聚合"。

本模块做两件事（互不替代）：
    1. **论文分箱（主口径，确定性）**：把每趟行程的门到门平均速度归入
       论文实测支撑的速度档。档位边界来自：
         · 吴睿中等（2024）《车辆载重对重型货车碳排放的影响》——
           95.2% 逐秒数据集中在 10~80 km/h；<20 km/h 时载重对 CO₂ 排放
           因子影响显著（满载 49t 为空载 18t 的 2.8 倍）；>110 km/h 判
           速度异常剔除；VSP 以 1 kW/t 为间隔分档。
         · 白孝轩等（2026）《考虑载货质量的柴油车多元动态 CO₂ 排放因子
           定量研究》——53 辆 PEMS 实测，重型车测试平均速度 20~70 km/h，
           排放因子是速度的非线性多项式（式 2），平均速度直接代入存在
           系统性偏差，分段越细偏差越小（Jensen 不等式）。
    2. **K-means 归档（验证层，数据驱动）**：对清洗后的速度做 1D K-means
       （K 扫描 2~6，肘部法选 K），把**数据自然聚类边界**与**论文档位边
       界**逐一对齐，输出偏差审计。最终档标签以论文为准——K-means 回答
       的是"我们数据自己的结构长什么样、和论文口径差多少"，不是另立一套。

边界纪律（与 OBD 实测轨同一套）：
    · 不改 ``src/pipeline.py`` 任何口径与 28 条主闸门判据；
    · 不写主链路结果表；结果单独落 ``data/processed/vsp_seg_*.parquet``；
    · 因子与 VSP 代理曲线只从 :mod:`src.factor_model` 读（唯一真源）；
    · 已知局限（诚实边界）：输入是**趟级平均速度**，不是逐秒剖面——
      OBD+GPS 上线后，同一套分档/归档框架可无缝切换到秒级输入。

因子口径差异（方案 B，2026-11-03）：分档轨用趟次表的**车辆组代表因子**，
主账用 **(车型, 载重)** 因子，两套口径不同 → 同一趟碳值不同。差异**逐趟
落成可查表** ``vsp_seg_factor_recon.parquet`` 并登记进 audit JSON，
**本模块不改计碳逻辑**（详见 docs/58 §3.6）。

用法::

    python -m src.vsp_segment --out data/processed
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src import schema, segment_cluster, segment_master
from src.factor_model import (
    INDUSTRY_SPEED_KMH,
    LOAD_ANCHORS,
    SECONDS_PER_HOUR,
    VSP_X,
    VSP_MULT,
    factor_lookup,
    vsp_from_speed,
)

#: 版本：档位边界或门禁规则变更必须 bump
SEG_VERSION = "PAPER-BIN-v1"
#: 计碳规则版本：规则卡（档位×载重→g/km）口径变更必须 bump
RULE_VERSION = "SEG-RULE-v1"

#: 论文锚定速度档（km/h）：左闭右开。依据见模块 docstring。
PAPER_BIN_EDGES = np.array([0.0, 20.0, 40.0, 60.0, 80.0, np.inf])
PAPER_BIN_LABELS = [
    "低速档 <20",
    "中低速档 20-40",
    "中速档 40-60",
    "中高速档 60-80",
    "高速档 ≥80",
]

#: 数据门禁（吴睿中 2024 §1.3：>110 km/h 为异常；<0 不可能）
SPEED_MIN_KMH = 0.0
SPEED_MAX_KMH = 110.0

#: K-means 扫描范围与收敛参数
K_MIN, K_MAX = 2, 6
KMEANS_MAX_ITER = 300
KMEANS_TOL = 1e-6
KMEANS_SEED = 20261103  # 固定种子：结果可复现

RESULT_TRIPS = "vsp_seg_trips.parquet"
RESULT_AUDIT = "vsp_seg_audit.json"
RESULT_CARBON = "vsp_seg_carbon.parquet"
RESULT_RULES = "vsp_seg_rule_table.parquet"
#: 因子口径对账表（方案 B：只登记差异，不改计碳逻辑）
RESULT_RECON = "vsp_seg_factor_recon.parquet"

#: 因子口径差异登记阈值（%）：代表因子 vs 主账 (车型,载重) 因子
FACTOR_RECON_THRESHOLD_PCT = 1.0


# --------------------------------------------------------------------------
# 数据门禁
# --------------------------------------------------------------------------
def _json_safe(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """NaN → None：严格 JSON 不允许 NaN，缺失一律留空（不写 0）。"""
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({k: (None if isinstance(v, float) and math.isnan(v) else v)
                    for k, v in r.items()})
    return out


def speed_gates(speed_kmh: pd.Series) -> pd.DataFrame:
    """逐趟速度门禁：返回 flags 列表（空列表 = 通过）。"""
    s = pd.to_numeric(speed_kmh, errors="coerce")
    flags: List[List[str]] = []
    for v in s:
        row: List[str] = []
        if pd.isna(v):
            row.append("speed_missing")
        else:
            if v < SPEED_MIN_KMH:
                row.append("speed_negative")
            if v > SPEED_MAX_KMH:
                row.append("speed_over_110")
        flags.append(row)
    return pd.DataFrame({"speed_flag_seg": flags})


def paper_bin_label(speed_kmh: float) -> str:
    """单点归档：论文档位标签（左闭右开；NaN → invalid）。"""
    if pd.isna(speed_kmh) or speed_kmh < SPEED_MIN_KMH or speed_kmh > SPEED_MAX_KMH:
        return "invalid"
    for i in range(len(PAPER_BIN_LABELS)):
        lo, hi = PAPER_BIN_EDGES[i], PAPER_BIN_EDGES[i + 1]
        if lo <= speed_kmh < hi:
            return PAPER_BIN_LABELS[i]
    return PAPER_BIN_LABELS[-1]  # ≥80 的有限值兜底（inf 边界右开）


# --------------------------------------------------------------------------
# 1D K-means（纯 numpy，不引入新依赖）
# --------------------------------------------------------------------------
def kmeans_1d(values: np.ndarray, k: int,
              seed: int = KMEANS_SEED,
              max_iter: int = KMEANS_MAX_ITER,
              tol: float = KMEANS_TOL) -> Tuple[np.ndarray, np.ndarray, float]:
    """1D Lloyd's k-means。返回 (labels, centers, inertia)。

    k-means++ 初始化 + 固定种子，结果确定可复现。
    """
    rng = np.random.default_rng(seed)
    raw = np.asarray(values, dtype=float)
    # 排序只为算得稳；labels 必须映射回**输入顺序**（否则标签是排序后下标）
    order = np.argsort(raw, kind="stable")
    x = raw[order]
    n = x.size
    # ---- k-means++ 初始化 ----
    centers = [float(x[rng.integers(n)])]
    while len(centers) < k:
        d2 = np.min((x[:, None] - np.asarray(centers)[None, :]) ** 2, axis=1)
        total = float(d2.sum())
        if total <= 0.0:
            centers.append(float(x[rng.integers(n)]))
            continue
        prob = d2 / total
        centers.append(float(x[rng.choice(n, p=prob)]))
    centers = np.asarray(sorted(centers), dtype=float)

    labels = np.zeros(n, dtype=int)
    inertia = np.inf
    for _ in range(max_iter):
        # assign
        dist = np.abs(x[:, None] - centers[None, :])
        new_labels = dist.argmin(axis=1)
        # update
        new_centers = centers.copy()
        for j in range(k):
            mask = new_labels == j
            if mask.any():
                new_centers[j] = x[mask].mean()
        new_inertia = float(((x - new_centers[new_labels]) ** 2).sum())
        shift = float(np.abs(new_centers - centers).max())
        labels, centers, inertia = new_labels, new_centers, new_inertia
        if shift <= tol:
            break
    # 空簇防御：把空簇中心丢弃（返回实际簇数 <= k）
    used = sorted(set(labels.tolist()))
    remap = {old: new for new, old in enumerate(used)}
    labels = np.asarray([remap[l] for l in labels], dtype=int)
    centers = centers[used]
    # 映射回**输入顺序**：inv[原始下标] = 排序后下标
    inv = np.empty(order.size, dtype=int)
    inv[order] = np.arange(order.size)
    labels = labels[inv]
    return labels, centers, inertia


def elbow_k(values: np.ndarray) -> Tuple[int, List[Dict[str, float]]]:
    """K 扫描（K_MIN..K_MAX）+ 最大二阶差分肘部法，返回 (K*, 扫描表)。"""
    x = np.asarray(values, dtype=float)
    scan: List[Dict[str, float]] = []
    for k in range(K_MIN, K_MAX + 1):
        _, centers, inertia = kmeans_1d(x, k)
        scan.append({"k": float(k), "inertia": inertia})
    # 肘部：inertia 二阶差分最大的点（下降速率骤减处）
    ks = [int(r["k"]) for r in scan]
    inert = [r["inertia"] for r in scan]
    if len(ks) < 3:
        k_star = ks[0]
    else:
        second = [inert[i - 1] - 2 * inert[i] + inert[i + 1]
                  for i in range(1, len(inert) - 1)]
        k_star = ks[second.index(max(second)) + 1]
    return k_star, scan


def boundary_alignment(centers: np.ndarray) -> List[Dict[str, Any]]:
    """K-means 簇心与论文档位代表速度（档中值）的对齐审计。

    论文档中值：10 / 30 / 50 / 70 / 90（10~80 主区间 + 高速档兜底）。
    输出每个簇心最近的论文档中值与偏差（km/h）——用于回答
    "数据自然结构和论文口径差多少"。
    """
    paper_mids = np.array([10.0, 30.0, 50.0, 70.0, 90.0])
    paper_names = ["低速(论文10)", "中低速(论文30)", "中速(论文50)",
                   "中高速(论文70)", "高速(论文90)"]
    out: List[Dict[str, Any]] = []
    for c in np.sort(centers):
        j = int(np.abs(paper_mids - c).argmin())
        out.append({
            "kmeans_center_kmh": round(float(c), 2),
            "nearest_paper_mid_kmh": float(paper_mids[j]),
            "nearest_paper_bin": paper_names[j],
            "deviation_kmh": round(float(c - paper_mids[j]), 2),
        })
    return out


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def build_segments(trips: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """输入物理趟次表，输出分段结果表 + 审计块。"""
    if "speed_kmh" not in trips.columns or "trip_id" not in trips.columns:
        raise ValueError("trips 表缺少 trip_id / speed_kmh 列")

    gates = speed_gates(trips["speed_kmh"])
    df = pd.concat([trips.reset_index(drop=True), gates], axis=1)
    df["passed"] = df["speed_flag_seg"].apply(lambda f: len(f) == 0)

    clean = df[df["passed"]].copy()
    n_clean = int(len(clean))

    # ---- 论文分箱（主口径）----
    df["speed_bin"] = df["speed_kmh"].apply(paper_bin_label)
    bin_stats = (
        clean.assign(_bin=clean["speed_kmh"].apply(paper_bin_label))
        .groupby("_bin")
        .agg(trips=("trip_id", "size"),
             bin_mean_speed=("speed_kmh", "mean"),
             dist_sum=("distance_km", "sum"))
        .reindex(PAPER_BIN_LABELS)
        .fillna(0.0)
        .reset_index()
        .rename(columns={"_bin": "speed_bin"})
    )
    bin_stats["bin_mean_speed"] = bin_stats["bin_mean_speed"].round(2)
    bin_stats["dist_sum"] = bin_stats["dist_sum"].round(2)
    bin_stats["trip_share_pct"] = (
        bin_stats["trips"] / max(n_clean, 1) * 100.0).round(2)

    # 每档 VSP 代理值（复用 factor_model 唯一真源，不自造曲线）
    bin_stats["bin_vsp_proxy"] = [
        round(float(vsp_from_speed([m])[0]), 3) if m > 0 else 0.0
        for m in bin_stats["bin_mean_speed"]
    ]

    # ---- K-means 归档（验证层）----
    clean_speeds = clean["speed_kmh"].to_numpy(dtype=float)
    k_star, scan = elbow_k(clean_speeds)
    labels, centers, inertia = kmeans_1d(clean_speeds, k_star)
    alignment = boundary_alignment(centers)

    audit: Dict[str, Any] = {
        "seg_version": SEG_VERSION,
        "method": "论文锚定分箱(主口径) + 1D K-means(归档验证)",
        "paper_basis": [
            "吴睿中等(2024) LC-VSP：10~80 km/h 为主区间、<20 km/h 载重效应显著、>110 判异常",
            "白孝轩等(2026) 多元动态因子：EF 为速度非线性多项式(式2)，分段降低 Jensen 偏差",
            "吴老师 2026-11-02 指导：分段标准锚定工科论文实测档位，K-means 负责归档",
        ],
        "gates": {
            "speed_min_kmh": SPEED_MIN_KMH,
            "speed_max_kmh": SPEED_MAX_KMH,
            "total": int(len(df)),
            "passed": n_clean,
            "flagged": int((~df["passed"]).sum()),
        },
        "paper_bins": [
            {"speed_bin": r["speed_bin"], "trips": int(r["trips"]),
             "trip_share_pct": float(r["trip_share_pct"]),
             "bin_mean_speed": float(r["bin_mean_speed"]),
             "bin_vsp_proxy": float(r["bin_vsp_proxy"])}
            for r in bin_stats.to_dict("records")
        ],
        "kmeans": {
            "k_selected": int(k_star),
            "selection": "肘部法(惯性二阶差分)，K 扫描 2~6",
            "k_scan": scan,
            "centers_kmh": [round(float(c), 2) for c in np.sort(centers)],
            "inertia": round(inertia, 2),
            "boundary_alignment_vs_paper": alignment,
            "verdict": "审计证据：数据自然边界 vs 论文档位；最终档标签以论文为准",
        },
        "known_limit": "输入为趟级门到门平均速度，非逐秒剖面；OBD+GPS 上线后框架不变、输入升级",
    }

    out_cols = ["trip_id", "month", "speed_kmh", "distance_km", "ef_g_km",
                "vehicle_group", "speed_bin", "speed_flag_seg", "passed"]
    cols = [c for c in out_cols if c in df.columns]
    return df[cols], audit


# --------------------------------------------------------------------------
# 计碳规则卡 + 分档计碳（只定规则，不做优化）
# --------------------------------------------------------------------------
def clean_speed_for_time(speed_kmh: pd.Series) -> np.ndarray:
    """时间口径的速度清洗：与 carbon_model.VSP 轨同一策略（≤1 → 行业均速，
    截断 1~130）——保证分档轨与现行 VSP 轨**只差 VSP 来源**，可干净对拍。"""
    s = pd.to_numeric(speed_kmh, errors="coerce")
    s = s.where(s > 1.0, INDUSTRY_SPEED_KMH)
    return s.clip(lower=1.0, upper=130.0).to_numpy(dtype=float)


def bin_multiplier(bin_mean_speed: float) -> float:
    """档均速 → VSP 代理 → 排放率乘数（唯一真源：factor_model 曲线）。"""
    vsp = float(vsp_from_speed([bin_mean_speed])[0])
    return float(np.interp(vsp, VSP_X, VSP_MULT))


def build_rule_table() -> pd.DataFrame:
    """**分段计碳规则卡**：档位 × 载重锚点 → ER(g/s) 与 EF(g/km)。

    这是交给下游（Q-learning 路径成本）的**唯一接口**：
        路段成本(kg) = EF(档, 载重) × 路段里程 / 1000
    规则卡不含任何优化逻辑——优化是下一棒的事。
    """
    rows: List[Dict[str, Any]] = []
    for label, mid in zip(PAPER_BIN_LABELS, [10.0, 30.0, 50.0, 70.0, 90.0]):
        mult = bin_multiplier(mid)
        vsp = float(vsp_from_speed([mid])[0])
        for load in LOAD_ANCHORS:
            factor = float(factor_lookup(np.asarray([load]))[0])   # g/km @50km/h 基准
            er = factor / 72.0 * mult                              # g/s（72 = 3600/50）
            ef = er * SECONDS_PER_HOUR / mid                       # g/km：ER 按档均速摊到公里
            rows.append({
                "speed_bin": label,
                "bin_mid_kmh": mid,
                "bin_vsp_proxy": round(vsp, 3),
                "vsf_multiplier": round(mult, 4),
                "load_t": float(load),
                "factor_base_gkm": round(factor, 2),
                "er_gs": round(er, 4),
                "ef_gkm": round(ef, 2),
                "rule_version": RULE_VERSION,
            })
    return pd.DataFrame(rows)


def rule_ef(rule_table: pd.DataFrame, speed_bin: str, load_t: float) -> float:
    """从规则卡查 EF(g/km)：档位精确匹配 + 载重线性插值（越界取端点）。"""
    sub = rule_table[rule_table["speed_bin"] == speed_bin]
    if sub.empty:
        raise KeyError(f"规则卡无此档位: {speed_bin}")
    sub = sub.sort_values("load_t")
    return float(np.interp(load_t, sub["load_t"].to_numpy(),
                           sub["ef_gkm"].to_numpy()))


def bin_carbon(seg: pd.DataFrame, audit: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """分档计碳（规则=档位 VSP 决定排放率，时间按本趟实际）：

        kg_binned_i = EF 基准_i(g/km) × mult(档) / 72 × 秒_i / 1000
        秒_i = 里程_i / 清洗速度_i × 3600

    同表并排现行 VSP 轨（本趟速度→VSP），两者**只差 VSP 来源**，
    差值即"分档规则"本身带来的口径变化，全程可对拍。
    """
    if "ef_g_km" not in seg.columns:
        raise ValueError("趟次表缺少 ef_g_km（法定因子 g/km）列")
    df = seg.copy()
    df["speed_clean"] = clean_speed_for_time(df["speed_kmh"])
    df["seconds"] = df["distance_km"] / df["speed_clean"] * SECONDS_PER_HOUR

    bin_mid = {r["speed_bin"]: r["bin_mean_speed"] for r in audit["paper_bins"]}
    bin_mid = {k: (v if v > 0 else 10.0) for k, v in bin_mid.items()}  # 空档兜底

    mult_bin = df["speed_bin"].map(lambda b: bin_multiplier(bin_mid.get(b, 10.0)))
    mult_bin = pd.to_numeric(mult_bin, errors="coerce")
    valid = df["passed"].to_numpy() & mult_bin.notna().to_numpy()

    # 缺失一律留空（NaN），不写默认因子/默认倍率——缺什么就算不出碳，
    # 静默兜底会把「没数据」变成「看起来有结果」。
    mult_bin = mult_bin.to_numpy(dtype=float)
    factor = pd.to_numeric(df["ef_g_km"], errors="coerce").to_numpy(dtype=float)
    seconds = df["seconds"].to_numpy(dtype=float)

    er_bin = factor / 72.0 * mult_bin
    kg_binned = er_bin * seconds / 1000.0
    kg_binned[~valid] = np.nan

    vsp_own = vsp_from_speed(df["speed_clean"])
    er_own = factor / 72.0 * np.interp(vsp_own, VSP_X, VSP_MULT)
    kg_vsp = er_own * seconds / 1000.0
    kg_vsp[~valid] = np.nan

    df["kg_co2_vsp_own"] = np.round(kg_vsp, 6)
    df["kg_co2_binned"] = np.round(kg_binned, 6)
    # 被门禁排除的趟：两条轨都算不出碳 → 偏差**留空**，不写 0
    df["binned_delta_pct"] = np.round(
        np.where(kg_vsp > 0, (kg_binned - kg_vsp) / kg_vsp * 100.0, np.nan), 3)

    ok = df[df["passed"]].copy()
    per_bin = (ok.groupby("speed_bin")
                 .agg(trips=("trip_id", "size"),
                      km=("distance_km", "sum"),
                      kg_vsp=("kg_co2_vsp_own", "sum"),
                      kg_binned=("kg_co2_binned", "sum"),
                      avg_delta_pct=("binned_delta_pct", "mean"))
                 .reindex(PAPER_BIN_LABELS).reset_index())
    # 空档：趟数/里程/碳量是"真的 0 趟"，保留 0；比率类指标算不出 → 留空
    for c in ("trips", "km", "kg_vsp", "kg_binned"):
        per_bin[c] = per_bin[c].fillna(0.0)
    per_bin["avg_delta_pct"] = per_bin["avg_delta_pct"].where(per_bin["trips"] > 0)
    per_bin["ef_binned_gkm"] = np.where(
        per_bin["km"] > 0, per_bin["kg_binned"] * 1000.0 / per_bin["km"], np.nan)
    for c in ("km", "kg_vsp", "kg_binned"):
        per_bin[c] = per_bin[c].round(3)
    per_bin["ef_binned_gkm"] = per_bin["ef_binned_gkm"].round(2)
    per_bin["avg_delta_pct"] = per_bin["avg_delta_pct"].round(3)

    kg_vsp_sum = float(ok["kg_co2_vsp_own"].sum())
    kg_bin_sum = float(ok["kg_co2_binned"].sum())
    summary = {
        "rule_version": RULE_VERSION,
        "rule_statement": "排放率由速度档决定（档均速→档VSP→档乘数），时间按本趟实际；因子基准沿用趟次法定因子(g/km)",
        "downstream_contract": "Q-learning/运筹侧以 vsp_seg_rule_table.parquet 的 ef_gkm(档,载重) × 里程 为路段成本系数；本模块不做优化",
        "totals": {
            "km": round(float(ok["distance_km"].sum()), 3),
            "kg_vsp_own_speed": round(kg_vsp_sum, 3),
            "kg_binned": round(kg_bin_sum, 3),
            "delta_t": round(kg_bin_sum - kg_vsp_sum, 3),
            "delta_pct": (round((kg_bin_sum / kg_vsp_sum - 1.0) * 100.0, 3)
                          if kg_vsp_sum > 0 else None),
            "excluded_trips": int((~df["passed"]).sum()),
        },
        "per_bin": _json_safe(per_bin.to_dict("records")),
    }
    cols = ["trip_id", "month", "speed_kmh", "speed_bin", "distance_km",
            "kg_co2_vsp_own", "kg_co2_binned", "binned_delta_pct", "passed"]
    return df[[c for c in cols if c in df.columns]], summary


# --------------------------------------------------------------------------
# 因子口径对账（方案 B：只登记差异，不改计碳逻辑）
# --------------------------------------------------------------------------
def build_factor_reconciliation(seg: pd.DataFrame, carbon: pd.DataFrame,
                                ledger: pd.DataFrame,
                                threshold_pct: float = FACTOR_RECON_THRESHOLD_PCT
                                ) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """**因子口径对账表**：分档轨 vs 主账轨，逐趟可查。

    背景（口径硬伤，登记在案，不在本模块修）：
        · 分档轨用趟次表的**车辆组代表因子** ``ef_g_km``（6 个代表组锚点）；
        · 主账段表用 **(车型, 载重)** 因子 ``factor_gkm``（17 个组合）。
        两套因子口径不同 → 同一趟的碳值不同，差异**全部来自因子**，与速度、
        里程无关（里程逐趟一致，速度差 ≤5e-4 km/h）。

    本函数**不修改任何计碳结果**：只读取 :func:`bin_carbon` 已算好的
    ``kg_co2_vsp_own`` / ``kg_co2_binned``，与主账物理轨
    (``kg_co2_phys`` 分摊合计) 并排对拍，把差异逐趟落成可查表，
    并给出净差分解（因子效应 / 分档乘数效应）。

    缺失值一律留空（NaN），不写 0。
    """
    need = {"trip_id", "factor_gkm", "kg_co2_phys"}
    missing = need - set(ledger.columns)
    if missing:
        raise ValueError(f"主账段表缺少列：{sorted(missing)}")

    agg_map: Dict[str, Any] = {
        "ledger_factor_gkm": ("factor_gkm", "first"),
        "ledger_kg_phys": ("kg_co2_phys", "sum"),
    }
    for src, dst in (("load_t", "ledger_load_t"),
                     ("vclass", "ledger_vclass"),
                     ("vehicle_type", "ledger_vehicle_type"),
                     ("trip_km", "ledger_dist_km"),
                     ("speed_kmh", "ledger_speed_kmh"),
                     ("factor_version", "ledger_factor_version"),
                     ("vtype_code", "ledger_vtype_code")):
        if src in ledger.columns:
            agg_map[dst] = (src, "first")
    led = ledger.groupby("trip_id", as_index=False).agg(**agg_map)

    left_cols = [c for c in ("trip_id", "month", "speed_kmh", "distance_km",
                             "ef_g_km", "vehicle_group") if c in seg.columns]
    right_cols = [c for c in ("trip_id", "kg_co2_vsp_own", "kg_co2_binned",
                              "passed") if c in carbon.columns]
    df = seg[left_cols].merge(carbon[right_cols], on="trip_id",
                              how="inner", validate="1:1")
    df = df.merge(led, on="trip_id", how="left", validate="1:1")

    df["factor_diff_gkm"] = df["ef_g_km"] - df["ledger_factor_gkm"]
    df["factor_diff_pct"] = df["factor_diff_gkm"] / df["ledger_factor_gkm"] * 100.0
    # 三条差：因子效应（逐趟速度轨 vs 主账）/ 分档乘数效应 / 净差
    df["kg_delta_factor"] = df["kg_co2_vsp_own"] - df["ledger_kg_phys"]
    df["kg_delta_multiplier"] = df["kg_co2_binned"] - df["kg_co2_vsp_own"]
    df["kg_delta_binned"] = df["kg_co2_binned"] - df["ledger_kg_phys"]

    if "passed" in df.columns:
        ok = df[df["passed"]].copy()
    else:
        ok = df.copy()
    unmatched = int(ok["ledger_factor_gkm"].isna().sum())
    ok = ok[ok["ledger_factor_gkm"].notna()]

    mis = ok[ok["factor_diff_pct"].abs() > threshold_pct].copy()

    kg_led = float(ok["ledger_kg_phys"].sum())
    kg_vsp = float(ok["kg_co2_vsp_own"].sum())
    kg_bin = float(ok["kg_co2_binned"].sum())
    mis_led = float(mis["ledger_kg_phys"].sum())
    mis_vsp = float(mis["kg_co2_vsp_own"].sum())
    mis_bin = float(mis["kg_co2_binned"].sum())

    def _pct(num: float, den: float) -> Optional[float]:
        if den == 0.0:
            return None
        return round(num / den * 100.0, 4)

    net = kg_bin - kg_led
    mis_net = mis_bin - mis_led
    summary: Dict[str, Any] = {
        "status": "ok",
        "policy": ("并列轨口径差异登记：分档轨沿用车辆组代表因子，主账按(车型,载重)因子；"
                   "本模块不改计碳逻辑，差异逐趟落成可查表"),
        "ledger_file": "data/processed/baseline_carbon.parquet",
        "ledger_factor_version": (str(led["ledger_factor_version"].iloc[0])
                                  if "ledger_factor_version" in led.columns
                                  and len(led) else None),
        "basis": "主账物理轨 kg_co2_phys（分摊合计，对齐通过门禁的同一趟群）",
        "threshold_pct": float(threshold_pct),
        "trips_compared": int(len(ok)),
        "trips_unmatched_ledger": unmatched,
        "mismatch_trips": int(len(mis)),
        "mismatch_share_pct": round(len(mis) / max(len(ok), 1) * 100.0, 2),
        "mismatch_carbon_share_pct": _pct(mis_bin, kg_bin),
        "median_abs_factor_diff_pct": (round(float(mis["factor_diff_pct"].abs().median()), 2)
                                       if len(mis) else None),
        "max_abs_factor_diff_pct": (round(float(mis["factor_diff_pct"].abs().max()), 2)
                                    if len(mis) else None),
        "kg_ledger_physical_total": round(kg_led, 3),
        "kg_seg_vsp_own_total": round(kg_vsp, 3),
        "kg_seg_binned_total": round(kg_bin, 3),
        "delta_kg_factor_effect": round(kg_vsp - kg_led, 3),
        "delta_pct_factor_effect": _pct(kg_vsp - kg_led, kg_led),
        "delta_kg_multiplier_effect": round(kg_bin - kg_vsp, 3),
        "delta_pct_multiplier_effect": _pct(kg_bin - kg_vsp, kg_vsp),
        "delta_kg_net": round(net, 3),
        "delta_pct_net": _pct(net, kg_led),
        "mismatch_delta_kg_factor": round(mis_vsp - mis_led, 3),
        "mismatch_delta_kg_multiplier": round(mis_bin - mis_vsp, 3),
        "mismatch_delta_kg_net": round(mis_net, 3),
        "mismatch_share_of_net_delta_pct": _pct(mis_net, net),
        "lookup_file": RESULT_RECON,
        "verdict": ("净差 = 因子口径效应 + 分档乘数效应（档均速 VSP 替代本趟速度 VSP）；"
                    "因子效应集中在少数趟，可按 trip_id 查 " + RESULT_RECON),
    }

    out_cols = ["trip_id", "month", "vehicle_group", "ledger_vehicle_type",
                "ledger_vclass", "ledger_load_t", "ledger_vtype_code",
                "speed_kmh", "ledger_speed_kmh", "distance_km", "ledger_dist_km",
                "ef_g_km", "ledger_factor_gkm", "factor_diff_gkm", "factor_diff_pct",
                "ledger_kg_phys", "kg_co2_vsp_own", "kg_co2_binned",
                "kg_delta_factor", "kg_delta_multiplier", "kg_delta_binned",
                "ledger_factor_version", "seg_version", "rule_version"]
    out = mis[[c for c in out_cols if c in mis.columns]].copy()
    out["seg_version"] = SEG_VERSION
    out["rule_version"] = RULE_VERSION
    for c in ("factor_diff_gkm", "factor_diff_pct", "kg_delta_factor",
              "kg_delta_multiplier", "kg_delta_binned"):
        if c in out.columns:
            out[c] = out[c].round(6)
    for c in ("ledger_kg_phys", "kg_co2_vsp_own", "kg_co2_binned"):
        if c in out.columns:
            out[c] = out[c].round(6)
    out = out.sort_values(["factor_diff_pct"], key=lambda s: s.abs(),
                          ascending=False).reset_index(drop=True)
    return out, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="论文锚定速度分段 + K-means 归档")
    parser.add_argument("--out", default="data/processed",
                        help="输出目录（默认 data/processed）")
    parser.add_argument("--trips", default="data/processed/vsp_physical_trips.parquet",
                        help="输入趟次表路径")
    parser.add_argument("--ledger", default="data/processed/baseline_carbon.parquet",
                        help="主账段表路径（因子口径对账用；缺失则跳过对账）")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    trips = pd.read_parquet(args.trips)
    ledger_path = Path(args.ledger)
    ledger_df = pd.read_parquet(ledger_path) if ledger_path.exists() else None

    seg, audit = build_segments(trips)

    # ---- 多维 K-means 并列轨：只追加 cluster_id，不改分档/计碳 ----
    cluster = segment_cluster.build_cluster_table(
        trips, grain="trip", feature_set="proxy_v1", ledger=ledger_df)
    seg = segment_cluster.attach_cluster_ids(seg, cluster["assignments"], key="trip_id")
    audit["segment_cluster"] = cluster["audit"]

    seg_path = out_dir / RESULT_TRIPS
    audit_path = out_dir / RESULT_AUDIT
    seg.to_parquet(seg_path, index=False)
    cluster["assignments"].to_parquet(out_dir / segment_cluster.RESULT_CLUSTERS, index=False)
    cluster["profiles"].to_parquet(out_dir / segment_cluster.RESULT_PROFILES, index=False)
    cluster["plate_map"].to_parquet(out_dir / segment_cluster.RESULT_PLATE_MAP, index=False)
    (out_dir / segment_cluster.RESULT_AUDIT).write_text(
        json.dumps(cluster["audit"], ensure_ascii=False, indent=2), encoding="utf-8")
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    # ---- 计碳规则卡 + 分档计碳（只定规则，优化在下游 Q-learning）----
    rules = build_rule_table()
    carbon, carbon_summary = bin_carbon(seg, audit)
    rules_path = out_dir / RESULT_RULES
    carbon_path = out_dir / RESULT_CARBON
    rules.to_parquet(rules_path, index=False)
    carbon.to_parquet(carbon_path, index=False)
    audit["carbon"] = carbon_summary
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    # ---- 因子口径对账（方案 B：只登记，不改计碳逻辑）----
    recon_path = out_dir / RESULT_RECON
    if ledger_df is not None:
        recon, recon_summary = build_factor_reconciliation(seg, carbon, ledger_df)
        recon.to_parquet(recon_path, index=False)
        audit["factor_reconciliation"] = recon_summary
    else:
        recon = pd.DataFrame()
        audit["factor_reconciliation"] = {
            "status": "skipped",
            "reason": f"主账段表不存在：{ledger_path}",
            "policy": "并列轨口径差异登记：分档轨沿用车辆组代表因子，主账按(车型,载重)因子",
        }
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    # ---- 京东趟级 trip_proxy 主表（5 档 + 3 簇并列，供聚合/页面只读）----
    # 主表不是新计碳逻辑：carbon_kg 逐值等于 baseline_carbon.kg_co2 按 trip 求和。
    # 输入契约不满足时明确 skip（老测试/旧数据不伪造一张空表）。
    missing_contract = []
    if ledger_df is None:
        missing_contract.append("ledger")
    else:
        missing_contract += sorted(segment_master.LEDGER_REQUIRED - set(ledger_df.columns))
    missing_contract += sorted(segment_master.SEG_REQUIRED - set(seg.columns))
    missing_contract += sorted(segment_master.ASSIGN_REQUIRED
                               - set(cluster["assignments"].columns))
    if missing_contract:
        audit["segment_master"] = {
            "status": "skipped",
            "reason": "输入契约不满足",
            "missing": missing_contract,
        }
    else:
        kpi_path = out_dir / "kpi.json"
        kpi_total = None
        if kpi_path.exists():
            kpi_total = json.loads(kpi_path.read_text(encoding="utf-8")).get("total_kg")
        master, master_audit = segment_master.build_master(
            seg, cluster["assignments"], ledger_df,
            trips=trips, kpi_total_kg=kpi_total)
        master_path = out_dir / segment_master.RESULT_MASTER
        master.to_parquet(master_path, index=False)
        (out_dir / segment_master.RESULT_AUDIT).write_text(
            json.dumps(master_audit, ensure_ascii=False, indent=2), encoding="utf-8")
        audit["segment_master"] = master_audit
        print(f"[vsp_segment] master -> {master_path} ({len(master)} rows); "
              f"carbon {master_audit['carbon_kg']:.3f} kg; "
              f"recon {master_audit['reconciliation'].get('status')}")
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    # 自检输出契约
    for key in (RESULT_TRIPS, RESULT_CARBON, RESULT_RULES, RESULT_RECON,
                segment_cluster.RESULT_CLUSTERS, segment_cluster.RESULT_PROFILES,
                segment_cluster.RESULT_PLATE_MAP, segment_cluster.RESULT_AUDIT,
                segment_master.RESULT_MASTER, segment_master.RESULT_AUDIT):
        assert key in schema.RESULT_FILES.values(), f"{key} 未在 schema.RESULT_FILES 注册"
    print(f"[vsp_segment] trips -> {seg_path} ({len(seg)} rows)")
    print(f"[vsp_segment] audit -> {audit_path}")
    print(f"[vsp_segment] rules -> {rules_path} ({len(rules)} rows)")
    print(f"[vsp_segment] carbon -> {carbon_path} ({len(carbon)} rows)")
    print(f"[vsp_segment] gates: {audit['gates']['passed']}/{audit['gates']['total']} passed; "
          f"K*={audit['kmeans']['k_selected']}; "
          f"centers={audit['kmeans']['centers_kmh']}")
    t = audit["carbon"]["totals"]
    print(f"[vsp_segment] carbon: vsp_own={t['kg_vsp_own_speed']} kg | "
          f"binned={t['kg_binned']} kg | delta={t['delta_pct']}%")
    fr = audit["factor_reconciliation"]
    if fr.get("status") == "ok":
        print(f"[vsp_segment] factor recon -> {recon_path} ({len(recon)} rows); "
              f"mismatch {fr['mismatch_trips']}/{fr['trips_compared']} trips; "
              f"net {fr['delta_kg_net']} kg ({fr['delta_pct_net']}%) = "
              f"factor {fr['delta_kg_factor_effect']} + mult {fr['delta_kg_multiplier_effect']}")
    else:
        print(f"[vsp_segment] factor recon skipped: {fr.get('reason')}")


if __name__ == "__main__":
    main()
