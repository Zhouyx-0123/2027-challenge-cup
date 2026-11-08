# -*- coding: utf-8 -*-
"""生成“路段口径总览”信息图（单页 PNG，可直接放进汇报/答辩）。"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.segment_aggregate import aggregate_master  # noqa: E402
from src.segment_master import PAPER_BIN_ORDER  # noqa: E402

OUT = ROOT.parent / "可视化_路段聚合_20261106.png"

# 深色底、青/珊瑚双主色：和 DSH 的深色界面一致，投影也不刺眼
BG = "#0A1220"
PANEL = "#101C2E"
EDGE = "#22324A"
GRID = "#26364D"
TEXT = "#E8F0FA"
MUT = "#8FA3BD"
MINT = "#4FD1C5"
BLUE = "#6C8CFF"
CORAL = "#FF7A59"
GOLD = "#F6C85F"

plt.rcParams.update({
    "font.family": "Microsoft YaHei",
    "axes.unicode_minus": False,
    "figure.facecolor": BG,
    "savefig.facecolor": BG,
    "text.color": TEXT,
    "axes.labelcolor": TEXT,
    "xtick.color": MUT,
    "ytick.color": MUT,
})

m = pd.read_parquet(ROOT / "data/processed/jd_segment_master.parquet")
b = aggregate_master(m, "paper_bin")
c = aggregate_master(m, "cluster_id")

b["trip_share"] = b["trips"] / b["trips"].sum() * 100
b["km_share"] = b["distance_km"] / b["distance_km"].sum() * 100
b["carbon_share"] = b["carbon_kg"] / b["carbon_kg"].sum() * 100
c["trip_share"] = c["trips"] / c["trips"].sum() * 100
c["carbon_share"] = c["carbon_kg"] / c["carbon_kg"].sum() * 100

valid = m[m["passed"].fillna(False).astype(bool)]
cross = pd.crosstab(valid["paper_bin"], valid["cluster_id"])
cross = cross.reindex(index=PAPER_BIN_ORDER, columns=[0, 1, 2], fill_value=0)
row_pct = cross.div(cross.sum(axis=1), axis=0) * 100

n_rows = len(m)
n_valid = int(valid.shape[0])
n_invalid = n_rows - n_valid
total_kg = float(m["carbon_kg"].sum())
valid_kg = float(valid["carbon_kg"].sum())
invalid_kg = total_kg - valid_kg

fig = plt.figure(figsize=(16, 11), dpi=180)
fig.text(0.045, 0.952, "路段口径总览", fontsize=30, fontweight="bold", color=TEXT)
fig.text(
    0.045, 0.917,
    "5 档为主 · 3 簇并列 ｜ 一张 trip 主表，两种看法；碳值只来自主账，聚合只加可加字段",
    fontsize=12.5, color=MUT,
)
fig.text(0.955, 0.945, "SEG-MASTER-v1  ·  2026-11-06", ha="right", fontsize=9.5, color=MUT)
fig.add_artist(plt.Line2D([0.045, 0.955], [0.905, 0.905], color=EDGE, lw=1.0))


def panel_title(ax, title: str, subtitle: str) -> None:
    ax.set_title(title, loc="left", fontsize=16, fontweight="bold",
                 color=TEXT, pad=34)
    ax.text(0.0, 1.035, subtitle, transform=ax.transAxes,
            fontsize=9.5, color=MUT, va="bottom")


def style_axes(ax) -> None:
    ax.set_facecolor(PANEL)
    for spine in ax.spines.values():
        spine.set_color(EDGE)
        spine.set_linewidth(1.0)
    ax.tick_params(length=0)


# ---------- 顶部流程 ----------
ax_flow = fig.add_axes([0.045, 0.775, 0.91, 0.105])
ax_flow.set_xlim(0, 1)
ax_flow.set_ylim(0, 1)
ax_flow.axis("off")

steps = [
    ("33,214 运单", "JDVB 凭证\n财务/碳分摊最小单位"),
    ("12,167 趟", "按 trip_id 合并\n一行 = 一趟"),
    ("29 列主表", "碳 2,258,438.376 kg\n= kpi total_kg"),
    ("5 档 + 3 簇", "规则档位 ∥ 运营画像\n碳值不变"),
    ("安全聚合", "过滤 → 求和\n先加后除"),
]
x0, w, gap = 0.015, 0.168, 0.036
for i, (head, sub) in enumerate(steps):
    x = x0 + i * (w + gap)
    box = FancyBboxPatch(
        (x, 0.20), w, 0.62,
        boxstyle="round,pad=0.012,rounding_size=0.018",
        facecolor="#142238", edgecolor=MINT if i in (2, 4) else EDGE,
        linewidth=1.4 if i in (2, 4) else 1.0, transform=ax_flow.transAxes,
    )
    ax_flow.add_patch(box)
    ax_flow.text(x + w / 2, 0.64, head, ha="center", va="center",
                 fontsize=13, fontweight="bold", color=TEXT)
    ax_flow.text(x + w / 2, 0.40, sub, ha="center", va="center",
                 fontsize=8.4, color=MUT, linespacing=1.25)
    if i < len(steps) - 1:
        ax_flow.add_patch(FancyArrowPatch(
            (x + w + 0.006, 0.51), (x + w + gap - 0.006, 0.51),
            arrowstyle="-|>", mutation_scale=12, color=MINT, lw=1.2,
            transform=ax_flow.transAxes,
        ))

# ---------- 5 档 ----------
ax_b = fig.add_axes([0.045, 0.350, 0.440, 0.375])
style_axes(ax_b)
panel_title(ax_b, "5 档：碳集中在中高速档",
            "有效 12,036 趟；三类占比统一用有效样本做分母")

bins = b["paper_bin"].tolist()
y = np.arange(len(bins))
h = 0.24
ax_b.barh(y - h, b["trip_share"], height=h, color=MINT,
          label="趟数占比", zorder=2)
ax_b.barh(y, b["km_share"], height=h, color=BLUE,
          label="里程占比", zorder=2)
ax_b.barh(y + h, b["carbon_share"], height=h, color=CORAL,
          label="碳占比", zorder=2)

for vals, off, col in ((b["trip_share"], -h, MINT),
                       (b["km_share"], 0, BLUE),
                       (b["carbon_share"], h, CORAL)):
    for yy, v in zip(y, vals):
        ax_b.text(v + 1.0, yy + off, f"{v:.1f}%", va="center",
                  fontsize=8.1, color=col)

ax_b.set_yticks(y, [s.replace("档", " ") for s in bins])
ax_b.set_ylim(len(bins) - 0.55, -0.55)
ax_b.set_xlim(0, 88)
ax_b.set_xticks([])
ax_b.grid(axis="x", color=GRID, lw=0.8, zorder=0)
ax_b.legend(loc="upper right", bbox_to_anchor=(1.0, 0.99), ncol=1,
            frameon=False, fontsize=8.2, labelcolor=TEXT,
            handlelength=1.0, borderaxespad=0.0)
ax_b.text(0.99, 0.03, "60–80 档：53.7% 里程 → 61.2% 碳",
          transform=ax_b.transAxes, ha="right", va="bottom",
          fontsize=9.0, color=GOLD)

# ---------- 3 簇 ----------
ax_c = fig.add_axes([0.515, 0.350, 0.440, 0.375])
style_axes(ax_c)
panel_title(ax_c, "3 簇：趟数与碳量明显错位",
            "同一批有效趟；空心=趟数占比，实心=碳占比")

ypos = [2, 1, 0]
profiles = ["簇 0\n短途·轻载", "簇 1\n中距·中载", "簇 2\n长途·重载"]
cluster_colors = [MINT, BLUE, CORAL]
for yy, (_, row), col in zip(ypos, c.iterrows(), cluster_colors):
    trip, carbon = float(row["trip_share"]), float(row["carbon_share"])
    ax_c.plot([trip, carbon], [yy, yy], color=col, lw=3.2, alpha=0.55, zorder=1)
    ax_c.scatter([trip], [yy], s=95, facecolor=BG, edgecolor=col, linewidth=2.0, zorder=3)
    ax_c.scatter([carbon], [yy], s=105, color=col, zorder=4)
    ax_c.text(trip, yy + 0.18, f"趟 {trip:.1f}%", ha="center", va="bottom",
              fontsize=9.0, color=MUT)
    ax_c.text(carbon, yy - 0.20, f"碳 {carbon:.1f}%", ha="center", va="top",
              fontsize=9.0, color=col, fontweight="bold")

ax_c.set_yticks(ypos, profiles)
ax_c.set_ylim(-0.6, 2.65)
ax_c.set_xlim(0, 100)
ax_c.set_xticks([0, 20, 40, 60, 80, 100], ["0", "20", "40", "60", "80", "100%"])
ax_c.grid(axis="x", color=GRID, lw=0.8, zorder=0)
ax_c.text(0.98, 0.97, "簇 2：11.6% 的趟 → 80.5% 的碳",
          transform=ax_c.transAxes, ha="right", va="top",
          fontsize=10.0, color=CORAL, fontweight="bold",
          bbox=dict(boxstyle="round,pad=0.35", facecolor="#1B2638",
                    edgecolor=CORAL, linewidth=0.8))
ax_c.text(0.02, 0.04, "簇 0：55.2% 的趟，只占 3.9% 的碳",
          transform=ax_c.transAxes, ha="left", va="bottom",
          fontsize=9.0, color=MINT)

# ---------- 5×3 热力图 ----------
ax_h = fig.add_axes([0.045, 0.075, 0.470, 0.185])
style_axes(ax_h)
panel_title(ax_h, "5×3 交叉：两套边界不重合",
            "单元格 = 趟数 / 行内占比；颜色越亮，行内集中度越高")
cmap = LinearSegmentedColormap.from_list(
    "segheat", ["#13233A", "#1E5A68", "#2E8C86", "#F6C85F", "#FF7A59"])
im = ax_h.imshow(row_pct.values, cmap=cmap, vmin=0, vmax=100, aspect="auto")
ax_h.set_xticks(range(3), ["簇 0\n短途轻载", "簇 1\n中距中载", "簇 2\n长途重载"])
ax_h.set_yticks(range(5), [s.replace("档", " ") for s in PAPER_BIN_ORDER])
ax_h.tick_params(axis="both", labelsize=8.6)
ax_h.set_xticks(np.arange(-0.5, 3, 1), minor=True)
ax_h.set_yticks(np.arange(-0.5, 5, 1), minor=True)
ax_h.grid(which="minor", color=PANEL, linewidth=4)
ax_h.tick_params(which="minor", length=0)
for i in range(5):
    for j in range(3):
        n = int(cross.iloc[i, j])
        p = float(row_pct.iloc[i, j])
        ax_h.text(j, i, f"{n:,}\n{p:.1f}%", ha="center", va="center",
                  fontsize=8.7, color=BG if p >= 70 else TEXT,
                  fontweight="bold" if p >= 70 else "normal", linespacing=1.15)

# ---------- 聚合守则 ----------
ax_g = fig.add_axes([0.545, 0.075, 0.410, 0.185])
ax_g.set_xlim(0, 1)
ax_g.set_ylim(0, 1)
ax_g.axis("off")
ax_g.add_patch(FancyBboxPatch(
    (0.0, 0.0), 1.0, 1.0,
    boxstyle="round,pad=0.010,rounding_size=0.018",
    facecolor=PANEL, edgecolor=EDGE, linewidth=1.0,
    transform=ax_g.transAxes, clip_on=False,
))
ax_g.text(0.04, 0.90, "聚合守则（唯一入口）", fontsize=14.5,
          fontweight="bold", color=TEXT, va="top")
ax_g.text(0.04, 0.81, "先过滤 → 求和 → 先加后除", fontsize=9.2,
          color=MUT, va="top")
ax_g.text(0.04, 0.70, "可加", fontsize=9.5, color=MINT, fontweight="bold", va="top")
ax_g.text(0.17, 0.70, "carbon_kg · distance_km · duration_h · n_waybills",
          fontsize=8.4, color=TEXT, va="top")
ax_g.text(0.04, 0.58, "比率", fontsize=9.5, color=GOLD, fontweight="bold", va="top")
ax_g.text(0.17, 0.58, "EF = 1000Σ碳 / Σ里程", fontsize=8.8, color=TEXT, va="top")
ax_g.text(0.17, 0.49, "时间加权速度 = Σ里程 / Σ时长", fontsize=8.8, color=TEXT, va="top")
ax_g.text(0.17, 0.40, "吨公里载重 = Σ(载重×里程) / Σ里程", fontsize=8.8, color=TEXT, va="top")
ax_g.text(0.04, 0.29, "禁止", fontsize=9.5, color=CORAL, fontweight="bold", va="top")
ax_g.text(0.17, 0.29, "平均 EF / 平均速度 / 运单数当碳权重 / invalid 当 0",
          fontsize=8.2, color=TEXT, va="top")
ax_g.text(0.04, 0.10, "aggregate_master(master, by) 默认剔除 131 条 invalid；"
                      "include_invalid=True 才进全量分母",
          fontsize=7.9, color=MUT, va="center")

fig.text(
    0.045, 0.030,
    f"数据：jd_segment_master.parquet · {n_rows:,} 行 / 33,214 运单 · "
    f"总碳 {total_kg:,.3f} kg = kpi total_kg · "
    f"有效 {n_valid:,} / invalid {n_invalid}（{invalid_kg:,.3f} kg，{invalid_kg/total_kg*100:.4f}%）",
    fontsize=9.0, color=MUT,
)

OUT.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(OUT, dpi=180, bbox_inches="tight", pad_inches=0.22)
print(OUT)
print("bins", b[["paper_bin", "trips", "trip_share", "km_share", "carbon_share"]].round(2).to_dict("records"))
print("clusters", c[["cluster_id", "trips", "trip_share", "carbon_share"]].round(2).to_dict("records"))
print("cross", cross.to_dict())
