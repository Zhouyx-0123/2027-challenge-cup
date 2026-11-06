# -*- coding: utf-8 -*-
"""真实高德路网 · 历史碳画像 · 四策略 Q 选择演示面板。"""
from __future__ import annotations

import html
import sys
from datetime import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import altair as alt
import pandas as pd
import streamlit as st

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
ROOT = APP_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import live_services  # noqa: F401,E402  # 先把仓库根加入 sys.path
from components import route_map  # noqa: E402
from src.rl.historical import (HistoricalProfile, ODOption, build_od_catalog,  # noqa: E402
                               build_profile)
from src.rl.real_world import load_policy_payload, solve_real_route  # noqa: E402

DATA_DIR = ROOT / "data" / "processed"

CSS = """
<style>
.ql-wrap{background:var(--ct-glass);border-radius:24px;padding:20px 22px;
  box-shadow:0 0 0 1px var(--ct-ring);margin:4px 0 14px}
.ql-kicker{font:10px 'JetBrains Mono',monospace;letter-spacing:.14em;
  color:var(--ct-mint,#5FE3D2);margin-bottom:8px}
.ql-title{font-family:var(--ct-font-display);font-size:24px;font-weight:760;
  letter-spacing:-.03em;color:var(--ct-ink);margin:0 0 6px}
.ql-sub{font-size:13px;line-height:1.8;color:var(--ct-ink-3);max-width:980px}
.ql-kpis{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin:14px 0 4px}
.ql-kpi{padding:12px 14px;border-radius:16px;background:rgba(255,255,255,.045);
  box-shadow:0 0 0 1px var(--ct-ring)}
.ql-kpi span{display:block;font-size:11px;color:var(--ct-ink-3);margin-bottom:5px}
.ql-kpi b{font-family:var(--ct-font-display);font-size:22px;letter-spacing:-.03em;
  color:var(--ct-ink)}
.ql-kpi small{display:block;font-size:10px;color:var(--ct-ink-3);margin-top:4px}
.ql-flag{display:inline-flex;gap:6px;align-items:center;padding:5px 10px;border-radius:999px;
  font-size:11px;margin:2px 0 10px}
.ql-ok{background:rgba(95,227,210,.10);color:#8BF3E6;box-shadow:0 0 0 1px rgba(95,227,210,.25)}
.ql-warn{background:rgba(246,200,95,.10);color:#F6C85F;box-shadow:0 0 0 1px rgba(246,200,95,.25)}
.ql-note{font-size:11px;line-height:1.75;color:var(--ct-ink-3);
  border-left:2px solid var(--ct-ring-2);padding-left:10px;margin-top:6px}
@media(max-width:900px){.ql-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
"""


@st.cache_data(show_spinner=False)
def load_bundle() -> Tuple[HistoricalProfile, List[ODOption], Dict[str, Any]]:
    master = pd.read_parquet(DATA_DIR / "jd_segment_master.parquet")
    baseline = pd.read_parquet(DATA_DIR / "baseline_carbon.parquet")
    route_od = pd.read_parquet(DATA_DIR / "route_od.parquet")
    candidates = pd.read_parquet(DATA_DIR / "route_candidates.parquet")
    profile = build_profile(master)
    options, meta = build_od_catalog(master, baseline, route_od, candidates)
    return profile, options, meta


def _distinct_routes(option: ODOption) -> int:
    return len({round(float(c.distance_km), 1) for c in option.candidates})


def _default_index(options: List[ODOption]) -> int:
    # 默认选一个“四策略真的会分叉”的历史 OD：时间优先走 S1，其余走 S6。
    for origin_key, dest_key in (("句容", "奥南"), ("北苑", "松江"), ("车墩", "昆山")):
        for index, option in enumerate(options):
            if origin_key in option.origin and dest_key in option.dest:
                return index
    return 0


def _option_label(option: ODOption) -> str:
    return "%s → %s · %s 趟 · 均 %.1f km/趟" % (
        option.origin, option.dest, "{:,}".format(int(option.trips)),
        option.total_km / max(int(option.trips), 1))


def _kpi(label: str, value: str, note: str = "") -> str:
    return ("<div class='ql-kpi'><span>" + html.escape(label) + "</span><b>"
            + html.escape(value) + "</b>"
            + ("<small>" + html.escape(note) + "</small>" if note else "") + "</div>")


def _render_intro() -> None:
    st.markdown(
        "<div class='ql-wrap'><div class='ql-kicker'>REAL AMAP · HISTORICAL EF · Q POLICY</div>"
        "<div class='ql-title'>真实路网上的 Q 策略</div>"
        "<div class='ql-sub'>数据链：京东 33,214 张运单 → 12,167 趟 → 5 档×4 载重带历史 EF 画像"
        "（TTW 2.63 kgCO2/L，与主账 E₀ 同口径）→ 高德 287 个缓存 OD / 四种策略实算里程·时长·收费"
        "→ Q 表在真实 OD 上离线拟合后给出策略，四策略枚举作为并排基线与兜底。</div></div>",
        unsafe_allow_html=True)
    st.caption("演示通道：只读 data/processed 的缓存表，不写主碳账，不改 E₀，不进 28 项闸门。")


def _render_kpis(result: Dict[str, Any]) -> None:
    selected = result["selected"]
    q = result["q"]
    flag = ("Q 直接选中" if result["selected_is_q"] else
            ("Q 回退枚举最优" if q.get("fallback") else "Q 未启用，枚举最优"))
    cls = "ql-ok" if result["selected_is_q"] else "ql-warn"
    st.markdown(
        "<div class='ql-kpis'>"
        + _kpi("选中策略", selected["label"], selected["route_id"] + " · " + flag)
        + _kpi("预计时长", "%.1f min" % selected["time_min"], "均速 %.1f km/h" % selected["avg_speed_kmh"])
        + _kpi("碳排", "%.2f kg" % selected["carbon_kg"], "TTW 历史 EF")
        + _kpi("综合费用", "%.2f 元" % selected["cost_yuan"], "含油/碳/路桥")
        + _kpi("里程", "%.1f km" % selected["distance_km"], "收费 %.0f 元" % selected["toll_yuan"])
        + "</div>"
        + "<div class='ql-flag " + cls + "'>" + html.escape(flag)
        + " · Q 置信度 " + ("%.3f" % float(q.get("confidence") or 0.0))
        + " · " + html.escape(str(q.get("reason") or "")) + "</div>",
        unsafe_allow_html=True)


def _render_map(result: Dict[str, Any]) -> None:
    selected = result["selected"]
    route = dict(result["map_route"])
    route["route_label"] = "%s · %s" % (selected["label"], selected["route_id"])
    route["badge"] = "Q 选择" if result["selected_is_q"] else "枚举最优"
    route["recommended"] = True
    st.markdown("#### 真实高德几何")
    st.caption("几何 = 高德缓存主路线 polyline；四种策略共享同一几何，下表指标为高德四策略实算。")
    route_map.render_route_map(
        [route], result["origin"], result["destination"],
        origin_label=result["origin_label"], destination_label=result["destination_label"])


def _render_q(result: Dict[str, Any]) -> None:
    q = result["q"]
    st.markdown("#### Q 策略读数")
    labels = q.get("q_labels") or []
    values = q.get("q_values") or []
    if values and labels:
        frame = pd.DataFrame({"策略": labels[:len(values)],
                              "Q 值": [float(v) for v in values]})
        low = float(frame["Q 值"].min())
        high = float(frame["Q 值"].max())
        pad = max((high - low) * 0.18, 0.0008)
        frame["基准"] = low
        base = alt.Chart(frame).encode(
            y=alt.Y("策略:N", sort=list(frame["策略"]),
                    axis=alt.Axis(labelAngle=0, title=None)))
        stem = base.mark_rule(color="#33506B", strokeWidth=2).encode(
            x=alt.X("基准:Q", scale=alt.Scale(domain=[low - pad, high + pad], zero=False),
                    axis=alt.Axis(format=".3f", title="Q 值（越接近 0 越好）")),
            x2="Q 值:Q")
        dots = base.mark_circle(size=150, color="#5FE3D2").encode(
            x=alt.X("Q 值:Q", scale=alt.Scale(domain=[low - pad, high + pad], zero=False)),
            tooltip=[alt.Tooltip("策略:N"), alt.Tooltip("Q 值:Q", format=".6f")])
        value_text = base.mark_text(align="left", dx=8, color="#DCE8F5", fontSize=11).encode(
            x=alt.X("Q 值:Q", scale=alt.Scale(domain=[low - pad, high + pad], zero=False)),
            text=alt.Text("Q 值:Q", format=".4f"))
        st.altair_chart((stem + dots + value_text).properties(height=215),
                        width="stretch")
        st.caption("Q 值是负的归一化运营成本（越接近 0 越好），不是元；"
                   "四策略很接近时横轴会自动放大。")
    elif q.get("available"):
        st.info("Q 表已加载，但该状态没有有效 Q 值。")
    else:
        st.warning("未找到 Q 策略文件；当前只做四策略枚举。")
    features = q.get("features") or []
    names = q.get("feature_names") or []
    if features and names:
        st.dataframe(
            pd.DataFrame({"状态特征": names[:len(features)],
                          "取值": [round(float(x), 4) for x in features]}),
            hide_index=True, width="stretch")
    if q.get("fallback"):
        st.warning(q.get("reason") or "Q 结果已回退。")
    st.caption(q.get("note") or "")


def _render_solutions(result: Dict[str, Any]) -> None:
    st.markdown("#### 四策略对比")
    rows = []
    for item in result["solutions"]:
        rows.append({
            "策略": item["label"],
            "路线": item["route_id"],
            "里程 km": item["distance_km"],
            "时长 min": item["time_min"],
            "碳 kg": item["carbon_kg"],
            "费用 元": item["cost_yuan"],
            "运营成本": item["operation_cost"],
            "标记": "Q 选中" if result.get("selected_is_q") and item.get("is_selected") else
                    ("枚举最优" if item.get("is_best") else
                     ("选中" if item.get("is_selected") else "")),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("运营成本是相对 AMap 策略 0 参考路线的归一化综合成本，不是绝对元；"
               "策略 0 的运营成本恒为 1.0。")


def _render_segments(result: Dict[str, Any]) -> None:
    selected = result["selected"]
    st.markdown("#### 逐段碳分解")
    rows = []
    for seg in selected.get("segments") or []:
        cell = str(seg.get("ef_cell") or "")
        speed_bin, _, load_bin = cell.partition("/")
        rows.append({
            "段": seg["index"] + 1,
            "路段": seg["sid"],
            "起点 km": seg["from_km"],
            "终点 km": seg["to_km"],
            "里程 km": seg["length_km"],
            "速度 km/h": seg["speed_kmh"],
            "时间 min": seg["time_min"],
            "碳 kg": seg["carbon_kg"],
            "EF g/km": seg["ef_g_per_km"],
            "速度带": speed_bin,
            "载重带": load_bin,
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=260)
    st.caption("EF 来自历史 12,036 趟有效样本的 5 档×4 载重带里程加权画像；"
               "低样本单元会回退到载重/速度/全局均值。")


def _render_notes(result: Dict[str, Any]) -> None:
    with st.expander("口径与边界", expanded=False):
        for note in result.get("notes") or []:
            st.write("- " + note)
        st.write("- 287 个 OD 中 22 个存在不同候选路线；其余四策略指标接近或相同。")
        st.write("- 若 Q 选择只在同一路线上换标签，运营成本可能不变；"
                 "此时以路线和费用表为准，不要只看策略名。")


def render(carbon_price: float = 100.0) -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    _render_intro()
    try:
        profile, options, meta = load_bundle()
    except Exception as exc:  # pragma: no cover - 数据缺失由页面提示
        st.error("无法读取真实 OD 缓存表：%s" % exc)
        return
    if not options:
        st.warning("没有可用真实 OD（需要 route_od 的 amap_rest 几何）。")
        return

    st.markdown("#### 选择一趟真实历史 OD")
    multi_only = st.checkbox("只看多路线 OD（四策略中有不同里程）", value=False)
    pool = [o for o in options if (not multi_only or _distinct_routes(o) > 1)]
    if not pool:
        st.warning("当前筛选下没有 OD。")
        return
    default = _default_index(pool)
    idx = st.selectbox("OD", list(range(len(pool))), index=default,
                       format_func=lambda i: _option_label(pool[i]))
    chosen = pool[idx]
    hh = int(chosen.departure_minute // 60) % 24
    mm = int(round(chosen.departure_minute % 60)) % 60
    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        load = st.number_input("载重 t", min_value=1.0, max_value=60.0,
                               value=float(chosen.load_t), step=0.5)
    with c2:
        departure = st.time_input("出发时刻（历史中位）", value=time(hh, mm), step=900)
    with c3:
        st.metric("历史中位速度", "%.1f km/h" % chosen.speed_kmh,
                  help="来自该 OD 已通过的京东趟；仅作参照，不参与 Q 求解。")
    carbon = st.number_input("碳价 元/t", min_value=0.0, max_value=1000.0,
                             value=float(carbon_price), step=10.0)
    departure_minute = departure.hour * 60 + departure.minute

    signature = (chosen.od_key, round(float(load), 3), int(departure_minute),
                 round(float(carbon), 2))
    run = st.button("运行 Q 策略求解", type="primary", width="stretch")
    if run:
        payload = load_policy_payload()
        with st.spinner("正在构建真实路网、枚举四策略并读取 Q 表…"):
            try:
                result = solve_real_route(
                    chosen, profile, load_t=float(load),
                    departure_minute=float(departure_minute),
                    carbon_price=float(carbon), policy_payload=payload)
            except Exception as exc:
                st.error("求解失败：%s" % exc)
                return
        st.session_state["ql_real_result"] = result
        st.session_state["ql_real_signature"] = signature

    result = st.session_state.get("ql_real_result")
    if result is None:
        st.info("选择 OD 后点击“运行 Q 策略求解”。演示会读取真实高德几何与历史 EF 画像，"
                "不调用实时高德 REST 接口。")
        st.caption("缓存 OD：%d 个；覆盖历史趟：%s；策略数：4。" % (
            int(meta.get("n_od", len(options))),
            "{:,}".format(int(meta.get("n_trips", 0)))))
        return
    if st.session_state.get("ql_real_signature") != signature:
        st.warning("参数已改变，当前显示的是上一次求解结果；重新运行以更新。")
    _render_kpis(result)
    left, right = st.columns([1.45, 1.0])
    with left:
        _render_map(result)
    with right:
        _render_q(result)
    _render_solutions(result)
    _render_segments(result)
    _render_notes(result)


__all__ = ["render", "load_bundle"]
