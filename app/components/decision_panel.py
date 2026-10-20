# -*- coding: utf-8 -*-
"""决策面板组件：动态路线 + λ 选车的业务展示（容器无关）。

**为什么有这个文件**（docs/25 §3.1「内容与容器解耦」）：
    现在它是第 3 页的一个区块，国赛期要把它升级成**独立第 6 页**。
    如果内容写在页面里，升级就得重写；写成组件后，升级只需新建一个 10 行的
    page 文件调用同一个函数——**零重写**。

**这个组件的边界**：
    - 只做展示：取数由调用方传入，本组件不读文件、不调业务接口、不做任何碳排计算；
    - 碳排/成本/λ 结论全部来自结果表（route_carbon.parquet / route_select.parquet），
      浏览器端零计算（docs/20 §4 前端只读铁律的延伸）；
    - 地图有则画高德、无则画 Plotly 离线图——**降级在组件内部闭环**，调用方不必写分支。

调用方只做三件事：取数 → 摆组件 → 传参。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from components import amapview, cards, mapview

# 展示口径的车型色（与 components/theme.py 对齐）
_VCLASS_TONE = {1: "mint", 2: "amber", 3: "coral"}


def _fmt(value: Any, digits: int = 0) -> str:
    try:
        if value is None or pd.isna(value):
            return "—"
        return ("{:,.%df}" % digits).format(float(value))
    except (TypeError, ValueError):
        return "—"


def _route_to_payload(row: pd.Series, kg_co2: Optional[float] = None) -> Dict[str, Any]:
    """结果表行 → 浏览器端契约（docs/24 §4）。

    只搬运，不计算：kg_co2 若未给出则留空，由调用方从 route_carbon 传入。
    """
    polyline = row.get("polyline", "")
    points: List[List[float]] = []
    if isinstance(polyline, str) and polyline:
        for chunk in polyline.split(";"):
            if "," not in chunk:
                continue
            lng_text, lat_text = chunk.split(",", 1)
            try:
                points.append([float(lng_text), float(lat_text)])
            except ValueError:
                continue
    return {
        "od_key": str(row.get("od_key", "")),
        "origin": str(row.get("origin", "")),
        "dest": str(row.get("dest", "")),
        "o_lng": float(row.get("o_lng") or 0.0),
        "o_lat": float(row.get("o_lat") or 0.0),
        "d_lng": float(row.get("d_lng") or 0.0),
        "d_lat": float(row.get("d_lat") or 0.0),
        "distance_km": float(row.get("distance_km") or 0.0),
        "duration_min": float(row.get("duration_min") or 0.0),
        "toll_yuan": float(row.get("toll_yuan") or 0.0),
        "kg_co2": float(kg_co2) if kg_co2 is not None else 0.0,
        "vclass": int(row.get("vclass") or 1),
        "strategy": row.get("strategy"),
        "polyline": points,
        "source": str(row.get("source", "")),
    }


def _offline_fallback(segments: Optional[pd.DataFrame], height: int) -> None:
    """在线不可用时的离线底图（Plotly 矢量示意，不请求瓦片）。"""
    if segments is not None and not segments.empty:
        fig = mapview.flow_map(segments, height=height)
        mapview.render(fig, height=height)
    else:
        st.info("离线示意图数据未就绪——路线指标与约束结论仍可正常查看。")


def render(route_df: pd.DataFrame,
           carbon_df: Optional[pd.DataFrame] = None,
           select_df: Optional[pd.DataFrame] = None,
           segments: Optional[pd.DataFrame] = None,
           lam: float = 100.0,
           height: int = 520,
           key_prefix: str = "route_panel",
           show_selector: bool = True) -> None:
    """动态路线决策面板。

    参数
    ----
    route_df    路线表 route_od.parquet（必需）
    carbon_df   路线情景段表 route_carbon.parquet（可选，用于算该 OD 的路线碳排）
    select_df   λ 推荐表 route_select.parquet（可选，用于展示推荐车型）
    segments    段表（可选，离线回退画布用）
    lam         内部碳价（元/t），仅用于展示 λ 视角
    """
    if route_df is None or route_df.empty:
        cards.empty_state(
            "路线表还没生成",
            "运行  python -m tools.route_fetch  生成 route_od.parquet（热点 OD 真路线）。")
        return

    # ---- OD 选择 ----
    options = route_df
    picked_key = None
    if show_selector and len(route_df) > 1:
        labels = [
            "%s → %s（%.0f km）%s" % (
                str(r.get("origin", "")), str(r.get("dest", "")),
                float(r.get("distance_km") or 0.0),
                "（离线）" if int(r.get("flag_route_est", 0) or 0) else "")
            for _, r in route_df.iterrows()
        ]
        picked = st.selectbox("选一条线路（网点级 OD）", labels, index=0,
                              key=key_prefix + "_od")
        picked_key = int(labels.index(picked))
    if picked_key is None:
        picked_key = 0

    row = route_df.iloc[picked_key]

    # ---- 地图：在线高德 / 离线 Plotly（降级在组件内闭环）----
    kg_co2 = None
    if carbon_df is not None and not carbon_df.empty and "od_site" in carbon_df.columns:
        sub = carbon_df[carbon_df["od_site"].astype(str) == str(row.get("od_site", ""))]
        if not sub.empty and "kg_co2_route_legal" in sub.columns:
            kg_co2 = float(pd.to_numeric(sub["kg_co2_route_legal"], errors="coerce").fillna(0.0).sum())

    payload = _route_to_payload(row, kg_co2)
    online_ok = False
    if payload["o_lng"] and payload["d_lng"]:
        online_ok = amapview.route_map_single(payload, height=height)
    if not online_ok:
        _offline_fallback(segments, height)

    # ---- 指标卡 ----
    est = int(row.get("flag_route_est", 0) or 0)
    source = str(row.get("source", ""))
    tone = "coral" if est else "mint"
    src_label = ("高德真路线" if source == "amap_rest"
                 else ("离线估算（haversine×1.28）" if source == "offline_haversine"
                       else "坐标缺失，未生成路线"))
    cards.stats([
        {"label": "路线距离", "value": _fmt(row.get("distance_km"), 1) + " km",
         "sub": "路线情景口径 L1"},
        {"label": "路线时长", "value": _fmt(row.get("duration_min"), 0) + " min",
         "sub": "高德驾车规划"},
        {"label": "过路费", "value": _fmt(row.get("toll_yuan"), 0) + " 元"},
        {"label": "路线碳排", "value": _fmt(kg_co2, 1) + " kg",
         "tone": tone, "sub": src_label},
    ], cols=4)
    if est:
        cards.note("该 OD 是离线兜底：地图只显示起终点，没有真实路径；"
                   "选一条未标“离线”的 OD 可看高德真路线。")

    # ---- λ 视角：推荐车型（来自 route_select.parquet，不在前端算）----
    if select_df is not None and not select_df.empty:
        match = select_df[select_df["od_key"].astype(str) == str(row.get("od_key", ""))]
        if not match.empty:
            best = match.iloc[0]
            carbon_gain = float(best.get("carbon_cost_yuan") or 0.0)
            cards.formula(
                "λ = <span class='hl'>%s 元/t</span> 下的推荐："
                "<span class='hl2'>%s</span><br>"
                "路线碳排 %s t → 碳价收益 %s 元｜运营成本 %s 元 → 综合得分 %s 元<br>"
                "<span class='dim'>评分式：运营成本 + λ × 碳排（展示口径，非业务重算）</span>"
                % (_fmt(lam, 0), str(best.get("vehicle_type", "—")),
                   _fmt(float(best.get("kg_co2_route") or 0.0) / 1000.0, 4),
                   _fmt(carbon_gain, 0),
                   _fmt(best.get("opex_yuan"), 0),
                   _fmt(best.get("score_yuan"), 0)))
        else:
            cards.note("该 OD 未进入 λ 推荐表（route_select.parquet 未生成或未覆盖）。")
    else:
        cards.note("λ 推荐表未生成——路线指标与地图不受影响。"
                   "生成方式：python -m src.pipeline --with-routes")

    # ---- 口径说明 ----
    cards.note(
        "**三层口径**：L0 基准账（宽表实测里程 × 因子，E₀ 永不动）｜"
        "L1 路线情景（高德路线距离/时长 × 同一因子，只做差量分析）｜"
        "L2 λ 决策（L1 + 运营成本 + λ）。三者在本页分列标注，**不混算**。"
    )
