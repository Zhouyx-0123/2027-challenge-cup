"""FLAG 组件（A1）：重复 / 异常 / 口径换算 / 估算值。

约定（全项目统一，页面不许自己定颜色）：
    flag_dup        灰底   重复行（宽表保留，基准剔除）
    flag_anomaly    黄底   时间戳/速度异常（保留计碳）
    flag_basis      黄底   载重口径换算（总质量/准牵引 → 载质量）
    flag_estimated  黄框   有字段被历史/行业均值填补，hover 看 estimate_note

黄框为什么用 HTML 表：st.dataframe 的 Styler 只稳定支持背景色，边框常被网格吃掉。
所以明细表用 Styler 上底色，估算值清单另用一张带 2px 黄边的 HTML 表，答辩时一定能看见。
"""

from __future__ import annotations

import html
from typing import Any, Dict, Iterable, Optional

import pandas as pd
import streamlit as st

GRAY = "#E5E7EB"
YELLOW = "#FFF3C4"
EST = "#FFF8E1"
BORDER = "#F5C86B"

FLAG_COLUMNS = ["flag_dup", "flag_anomaly", "flag_basis", "flag_estimated"]

FLAG_META = [
    {"col": "flag_dup", "label": "重复行", "color": "#9CA3AF",
     "desc": "已去重，不计入基准", "key": "dup_rows", "scope": "all"},
    {"col": "flag_anomaly", "label": "时间戳异常", "color": "#F5C86B",
     "desc": "保留计碳，只影响速度统计", "key": "anomaly_rows", "scope": "unique"},
    {"col": "flag_basis", "label": "载重口径换算", "color": "#F5C86B",
     "desc": "总质量/准牵引 → 载质量", "key": "basis_rows", "scope": "unique"},
    {"col": "flag_estimated", "label": "估算值", "color": "#FF8A5B",
     "desc": "缺失值按历史/行业均值填补", "key": "estimated_rows", "scope": "unique"},
]

FLAG_LABELS = {m["col"]: m["label"] for m in FLAG_META}

RENAME = {
    "waybill_no": "运单号", "plate": "车牌", "vehicle_type": "车型", "vclass": "类别",
    "load_t": "载重(t)", "load_raw_t": "原始载重(t)", "mass_basis": "载重口径",
    "origin": "起点", "dest": "终点", "od_pair": "线路", "dist_km": "里程(km)",
    "month": "月份", "factor_gkm": "因子(g/km)", "er_gs": "排放率(g/s)",
    "kg_co2": "碳排(kg)", "kg_co2_phys": "物理轨(kg)", "delta_pct": "双轨偏差(%)",
    "speed_kmh": "速度(km/h)", "estimate_note": "估算原因", "标记": "标记",
}


def _flag_series(df: pd.DataFrame, col: str) -> pd.Series:
    if df is not None and col in df.columns:
        return pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    if df is None:
        return pd.Series(dtype=int)
    return pd.Series(0, index=df.index, dtype=int)


def flag_summary(df: Optional[pd.DataFrame] = None,
                 counts: Optional[Dict[str, int]] = None) -> pd.DataFrame:
    """四类标记的计数与占比。counts 来自 services.flag_counts()，优先使用。"""
    counts = counts or {}
    total_all = int(counts.get("rows_wide") or (len(df) if df is not None else 0) or 0)
    total_unique = int(counts.get("rows_dedup") or (len(df) if df is not None else 0) or 0)

    rows = []
    for meta in FLAG_META:
        col = meta["col"]
        if meta["key"] in counts:
            n = int(counts[meta["key"]] or 0)
        elif df is not None and not df.empty:
            n = int(_flag_series(df, col).sum())
        else:
            n = 0
        denom = total_all if meta["scope"] == "all" else total_unique
        share = ("%.2f%%" % (100.0 * n / denom)) if denom else "—"
        rows.append({"标记": meta["label"], "行数": n, "占比": share, "说明": meta["desc"]})
    return pd.DataFrame(rows)


def flag_legend() -> None:
    chips = []
    for meta in FLAG_META:
        if meta["col"] == "flag_estimated":
            dot = "border:2px solid %s;background:transparent" % BORDER
        else:
            dot = "background:%s" % meta["color"]
        chips.append(
            "<span class='cm-flag'><i class='dot' style='" + dot + "'></i>"
            + meta["label"] + " · " + meta["desc"] + "</span>"
        )
    st.markdown("<div class='cm-flagbar'>" + "".join(chips) + "</div>",
                unsafe_allow_html=True)


def _row_marks(view: pd.DataFrame) -> list:
    marks = []
    for _, row in view.iterrows():
        tags = []
        if int(row.get("flag_dup", 0) or 0):
            tags.append("重复")
        if int(row.get("flag_anomaly", 0) or 0):
            tags.append("异常")
        if int(row.get("flag_basis", 0) or 0):
            tags.append("口径")
        if int(row.get("flag_estimated", 0) or 0):
            tags.append("估算")
        marks.append(" / ".join(tags) if tags else "—")
    return marks


def flagged_table(
    df: pd.DataFrame,
    columns: Optional[Iterable[str]] = None,
    max_rows: int = 200,
    height: Optional[int] = None,
    show_legend: bool = True,
) -> None:
    """带四色标记的明细表；超过 max_rows 只显示前若干行并注明。"""
    if df is None or df.empty:
        st.info("暂无明细数据")
        return

    flags = {c: _flag_series(df, c) for c in FLAG_COLUMNS}
    view = df.head(max_rows).copy()
    if columns:
        keep = [c for c in columns if c in view.columns]
        if keep:
            view = view[keep]

    view.insert(0, "标记", _row_marks(view))
    disp = view.rename(columns={k: v for k, v in RENAME.items() if k in view.columns})

    def _row_style(row: pd.Series) -> list:
        idx = row.name
        dup = int(flags["flag_dup"].get(idx, 0))
        anomaly = int(flags["flag_anomaly"].get(idx, 0))
        basis = int(flags["flag_basis"].get(idx, 0))
        est = int(flags["flag_estimated"].get(idx, 0))
        if dup:
            css = "background-color:%s;color:#111111;" % GRAY
        elif est:
            css = "background-color:%s;color:#111111;border:2px solid %s;" % (EST, BORDER)
        elif anomaly or basis:
            css = "background-color:%s;color:#111111;" % YELLOW
        else:
            css = ""
        return [css] * len(row)

    styler = disp.style.apply(_row_style, axis=1)
    kwargs: Dict[str, Any] = {"width": "stretch"}
    if height:
        kwargs["height"] = height
    st.dataframe(styler, **kwargs)
    if len(df) > max_rows:
        st.caption("仅显示前 " + str(max_rows) + " 行，共 " + str(len(df)) + " 行")
    if show_legend:
        flag_legend()


def _fmt_num(value: Any, digits: int = 0) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if v != v:
        return "—"
    return ("{:,.%df}" % digits).format(v)


def estimated_panel(df: pd.DataFrame, n: int = 8) -> None:
    """估算值清单：每行套 2px 黄框，明确告诉评委“这是估算值”。"""
    if df is None or df.empty or "flag_estimated" not in df.columns:
        return
    flag = _flag_series(df, "flag_estimated")
    est = df[flag == 1]
    total = len(df)
    if est.empty:
        st.success("本页没有估算值——所有字段都来自原始表。")
        return

    share = 100.0 * len(est) / max(total, 1)
    head = ("<div class='cm-est-wrap'><div class='cm-est-head'>🟡 黄框 = 估算值"
            "<span>共 %d 行，占本页 %.1f%%；只填补、不删除，flag_estimated=1</span></div>"
            % (len(est), share))
    body = ["<table class='cm-est-table'><thead><tr>"
            "<th>运单号</th><th>线路</th><th>里程(km)</th><th>碳排(kg)</th>"
            "<th>估算原因</th></tr></thead><tbody>"]
    for _, row in est.head(n).iterrows():
        body.append(
            "<tr class='est'>"
            "<td>" + html.escape(str(row.get("waybill_no", "—"))) + "</td>"
            "<td>" + html.escape(str(row.get("od_pair", "—"))) + "</td>"
            "<td>" + _fmt_num(row.get("dist_km", None), 1) + "</td>"
            "<td>" + _fmt_num(row.get("kg_co2", None), 1) + "</td>"
            "<td class='reason'>" + html.escape(str(row.get("estimate_note", "—"))) + "</td>"
            "</tr>")
    body.append("</tbody></table>")
    foot = ("<div class='cm-est-foot'>填补规则：同线历史均值 → 行业绕行系数 → 全样本中位数；"
            "车型找不到按载重推断；发票匹配不上按同类别历史均价。所有填补都留痕。</div></div>")
    st.markdown(head + "".join(body) + foot, unsafe_allow_html=True)
    if len(est) > n:
        st.caption("仅列出前 %d 行；完整清单见下方明细表（筛“估算”）。" % n)
