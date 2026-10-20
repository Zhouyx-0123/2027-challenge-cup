"""第 6 页 · 路线沙盘（预览）：高德给路，我们给决策。

这一页是 docs/25 §3.1「内容与容器解耦」的预览容器：
- 地图/路线/指标全部来自 data/processed/route_*.parquet；
- 前端不做路线求解、不做碳排/成本重算；
- 当前是“单线沙盘”：每个 OD 只有一条高德策略；
- 下一阶段在 evaluator 预生成 2–4 条候选路线 + 权重评分，本页只换数据，不重写。
"""
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import pandas as pd
import streamlit as st

import services
from components import cards, decision_panel, layout, theme

layout.setup("6 路线沙盘")
src = services.source()
gate = services.gate()
layout.sidebar_params(services.source_name(), gate, current="6")

mock = services.is_mock()
lam = float(st.session_state.get("lam", 100.0))
route_df = services.routes()
carbon_df = services.route_carbon()
select_df = services.route_select()

theme.hero(
    "路线沙盘：高德给路，我们给决策",
    "先看真实路网，再把路线碳排、过路费、时长和 λ 放到同一张表里；"
    "候选路线与权重模型是下一步，不在前端求解。",
    kicker="06 / ROUTE LAB · PREVIEW",
    badges=[layout.source_badge(services.source_name()), layout.gate_badge(gate),
            "310 条 OD", "L1 路线情景", "预览页 · 不计入五页主线"],
)
if mock:
    layout.demo_note(str(src.get("reason", "")))

cards.chips([
    "五页主线之外的第 6 页预览",
    "高德只负责几何/距离/时长",
    "碳排与推荐来自结果表",
    "静态路网 · 非实时",
], tones=["skip", "mint", "mint", "warn"])

if route_df.empty:
    layout.section("路线沙盘", "还没有 route_od.parquet")
    cards.empty_state(
        "路线表还没生成",
        "先运行  python -m tools.route_fetch  预取高德路线，"
        "再运行  python -m src.pipeline --with-routes。")
    layout.conclusion("没有路线表就不画路线——前端只读结果表。")
    st.stop()

n_online = int((route_df["source"] == "amap_rest").sum()) if "source" in route_df.columns else 0
n_est = int((route_df.get("flag_route_est", pd.Series(dtype=int)) == 1).sum())
cards.stats([
    {"label": "可看 OD", "value": services.fmt(len(route_df)), "tone": "mint",
     "sub": "网点级 OD"},
    {"label": "高德真路线", "value": services.fmt(n_online), "tone": "mint",
     "sub": "含 polyline，地图上能画线"},
    {"label": "离线兜底", "value": services.fmt(n_est), "tone": "amber",
     "sub": "只有端点，无真实路径"},
    {"label": "路线几何", "value": "静态", "tone": "info",
     "sub": "非实时；缓存结果表"},
], cols=4)

layout.section("路线沙盘", "选一条 OD：地图、路线指标、λ 推荐一起看")
decision_panel.render(
    route_df,
    carbon_df=carbon_df,
    select_df=select_df,
    segments=services.segments(),
    lam=lam,
    height=600,
    key_prefix="p6_route",
)

# ---- 路线清单：让评委知道不是只画了一条线 ----
layout.section("路线清单（前 20 条）", "全部来自 route_od.parquet；点上方下拉可切换任意 OD")
show = route_df.copy()
show["来源"] = show.apply(
    lambda r: "高德真路线" if str(r.get("source")) == "amap_rest"
    else ("离线兜底" if int(r.get("flag_route_est", 0) or 0) else "坐标缺失"), axis=1)
show = show.head(20)
rows = []
for _, r in show.iterrows():
    rows.append([
        str(r.get("origin", "")), str(r.get("dest", "")),
        services.fmt(r.get("distance_km"), 1), services.fmt(r.get("duration_min"), 0),
        services.fmt(r.get("toll_yuan"), 0), str(r.get("来源", "")),
    ])
cards.htable(["起点", "终点", "距离(km)", "时长(min)", "过路费(元)", "来源"],
             rows, num_cols=[2, 3, 4])
st.download_button(
    "下载路线清单 CSV",
    data=route_df.to_csv(index=False).encode("utf-8-sig"),
    file_name="route_od.csv", mime="text/csv", use_container_width=True)
cards.note("当前每个 OD 只有一条高德策略；所以这一页是“单线沙盘”，不是“多路线比选”。"
           "高德真路线 %d 条，离线 %d 条；离线 OD 只显示端点，下拉框已标“（离线）”。"
           % (n_online, n_est))

# ---- 下一步模型接口：只写契约，不在前端算 ----
layout.section("下一步：候选路线 + 权重评分", "模型在 evaluator 预计算，本页只读结果表")
cards.htable(
    ["模块", "现在", "下一步（模型接进来后）"],
    [
        ["高德路线", "每 OD 1 条策略（32 综合）", "每 OD 2–4 条候选：32/33/34/35"],
        ["评分", "λ=100 单点推荐车型", "碳排/成本/时长/约束加权评分"],
        ["页面", "单线沙盘", "候选路线对比 + 推荐徽标 + 为什么"],
        ["前端边界", "只读 route_*.parquet", "仍然只读，不在浏览器求解"],
    ],
    num_cols=[],
)
cards.note("下一步的数据契约：route_candidates.parquet（候选路线）+ route_weights.json（权重）；"
           "权重与评分在 evaluator/pipeline 预计算，页面只展示推荐与解释。"
           "这样高德只提供路网资源，路线选择仍然由我们的碳-成本模型决定。")

layout.conclusion(
    "高德负责“路怎么走”，我们负责“走哪条、派什么车、值不值”。"
    "现在先把真实路网和指标看明白；候选路线和权重模型接进来时，这一页不需要重写。"
)
