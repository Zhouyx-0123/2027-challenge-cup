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
from typing import Any, Dict

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = ROOT / "data" / "processed"
WIDE_PATH = PROCESSED_DIR / "wide_data.parquet"
BASELINE_PATH = PROCESSED_DIR / "baseline_carbon.parquet"

FACTOR_VERSION = "WRZ2024-T8-v1"
FACTOR_SOURCE = "吴睿中等（2024）表 8；VSP=2 kW/t 工况；30 t → 14.67 g/s"

# O1 · 因子表：载重锚点(t) → 法定因子(g CO₂/km)
# 6 个锚点与附件2 的 6 类登记车型一一对应；30 t 锚点 = 1055.95 g/km，
# 在 50 km/h 下正好是 14.666 g/s（= 1055.95 / 72）。
LOAD_ANCHORS = np.array([3.0, 4.5, 12.0, 18.0, 30.0, 45.0], dtype=float)
FACTOR_ANCHORS = np.array([320.18, 394.56, 544.75, 759.24, 1055.95, 1581.84], dtype=float)

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

    # 法定轨
    factor = factor_lookup(load)
    kg_legal = factor * dist / 1000.0

    # 物理轨：瞬时排放率(g/s) × 行驶秒数
    vsp = vsp_from_speed(speed)
    er = factor_lookup(load) / 72.0 * np.interp(vsp, VSP_X, VSP_MULT)
    kg_phys = er * (dist / speed) * SECONDS_PER_HOUR / 1000.0

    delta = np.where(kg_legal > 0, (kg_phys - kg_legal) / kg_legal * 100.0, 0.0)
    out_range = (load_raw < LOAD_ANCHORS[0]) | (load_raw > LOAD_ANCHORS[-1])

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
    print("=" * 78)
