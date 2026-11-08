# -*- coding: utf-8 -*-
"""数据链思维图（mind map）：一屏看清 6 条链的现状、口径与运作方式。

2026-11-08 收口修订（实测事实）：
  1) ⑤ 发票链油耗口径改为「油箱周期分摊法」（旧口径说法已从全图删除）；
  2) 新增 G2 三源对账（核验）节点：运单/E0、发票、OBD 三路只读汇入 → 核验 E0，不是 G2 生成 E0；
  3) E0 与 A4 拆成两张独立台账卡：E0「已闭环 28/28」、A4「未批准 0/12 正式行
    （5 实质谓词 0/12 阻断 / 4 结构谓词 12/12 但值是 SYNTHETIC 占位）」，
     两卡只可关联、不可合并/相减；
  4) 新增 δ 回灌虚线：执行 → OBD 实测 → 复盘 → 下一版 EF，旁注「只校 EF 数值，不回写 E0/A4」。
"""
from __future__ import annotations
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Circle, Polygon
from matplotlib.path import Path
from matplotlib.patches import PathPatch
from matplotlib import colors as mcolors
from pathlib import Path as FsPath

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC"]
plt.rcParams["axes.unicode_minus"] = False

BG = "#07080A"
WHITE = "#F3F5F8"
MUTED = "#7C8798"
DIM = "#4A5563"
CHILD = "#C3CDD9"
TEAL = "#5FE3D2"
AMBER = "#E9C46A"
ORANGE = "#FF8A5B"
PURPLE = "#A78BFA"
GREEN = "#7ED957"
BLUE = "#46B7FF"
VERIFY = "#9EC1FF"

W, H = 26.0, 15.2
fig = plt.figure(figsize=(W, H), dpi=200)
fig.patch.set_facecolor(BG)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
ax.set_facecolor(BG)

for i in range(1, 20):
    ax.plot([i / 20, i / 20], [0, 1], color="#FFFFFF", lw=0.4, alpha=0.022, zorder=0)
for j in range(1, 12):
    ax.plot([0, 1], [j / 12, j / 12], color="#FFFFFF", lw=0.4, alpha=0.022, zorder=0)

DASH = (0, (4.0, 2.6))


def box(x, y, w, h, fc, ec, lw=1.0, alpha=1.0, ec_alpha=1.0, r=0.012, z=3):
    p = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=%s" % r,
                       facecolor=fc, edgecolor=ec, linewidth=lw, alpha=alpha, zorder=z)
    p.set_edgecolor(mcolors.to_rgba(ec, ec_alpha))
    ax.add_patch(p)
    return p


def txt(x, y, s, size=10, color=WHITE, weight="normal", ha="left", va="center", z=5, family=None, style="normal"):
    ax.text(x, y, s, fontsize=size, color=color, fontweight=weight, ha=ha, va=va,
            zorder=z, family=family, style=style)


def dashed_arrow(p0, p1, color=VERIFY, rad=0.0, lw=1.15, ms=8, alpha=0.9, z=6):
    a = FancyArrowPatch(p0, p1, arrowstyle="-|>", mutation_scale=ms,
                        connectionstyle="arc3,rad=%s" % rad, linestyle=DASH,
                        color=color, lw=lw, alpha=alpha, zorder=z,
                        shrinkA=0, shrinkB=0)
    ax.add_patch(a)
    return a


# ---------------- 标题 ----------------
txt(0.022, 0.963, "数据链条 · 思维图", 26, WHITE, "bold")
txt(0.022, 0.928,
    "物流企业碳感知智能决策系统（CarbonTide）　实测快照 2026-11-08 10:05　"
    "主账 run 20261027-152051-71d711d6 · 28/28 闸门 PASS · code edc1202",
    9.4, MUTED)

# 图例（5 个状态点 + 1 个虚线语义）
legend = [("已闭环", TEAL), ("未批准", AMBER), ("已跑通", ORANGE), ("数据空", PURPLE), ("演示", GREEN)]
lx = 0.545
for name, color in legend:
    ax.add_patch(Circle((lx, 0.962), 0.0034, facecolor=color, edgecolor="none", alpha=0.95, zorder=6))
    txt(lx + 0.009, 0.962, name, 8.4, color)
    lx += 0.068
ax.plot([lx, lx + 0.013], [0.962, 0.962], color=VERIFY, lw=1.15, ls=DASH, zorder=6)
txt(lx + 0.018, 0.962, "虚线 = 只读核验 / 回灌", 8.2, VERIFY)

# ---------------- 中心 ----------------
cx0, cy0, cw, ch = 0.452, 0.395, 0.096, 0.212
for pad, a in ((0.012, 0.05), (0.006, 0.08)):
    box(cx0 - pad, cy0 - pad, cw + 2 * pad, ch + 2 * pad, BLUE, BLUE, lw=0, alpha=a, ec_alpha=0)
box(cx0, cy0, cw, ch, "#0C1117", BLUE, lw=1.4, ec_alpha=0.55)
txt(0.500, 0.573, "CARBONTIDE", 7.4, BLUE, "bold", ha="center")
txt(0.500, 0.535, "碳感知智能决策", 15.5, WHITE, "bold", ha="center")
txt(0.500, 0.497, "数据链 v2026-11-08", 8.8, MUTED, ha="center")
ax.plot([cx0 + 0.012, cx0 + cw - 0.012], [0.474, 0.474], color="#FFFFFF", lw=0.6, alpha=0.14, zorder=5)
txt(0.500, 0.455, "6 条链 · 2 本账", 9.2, WHITE, ha="center")
txt(0.500, 0.430, "E0 主账 ∣ A4 交付", 7.8, MUTED, ha="center")
txt(0.500, 0.407, "铁律：页面只读，数字只来自 data/processed", 6.8, DIM, ha="center", style="italic")

# ---------------- 分支数据 ----------------
RIGHT = [
    dict(num="①", title="主碳链 · E0 主账", tag="已闭环", color=TEAL, top=0.900, side=1, lines=[
        ("京东运单 36,329 行 → preprocess（去重 3,115、载重换算）", CHILD),
        ("wide_data.parquet　36,329 × 68", CHILD),
        ("optimizer 按 trip_id 分摊 → 33,214 段", CHILD),
        ("baseline_carbon.parquet　33,214 × 75", CHILD),
        ("evaluator → kpi.json：E0 2,258,438.376 kg", TEAL),
        ("12,167 实趟 · 2,174,174 km · 28/28 闸门 PASS", CHILD),
    ]),
    dict(num="②", title="路段 / 聚类 · EF 规则", tag="已闭环", color=BLUE, top=0.602, side=1, lines=[
        ("jd_segment_master 12,167 × 29（passed 12,036）", CHILD),
        ("论文 5 速度档 ＋ KMeans3（silhouette 0.425）", CHILD),
        ("簇 × 载重 EF 共 6 格，覆盖 11,964 趟（99.4%）", CHILD),
        ("有效碳 2,214,933.44 kg｜invalid 43,504.94 kg", CHILD),
        ("→ 簇规则 / 分组聚合 / Q 成本模型", CHILD),
        ("里程 2,174,173.86 km（与主账 delta 0）· EF 只由 δ 校", BLUE),
    ]),
    dict(num="③", title="碳账交付（A4 台账）", tag="未批准", color=AMBER, top=0.304, side=1, lines=[
        ("carbon_ledger_export → carbon_ledger_*.json/parquet", CHILD),
        ("a4_ledger/carbon_ledger_main.xlsx　13 sheet", CHILD),
        ("12 趟行 · 2.439298 t · 全 simulated", CHILD),
        ("正式行 0 → gate NO_APPROVED_ROWS（5 实质谓词 0/12）", AMBER),
        ("前端「碳核算与台账」页只读展示", CHILD),
        ("口径：单趟抽样账 · 不可与 E0 合并/相减", AMBER),
    ]),
]
LEFT = [
    dict(num="④", title="OBD 实测旁路", tag="已跑通", color=ORANGE, top=0.900, side=-1, lines=[
        ("ESP32-C3 → WiFi POST /api/upload_obd_gps", CHILD),
        ("Flask :5000 令牌 401 · 限流 60/min · 审计 JSONL", CHILD),
        ("obd_stream.csv 26 列（ts 统一转 UTC）", CHILD),
        ("obd_pipeline → points / vehicle / trips / summary", CHILD),
        ("2026-11-08 10:01 实收 30 点 → 3.8135 kg · 4.03 km", ORANGE),
        ("校准 10 层（5 × 2）· VSP 对拍 +3.75%", CHILD),
    ]),
    dict(num="⑤", title="财务 / 发票链", tag="数据空", color=PURPLE, top=0.602, side=-1, lines=[
        ("发票图片 / PDF → OCR（DeepSeek Vision，mock 标记）", CHILD),
        ("财务主表 33 列（finance_master_*，当前 0 条）", CHILD),
        ("发票台账 30 列 → 油耗中间层（油箱周期分摊法）", CHILD),
        ("车辆 × 月油耗 → fleet_audit_trail.json", CHILD),
        ("× 断点：不回写 wide_data（invoice_ok = −1）", "#FF6B6B"),
        ("空三列：invoice_no / invoice_ok / invoice_amount", "#8A6A6A"),
    ]),
    dict(num="⑥", title="路线决策 Q-learning", tag="演示", color=GREEN, top=0.304, side=-1, lines=[
        ("310 OD（295 amap_rest）→ 287 可演示", CHILD),
        ("4 策略 → 1,240 条候选链", CHILD),
        ("Q 表：34,440 次更新 / 30 epoch", CHILD),
        ("状态 = 工况簇 × 时段　动作 = 4 策略", CHILD),
        ("路线沙盘页第二 Tab（只读，不进 E0）", CHILD),
        ("产物：config/rl_policy_real.json", CHILD),
    ]),
]

PILL_H = 0.048
PILL_W = 0.250
STEP = 0.0272

for group in (RIGHT, LEFT):
    for b in group:
        color = b["color"]
        side = b["side"]
        px = 0.625 if side == 1 else 0.375 - PILL_W
        py = b["top"] - PILL_H
        x0 = 0.548 if side == 1 else 0.452
        y0 = 0.500
        x1 = px if side == 1 else px + PILL_W
        y1 = py + PILL_H / 2
        dx = 0.035 * side
        path = Path([(x0, y0), (x0 + dx, y0), (x1 - dx, y1), (x1, y1)],
                    [Path.MOVETO, Path.CURVE4, Path.CURVE4, Path.CURVE4])
        ax.add_patch(PathPatch(path, facecolor="none", edgecolor=color, lw=1.9, alpha=0.7, zorder=2))
        box(px, py, PILL_W, PILL_H, color, color, lw=1.1, alpha=0.10, ec_alpha=0.55, r=0.016)
        ax.add_patch(Circle((px + 0.019, py + PILL_H / 2), 0.0105, facecolor=color, edgecolor="none",
                            alpha=0.22, zorder=6))
        txt(px + 0.019, py + PILL_H / 2 - 0.0015, b["num"], 8.4, color, "bold", ha="center")
        txt(px + 0.038, py + PILL_H / 2, b["title"], 11.6, color, "bold")
        txt(px + PILL_W - 0.012, py + PILL_H / 2, b["tag"], 8.4, color, ha="right")
        spine_x = px + 0.038 if side == 1 else px + PILL_W - 0.038
        first_y = py - 0.0225
        last_y = first_y - STEP * (len(b["lines"]) - 1)
        ax.plot([spine_x, spine_x], [py, last_y], color=color, lw=0.9, alpha=0.35, zorder=2)
        for k, (line, lc) in enumerate(b["lines"]):
            y = first_y - k * STEP
            tick = 0.010 * side
            ax.plot([spine_x, spine_x + tick], [y, y], color=color, lw=0.9, alpha=0.45, zorder=2)
            ax.add_patch(Circle((spine_x + tick + 0.004 * side, y), 0.0016,
                                facecolor=color, edgecolor="none", alpha=0.75, zorder=6))
            tx = spine_x + tick + 0.010 * side
            txt(tx, y, line, 9.5, lc, ha="left" if side == 1 else "right")

# ---------------- 顶部中间：G2 三源对账（核验） ----------------
gx, gy, gw, gh = 0.420, 0.644, 0.160, 0.148
box(gx, gy, gw, gh, VERIFY, VERIFY, lw=1.2, alpha=0.07, ec_alpha=0.60, r=0.012)
txt(gx + 0.010, gy + gh - 0.014, "G2 三源对账（核验）", 9.2, VERIFY, "bold")
txt(gx + gw - 0.010, gy + gh - 0.014, "只读", 6.8, MUTED, ha="right")
ax.plot([gx + 0.010, gx + gw - 0.010], [gy + gh - 0.028, gy + gh - 0.028], color="#FFFFFF", lw=0.6, alpha=0.14, zorder=5)

rows = [
    ("① 运单/E0 · 36,329 行", TEAL, 0.744),
    ("⑤ 发票 · 台账 0 条", PURPLE, 0.722),
    ("④ OBD 实测 · 30 点", ORANGE, 0.700),
]
for label, color, ry in rows:
    ax.add_patch(Circle((gx + 0.014, ry), 0.0042, facecolor=color, edgecolor="none", alpha=0.85, zorder=6))
    txt(gx + 0.024, ry, label, 6.8, color)
    ax.plot([gx + 0.014, gx + gw - 0.086], [ry, ry], color=color, lw=0.7, alpha=0.18, zorder=2)
    dashed_arrow((gx + gw - 0.086, ry), (gx + gw - 0.064, 0.722), color=color, rad=0.0, lw=1.0, ms=6.5, alpha=0.95)
ax.plot([gx + gw - 0.062, gx + gw - 0.062], [0.688, 0.756], color=AMBER, lw=1.6, alpha=0.85, zorder=6)
txt(gx + gw - 0.055, 0.740, "核验 E0", 7.6, AMBER, "bold")
txt(gx + gw - 0.055, 0.720, "不生成 E0", 6.4, MUTED)
txt(gx + gw - 0.055, 0.702, "不改任何数", 6.4, MUTED)
txt(gx + 0.010, 0.672, "现状：发票源空 → 仅运单/E0 × OBD 两源可核", 6.7, MUTED)
txt(gx + 0.010, 0.657, "结论：E0 闸门 28/28；A4 不并入两源账", 6.7, CHILD)

# ---------------- 底部中间：两本账（E0 / A4 分开画） ----------------
# 中心 → 两本账
ax.plot([0.500, 0.500], [0.395, 0.305], color="#FFFFFF", lw=0.9, alpha=0.16, zorder=2)
txt(0.500, 0.296, "两本账 · 可关联、不可合并/相减", 7.6, AMBER, "bold", ha="center")

e0x, a4x, cwy, cwh = 0.394, 0.510, 0.200, 0.090
for cx, color, num, name, big, l2, l3, l4 in (
    (e0x, TEAL, "①", "E0 主账", "2,258,438.376 kg", "已闭环 28/28 闸门", "10 目标 + 18 结构", "12,167 趟 · 2,174,174 km"),
    (a4x, AMBER, "③", "A4 交付台账", "2.439298 t", "未批准 · 正式行 0/12", "5 实质谓词 0/12 阻断", "12 行全 simulated"),
):
    box(cx, cwy, 0.096, cwh, color, color, lw=1.1, alpha=0.09, ec_alpha=0.75, r=0.010)
    ax.plot([cx + 0.008, cx + 0.088], [cwy + cwh - 0.008, cwy + cwh - 0.008], color=color, lw=1.6, alpha=0.8, zorder=6)
    txt(cx + 0.008, cwy + cwh - 0.022, "%s %s" % (num, name), 7.6, color, "bold")
    txt(cx + 0.008, cwy + cwh - 0.042, big, 9.8, WHITE, "bold")
    txt(cx + 0.008, cwy + cwh - 0.060, l2, 7.4, color)
    txt(cx + 0.008, cwy + cwh - 0.073, l3, 6.5, MUTED)
    txt(cx + 0.008, cwy + cwh - 0.084, l4, 6.5, MUTED)

# 两卡之间：可关联但不可合并/相减
ax.plot([e0x + 0.096, a4x], [0.245, 0.245], color=AMBER, lw=1.0, ls=DASH, alpha=0.8, zorder=4)
ax.add_patch(Circle((0.500, 0.245), 0.0078, facecolor=BG, edgecolor=AMBER, lw=1.0, zorder=6))
txt(0.500, 0.244, "≠", 7.6, AMBER, "bold", ha="center")

# ---------------- 底部：δ 回灌闭环（虚线，只校 EF） ----------------
chip_w, chip_gap, chip_y, chip_h = 0.050, 0.011, 0.112, 0.034
steps = [("执行", TEAL), ("OBD 实测", ORANGE), ("复盘", CHILD), ("下一版 EF", BLUE)]
x = 0.500 - (len(steps) * chip_w + (len(steps) - 1) * chip_gap) / 2
chip_x = []
for name, color in steps:
    box(x, chip_y, chip_w, chip_h, color, color, lw=0.9, alpha=0.10, ec_alpha=0.65, r=0.008)
    txt(x + chip_w / 2, chip_y + chip_h / 2 - 0.001, name, 6.9, color, "bold", ha="center")
    chip_x.append(x)
    x += chip_w + chip_gap
for i in range(len(steps) - 1):
    dashed_arrow((chip_x[i] + chip_w, chip_y + chip_h / 2), (chip_x[i + 1], chip_y + chip_h / 2),
                 color=VERIFY, lw=1.0, ms=6.0, alpha=0.9)

# 回灌回路：从「下一版 EF」回到「执行」
rx0 = chip_x[0] + chip_w / 2
rx1 = chip_x[-1] + chip_w / 2
ay = 0.162
ret = Path([(rx1, chip_y + chip_h), (rx1, ay - 0.004), (rx1 - 0.004, ay), (rx0 + 0.004, ay),
            (rx0, ay), (rx0, ay - 0.004), (rx0, chip_y + chip_h)],
           [Path.MOVETO, Path.LINETO, Path.CURVE3, Path.LINETO,
            Path.CURVE3, Path.LINETO, Path.LINETO])
ax.add_patch(PathPatch(ret, facecolor="none", edgecolor=VERIFY, lw=1.05, ls=DASH, alpha=0.85, zorder=5))
ax.add_patch(Polygon([(rx0 - 0.0038, chip_y + chip_h + 0.0075), (rx0 + 0.0038, chip_y + chip_h + 0.0075),
                      (rx0, chip_y + chip_h + 0.0005)], closed=True, color=VERIFY, zorder=6))
txt(rx0 + 0.008, 0.172, "回灌", 6.6, VERIFY)
txt(0.500, 0.172, "δ 只校 EF 数值，不回写 E0/A4", 6.8, AMBER, "bold", ha="center")

# ---------------- 底部：运行面 ----------------
by, bh = 0.022, 0.058
box(0.020, by, 0.960, bh, "#0B0F14", "#FFFFFF", lw=0.8, ec_alpha=0.10, r=0.010)
items = [
    ("决策台", "Streamlit :8501 · 六页只读", BLUE),
    ("网关", "Flask :5000 · 只校验落盘，不算碳", ORANGE),
    ("主链复算", "python -m src.pipeline", TEAL),
    ("实测轨", "python -m src.obd_pipeline", ORANGE),
    ("契约", "obd-gps-contract-v1 · telemetry_contract", PURPLE),
    ("体检", "python -m tools.doctor / tools.launch --check", AMBER),
]
seg = 0.960 / len(items)
for i, (k, v, color) in enumerate(items):
    x = 0.020 + i * seg + 0.018
    ax.plot([x - 0.010, x - 0.010], [by + 0.012, by + bh - 0.012], color="#FFFFFF", lw=0.6, alpha=0.10, zorder=4)
    txt(x, by + bh - 0.019, k, 8.2, color, "bold")
    txt(x, by + 0.017, v, 7.6, MUTED)

out = FsPath(__file__).resolve().parent / "mind_map_data_chain_20261108.png"
out.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(out, facecolor=BG, dpi=200, pil_kwargs={"compress_level": 1})
print("saved", out, out.stat().st_size)
