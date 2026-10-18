"""图表封装：全项目统一用 Streamlit 原生图，换图只改这里。"""

from __future__ import annotations

from typing import Optional

import pandas as pd
import streamlit as st


def bar(df: pd.DataFrame, x: str, y: str, title: Optional[str] = None, height: int = 320) -> None:
    if title:
        st.markdown("**" + title + "**")
    if df is None or df.empty or x not in df.columns or y not in df.columns:
        st.caption("暂无数据")
        return
    data = df[[x, y]].dropna().set_index(x)[y]
    st.bar_chart(data, height=height)


def line(df: pd.DataFrame, x: str, y: str, title: Optional[str] = None, height: int = 300) -> None:
    if title:
        st.markdown("**" + title + "**")
    if df is None or df.empty or x not in df.columns or y not in df.columns:
        st.caption("暂无数据")
        return
    data = df[[x, y]].dropna().set_index(x)[y]
    st.line_chart(data, height=height)


def scatter(df: pd.DataFrame, x: str, y: str, title: Optional[str] = None, height: int = 320) -> None:
    if title:
        st.markdown("**" + title + "**")
    if df is None or df.empty or x not in df.columns or y not in df.columns:
        st.caption("暂无数据")
        return
    st.scatter_chart(df[[x, y]].dropna(), x=x, y=y, height=height)
