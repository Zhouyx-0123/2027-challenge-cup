# -*- coding: utf-8 -*-
"""数据流总览：碳主链 x 财务链 x 实测旁路（汇报级单页）。"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(r"D:/oceanbase02/可视化_数据流_20261107.png")

BG = "#08111F"
PANEL = "#0D1A2B"
BOX = "#101F33"
GRID = "#1C2F49"
WHITE = "#F4F7FB"
MUTED = "#A9B8CC"
DIM = "#7F91A8"
CARBON = "#6C8CFF"
FIN = "#F6C85F"
OBD = "#4FD1C5"
RED = "#FF6B6B"
GREEN = "#63D6A3"

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC"]
plt.rcParams["axes.unicode_minus"] = False

W, H = 16.0, 10.2
fig = plt.figure(figsize=(W, H), dpi=220)
fig.patch.set_facecolor(BG)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, 1)
ax.set_ylim(0, 1)
ax.axis("off")


def rrect(x, y, w, h, fc, ec, lw=1.0, alpha=1.0, ls="-", z=2):
    patch = FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.0,rounding_size=0.010",
        linewidth=lw, edgecolor=ec, facecolor=fc,
        alpha=alpha, linestyle=ls, zorder=z,
    )
    ax.add_patch(patch)
    return patch


def card(x, y, w, h, title, lines, color, title_size=10.2, line_size=8.0):
    rrect(x, y, w, h, BOX, color, lw=1.1, alpha=0.98)
    # 左侧色条
    ax.add_patch(Rectangle((x, y), 0.0045, h, color=color, lw=0, zorder=4))
    ax.text(x + 0.012, y + h - 0.014, title, ha="left", va="top",
            fontsize=title_size, color=WHITE, fontweight="bold", zorder=5)
    yy = y + h - 0.040
    for line in lines:
        ax.text(x + 0.012, yy, line, ha="left", va="top",
                fontsize=line_size, color=MUTED, zorder=5)
        yy -= 0.0205


def chip(x, label, value, color):
    rrect(x, 0.872, 0.145, 0.040, "#0C1A2B", GRID, lw=0.8, alpha=0.92)
    ax.text(x + 0.009, 0.899, label, ha="left", va="center",
            fontsize=6.6, color=DIM, zorder=5)
    ax.text(x + 0.009, 0.884, value, ha="left", va="center",
            fontsize=12.0, color=color, fontweight="bold", zorder=5)


def arrow(x1, y1, x2, y2, color, ls="-", lw=1.7, rad=0.0, z=8):
    p = FancyArrowPatch(
        (x1, y1), (x2, y2),
        arrowstyle="-|>", mutation_scale=12,
        linewidth=lw, color=color, linestyle=ls,
        connectionstyle=f"arc3,rad={rad}", zorder=z,
        shrinkA=0, shrinkB=0,
    )
    ax.add_patch(p)


def lane(y0, y1, label, color):
    rrect(0.012, y0, 0.976, y1 - y0, PANEL, GRID, lw=0.8, alpha=0.42, z=1)
    ax.add_patch(Rectangle((0.012, y0), 0.0055, y1 - y0, color=color, lw=0, alpha=0.95, zorder=2))
    ax.text(0.006, (y0 + y1) / 2, label, rotation=90, ha="center", va="center",
            fontsize=8.4, color=color, fontweight="bold", zorder=5)


# ---------------- Header ----------------
ax.text(0.025, 0.973, "数据流总览：碳主链 × 财务链", ha="left", va="top",
        fontsize=21, color=WHITE, fontweight="bold")
ax.text(0.025, 0.940,
        "从原始凭证到趟级碳账与费用主表　实线=已落盘　虚线=代码已备但当前无数据/未接线　红虚线=断点",
        ha="left", va="top", fontsize=8.4, color=MUTED)
ax.text(0.975, 0.973, "2026-11-07", ha="right", va="top",
        fontsize=8.0, color=DIM)
ax.text(0.975, 0.951, "以 data/processed 与 outputs 实际文件为准", ha="right", va="top",
        fontsize=6.8, color=DIM)

# ---------------- Metric strip ----------------
xs = [0.025 + i * 0.158 for i in range(6)]
chip(xs[0], "运单原始行", "36,329", CARBON)
chip(xs[1], "去重后段", "33,214", CARBON)
chip(xs[2], "物理趟", "12,167", CARBON)
chip(xs[3], "E0 主账 kgCO2", "2,258,438", GREEN)
chip(xs[4], "主闸门", "28 / 28", GREEN)
chip(xs[5], "财务主表记录", "0 条", FIN)

# ---------------- Lane 1: carbon ----------------
lane(0.685, 0.845, "碳主链", CARBON)
bx = [0.025, 0.220, 0.415, 0.610, 0.805]
bw = 0.170
by = 0.705
bh = 0.105

card(bx[0], by, bw, bh, "原始运单",
     ["36,329 行 · 京东 JDB", "订单 + 运单 + 车型表"], CARBON)
card(bx[1], by, bw, bh, "preprocess.py",
     ["清洗 · 去重 3,115 · 载重换算", "wide_data.parquet 36,329×68"], CARBON)
card(bx[2], by, bw, bh, "optimizer.py",
     ["按 trip_id 分摊到段", "baseline_carbon.parquet 33,214×75"], CARBON)
card(bx[3], by, bw, bh, "evaluator.py",
     ["方案 · 热点 · OD 带宽", "kpi.json  E0 2,258,438.38 kg"], CARBON)
card(bx[4], by, bw, bh, "页面 / 导出",
     ["首页 · 碳核算台账 · 路线沙盘", "12,167 趟 · 28/28 PASS"], CARBON)

for i in range(4):
    arrow(bx[i] + bw, by + bh / 2, bx[i + 1] - 0.003, by + bh / 2, CARBON)

# ---------------- Lane 2: finance ----------------
lane(0.385, 0.645, "财务链", FIN)
ty = 0.525
th = 0.095
byf = 0.395

card(bx[0], ty, bw, th, "发票图片 / PDF",
     ["上传 · 批量目录", "票图 / 票面字段"], FIN, title_size=9.8, line_size=7.6)
card(bx[1], ty, bw, th, "OCR 内核",
     ["src/invoice.py · DeepSeek Vision", "mock 标记 · 字段归一"], FIN, title_size=9.8, line_size=7.6)
card(bx[2], ty, bw, th, "财务主表 33 列",
     ["finance_master_real/simulated.xlsx", "当前 0 条 · 证据图 sha256"], FIN, title_size=9.8, line_size=7.6)
card(bx[3], ty, bw, th, "双验 / 费用归集",
     ["费用类别 · 税额 · 抵扣", "待核验 / 待确认"], FIN, title_size=9.8, line_size=7.6)
card(bx[4], ty, bw, th, "Excel 报表",
     ["下载 · 人工归集", "未自动出报表"], FIN, title_size=9.8, line_size=7.6)

card(bx[2], byf, bw, th, "发票台账 30 列",
     ["outputs/invoice_ledger.csv", "当前无文件"], FIN, title_size=9.8, line_size=7.6)
card(bx[3], byf, bw, th, "油耗中间层",
     ["满箱法：同车牌相邻加油", "按 trip_km 分摊"], FIN, title_size=9.8, line_size=7.6)
card(bx[4], byf, bw, th, "车辆×月油耗",
     ["实测 L/100km · 异常线索", "fleet_audit_trail.json"], FIN, title_size=9.8, line_size=7.6)

for i in range(3):
    arrow(bx[i] + bw, ty + th / 2, bx[i + 1] - 0.003, ty + th / 2, FIN)
# OCR -> invoice ledger (batch path)
arrow(bx[1] + bw / 2, ty, bx[2] + bw / 2, byf + th + 0.002, FIN, rad=-0.12)
for i in range(2):
    arrow(bx[2 + i] + bw, byf + th / 2, bx[3 + i] - 0.003, byf + th / 2, FIN)

# same invoice, two sinks: dashed bridge
arrow(bx[2] + bw / 2, ty, bx[2] + bw / 2, byf + th, FIN, ls="--", lw=1.0)
ax.text(bx[2] + bw / 2 + 0.008, (ty + byf + th) / 2, "未自动同步", ha="left", va="center",
        fontsize=6.3, color=FIN, alpha=0.9, zorder=6)

# red break: finance master does not write wide_data
arrow(bx[2] + bw / 2, ty + th, bx[1] + bw / 2, by - 0.002, RED, ls="--", lw=1.5)
ax.text(0.505, 0.650,
        "未回写 wide_data\ninvoice_ok 全 = -1", ha="left", va="bottom",
        fontsize=6.7, color=RED, linespacing=1.35, zorder=10,
        bbox=dict(boxstyle="round,pad=0.28", facecolor="#08111F", edgecolor=RED, linewidth=0.6, alpha=0.92))

# ---------------- Lane 3: OBD ----------------
lane(0.145, 0.335, "实测旁路", OBD)
oy = 0.210
oh = 0.095
card(bx[0], oy, bw, oh, "OBD-II / 车端",
     ["ESP32-C3 · J1939 250kbps", "WiFi → HTTP"], OBD, title_size=9.8, line_size=7.6)
card(bx[1], oy, bw, oh, "Flask 接入",
     ["server.py", "/api/upload_obd_gps"], OBD, title_size=9.8, line_size=7.6)
card(bx[2], oy, bw, oh, "obd_stream.csv",
     ["outputs/obd_gps", "60 次/分钟限流"], OBD, title_size=9.8, line_size=7.6)
card(bx[3], oy, bw, oh, "obd_pipeline",
     ["src/obd_gps.py", "规范化 · 分趟 · 清洗"], OBD, title_size=9.8, line_size=7.6)
card(bx[4], oy, bw, oh, "OBD 校准",
     ["10 层 = 5 速度 × 2 载重", "factor 1.0 · prior_only"], OBD, title_size=9.8, line_size=7.6)

for i in range(4):
    arrow(bx[i] + bw, oy + oh / 2, bx[i + 1] - 0.003, oy + oh / 2, OBD)

ax.text(0.025, 0.170,
        "当前只有 demo：obd_points 6,600 点 / 2 条轨迹　不写 E0 主账　不进 28 闸门",
        ha="left", va="center", fontsize=7.0, color=DIM)

# ---------------- Bottom status cards ----------------
cards = [
    (0.025, "碳主链：已闭环", [
        "36,329 → 33,214 段 → 12,167 趟",
        "E0 2,258,438.38 kg · 28/28 PASS",
        "wide_data / baseline / kpi 均在",
    ], GREEN),
    (0.260, "财务链：代码齐、数据空", [
        "33 列主表 + 30 列台账 + 满箱法",
        "outputs/finance 等三处均不存在",
        "当前 0 条；主表未回写碳链",
    ], FIN),
    (0.495, "唯一弱连接：wide_data 发票 3 列", [
        "invoice_no / invoice_ok / invoice_amount",
        "invoice_ok = -1（36,329 行全量）",
        "invoice_amount 全空",
    ], RED),
    (0.730, "旁路：不改变 E0", [
        "OBD demo 6,600 点；校准 factor 1.0",
        "路段主表 12,167×29（5 档+3 簇）",
        "Q-learning 287 OD / 4 策略",
    ], OBD),
]
for x, title, lines, color in cards:
    rrect(x, 0.030, 0.230, 0.086, "#0C1828", color, lw=0.9, alpha=0.92)
    ax.add_patch(Rectangle((x, 0.030), 0.004, 0.086, color=color, lw=0, zorder=4))
    ax.text(x + 0.010, 0.100, title, ha="left", va="top",
            fontsize=8.4, color=WHITE, fontweight="bold", zorder=5)
    yy = 0.081
    for line in lines:
        ax.text(x + 0.010, yy, line, ha="left", va="top",
                fontsize=6.9, color=MUTED, zorder=5)
        yy -= 0.017

ax.text(0.025, 0.013,
        "来源：data/processed/_source.json、kpi.json、gate_report.json；src/finance_workflow.py、tools/invoice_to_master.py、src/fleet_efficiency.py。文件存在性按本机 2026-11-07 检查。",
        ha="left", va="bottom", fontsize=6.0, color="#5F7288")

OUT.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(OUT, dpi=220, facecolor=BG, bbox_inches="tight", pad_inches=0.10)
print(OUT)
print(OUT.stat().st_size)
