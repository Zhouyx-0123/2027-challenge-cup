# -*- coding: utf-8 -*-
"""OBD 稀疏校准方案单页图：先验、收缩、试点、护栏。"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import FancyBboxPatch

ROOT = Path(__file__).resolve().parents[2]
PROCESSED = ROOT / "data" / "processed"
OUT_DIR = ROOT / "outputs" / "obd_calibration"
PNG = Path(r"D:\oceanbase02\可视化_OBD校准方案_20261106.png")

BG = "#0A1220"
PANEL = "#101C2E"
PANEL2 = "#0E1929"
INK = "#E8F1F8"
MUTED = "#8FA6BC"
TEAL = "#4FD1C5"
BLUE = "#6C8CFF"
CORAL = "#FF7A59"
GOLD = "#F6C85F"
GRID = "#24364A"

SPEED_LABELS = ["低速 <20", "中低速 20-40", "中速 40-60", "中高速 60-80", "高速 ≥80"]
LOAD_LABELS = ["轻载 <20t", "重载 ≥20t"]


def _panel(fig, x, y, w, h):
    fig.patches.append(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.006,rounding_size=0.012",
        transform=fig.transFigure, facecolor=PANEL, edgecolor=GRID, linewidth=1.0, zorder=-10))


def _card(fig, x, y, w, h, value, label, sub, color):
    _panel(fig, x, y, w, h)
    fig.text(x + 0.014, y + h - 0.034, value, color=color, fontsize=20, weight="bold")
    fig.text(x + 0.014, y + h - 0.061, label, color=INK, fontsize=10.5)
    fig.text(x + 0.014, y + 0.017, sub, color=MUTED, fontsize=8.4)


def main() -> int:
    prior = pd.read_csv(OUT_DIR / "prior_5x2.csv")
    cal = pd.read_csv(OUT_DIR / "calibration_5x2.csv")
    audit = pd.read_json(OUT_DIR / "audit.json")
    applied = audit["apply"]
    pilot = audit["pilot"]

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial"]
    plt.rcParams["axes.unicode_minus"] = False
    fig = plt.figure(figsize=(14.6, 9.2), facecolor=BG)

    fig.text(0.035, 0.955, "OBD 稀疏校准方案", color=INK, fontsize=27, weight="bold")
    fig.text(0.035, 0.922, "没有大量实测 OBD，也能先把账算清楚：主账不依赖 OBD，OBD 只做校准轨",
             color=MUTED, fontsize=11.2)
    fig.text(0.965, 0.955, "OBD-CAL-v1 · 2026-11-06", color=TEAL, fontsize=9, ha="right")

    _card(fig, 0.035, 0.800, 0.215, 0.092,
          "2,258,438", "主账 E0 · kgCO2", "油票 + 里程 + 法定因子，不靠 OBD", TEAL)
    _card(fig, 0.265, 0.800, 0.215, 0.092,
          "12,036", "先验样本 · 趟", "5 档速度 × 2 档载重 = 10 层", BLUE)
    _card(fig, 0.495, 0.800, 0.215, 0.092,
          "25 / 层", "完整试点 · 单", "CV=25%、margin=10% 的目标", GOLD)
    _card(fig, 0.725, 0.800, 0.240, 0.092,
          "因子 1.0", "当前无真实 OBD", "10/10 层 prior_only，不冒充校准", CORAL)

    # 左：先验热力图
    _panel(fig, 0.035, 0.300, 0.475, 0.465)
    fig.text(0.055, 0.723, "先验：载重比速度更能解释 EF", color=INK, fontsize=13.5, weight="bold")
    fig.text(0.055, 0.697, "每格上行 = 先验 EF g/km，下行 = 该层碳占比；分母是 passed 的 2,214,933 kg",
             color=MUTED, fontsize=8.4)
    ax = fig.add_axes([0.095, 0.365, 0.385, 0.300])
    ax.set_zorder(3)
    ax.set_facecolor(PANEL)
    grid = np.full((5, 2), np.nan)
    share = np.full((5, 2), np.nan)
    for i, speed in enumerate(["<20", "20-40", "40-60", "60-80", ">=80"]):
        for j, load in enumerate(["light", "heavy"]):
            row = prior[(prior["speed_bin"] == speed) & (prior["load_group"] == load)].iloc[0]
            grid[i, j] = float(row["prior_ef_g_km"])
            share[i, j] = float(row["prior_carbon_kg"]) / 2214933.439918 * 100.0
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list(
        "ef", ["#123047", "#1F6F78", "#4FD1C5", "#F6C85F", "#FF7A59"])
    ax.imshow(grid, cmap=cmap, vmin=400, vmax=1450, aspect="auto")
    ax.set_xticks([0, 1], LOAD_LABELS)
    ax.set_yticks(range(5), SPEED_LABELS)
    ax.tick_params(colors=INK, labelsize=9, length=0)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    for i in range(5):
        for j in range(2):
            ax.text(j, i - 0.10, "%.0f" % grid[i, j], ha="center", va="center",
                    color="#08121F" if grid[i, j] > 900 else "#EAF6FA",
                    fontsize=12, weight="bold")
            ax.text(j, i + 0.20, "碳 %.1f%%" % share[i, j], ha="center", va="center",
                    color="#08121F" if grid[i, j] > 900 else MUTED, fontsize=7.8)
    fig.text(0.095, 0.675, "颜色越暖，EF 越高", color=MUTED, fontsize=7.4)
    fig.text(0.492, 0.675, "60-80 重载 = 51.8% 碳", color=CORAL,
             fontsize=8.2, ha="right", weight="bold")

    # 右：校准与试点
    _panel(fig, 0.530, 0.300, 0.435, 0.465)
    fig.text(0.550, 0.723, "校准：让少量样本说话，但不让 3 单把账拉飞", color=INK, fontsize=13.5, weight="bold")
    fig.text(0.550, 0.697, "每层 w=n/(n+30)，后验=w×OBD+(1-w)×先验；样本不足自动退回先验",
             color=MUTED, fontsize=8.4)

    fig.text(0.550, 0.646, "每层目标", color=MUTED, fontsize=8.5)
    fig.text(0.550, 0.610, "25", color=GOLD, fontsize=30, weight="bold")
    fig.text(0.590, 0.618, "单", color=INK, fontsize=11)
    fig.text(0.550, 0.578, "n = ceil((1.96 × CV / margin)²)", color=TEAL, fontsize=10.2)
    fig.text(0.550, 0.553, "CV=25% · margin=10% → 24.01 → 25", color=MUTED, fontsize=8.2)

    # 两档试点条
    def stage(y, title, n, sub, color):
        _panel(fig, 0.550, y, 0.385, 0.062)
        fig.text(0.562, y + 0.036, title, color=INK, fontsize=9.6, weight="bold")
        fig.text(0.562, y + 0.012, sub, color=MUTED, fontsize=7.8)
        fig.text(0.916, y + 0.018, n, color=color, fontsize=15, weight="bold", ha="right")
    stage(0.475, "最小试点 · 先估 CV", "60", "每层 6 单；只验证链路，不对外定稿", BLUE)
    stage(0.400, "完整试点 · 可定稿", "250", "每层 25 单；10 层全覆盖", GOLD)

    # 状态阶梯
    fig.text(0.550, 0.372, "状态阶梯", color=MUTED, fontsize=8.0)
    states = [("prior_only", "n=0", CORAL), ("provisional", "1–9 或 <200km", GOLD), ("calibrated", "≥10 且 ≥200km", TEAL)]
    x0 = 0.550
    for idx, (name, cond, color) in enumerate(states):
        x = x0 + idx * 0.130
        _panel(fig, x, 0.325, 0.120, 0.038)
        fig.text(x + 0.010, 0.349, name, color=color, fontsize=8.4, weight="bold")
        fig.text(x + 0.010, 0.334, cond, color=MUTED, fontsize=6.8)

    # 底部：当前状态与护栏
    _panel(fig, 0.035, 0.055, 0.930, 0.165)
    fig.text(0.055, 0.185, "当前运行结果", color=INK, fontsize=12, weight="bold")
    fig.text(0.055, 0.147,
             "0 条真实 OBD  ·  10/10 层 prior_only  ·  factor=1.0  ·  校准后合计 = 主账 passed 2,214,933.44 kg",
             color=TEAL, fontsize=9.8)
    fig.text(0.055, 0.119,
             "全量 E0 2,258,438.376 kg 不变  ·  131 条 invalid 单列 43,504.936 kg，不参与校准、不写 0  ·  demo 默认剔除",
             color=MUTED, fontsize=8.6)
    fig.text(0.055, 0.086,
             "护栏：不改 E0  ·  不进 28 闸门  ·  不写 data/processed  ·  不把情景当已实现减排",
             color=CORAL, fontsize=8.6)
    fig.text(0.965, 0.086,
             "下一步：60 单 → 估 CV → 补到 250 单",
             color=GOLD, fontsize=9.4, ha="right", weight="bold")
    fig.text(0.035, 0.022,
             "数据：outputs/obd_calibration  ·  jd_segment_master.parquet  ·  docs/62",
             color="#5E7488", fontsize=7.4)
    fig.text(0.965, 0.022, "SEG-MASTER-v1 / OBD-CAL-v1 · 仅演示口径，不改主账",
             color="#5E7488", fontsize=7.4, ha="right")

    PNG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(PNG, dpi=200, facecolor=BG, bbox_inches="tight", pad_inches=0.08)
    print(PNG, PNG.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
