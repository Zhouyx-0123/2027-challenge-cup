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

用法::

    python -m src.vsp_segment --out data/processed
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from src import schema
from src.factor_model import vsp_from_speed

#: 版本：档位边界或门禁规则变更必须 bump
SEG_VERSION = "PAPER-BIN-v1"

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


# --------------------------------------------------------------------------
# 数据门禁
# --------------------------------------------------------------------------
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
    x = np.sort(np.asarray(values, dtype=float))
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

    out_cols = ["trip_id", "month", "speed_kmh", "distance_km",
                "vehicle_group", "speed_bin", "speed_flag_seg", "passed"]
    cols = [c for c in out_cols if c in df.columns]
    return df[cols], audit


def main() -> None:
    parser = argparse.ArgumentParser(description="论文锚定速度分段 + K-means 归档")
    parser.add_argument("--out", default="data/processed",
                        help="输出目录（默认 data/processed）")
    parser.add_argument("--trips", default="data/processed/vsp_physical_trips.parquet",
                        help="输入趟次表路径")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    trips = pd.read_parquet(args.trips)

    seg, audit = build_segments(trips)
    seg_path = out_dir / RESULT_TRIPS
    audit_path = out_dir / RESULT_AUDIT
    seg.to_parquet(seg_path, index=False)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    # 自检输出契约
    assert RESULT_TRIPS in schema.RESULT_FILES.values(), \
        "vsp_seg_trips 未在 schema.RESULT_FILES 注册"
    print(f"[vsp_segment] trips -> {seg_path} ({len(seg)} rows)")
    print(f"[vsp_segment] audit -> {audit_path}")
    print(f"[vsp_segment] gates: {audit['gates']['passed']}/{audit['gates']['total']} passed; "
          f"K*={audit['kmeans']['k_selected']}; "
          f"centers={audit['kmeans']['centers_kmh']}")


if __name__ == "__main__":
    main()
