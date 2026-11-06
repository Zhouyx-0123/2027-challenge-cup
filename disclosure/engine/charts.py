# -*- coding: utf-8 -*-
"""
charts.py —— 用 Pillow 画披露底稿里的示意图（不依赖 matplotlib）

目前两张图：
    draw_gap_chart(...)  每条记录的「实际排放 vs 额度」，让“缺口=0”一眼可见
    draw_split_chart(...) 绿框/黄框的排放与成本占比

字体优先用微软雅黑，找不到就退回默认字体（中文可能显示为方块，但不影响程序运行）。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
]

COLOR_TEXT = (0x33, 0x33, 0x33)
COLOR_MUTED = (0x80, 0x80, 0x80)
COLOR_GREEN = (0x37, 0x56, 0x23)
COLOR_YELLOW = (0xBF, 0x8F, 0x00)
COLOR_ALLOW = (0xD6, 0xD6, 0xD6)
COLOR_RED = (0xC0, 0x00, 0x00)
COLOR_LINE = (0xE0, 0xE0, 0xE0)


def _font(size: int, bold: bool = False):
    """按字号取字体，找不到就退回 Pillow 默认字体。"""
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _text(draw, xy, text, font, fill=COLOR_TEXT, anchor="la"):
    """画一行文字。"""
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def draw_gap_chart(rows: list, baseline: float, path: Path, dpi_title: str = "") -> Path:
    """画「实际排放 vs 额度」横向对比图。

    Args:
        rows: [(label, emission_kg, allowance_kg, is_green), ...]
        baseline: 基准线数值，用于副标题。
        path: 输出 PNG 路径。
        dpi_title: 预留参数。

    Returns:
        输出路径。
    """
    n = max(1, len(rows))
    width = 1600
    top = 120
    row_h = 96 if n <= 5 else max(58, 640 // n)
    height = top + n * row_h + 120
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    f_title = _font(38, True)
    f_sub = _font(24)
    f_label = _font(26 if row_h >= 70 else 20)
    f_val = _font(24 if row_h >= 70 else 18, True)
    f_legend = _font(24)

    _text(draw, (40, 34), "逐条输入排放 vs 情景额度（仅用于解释明细）", f_title)
    _text(draw, (40, 84), f"有效情景阈值 {baseline:.4f} kg/km＝假设强度×控排系数；非主管部门核定配额",
          f_sub, COLOR_MUTED)

    bar_x = 470
    bar_max = width - bar_x - 190
    max_val = max([max(e, a) for _, e, a, _ in rows] + [1.0])

    for i, (label, emission, allowance, is_green) in enumerate(rows):
        y = top + i * row_h
        gap_h = max(8, int(row_h * 0.36))
        _text(draw, (40, y + 14), label, f_label)

        # 额度（灰）
        w_allow = max(2, int(bar_max * allowance / max_val))
        draw.rounded_rectangle([bar_x, y + 2, bar_x + w_allow, y + 2 + gap_h], radius=5, fill=COLOR_ALLOW)
        _text(draw, (bar_x + w_allow + 12, y + 2), f"{allowance:,.2f}", f_val, COLOR_MUTED)

        # 实际（绿/黄）
        over = emission > allowance
        color = COLOR_RED if over else (COLOR_GREEN if is_green else COLOR_YELLOW)
        w_emit = max(2, int(bar_max * emission / max_val))
        y2 = y + gap_h + 8
        draw.rounded_rectangle([bar_x, y2, bar_x + w_emit, y2 + gap_h], radius=5, fill=color)
        _text(draw, (bar_x + w_emit + 12, y2), f"{emission:,.2f}", f_val, color)

        # 额度参考线（虚线），让“还差多少”一眼可见
        lx = bar_x + w_allow
        dash_color = COLOR_RED if over else (0x9E, 0x9E, 0x9E)
        for yy in range(y + 1, y2 + gap_h + 3, 9):
            draw.line([lx, yy, lx, min(yy + 5, y2 + gap_h + 3)], fill=dash_color, width=2)

    ly = top + n * row_h + 26
    draw.rectangle([40, ly + 6, 76, ly + 30], fill=COLOR_GREEN)
    _text(draw, (88, ly + 6), "绿框（范围一）实际", f_legend)
    draw.rectangle([330, ly + 6, 366, ly + 30], fill=COLOR_YELLOW)
    _text(draw, (378, ly + 6), "黄框（范围三）实际", f_legend)
    draw.rectangle([620, ly + 6, 656, ly + 30], fill=COLOR_ALLOW)
    _text(draw, (668, ly + 6), "情景额度（含控排系数）", f_legend)
    draw.rectangle([960, ly + 6, 996, ly + 30], fill=COLOR_RED)
    _text(draw, (1008, ly + 6), "红色：本行正差额", f_legend)

    draw.line([40, height - 56, width - 40, height - 56], fill=COLOR_LINE, width=2)
    _text(draw, (40, height - 44),
          "净缺口须汇总绿框排放与情景额度后取正；逐条正差额不可直接相加。", f_sub, COLOR_MUTED)

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path
