# -*- coding: utf-8 -*-
"""生成“运输碳的商业问题”单页信息图（汇报/答辩用）。"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Circle
import numpy as np

OUT = r"D:/oceanbase02/可视化_商业问题_20261106.png"

BG = "#0A1220"
PANEL = "#101C2E"
PANEL2 = "#0D1828"
EDGE = "#22324A"
GRID = "#26364D"
TEXT = "#E8F0FA"
MUT = "#8FA3BD"
MINT = "#4FD1C5"
BLUE = "#6C8CFF"
CORAL = "#FF7A59"
GOLD = "#F6C85F"
RED = "#FF5D5D"

plt.rcParams.update({
    "font.family": "Microsoft YaHei",
    "axes.unicode_minus": False,
    "figure.facecolor": BG,
    "savefig.facecolor": BG,
    "text.color": TEXT,
})

fig = plt.figure(figsize=(16, 10.2), dpi=180)

# ---------- header ----------
fig.text(0.045, 0.950, "运输碳的商业问题", fontsize=31, fontweight="bold", color=TEXT)
fig.text(0.045, 0.912, "计算已经过剩，证明严重不足：从“算碳”到“证明碳”", fontsize=13.5, color=MUT)
fig.text(0.955, 0.947, "BUSINESS PROBLEM  ·  2026-11-06", ha="right", fontsize=9.5, color=MUT)
fig.add_artist(plt.Line2D([0.045, 0.955], [0.898, 0.898], color=EDGE, lw=1.0))


def panel(rect, title=None, subtitle=None, face=PANEL, edge=EDGE):
    x, y, w, h = rect
    fig.add_artist(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.008,rounding_size=0.012",
        transform=fig.transFigure, facecolor=face, edgecolor=edge, lw=1.0, zorder=0))
    ax = fig.add_axes(rect)
    ax.set_facecolor("none")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    if title:
        ax.text(0.035, 0.94, title, fontsize=14.5, fontweight="bold", color=TEXT, va="top", transform=ax.transAxes)
    if subtitle:
        ax.text(0.035, 0.865, subtitle, fontsize=9.5, color=MUT, va="top", transform=ax.transAxes)
    return ax


# ---------- top stats ----------
stats = [
    ("90%", "披露物流碳排", "领先货主已经“有数”", MINT),
    ("35%", "采用 ISO 14083/GLEC", "方法仍不统一", BLUE),
    ("41%", "收集供应商数据", "证据链仍未建立", CORAL),
    ("22%", "设物流专项气候目标", "试点多、规模化少", GOLD),
]
for i, (big, label, note, color) in enumerate(stats):
    x = 0.045 + i * 0.231
    ax = panel((x, 0.735, 0.213, 0.145), face=PANEL2)
    ax.text(0.06, 0.78, big, fontsize=26, fontweight="bold", color=color, va="top")
    ax.text(0.06, 0.40, label, fontsize=10.5, color=TEXT, va="top")
    ax.text(0.06, 0.20, note, fontsize=8.5, color=MUT, va="top")
fig.text(0.045, 0.716, "需求侧：披露已经普及，证据没有跟上 ｜ 样本：50 家领先中外货主（IPE CATI 2025）",
         fontsize=9.2, color=MUT)

# ---------- 2x2 positioning ----------
ax = panel((0.045, 0.325, 0.545, 0.375), "竞争定位：空位在“运输专用 × 独立证据”",
           "横轴：计算/报告 → 验证/证据 ｜ 纵轴：通用企业 → 运输专用（定性判断，非市场份额）")
ax.set_xlim(0, 10)
ax.set_ylim(0, 10)
ax.set_xticks([])
ax.set_yticks([])
for spine in ax.spines.values():
    spine.set_visible(False)

# plot area inside panel
ax.plot([0.9, 9.5], [0.8, 0.8], color=EDGE, lw=1)
ax.plot([0.9, 0.9], [0.8, 7.7], color=EDGE, lw=1)
ax.plot([5.0, 5.0], [0.8, 7.7], color=GRID, lw=0.8, ls=(0, (4, 4)))
ax.plot([0.9, 9.5], [4.5, 4.5], color=GRID, lw=0.8, ls=(0, (4, 4)))

# quadrant labels
ax.text(1.15, 7.55, "运输专用 · 计算/自证", fontsize=9.2, color=MUT)
ax.text(5.55, 7.55, "运输专用 · 验证/证据", fontsize=9.2, color=MINT, fontweight="bold")
ax.text(1.15, 1.10, "通用 · 计算/账本", fontsize=9.5, color=MUT)
ax.text(5.25, 1.10, "通用 · 验证", fontsize=9.5, color=MUT)

# axis labels
ax.text(5.2, 0.30, "计算 / 报告  →  验证 / 证据", ha="center", fontsize=9.0, color=MUT)
ax.text(0.48, 4.2, "通用  →  运输专用", rotation=90, va="center", ha="center", fontsize=9.0, color=MUT)

players = [
    ("京东 SCEMP", 1.7, 7.0, CORAL, "承运人自证"),
    ("顺丰丰和", 2.5, 6.2, CORAL, "承运人自证"),
    ("碳阻迹", 1.6, 5.4, CORAL, "核算+咨询"),
    ("G7 / 中交兴路", 3.6, 6.9, BLUE, "有数据，无碳语义"),
    ("Climatiq", 5.2, 5.5, BLUE, "因子 API"),
    ("CTI / BV / SGS", 6.7, 6.8, MINT, "人工核查"),
    ("CO2 Connect", 7.7, 5.8, MINT, "海外证据层"),
    ("SAP Green Ledger", 3.4, 2.8, MUT, "通用碳总账"),
]
for name, x, y, color, note in players:
    ax.scatter([x], [y], s=90, color=color, edgecolor=BG, linewidth=1.2, zorder=3)
    ax.text(x + 0.16, y + 0.14, name, fontsize=8.6, color=TEXT, va="bottom")
    ax.text(x + 0.16, y - 0.20, note, fontsize=7.2, color=MUT, va="top")

# us
ax.scatter([8.55], [7.95], s=520, color=GOLD, alpha=0.16, zorder=2)
ax.scatter([8.55], [7.95], s=230, marker="*", color=GOLD, edgecolor="#FFE7A8", linewidth=0.8, zorder=4)
ax.text(8.55, 8.52, "我们", fontsize=11, color=GOLD, fontweight="bold", ha="center")
ax.text(9.55, 7.35, "独立证据层", fontsize=7.6, color=GOLD, ha="right")

# ---------- five comparisons ----------
ax2 = panel((0.610, 0.325, 0.345, 0.365), "五重比对：到底跟谁比",
            "不是缺公式，是缺把五个数对齐的证据链")
rows = [
    ("申报", "实测", "少报 / 漏报 / 重复"),
    ("油票", "OBD", "账外油 / 偷油 / 分配错"),
    ("实际路线", "合理路线", "绕路 / 空驶"),
    ("本趟", "同线基准", "车型 / 载重错配"),
    ("报告口径", "标准/因子", "边界错 / 因子过期"),
]
y0 = 0.66
for i, (a, b, c) in enumerate(rows):
    y = y0 - i * 0.135
    ax2.add_patch(FancyBboxPatch((0.035, y - 0.035), 0.115, 0.085,
                                 boxstyle="round,pad=0.006,rounding_size=0.012",
                                 facecolor="#17283D", edgecolor=EDGE, lw=0.8))
    ax2.add_patch(FancyBboxPatch((0.205, y - 0.035), 0.115, 0.085,
                                 boxstyle="round,pad=0.006,rounding_size=0.012",
                                 facecolor="#17283D", edgecolor=EDGE, lw=0.8))
    ax2.text(0.0925, y + 0.007, a, ha="center", va="center", fontsize=8.4, color=TEXT)
    ax2.text(0.2625, y + 0.007, b, ha="center", va="center", fontsize=8.4, color=TEXT)
    ax2.annotate("", xy=(0.345, y + 0.007), xytext=(0.325, y + 0.007),
                 arrowprops=dict(arrowstyle="-|>", color=MUT, lw=0.9))
    ax2.text(0.375, y + 0.007, c, fontsize=7.6, color=MUT, va="center")
fig.text(0.610, 0.305, "越靠上的比对，越能直接解释“差额去哪了”。", fontsize=8.2, color=MUT)

# ---------- bottom: what we do ----------
ax3 = panel((0.045, 0.055, 0.545, 0.230), "我们做什么：把“自报碳数”变成“可辩护的碳账”")
chips = [("运单", 0.05), ("油票", 0.20), ("OBD / GPS", 0.35)]
for label, x in chips:
    ax3.add_patch(FancyBboxPatch((x, 0.48), 0.12, 0.16,
                                 boxstyle="round,pad=0.008,rounding_size=0.02",
                                 facecolor="#17283D", edgecolor=EDGE, lw=0.8))
    ax3.text(x + 0.06, 0.56, label, ha="center", va="center", fontsize=8.8, color=TEXT)
ax3.annotate("", xy=(0.53, 0.56), xytext=(0.48, 0.56),
             arrowprops=dict(arrowstyle="-|>", color=MINT, lw=1.4))
ax3.text(0.55, 0.56, "五重比对", fontsize=10.5, color=MINT, fontweight="bold", va="center")
ax3.annotate("", xy=(0.70, 0.56), xytext=(0.65, 0.56),
             arrowprops=dict(arrowstyle="-|>", color=MINT, lw=1.4))
ax3.text(0.72, 0.62, "趟级碳账", fontsize=9.2, color=TEXT, va="center")
ax3.text(0.72, 0.50, "质量等级 / 异常清单 / 审计包", fontsize=8.2, color=MUT, va="center")
ax3.text(0.05, 0.20, "先证明，再减碳。 证据层是入口，路线/车型/能源建议是第二层收入。",
         fontsize=8.8, color=GOLD)

# ---------- bottom right: who pays / not do ----------
ax4 = panel((0.610, 0.055, 0.345, 0.230), "谁付钱 / 不做什么")
ax4.text(0.035, 0.66, "先买单的人", fontsize=9.5, color=MINT, fontweight="bold")
ax4.text(0.035, 0.54, "多承运人货主（Scope 3/出口/绿色采购）", fontsize=8.2, color=TEXT)
ax4.text(0.035, 0.45, "3PL（客户 RFP）· 核查机构（证据自动化）", fontsize=8.2, color=TEXT)
ax4.text(0.035, 0.28, "不做", fontsize=9.5, color=CORAL, fontweight="bold")
ax4.text(0.035, 0.16, "通用碳 SaaS · 全口径盘查 · 承运人自证 · 调度执行", fontsize=7.9, color=MUT)

# ---------- footer ----------
fig.text(0.045, 0.022,
         "数据：IPE CATI 2025 / 中汽碳公示平台 / 中物联 / 360iResearch-GII / 企业公开资料；定位图为定性判断，不是市场份额。",
         fontsize=7.8, color=MUT)
fig.text(0.955, 0.022, "D:/oceanbase02/商业问题定义_20261106.md", ha="right", fontsize=7.8, color=MUT)

fig.savefig(OUT, dpi=180, facecolor=BG)
print(OUT)