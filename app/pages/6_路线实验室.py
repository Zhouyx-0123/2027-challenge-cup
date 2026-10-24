"""第 6 页 · 路线实验室：三个模式（高德实时 / OSM 碳基线 / 对照）。

页面结构（docs/36）
--------------------
第 6 页本来就是"路线实验室"，用户心智在这里。所以**不新开第 9 页**——
不增加顶部导航数量，不打散"五页主线 + 路线实验室 + 两个演算页"的叙事。

页面顶部是**共用的 OD / 车型 / 载重选择器**，三个模式共用，选一次就行：

============ ================================================================
模式          内容
============ ================================================================
① 实时路线     高德 JS API 实时规划：路况、4 策略、生长动画、过路费。
  · 高德       **route_lab_html.py / amapview.py 完全保留，不动一行。**
② 碳核算基线   OSM/OSRM 路线（OpenLayers）：逐 step 道路等级 / 速度 / 碳排。
  · OSM        顶部必标：**非实时 · 固定因子 · 道路等级 proxy**
③ 对照         表格对比两套口径的距离/时间/碳排/道路结构/差异原因。
              **不把两张地图硬叠**——基准不同、口径不同，叠了反而误导。
============ ================================================================

口径纪律
--------
* 浏览器端零业务计算：碳排/成本/车型全部来自 data/processed/*.parquet；
* 高德只负责"路怎么走"；OSM 基线只负责"公开路网的可复核基线"；
* **高德实时 vs OSM 基线不是同一口径**，页面必须写清楚。
"""
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import pandas as pd
import streamlit as st

import services
from components import (amapview, baseline_compare, baseline_map, basemap_align,
                       cards, layout, mapview, olmapview, route_visual, theme)

layout.setup("6 路线实验室")
src = services.source()
gate = services.gate()
params_ui = layout.sidebar_params(services.source_name(), gate, current="6")
lam = float(params_ui.get("lam", 100.0))

rows = services.route_lab_rows()
mock = services.is_mock()

theme.hero_compact(
    "路线实验室：高德实时规划 + OSM 碳基线对照",
    "选一条网点级 OD，切换规划策略，实时重算路线；再切到 OSM 基线看逐 step "
    "道路等级与碳账。两套口径分开呈现，不混算。",
    kicker="06 / ROUTE LAB · LIVE + BASELINE",
    badges=[layout.source_badge(services.source_name()), layout.gate_badge(gate),
            "在线实时规划", "OSM 非实时基线", "L1 路线情景", "前端只读"],
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

base_steps = services.route_baseline_steps()
base_summary = services.route_baseline_summary()
base_ready = services.route_baseline_available()

cards.chips([
    "%d 条网点级 OD" % len(rows),
    "%d 条高德真路线" % n_online,
    "%d 条离线兜底" % n_est,
    ("基准碳覆盖 %.1f%%" % float(coverage)) if coverage is not None else "基准碳覆盖 —",
    ("策略级碳账 %d OD" % n_cand_od) if n_cand_od else "策略碳账未生成",
    ("OSM 基线 %d step" % len(base_steps)) if base_ready else "OSM 基线未生成",
    "金额单位：元",
], tones=["mint", "mint", "skip", "info", "mint" if n_cand_od else "warn",
          "mint" if base_ready else "warn", "warn"])

# ---------------------------------------------------------------- 三模式
# 先选模式，再决定 OD 控件：基线是固定试点，不能继续显示一条无关的实时 OD。
_modes = ["① 实时路线 · 高德", "② 碳核算基线 · OSM", "③ 对照"]
mode = st.segmented_control(
    "路线模式", _modes, default=_modes[0],
    label_visibility="collapsed", key="p6_mode",
) or _modes[0]

# ---------------------------------------------------------------- 共用选择器
# 实时/对照共用 OD；基线固定试点，只读展示，避免"选广州、画北京"的误导。
layout.section(
    "共用选择器 · OD / 车型 / 载重",
    "实时与对照模式共用 OD；OSM 基线当前固定为上海车墩→北京通州试点。"
    "车型与载重在基线侧固定为 30 t 牵引车 / 30 t。",
)

_sel_cols = st.columns([2.2, 1.1, 1.1])
picked_row = None
with _sel_cols[0]:
    if mode == _modes[1]:
        st.text_input(
            "OD（基线试点，固定）",
            "上海车墩集货分拣中心 → 北京通州分拣中心",
            disabled=True,
        )
    else:
        _od_options = []
        for _, r in rows.iterrows():
            _od_options.append("%s → %s（%.0f km）" % (
                str(r.get("origin", "")), str(r.get("dest", "")),
                float(r.get("distance_km") or 0.0)))
        picked_label = st.selectbox("OD（网点级）", _od_options, index=0, key="p6_od")
        picked_row = rows.iloc[_od_options.index(picked_label)]
with _sel_cols[1]:
    vehicle_label = st.selectbox("车型", ["按路线推荐车型"], index=0, key="p6_vehicle")
with _sel_cols[2]:
    load_label = st.selectbox("载重", ["按路线载重"], index=0, key="p6_load")

# 只渲染当前模式：避免 st.tabs 同时挂载高德 iframe + OSM 地图，
# 切页时旧地图/旧 iframe 残留造成"闪现原界面"。

# ================================================================ 模式 ① 高德
if mode == _modes[0]:
    layout.section(
        "实时路线实验室",
        "左侧选线路与策略；右侧路线由高德实时规划。碳账数字来自结果表，浏览器不重算。",
    )

    default_id = str(picked_row["od_key"])
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
        fig = mapview.route_preview(picked_row, height=620)
        mapview.render(fig, height=620)
        cards.stats([
            {"label": "路线距离", "value": services.fmt(picked_row.get("distance_km"), 1) + " km",
             "sub": "L1 路线情景"},
            {"label": "路线时长", "value": services.fmt(picked_row.get("duration_min"), 0) + " min",
             "sub": "高德驾车规划"},
            {"label": "过路费", "value": services.fmt(picked_row.get("toll_yuan"), 0) + " 元"},
            {"label": "路线碳排", "value": services.fmt(picked_row.get("co2_t"), 2) + " t",
             "tone": "mint", "sub": "结果表口径"},
            {"label": "运营成本", "value": services.fmt(picked_row.get("opex_yuan"), 0) + " 元",
             "sub": "能耗 + 过路 + 时间"},
            {"label": "推荐车型", "value": str(picked_row.get("vehicle_type") or "—"),
             "sub": "λ=100 策略口径"},
        ], cols=6)
        cards.note(
            "离线模式画的是 route_od.parquet 里的缓存几何（按点数抽稀），只作示意；"
            "在线模式会用 AMap.Driving 重新规划，逐点贴合公路。"
        )

    # ---- 同一 OD 的四策略对比 ----
    cand_df = services.route_candidate_carbon()
    if not cand_df.empty and "od_key" in cand_df.columns:
        sub = cand_df[cand_df["od_key"].astype(str) == str(picked_row["od_key"])].copy()
        if not sub.empty:
            layout.section(
                "同一 OD 的四种策略",
                "默认线路 %s → %s；金额为元，碳账为 λ=100 策略情景，"
                "几何以实验室实时规划为准。"
                % (str(picked_row.get("origin", "")), str(picked_row.get("dest", ""))),
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
                "距离/时长可能差几个百分点。同一 OD 的策略路线可能重合"
                "（如当前无拥堵时避堵=综合），所以表内数值相同并不代表没有切换。"
                "碳账是 L2 展示口径，不覆盖 L0 基准。"
                "下方路线账本按 route_select 默认策略口径；上表按候选路线 0/4/1/6。",
                sub="候选路线预取：python -m tools.route_candidates；"
                    "碳账重算：python -m src.pipeline --with-routes。",
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
        file_name="route_lab.csv", mime="text/csv", width="stretch",
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
        "碳排/运营成本/推荐车型来自 route_candidate_carbon.parquet"
        "（缺失时回退 route_select），浏览器只展示、不重算。",
        sub="L1 路线情景与 L0 基准账分列：E₀ 仍以 baseline_carbon.parquet 为准，本页不覆盖主口径。",
    )

# ================================================================ 模式 ② OSM 基线
elif mode == _modes[1]:
    if not base_ready:
        layout.section("碳核算基线 · OSM", "基线数据还没生成")
        cards.empty_state(
            "上海—北京基线表还没生成",
            "运行  python -m tools.route_baseline --origin 121.315087,31.014995 "
            "--dest 116.664264,39.909698 --coord gcj02 --vehicle 30t_tractor --load 30"
            " 生成 steps.parquet 与 summary.json 后刷新本页。")
        cards.note(
            "基线走公共 OSRM（仅 spike 用，正式版换本地 GraphHopper/OSRM），"
            "**不依赖任何高德 key**，因此可离线复现、可审计。")
    else:
        layout.section(
            "碳核算基线 · OSM（上海车墩 → 北京通州）",
            "OpenLayers 渲染 OSM/OSRM 路线，按道路等级着色；右侧逐 step 可查"
            "道路等级、速度、碳排。点击 step 定位，鼠标悬停路线可看该段详情。",
        )

        # 顶部声明条（组件内已渲染）+ 页面侧再声明一次，双重保险
        cards.chips([
            "非实时",
            "固定因子 fixed_50",
            "道路等级 = proxy",
            "traffic_mode = freeflow",
            "© OpenStreetMap contributors",
        ], tones=["warn", "warn", "coral", "warn", "skip"])

        # 底图：有天地图 token 用天地图（CGCS2000，原生对齐）；
        # 没有就用高德栅格（GCJ-02，服务端已把几何转成 GCJ-02 对齐）。
        # 两种都是国内持牌服务，都不会出现"线偏在路边"的错位。
        token = olmapview.load_tianditu_token()
        layers: list = []
        if token:
            layers.extend(olmapview.default_tianditu_layers(token))
        else:
            layers.append(basemap_align.amap_xyz_layer())

        payload = baseline_map.build_baseline_payload(base_steps, base_summary, layers=layers)
        olmapview.render(
            payload,
            height=780,
            title="碳基线 · OSM 路线（OpenLayers）",
            fallback_note=("底图：高德栅格（GCJ-02，已做基准对齐）" if not token else
                           "底图：天地图 WMTS（CGCS2000，零偏移）"),
        )
        cards.note(
            "**底图与坐标基准**："
            + ("当前用**天地图 WMTS**（CGCS2000，与 OSM 的 WGS-84 差 &lt; 1 m），"
               "路线几何原样绘制。"
               if token else
               "当前用**高德栅格**（GCJ-02）。路线几何来自 OSM（WGS-84），"
               "已在服务端转成 GCJ-02 后再下发，**与底图对齐，不存在偏移**。"
               "转换只改坐标，**距离/时长/碳排数值一个字节都没动**。")
            + "右上角可切换底图。",
            sub="底图只是参照物，路线与全部指标仍然来自 OSM/OSRM —— "
                "与上方「① 实时路线 · 高德」不是同一口径。",
        )

        _s = base_summary
        cards.stats([
            {"label": "路线距离", "value": services.fmt(_s.get("distance_km"), 1) + " km",
             "sub": "OSRM freeflow"},
            {"label": "路线时长", "value": services.fmt(_s.get("duration_min"), 0) + " min",
             "sub": "无实时路况"},
            {"label": "碳排基线", "value": services.fmt(_s.get("carbon_kg"), 0) + " kg",
             "tone": "mint", "sub": "%s / %s" % (_s.get("factor_version"), _s.get("factor_mode"))},
            {"label": "车型 / 载重", "value": "%s / %s t" % (_s.get("vehicle"), _s.get("load_t")),
             "sub": "固定不变"},
            {"label": "step 数", "value": services.fmt(_s.get("step_count"), 0),
             "sub": "逐 step 可复核"},
            {"label": "端点吸附", "value": "%.0f / %.0f m" % (
                float((_s.get("snap_distance_m") or {}).get("origin", 0.0)),
                float((_s.get("snap_distance_m") or {}).get("dest", 0.0))),
             "sub": "距 OSM 路网（容差 50 m）"},
        ], cols=6)

        # 道路等级占比（直接读 summary，不重算）
        layout.section("道路等级结构（proxy）", "按里程占比；等级由 OSM 名称/编号/速度推断，非官方等级")
        _share = _s.get("road_class_share") or {}
        _cards = []
        for _k, _v in _share.items():
            _cards.append({
                "label": olmapview.ROAD_LABELS.get(_k, _k),
                "value": "%.1f%%" % float(_v),
                "sub": "%d step" % int((_s.get("road_class_count") or {}).get(_k, 0)),
                "tone": "mint" if _k == "motorway" else ("info" if _k == "trunk" else "plain"),
            })
        if _cards:
            cards.stats(_cards, cols=min(6, len(_cards)))
        cards.note(
            "**G 开头不等于高速**：G + 1~2 位是高速（G15 沈海），G + 3 位是国道"
            "（G103 京台线）。本表的等级是 proxy，用来解释速度与路网结构差异，"
            "**不给碳排乘任何权重**——碳排只由距离 × 固定因子决定。",
            sub="等级判定规则见 src/routing/road_class.py；"
                "置信度 high/medium/low 逐 step 记录在 steps.parquet 的 road_class_confidence 列。",
        )

        # 逐 step 明细表
        layout.section("逐 step 明细", "距离 / 时长 / 速度 / 道路等级 / 碳排；完整表可下载")
        _show = base_steps.head(60)
        _rows = []
        for _, r in _show.iterrows():
            _rows.append([
                services.fmt(r.get("step_index"), 0),
                str(r.get("road_name") or "（无名路段）"),
                str(r.get("road_ref") or "—"),
                olmapview.ROAD_LABELS.get(str(r.get("road_class")), str(r.get("road_class"))),
                str(r.get("road_class_confidence") or "—"),
                services.fmt(r.get("distance_km"), 1),
                services.fmt(r.get("duration_min"), 1),
                services.fmt(r.get("speed_kmh"), 0),
                services.fmt(r.get("carbon_kg"), 1),
            ])
        cards.htable(
            ["#", "道路名", "编号", "等级(proxy)", "置信度", "距离(km)",
             "时长(min)", "速度(km/h)", "碳排(kg)"],
            _rows, num_cols=[0, 5, 6, 7, 8],
        )
        st.download_button(
            "下载逐 step 基线表 CSV",
            data=base_steps.drop(columns=["geometry_geojson"], errors="ignore")
            .to_csv(index=False).encode("utf-8-sig"),
            file_name="route_baseline_sh_bj_steps.csv", mime="text/csv", width="stretch",
        )

        cards.note(
            "**这组数字是什么**：不依赖高德 key 的公开路网基线。"
            "路线来自 OSRM（基于 OSM 路网），逐 step 给出距离/时长/速度/道路等级/碳排，"
            "可逐行复核。",
            sub="**这不是什么**：不是实时路况（traffic_mode=freeflow），"
                "不是精确碳账（factor_mode=fixed_50 固定因子），"
                "道路等级是 proxy 而非官方等级。",
        )

# ================================================================ 模式 ③ 对照
else:
    layout.section(
        "对照 · 高德实时 vs OSM 基线",
        "**这不是同一个口径。** 高德是实时规划 + 项目因子表；"
        "OSM 基线是 freeflow + 固定因子。表格逐维度列差异与原因。",
    )

    if not base_ready:
        cards.empty_state("OSM 基线还没生成", "先跑 python -m tools.route_baseline 生成基线表。")
    else:
        amap_side = baseline_compare.pick_amap_side(
            services.routes(), services.route_candidate_carbon())
        if not amap_side:
            cards.note("现有 AMap 缓存里没有可对照的长途 OD，暂无法对照。")
        else:
            compare_rows = baseline_compare.build_comparison(
                base_steps, base_summary, amap_side)
            table_rows = []
            for row in compare_rows:
                table_rows.append([
                    row["dim"], row["amap"], row["osm"], row["delta"], row["reason"],
                ])
            cards.htable(
                ["维度", "高德实时口径", "OSM 基线口径", "差异", "差异原因"],
                table_rows,
            )

            # 口径声明
            cards.note(
                "**“高德实时 vs OSM 基线”不是同一口径，不能横向比大小。**"
                "三点硬差异：① 坐标基准不同（GCJ-02 vs WGS-84）；"
                "② 路况口径不同（实时 vs freeflow）；"
                "③ 车型/因子不同（AMap 侧 9.6 m 厢式货车 18 t 走项目因子表，"
                "OSM 基线 30 t 牵引车 30 t 走固定因子）。",
                sub="所以本页**不把两张地图硬叠**——叠了会因基准不同而错位 500–800 m，"
                    "且会让读者做「哪个更优」的错误比较。",
            )

            # 差异来源的可核验依据
            layout.section("差异的可核验依据", "每一行差异都有出处，不是「感觉不一样」")
            _amp = (base_summary.get("snap_distance_m") or {})
            cards.kv_table([
                {"k": "OSM 基线 provider", "v": "%s（traffic_mode=%s）" % (
                    base_summary.get("provider"), base_summary.get("traffic_mode"))},
                {"k": "OSM 基线坐标基准", "v": "WGS-84（OSM/OSRM 原生）"},
                {"k": "AMap 侧坐标基准", "v": "GCJ-02（高德原生）"},
                {"k": "端点落在 OSM 路网的距离", "v": "起点 %.1f m / 终点 %.1f m（容差 50 m）" % (
                    float(_amp.get("origin", 0.0)), float(_amp.get("dest", 0.0)))},
                {"k": "AMap 对照 OD", "v": str(amap_side.get("od_pair") or "—")},
                {"k": "AMap 对照车型/载重", "v": "%s / %s t" % (
                    amap_side.get("vehicle_type") or "—",
                    services.fmt(amap_side.get("load_t"), 1))},
                {"k": "OSM 基线车型/载重", "v": "%s / %s t" % (
                    base_summary.get("vehicle"), base_summary.get("load_t"))},
                {"k": "碳因子", "v": "%s / %s（固定因子，非 VSP）" % (
                    base_summary.get("factor_version"), base_summary.get("factor_mode"))},
                {"k": "OSM 署名", "v": "© OpenStreetMap contributors（路线几何与道路等级）"},
            ])

            cards.note(
                "对照 OD 的起点与基线不同（苏州昆山 vs 上海车墩），"
                "因为现有 AMap 缓存里**没有**上海车墩→北京通州这条线——"
                "这是数据现状，不是笔误。要做严格同 OD 对照，"
                "需要先用高德实时规划拉一条上海车墩→北京通州并存入 route_od。",
                sub="本对照的价值在于口径透明，不在于数字接近。",
            )

layout.conclusion(
    "高德实时告诉你这条线现在怎么走最快；OSM 基线告诉你这条路本身有多长、"
    "结构如何、碳排大概多少。两套口径分开看、不混算——这才是能拿去汇报的对照。"
)
