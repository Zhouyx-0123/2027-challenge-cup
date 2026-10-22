# -*- coding: utf-8 -*-
"""情景演算页共用组件（第 7/8 页）：假设值黄框、结论头条、节省计量条、分解图。

边界（docs/20 §11）：
- 本组件**只做展示**：所有数字由调用方从 services（→ src/scenarios.py）取得后传入；
- 颜色/字体走 theme 与 charts 的既有约定，断网可渲染（纯 HTML + Plotly 本地渲染）；
- 假设值一律黄框标注（工程师建议 3 的延伸：估算值黄框，假设值同等待遇）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import streamlit as st

from . import cards

# 分解图四组件配色（与 theme 的 info/accent/accent-2/coral 对齐）
_COMP_COLORS = {
    "vehicle_net_residual": "#6FB1FF",
    "energy": "#5EE7FF",
    "maintenance": "#FFC46B",
    "insurance": "#FF8A5B",
}
_COMP_NAMES = {
    "vehicle_net_residual": "购置（净残值）",
    "energy": "能耗",
    "maintenance": "维护",
    "insurance": "保险",
}


def assumption_note(items: List[str], title: str = "假设值（黄框=非实测，可改）") -> None:
    """假设值清单：黄框逐条列出，评委一眼能看到哪些数字不是实测。"""
    if not items:
        return
    rows = "".join(
        "<li style='margin:2px 0'>" + cards.esc(x) + "</li>" for x in items)
    st.markdown(
        "<div style='border-left:3px solid var(--accent-2);"
        "background:rgba(245,200,107,0.08);border-radius:0 8px 8px 0;"
        "padding:10px 14px;margin:6px 0'>"
        "<div style='font-size:13px;font-weight:500;color:var(--accent-2)'>"
        + cards.esc(title) + "</div>"
        "<ul style='margin:6px 0 0 0;padding-left:18px;font-size:12px;"
        "color:var(--muted);line-height:1.7'>" + rows + "</ul></div>",
        unsafe_allow_html=True,
    )


def verdict(text: str, tone: str = "mint") -> None:
    """结论头条（ChargeMath 的一句话结论）：大字 + 高亮数字由调用方嵌入。"""
    color = {"mint": "var(--accent)", "amber": "var(--accent-2)",
             "coral": "var(--coral)"}.get(tone, "var(--accent)")
    st.markdown(
        "<div style='font-size:19px;font-weight:500;line-height:1.5;"
        "color:var(--text);margin:4px 0 10px 0'>"
        "<span style='color:" + color + "'>■</span> " + text + "</div>",
        unsafe_allow_html=True,
    )


def savings_meter(ice_label: str, bev_label: str, ice_value: float,
                  bev_value: float, unit: str = "万元/年") -> None:
    """节省计量条（ChargeMath live savings meter）：双轨成本对比 + 差额。"""
    peak = max(abs(ice_value), abs(bev_value), 1e-9)
    w_ice = 100.0 * abs(ice_value) / peak
    w_bev = 100.0 * abs(bev_value) / peak
    gap = ice_value - bev_value
    st.markdown(
        "<div class='cm-savings'>"
        "<div class='cm-savings-h'><span>年度成本对比 / ANNUAL COST</span>"
        "<span>单位：%s</span></div>"
        "<div class='cm-savings-row'>"
        "<span class='lbl'>%s</span>"
        "<div class='track'><i class='ice' style='width:%.1f%%'></i></div>"
        "<span class='val'>%s</span></div>"
        "<div class='cm-savings-row'>"
        "<span class='lbl'>%s</span>"
        "<div class='track'><i class='bev' style='width:%.1f%%'></i></div>"
        "<span class='val'>%s</span></div>"
        "<div class='cm-savings-delta'>年差额 <b>%s %s</b>"
        "<span>（燃油 − 电动；负值代表电动更省）</span></div>"
        "</div>"
        % (cards.esc(unit),
           cards.esc(ice_label), w_ice, cards.esc("%.1f" % ice_value),
           cards.esc(bev_label), w_bev, cards.esc("%.1f" % bev_value),
           cards.esc("%+.1f" % gap), cards.esc(unit)),
        unsafe_allow_html=True,
    )


def npv_stacked(npv_by_scenario: Dict[str, Dict[str, float]],
                labels: Optional[Dict[str, str]] = None,
                title: str = "NPV 四分解（元/辆）", height: int = 300) -> None:
    """NPV 分解对比：纯 HTML 堆叠条（不用 Plotly 默认图，避免工具栏/图例压标题）。"""
    labels = labels or {}
    names = list(npv_by_scenario.keys())
    if not names:
        st.caption("暂无 NPV 分解")
        return
    comps = ("vehicle_net_residual", "energy", "maintenance", "insurance")
    totals = []
    for n in names:
        totals.append(sum(max(float(npv_by_scenario[n].get(c, 0.0)) / 1e4, 0.0) for c in comps))
    peak = max(totals) if totals else 0.0
    peak = max(peak, 1e-9)
    legend = "".join(
        "<span><i style='background:%s'></i>%s</span>"
        % (_COMP_COLORS[c], cards.esc(_COMP_NAMES[c])) for c in comps
    )
    rows = []
    for n in names:
        segs = []
        total = 0.0
        for c in comps:
            v = float(npv_by_scenario[n].get(c, 0.0)) / 1e4
            total += v
            w = 100.0 * max(v, 0.0) / peak
            if w <= 0:
                continue
            txt = ("%.1f" % v) if w >= 7 else ""
            segs.append(
                "<span class='cm-npv-seg' style='width:%.3f%%;background:%s' "
                "title='%s：%.2f 万元'>%s</span>"
                % (w, _COMP_COLORS[c], cards.esc(_COMP_NAMES[c]), v, cards.esc(txt))
            )
        rows.append(
            "<div class='cm-npv-row'><div class='cm-npv-name'>%s</div>"
            "<div class='cm-npv-track'>%s</div>"
            "<div class='cm-npv-total'>%s</div></div>"
            % (cards.esc(labels.get(n, n)), "".join(segs), cards.esc("%.1f" % total))
        )
    st.markdown(
        "<div class='cm-npv'>"
        "<div class='cm-npv-h'><span>%s</span><span>单位：万元/辆</span></div>"
        "<div class='cm-npv-legend'>%s</div>%s"
        "<div class='cm-npv-foot'>总长度按最大总成本归一；四项相加 = 该场景 NPV。"
        "数值由调用方从 scenarios 结果表带入，页面只展示。</div>"
        "</div>" % (cards.esc(title), legend, "".join(rows)),
        unsafe_allow_html=True,
    )


def cumulative_chart(cumulative: List[Dict[str, Any]],
                     name_map: Optional[Dict[str, str]] = None,
                     title: str = "车队累计成本（万元，名义口径）",
                     height: int = 320) -> None:
    """累计成本曲线：纯 SVG 展示，避免 Plotly 默认坐标轴/工具栏破坏整体视觉。"""
    name_map = name_map or {}
    series: Dict[str, Dict[int, float]] = {}
    years = set()
    for point in cumulative:
        year = int(point.get("year", 0))
        years.add(year)
        for key, value in point.items():
            if key == "year":
                continue
            name = name_map.get(key, key)
            series.setdefault(name, {})[year] = float(value) / 1e4
    if not series or not years:
        st.caption("暂无累计成本曲线")
        return
    import math

    xs = sorted(years)
    raw_max = max(max(v.values()) for v in series.values()) or 1.0
    step = 1000.0 if raw_max >= 4000 else (500.0 if raw_max >= 1500 else (100.0 if raw_max >= 300 else 10.0))
    ymax = math.ceil(raw_max * 1.08 / step) * step
    W, H = 900, 250
    L, R, T, B = 58, 20, 18, 32
    pw, ph = W - L - R, H - T - B

    def sx(year: int) -> float:
        if len(xs) == 1:
            return L + pw / 2.0
        return L + (year - xs[0]) / float(xs[-1] - xs[0]) * pw

    def sy(value: float) -> float:
        return T + (ymax - value) / ymax * ph

    colors = ["#5EE7FF", "#FF7A5C", "#FFC46B", "#8FB8FF", "#2DD4A7"]
    grid = []
    for i in range(5):
        gy = T + ph * i / 4.0
        val = ymax * (4 - i) / 4.0
        grid.append(
            "<line x1='%d' y1='%.1f' x2='%d' y2='%.1f' class='cm-line-grid'/>"
            "<text x='%d' y='%.1f' class='cm-line-ytxt' text-anchor='end'>%s</text>"
            % (L, gy, W - R, gy, L - 9, gy + 3.5, ("%.0f" % val))
        )
    xlab = []
    for year in xs:
        xlab.append("<text x='%.1f' y='%d' class='cm-line-xtxt' text-anchor='middle'>%d</text>"
                    % (sx(year), H - 10, year))
    paths = []
    legend = []
    for idx, (name, vals) in enumerate(series.items()):
        color = colors[idx % len(colors)]
        pts = [(sx(y), sy(vals[y])) for y in sorted(vals)]
        if not pts:
            continue
        d = "M " + " L ".join("%.1f %.1f" % p for p in pts)
        paths.append("<path d='%s' class='cm-line-path' stroke='%s'/>" % (d, color))
        for (x, y), year in zip(pts, sorted(vals)):
            paths.append(
                "<circle cx='%.1f' cy='%.1f' r='3.2' class='cm-line-dot' fill='%s'>"
                "<title>%s · %d 年：%.0f 万元</title></circle>"
                % (x, y, color, cards.esc(name), year, vals[year])
            )
        lx, ly = pts[-1]
        paths.append("<text x='%.1f' y='%.1f' class='cm-line-last' fill='%s' text-anchor='end'>%.0f</text>"
                     % (lx - 2, ly - 8, color, vals[sorted(vals)[-1]]))
        legend.append("<span><i style='background:%s'></i>%s</span>" % (color, cards.esc(name)))

    st.markdown(
        "<div class='cm-line'>"
        "<div class='cm-line-h'><span>%s</span><span>单位：万元 · 名义口径</span></div>"
        "<div class='cm-line-legend'>%s</div>"
        "<svg class='cm-line-svg' viewBox='0 0 %d %d'>"
        "%s%s%s</svg>"
        "<div class='cm-line-foot'>曲线由 scenarios 结果表带入；页面只展示，不重算。</div></div>"
        % (cards.esc(title), "".join(legend), W, H,
           "".join(grid), "".join(xlab), "".join(paths)),
        unsafe_allow_html=True,
    )


def param_slider(label: str, min_value: float, max_value: float, value: float,
                 step: float = 1.0, help_text: str = "",
                 fmt: str = "%.1f", assumed: bool = False) -> float:
    """参数滑块：assumed=True 时标签带黄点（假设值标记）。

    Streamlit 要求 value/min/max 同类：整数边界走整数，小数边界统一转 float。
    """
    if (isinstance(min_value, int) and isinstance(max_value, int)
            and isinstance(step, int)):
        value = int(value)
    else:
        min_value, max_value, step = float(min_value), float(max_value), float(step)
        value = float(value)
    suffix = " <span style='color:var(--accent-2)'>●</span>" if assumed else ""
    st.markdown("<div style='font-size:13px;margin-bottom:2px'>"
                + cards.esc(label) + suffix + "</div>", unsafe_allow_html=True)
    return st.slider(label, min_value=min_value, max_value=max_value,
                     value=value, step=step, help=help_text or None,
                     label_visibility="collapsed", format=fmt)


def result_json(params: Dict[str, Any], result: Dict[str, Any]) -> None:
    """下载本次测算（参数 + 结果），答辩后可追溯。"""
    import json

    payload = {"params": params, "result": result,
               "note": "情景演算结果，非对拍口径（docs/20 §11）"}
    st.download_button(
        "下载本次测算 JSON",
        data=json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
        file_name="what_if_result.json", mime="application/json",
        width="stretch")
