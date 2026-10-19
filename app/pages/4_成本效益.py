"""第 4 页 · 成本效益：多花多少钱、几年回本、λ 一动推荐换不换。

数据契约（只读，不重算）：
- final_report.parquet：capex_wan / opex_save_wan / payback_years / unit_cost_yuan_t /
  emission_t / carbon_saving_t（evaluator 静态标定 + E₀ 等比缩放）
- kpi.json：E₀ 与汇总口径
- 侧栏全局参数：λ（内部碳价）与成本口径（赛题 / 碳价延展）

页面口径：λ 相关换算 = 排放量 × λ，属“碳价延展口径”的展示换算（侧栏 help 已定义），
在页面上逐处标注；业务口径（成本构成分解 E4）待曾广接真车队参数，未接前只列标定依据。
"""

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import numpy as np
import pandas as pd
import streamlit as st

import services
from components import cards, charts, layout, theme

layout.setup("4 成本效益")
src = services.source()
gate = services.gate()
layout.sidebar_params(services.source_name(), gate, current="4")

mock = services.is_mock()
k = services.kpis()
lam = float(st.session_state.get("lam", 100.0))
basis_mode = str(st.session_state.get("basis", "赛题口径"))

theme.hero(
    "成本效益：多花多少钱、几年回本",
    "决定回本的是两个数：一次性投资与年度节省；λ 把碳变成钱——"
    "它是全站唯一的定价旋钮，一动，回本年限就变。",
    kicker="04 / COST & PAYBACK",
    badges=[layout.source_badge(services.source_name()), layout.gate_badge(gate),
            "λ = " + services.fmt(lam, 0) + " 元/t", "碳价延展口径"],
)
if mock:
    layout.demo_note(str(src.get("reason", "")))

cards.steps([
    {"n": "00", "t": "首页"}, {"n": "01", "t": "基准核算"}, {"n": "02", "t": "高排放诊断"},
    {"n": "03", "t": "替代方案"}, {"n": "04", "t": "成本效益"}, {"n": "05", "t": "碳会计台账"},
], active=4)

sc = services.scenarios()
if sc.empty or "scenario" not in sc.columns:
    theme.section("成本对账", "")
    cards.empty_state(
        "方案表还没生成",
        "先运行  python -m src.pipeline  生成 final_report.parquet（七方案成本对比）。")
    layout.conclusion("没有方案表就不算账——前端只读，不编数字。")
    st.stop()

e0_t = float(k.get("e0_t") or 0.0)
main_row = sc[sc["is_main"] == True].iloc[0] if (sc["is_main"] == True).any() else sc.iloc[-1]


# ---- 展示口径换算（非业务重算）：λ 只进入“年节省”与“回本年限” ----
def annual_save_wan(row, lam_value: float) -> float:
    opex = float(row.get("opex_save_wan") or 0.0)
    saving_t = float(row.get("carbon_saving_t") or 0.0)
    return opex + saving_t * lam_value / 10000.0


def payback_at(row, lam_value: float):
    annual = annual_save_wan(row, lam_value)
    capex = float(row.get("capex_wan") or 0.0)
    return (capex / annual) if annual > 0 else None


# ================= A · λ 敏感性 =================
st.markdown("")
layout.section("λ 敏感性", "唯一旋钮：碳一定价，回本年限全线缩短，推荐序可能翻转")

alt = sc[(sc["capex_wan"] > 0)].copy()
pb_lam = {str(r["scenario"]): payback_at(r, lam) for _, r in alt.iterrows()}
pb_zero = {str(r["scenario"]): payback_at(r, 0.0) for _, r in alt.iterrows()}
fastest_now = min((v for v in pb_lam.values() if v is not None), default=None)
fastest_name = next((n for n, v in pb_lam.items() if v == fastest_now), "—")
main_pb_now = payback_at(main_row, lam)
main_pb_zero = payback_at(main_row, 0.0)

cards.stats([
    {"label": "当前 λ（元/tCO₂e）", "value": services.fmt(lam, 0), "tone": "info",
     "sub": "侧栏全局参数调整"},
    {"label": "主推 S5 回本", "value": services.fmt(main_pb_now, 2) + " 年", "tone": "amber",
     "sub": "λ=0 时 " + services.fmt(main_pb_zero, 2) + " 年"},
    {"label": "当前回本最快", "value": services.fmt(fastest_now, 2) + " 年", "tone": "mint",
     "sub": fastest_name},
    {"label": "λ=1000 时 S5 回本", "value": services.fmt(payback_at(main_row, 1000.0), 2) + " 年",
     "tone": "coral", "sub": "碳价越高，减排越值钱"},
], cols=4)

# 敏感性网格（展示口径）：λ 0–1000，步长 10
grid = np.arange(0, 1001, 10.0)
rows = []
for _, r in alt.iterrows():
    name = str(r["scenario"])
    for g in grid:
        rows.append({"λ": float(g), "方案": name,
                     "回本年限": payback_at(r, float(g))})
curve = pd.DataFrame(rows)
charts.line_multi(
    curve, "λ", "回本年限", "方案", vline_x=lam,
    vline_label="当前 λ = " + services.fmt(lam, 0),
    title="回本年限 × λ（展示口径：年度节省 = 运营节省 + 减排量 × λ）",
    height=400, ymax=12,
    labels={"λ": "内部碳价 λ（元/tCO₂e）", "回本年限": "回本年限（年）"})
cards.note("读法：每条线是一个方案；竖线是当前 λ。线越陡，说明该方案对碳价越敏感——"
           "减排量越大，碳价越高时回本改善越多。但回本最快 ≠ 推荐：旋钮量程内回本最快的"
           "S1 / S3 减排率均未达 70% 目标线，再快也不进推荐集。达标集合内 S5 回本始终不慢于 "
           "S7（λ=0 并列，λ>0 时 S5 减排量更大而更快）。S0 基准无投资，不在图上。")

# 当前 λ 下的对账表
rows = []
for _, r in alt.sort_values("order").iterrows():
    pb0 = payback_at(r, 0.0)
    pb1 = payback_at(r, lam)
    delta = (pb1 - pb0) if (pb0 is not None and pb1 is not None) else None
    rows.append([
        str(r["scenario"]),
        services.fmt(r.get("capex_wan"), 0),
        services.fmt(r.get("opex_save_wan"), 1),
        services.fmt(annual_save_wan(r, lam), 1),
        services.fmt(pb0, 2),
        services.fmt(pb1, 2),
        (("%+.2f" % delta) if delta is not None else "—"),
        "✓" if bool(r.get("meets_target")) else "—",
    ])
cards.htable(["方案", "投资(万元)", "运营节省(万元/年)", "含λ节省(万元/年)",
              "回本 λ=0(年)", "回本 当前λ(年)", "λ 影响(年)", "达标"],
             rows, num_cols=[1, 2, 3, 4, 5, 6])

# 达标集合内主推的减排计数优势（动态计算，避免硬编码过期）
meet = sc[sc["meets_target"] == True]
gap_pct = None
if len(meet) > 1:
    others = meet[meet["is_main"] != True]
    if not others.empty:
        gap_pct = float(main_row["reduction_pct"]) - float(others["reduction_pct"].max())

# 推荐翻转点（展示口径扫描）：λ 0–3000 步长 5，追踪“回本最快”头衔的易主
wide_grid = np.arange(0, 3001, 5.0)
reigns = []   # [(λ 起点, 方案名)]——冠军更替序列
for g in wide_grid:
    vals = {str(r["scenario"]): payback_at(r, float(g)) for _, r in alt.iterrows()}
    vals = {n: v for n, v in vals.items() if v is not None}
    if not vals:
        continue
    champ = min(vals.items(), key=lambda t: t[1])[0]
    if not reigns or reigns[-1][1] != champ:
        reigns.append((float(g), champ))

if len(reigns) > 1:
    segs = []
    for i, (lam0, name) in enumerate(reigns):
        row = alt[alt["scenario"].astype(str) == name].iloc[0]
        lam1 = reigns[i + 1][0] if i + 1 < len(reigns) else None
        span = ("λ " + services.fmt(lam0, 0)
                + ("–" + services.fmt(lam1, 0) if lam1 is not None else " 以上"))
        mark = "达标" if bool(row.get("meets_target")) else "未达标"
        segs.append(span + " <span class='hl2'>" + cards.esc(name) + "</span>（减排 "
                    + services.fmt(row.get("reduction_pct"), 1) + "% · " + mark + "）")
    cards.formula("推荐翻转点（展示口径扫描）：回本最快的方案随 λ 易主——"
                  + " → ".join(segs) + "。")
    dial_max = reigns[-1][0] if reigns[-1][1] == str(main_row["scenario"]) else None
    if dial_max is not None and dial_max <= 1000.0:
        cards.note("主推成为回本最快需 λ ≥ " + services.fmt(dial_max, 0)
                   + " 元/t，在旋钮量程内；但冠军易主不改变推荐——推荐看的是 70% 达标线。")
    else:
        cards.note("旋钮量程只有 0–1000 元/t：量程内主推从来不是回本最快的，"
                   + "但冠军 S1 / S3 减排率均未达 70% 目标线，进不了推荐集。"
                   + ("达标集合内 S5 回本始终不慢于 S7，且多减 "
                      + services.fmt(gap_pct, 1) + " 个百分点——同价选多减，主推是 S5。"
                      if gap_pct is not None else "主推满足 70% 达标线，是达标集合内减排计数最高的方案。"))
else:
    cards.note("在 λ ∈ [0, 3000] 内回本最快者不随碳价易主——但推荐不只看回本："
               "主推满足 70% 达标线，是达标集合内减排计数最高的方案。")

# ================= B · 成本对账 =================
st.markdown("")
layout.section("成本对账", "同一份数据、同一套账：每个方案的排放都能对回 E₀")

rows, row_cls = [], []
for _, r in sc.sort_values("order").iterrows():
    rows.append([
        str(r["scenario"]),
        services.fmt(r.get("capex_wan"), 1),
        services.fmt(r.get("opex_save_wan"), 1),
        services.fmt(r.get("payback_years"), 2),
        services.fmt(r.get("unit_cost_yuan_t"), 0),
        services.fmt(r.get("emission_t"), 2),
        services.fmt(r.get("carbon_saving_t"), 2),
        "✓ 达标" if bool(r.get("meets_target")) else "—",
    ])
    row_cls.append("main" if bool(r.get("is_main")) else ("base" if float(r["reduction_pct"]) <= 0 else ""))
cards.htable(["方案", "投资(万元)", "年节省(万元)", "回本 λ=0(年)", "单位减排成本(元/t)",
              "排放(t)", "减排量(t)", "达标"], rows, row_cls=row_cls,
             num_cols=[1, 2, 3, 4, 5, 6])

# 对拍检查（展示口径）：emission_t ≈ E₀ × (1 − 减排率)
bad = []
for _, r in sc.iterrows():
    expect = e0_t * (1.0 - float(r["reduction_pct"]) / 100.0)
    got = float(r.get("emission_t") or 0.0)
    if abs(got - expect) > 0.01:
        bad.append(str(r["scenario"]))
if bad:
    cards.chips(["对账不一致：" + "、".join(bad)], tones=["bad"])
else:
    cards.chips(["✓ %d 个方案排放量全部对回 E₀ × (1 − 减排率)" % len(sc)], tones=["ok"])
cards.note("这是页面级的展示校验，与 pipeline 出口闸门（schemes 结构项）互为备份；"
           "任何一项对不上，说明结果表被改过——以 gate_report.json 为准。")

# 投资回收曲线
st.markdown("")
cards.panel_head("投资回收曲线", "累计节省穿越投资额的年份即回本点",
                 "展示口径 · 年度节省含 λ")
pick = st.selectbox("选一个方案看曲线", alt["scenario"].astype(str).tolist(),
                    index=list(alt["scenario"].astype(str)).index(str(main_row["scenario"]))
                    if str(main_row["scenario"]) in list(alt["scenario"].astype(str)) else 0,
                    key="p4_pick")
specs = []
for name in [pick, str(main_row["scenario"]), "S7 绿氢 WTW + 电网清洁化"]:
    row = alt[alt["scenario"].astype(str) == name]
    if not row.empty:
        r = row.iloc[0]
        if all(s["name"] != str(r["scenario"]) for s in specs):
            specs.append({
                "name": str(r["scenario"]),
                "capex_wan": float(r.get("capex_wan") or 0.0),
                "annual_save_wan": annual_save_wan(r, lam),
                "is_main": bool(r.get("is_main")),
            })
s7 = alt[alt["scenario"].astype(str) == "S7 绿氢 WTW + 电网清洁化"]
s7_row = s7.iloc[0] if not s7.empty else None
charts.payback_curve(specs, years=15)
if s7_row is not None:
    cards.note("菱形标记 = 回本点。S5 与 S7 同价同运营节省（静态标定）：λ=0 时两条曲线重合；"
               + ("当前 λ > 0，S5 因减排量更大，累计节省曲线在上方、回本更早。"
                  if lam > 0 else "把侧栏 λ 拖离 0，S5 会因减排量更大而抬到 S7 上方。")
               + "差别在减排计数口径（" + services.fmt(main_row["reduction_pct"], 1) + "% vs "
               + services.fmt(float(s7_row["reduction_pct"]), 1) + "%），这正是“保守边界”行的意义。")
else:
    cards.note("菱形标记 = 回本点；曲线与投资水平线的交叉年份即静态回本点。")

# ================= C · 成本构成与口径 =================
st.markdown("")
layout.section("成本构成与口径", "分解表待真车队参数；接表前，标定依据逐条列示")

cards.chips(["成本口径：" + basis_mode,
             "赛题口径 = 购置 + 能耗 + 维护 − 补贴",
             "延展口径 = 排放量 × λ"], tones=["info", "skip", "skip"])
rows = [[str(r["scenario"]), services.fmt(r.get("capex_wan"), 1),
         services.fmt(r.get("opex_save_wan"), 1), str(r.get("basis", ""))]
        for _, r in sc.sort_values("order").iterrows()]
cards.htable(["方案", "投资(万元)", "年节省(万元)", "标定依据（verbatim）"], rows,
             num_cols=[1, 2])
cards.note("成本构成分解（购置净 + 能耗 + 维护 − 补贴，E4）与真实成本模型/敏感性归曾广，"
           "当前 SCENARIOS 为答辩稿表 19–21 静态标定；替换后重跑 python -m src.pipeline，"
           "本页与闸门不用动。")

layout.conclusion(
    "钱的决定因素是两个数：一次性投资与年度节省。λ=0 时 S5 回本 %s 年，λ=%s 时 %s 年；"
    "达标集合内 S5 回本始终不慢于 S7（λ>0 时更快），且多减 %s 个百分点——同价选多减，主推是 S5。"
    % (services.fmt(main_pb_zero, 1), services.fmt(lam, 0), services.fmt(main_pb_now, 1),
       services.fmt(gap_pct, 1) if gap_pct is not None else "—")
)
