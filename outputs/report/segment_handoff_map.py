# -*- coding: utf-8 -*-
"""路段碳交付的结构适配图（给碳账同学）。"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False

BG = "#07080A"
CARD = "#11151B"
TEAL = "#5FE3D2"
BLUE = "#46B7FF"
ORANGE = "#FF8A5B"
GOLD = "#E9C46A"
GREY = "#8A97A8"

fig, ax = plt.subplots(figsize=(16, 9), dpi=200)
fig.patch.set_facecolor(BG)
ax.set_facecolor(BG)
ax.set_xlim(0, 160); ax.set_ylim(0, 90); ax.axis("off")


def card(x, y, w, h, title, lines, color, bold_title=True):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.5,rounding_size=1.0",
                                linewidth=1.5, edgecolor=color, facecolor=CARD))
    ax.text(x + 1.6, y + h - 2.6, title, color=color, fontsize=13.5,
            fontweight="bold" if bold_title else "normal", va="top")
    for i, ln in enumerate(lines):
        ax.text(x + 1.7, y + h - 6.6 - i * 3.2, ln, color="#D6DEE8", fontsize=10, va="top")


def arrow(x1, y1, x2, y2, color=GREY, ls="-"):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=15,
                                 linewidth=1.4, color=color, linestyle=ls))


ax.text(5, 85, "路段碳 → 财务主表：结构适配", color="#FFFFFF", fontsize=23, fontweight="bold")
ax.text(5, 80.5, "12,167 段法定轨（里程×因子）+ 40 段守恒轨（OBD 油量×2.63）｜同结构、不混算",
        color=GREY, fontsize=10.5)

card(5, 55, 30, 21, "① 路段碳包", [
    "01_路段碳  33 列",
    "02_运单分摊  33,214 行",
    "03_28列入驻版  28+3 列",
    "04_字段字典 / 05_校验",
], BLUE)
card(40, 55, 32, 21, "② 你们的主表 28 列", [
    "…第 21 列：趟或段ID",
    "车牌号 / 核算批次ID",
    "双验判定 / 是否允许入账",
    "× 没有里程、没有碳排列",
], GOLD)
card(77, 55, 34, 21, "③ 补 3 列（显式）", [
    "里程（km）/ 碳排放（kgCO2e）",
    "计量方法 = legal / obd / vsp",
    "不塞备注：可求和、可筛选",
    "主表 28 列一字不改",
], TEAL)
card(116, 55, 39, 21, "⑤ 财务主表_路段碳", [
    "12,167 行 × 31 列",
    "碳 2,258,438.376 kg｜2,174,173.86 km",
    "A4 正式行仍 0（5 实质谓词 0/12）",
    "双验=待核验 / 入账=待确认",
], TEAL)

card(5, 28, 31, 20, "⑥ 守恒轨样例（另一条轨）", [
    "8 台 / 40 段 / 10,000 点",
    "2,524.7679 kg（油量×2.63）",
    "同 28+3 列，可走同一录入",
    "2026-11，与 12,167 段不重叠",
], ORANGE)
card(41, 28, 31, 20, "④ 录入.py", [
    "读 03 表 → 校验 → 按记录ID 去重",
    "临时文件原子替换，重跑 0 行",
    "另存 emission_fact.csv",
    "Excel 占用时报错，不写坏原表",
], ORANGE)

arrow(35.6, 65.5, 39.4, 65.5, BLUE)
arrow(72.6, 65.5, 76.4, 65.5, GOLD)
arrow(111.6, 65.5, 115.4, 65.5, TEAL)
arrow(94, 54.4, 63, 48.6, ORANGE, ls="--")
arrow(72.6, 42, 130, 54.4, ORANGE, ls=":")

ax.text(5, 22.5, "三条红线", color="#FFFFFF", fontsize=12, fontweight="bold")
red = [
    ("口径", "TTW 2.63 vs WTW 3.16 → 差 20.2%，是边界不同不是误差；对账先统一到 L 或 TTW"),
    ("两轨", "法定轨 12,167 段 × 守恒轨 40 段不可相加/相减；A4 必须按 method 分账"),
    ("未批准", "字段齐备 ≠ 真实性证据：5 条实质谓词 0/12，任何行不得标「已批准」"),
]
for i, (k, v) in enumerate(red):
    y = 17.6 - i * 4.2
    ax.text(5, y, k, color=GOLD, fontsize=10, fontweight="bold")
    ax.text(14, y, v, color="#C9D3DE", fontsize=9.8)

out = (Path(__file__).resolve().parents[2] / "交付_20261108_财务与碳账"
       / "05_路段碳_给碳账同学" / "结构适配图.png")
fig.savefig(out, facecolor=BG, bbox_inches="tight", pad_inches=0.35)
print("OK", out, out.stat().st_size)
