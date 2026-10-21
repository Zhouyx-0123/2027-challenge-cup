"""第 6 页 · 路线实验室：高德实时规划 + 平滑动画 + 元口径碳账。

这一页把原来的“单线沙盘预览”升级成可操作的路线实验室：
- 左侧选网点级 OD / 规划策略，右侧由 AMap.Driving 实时返回逐点路径；
- 路线贴合公路（约 0.1 km 一个点），带生长动画、方向箭头、在途回放、路况开关；
- 碳排/运营成本/碳成本/推荐车型全部来自 route_select.parquet，浏览器零业务计算；
- 金额一律按元展示：过路费、运营成本、碳成本、综合得分。

高德只负责“路怎么走”；“走哪条、排多少碳、值不值”仍由我们的结果表和模型决定。
"""
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import pandas as pd
import streamlit as st

import services
from components import amapview, cards, layout, mapview, theme

layout.setup("6 路线实验室")
src = services.source()
gate = services.gate()
params_ui = layout.sidebar_params(services.source_name(), gate, current="6")
lam = float(params_ui.get("lam", 100.0))

rows = services.route_lab_rows()
mock = services.is_mock()

theme.hero_compact(
    "路线实验室：高德实时规划，路线贴着公路走",
    "选一条网点级 OD，切换规划策略，实时重算路线；碳排、运营成本、碳成本一屏对齐，金额一律按元。",
    kicker="06 / ROUTE LAB · LIVE",
    badges=[layout.source_badge(services.source_name()), layout.gate_badge(gate),
            "在线实时规划", "L1 路线情景", "前端只读"],
)
if mock:
    layout.demo_note(str(src.get("reason", "")))

if rows.empty:
    layout.section("路线实验室", "还没有 route_od.parquet")
    cards.empty_state(
        "路线表还没生成",
        "先运行  python -m tools.route_fetch  预取高德路线，"
        "再运行  python -m src.pipeline --with-routes。")
    layout.conclusion("没有路线表就不画路线——前端只读结果表。")
    st.stop()

n_online = int((rows["source"] == "amap_rest").sum()) if "source" in rows.columns else 0
_flag = rows["flag_route_est"] if "flag_route_est" in rows.columns else pd.Series([0] * len(rows))
n_est = int((pd.to_numeric(_flag, errors="coerce").fillna(0) == 1).sum())
coverage = services.kpis().get("od_coverage_pct")
candidates = services.route_lab_candidates()
n_cand_od = sum(1 for v in candidates.values() if len(v) >= 4)

cards.chips([
    "%d 条网点级 OD" % len(rows),
    "%d 条高德真路线" % n_online,
    "%d 条离线兜底" % n_est,
    ("基准碳覆盖 %.1f%%" % float(coverage)) if coverage is not None else "基准碳覆盖 —",
    ("策略级碳账 %d OD" % n_cand_od) if n_cand_od else "策略碳账未生成",
    "金额单位：元",
], tones=["mint", "mint", "skip", "info", "mint" if n_cand_od else "warn", "warn"])

layout.section(
    "实时路线实验室",
    "左侧选线路与策略；右侧路线由高德实时规划。碳账数字来自结果表，浏览器不重算。",
)

default_id = str(rows.iloc[0]["od_key"])
online = amapview.route_lab(
    rows.to_dict("records"),
    height=820,
    default_key=default_id,
    lam=lam,
    candidates=candidates,
)

if not online:
    st.info(
        "未检测到 config/amap_js_key.txt 里的高德 JS API key：当前显示离线缓存几何。"
        "配好 key 后刷新页面，即切换为实时规划、贴合公路的动画路线。"
    )
    labels = [
        "%s → %s（%.0f km）" % (
            str(r.get("origin", "")), str(r.get("dest", "")),
            float(r.get("distance_km") or 0.0))
        for _, r in rows.head(60).iterrows()
    ]
    picked = st.selectbox("选一条线路（离线模式）", labels, index=0, key="p6_offline_od")
    row = rows.iloc[labels.index(picked)]
    fig = mapview.route_preview(row, height=620)
    mapview.render(fig, height=620)
    cards.stats([
        {"label": "路线距离", "value": services.fmt(row.get("distance_km"), 1) + " km",
         "sub": "L1 路线情景"},
        {"label": "路线时长", "value": services.fmt(row.get("duration_min"), 0) + " min",
         "sub": "高德驾车规划"},
        {"label": "过路费", "value": services.fmt(row.get("toll_yuan"), 0) + " 元"},
        {"label": "路线碳排", "value": services.fmt(row.get("co2_t"), 2) + " t",
         "tone": "mint", "sub": "结果表口径"},
        {"label": "运营成本", "value": services.fmt(row.get("opex_yuan"), 0) + " 元",
         "sub": "能耗 + 过路 + 时间"},
        {"label": "推荐车型", "value": str(row.get("vehicle_type") or "—"),
         "sub": "λ=100 策略口径"},
    ], cols=6)
    cards.note(
        "离线模式画的是 route_od.parquet 里的缓存几何（按点数抽稀），只作示意；"
        "在线模式会用 AMap.Driving 重新规划，逐点贴合公路。"
    )

# ---- 同一 OD 的四策略对比 ----
cand_df = services.route_candidate_carbon()
if not cand_df.empty and "od_key" in cand_df.columns:
    first_od = str(rows.iloc[0]["od_key"])
    sub = cand_df[cand_df["od_key"].astype(str) == first_od].copy()
    if not sub.empty:
        layout.section(
            "同一 OD 的四种策略",
            "默认线路 %s → %s；金额为元，碳账为 λ=100 策略情景，几何以实验室实时规划为准。"
            % (str(rows.iloc[0].get("origin", "")), str(rows.iloc[0].get("dest", ""))),
        )
        _order = {0: 0, 4: 1, 1: 2, 6: 3}
        sub = sub.assign(_order=sub["strategy"].map(lambda x: _order.get(int(x), 9))).sort_values("_order")
        srows = []
        for _, r in sub.iterrows():
            kg = pd.to_numeric(pd.Series([r.get("kg_co2_route")]), errors="coerce").iloc[0]
            srows.append([
                str(r.get("strategy_name") or r.get("strategy")),
                services.fmt(r.get("route_km"), 1),
                services.fmt(r.get("route_min"), 0),
                services.fmt(r.get("toll_yuan"), 0),
                services.fmt((float(kg) / 1000.0) if pd.notna(kg) else None, 2),
                services.fmt(r.get("opex_yuan"), 0),
                services.fmt(r.get("carbon_cost_yuan"), 0),
                services.fmt(r.get("score_yuan"), 0),
                str(r.get("vehicle_type") or "—"),
            ])
        cards.htable(
            ["策略", "距离(km)", "时长(min)", "过路费(元)", "碳排(t)",
             "运营成本(元)", "碳成本(元)", "综合得分(元)", "推荐车型"],
            srows, num_cols=[1, 2, 3, 4, 5, 6, 7],
        )
        cards.note(
            "REST 策略 0/4/1/6 与实验室的 JS policy 一一对应；两边分别请求，"
            "距离/时长可能差几个百分点。同一 OD 的策略路线可能重合（如当前无拥堵时避堵=综合），"
            "所以表内数值相同并不代表没有切换。碳账是 L2 展示口径，不覆盖 L0 基准。"
            "下方路线账本按 route_select 默认策略口径；上表按候选路线 0/4/1/6，"
            "个别 OD 因路线不同会有差异，以实验室实时几何为准。",
            sub="候选路线预取：python -m tools.route_candidates；碳账重算：python -m src.pipeline --with-routes。",
        )

# ---- 路线账本 ----
layout.section(
    "路线账本（前 20 条）",
    "按碳排排序；所有金额为元。路线几何可实时重算，碳账数字只读结果表。",
)
show = rows.head(20).copy()
table_rows = []
for _, r in show.iterrows():
    source = "高德真路线" if str(r.get("source")) == "amap_rest" else "离线兜底"
    table_rows.append([
        str(r.get("origin", "")), str(r.get("dest", "")),
        services.fmt(r.get("distance_km"), 1), services.fmt(r.get("duration_min"), 0),
        services.fmt(r.get("toll_yuan"), 0), services.fmt(r.get("co2_t"), 2),
        services.fmt(r.get("opex_yuan"), 0), services.fmt(r.get("carbon_cost_yuan"), 0),
        str(r.get("vehicle_type") or "—"), source,
    ])
cards.htable(
    ["起点", "终点", "距离(km)", "时长(min)", "过路费(元)", "碳排(t)",
     "运营成本(元)", "碳成本(元)", "推荐车型", "来源"],
    table_rows, num_cols=[2, 3, 4, 5, 6, 7],
)

csv_rows = rows.drop(columns=["polyline"], errors="ignore")
st.download_button(
    "下载路线账本 CSV",
    data=csv_rows.to_csv(index=False).encode("utf-8-sig"),
    file_name="route_lab.csv", mime="text/csv", use_container_width=True,
)

layout.section("这次修了什么", "从“抽象折线”到“贴着公路跑”")
cards.steps([
    {"n": "旧", "t": "400 点抽稀", "d": "1,375 km 只留 400 点，约 3.4 km 一个点；放大看就是折线"},
    {"n": "新", "t": "AMap.Driving", "d": "实时规划返回约 1.2 万个点，约 0.1 km 一个点，逐点贴合公路"},
    {"n": "渲染", "t": "生长 + 缓动", "d": "路线生长动画、镜头缓动、方向箭头、在途车辆回放"},
    {"n": "口径", "t": "几何实时 · 碳账只读", "d": "高德只给路；碳排/成本/车型仍来自结果表，浏览器不算"},
], active=-1)

cards.note(
    "金额口径：过路费、运营成本、碳成本（λ=100 策略碳账）、综合得分全部为元；"
    "碳排为 tCO₂e。几何、距离、时长、过路费由高德实时规划提供；"
    "碳排/运营成本/推荐车型来自 route_candidate_carbon.parquet（缺失时回退 route_select），"
    "浏览器只展示、不重算。",
    sub="L1 路线情景与 L0 基准账分列：E₀ 仍以 baseline_carbon.parquet 为准，本页不覆盖主口径。",
)

layout.conclusion(
    "高德负责“路怎么走”，我们负责“走哪条、排多少碳、值不值”。"
    "路线实时可重算，路线几何贴着公路；碳账数字仍然只读结果表，单位一律为元。"
)
