"""三色标记组件：重复 / 异常 / 口径换算。

约定（全项目统一，不许各页面自己定颜色）：
    flag_dup      灰底  重复行（已去重，不计入统计）
    flag_anomaly  黄底  时间戳异常，保留计碳
    flag_basis    黄底  载重按总质量换算
"""

from __future__ import annotations

from typing import Iterable, Optional

import pandas as pd
import streamlit as st

GRAY = "#EDEDED"
YELLOW = "#FFF3C4"

FLAG_COLUMNS = ["flag_dup", "flag_anomaly", "flag_basis"]

FLAG_LABELS = {
    "flag_dup": "重复行（已去重）",
    "flag_anomaly": "时间戳异常，保留计碳",
    "flag_basis": "载重按总质量换算",
}

RENAME = {
    "waybill_no": "运单号",
    "plate": "车牌",
    "vehicle_type": "车型",
    "vclass": "类别",
    "load_t": "载重(t)",
    "load_raw_t": "原始载重(t)",
    "mass_basis": "载重口径",
    "origin": "起点",
    "dest": "终点",
    "dist_km": "里程(km)",
    "month": "月份",
    "factor_gkm": "因子(g/km)",
    "kg_co2": "碳排(kg)",
    "delta_pct": "双轨偏差(%)",
}


def _flag_series(df: pd.DataFrame, col: str) -> pd.Series:
    if df is not None and col in df.columns:
        return pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    if df is None:
        return pd.Series(dtype=int)
    return pd.Series(0, index=df.index, dtype=int)


def flag_summary(df: pd.DataFrame) -> pd.DataFrame:
    """三类标记的计数与占比。"""
    total = 0 if df is None else len(df)
    rows = []
    for col in FLAG_COLUMNS:
        n = 0 if df is None or df.empty else int(_flag_series(df, col).sum())
        share = ("%.2f%%" % (100.0 * n / total)) if total else "—"
        rows.append({"标记": FLAG_LABELS[col], "行数": n, "占比": share})
    return pd.DataFrame(rows)


def flag_legend() -> None:
    st.caption("灰底 = 重复行（已去重，不计入）；黄底 = 异常或口径换算（保留，另标注原值与换算值）")


def flagged_table(
    df: pd.DataFrame,
    columns: Optional[Iterable[str]] = None,
    max_rows: int = 200,
    height: Optional[int] = None,
) -> None:
    """带三色标记的明细表。超过 max_rows 只显示前若干行并注明。"""
    if df is None or df.empty:
        st.info("暂无明细数据")
        return

    flags = {c: _flag_series(df, c) for c in FLAG_COLUMNS}
    view = df.head(max_rows).copy()
    if columns:
        keep = [c for c in columns if c in view.columns]
        if keep:
            view = view[keep]
    disp = view.rename(columns={k: v for k, v in RENAME.items() if k in view.columns})

    def _row_style(row: pd.Series) -> list:
        idx = row.name
        dup = int(flags["flag_dup"].get(idx, 0))
        anomaly = int(flags["flag_anomaly"].get(idx, 0))
        basis = int(flags["flag_basis"].get(idx, 0))
        if dup:
            color = GRAY
        elif anomaly or basis:
            color = YELLOW
        else:
            color = ""
        # 字色一起写死：暗色主题下白字压在浅色底上会看不见
        css = ("background-color: " + color + "; color: #111111") if color else ""
        return [css] * len(row)

    styler = disp.style.apply(_row_style, axis=1)
    kwargs = {"use_container_width": True}
    if height:
        kwargs["height"] = height
    st.dataframe(styler, **kwargs)
    if len(df) > max_rows:
        st.caption("仅显示前 " + str(max_rows) + " 行，共 " + str(len(df)) + " 行")
    flag_legend()
