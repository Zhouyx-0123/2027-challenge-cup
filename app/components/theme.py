"""视觉系统：暗色指挥中心风格。所有页面共用，页面里不写内联样式。

设计原则（答辩现场约束）：
  1. 纯内联 CSS，不加载任何外部字体/图片——断网演示不掉链子；
  2. 数字一律 tabular-nums，对拍表格才能逐位对齐；
  3. 语义色：mint=系统/碳，amber=估算/警示，coral=高排放热点，
     red=未通过，blue=信息，灰=已去重；
  4. 所有类名带 cm- 前缀，避免与 Streamlit 内部类冲突。
"""

from __future__ import annotations

from typing import Iterable, Optional

import streamlit as st

CSS = """
<style>
:root {
  --bg: #0B0F14;
  --bg-2: #0E141C;
  --panel: rgba(19, 26, 35, 0.72);
  --panel-2: rgba(24, 32, 43, 0.88);
  --line: rgba(255, 255, 255, 0.08);
  --line-2: rgba(255, 255, 255, 0.14);
  --accent: #2DD4A7;
  --accent-dim: rgba(45, 212, 167, 0.12);
  --accent-2: #F5C86B;
  --coral: #FF8A5B;
  --danger: #FF7A7A;
  --info: #6FB1FF;
  --text: #E6EDF3;
  --muted: #8A97A6;
  --muted-2: #5C6875;
  --mono: "SFMono-Regular", Consolas, "Cascadia Code", "Liberation Mono", monospace;
}
* { scrollbar-width: thin; scrollbar-color: rgba(255,255,255,0.16) transparent; }
::-webkit-scrollbar { width: 9px; height: 9px; }
::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.14); border-radius: 8px; }
::-webkit-scrollbar-thumb:hover { background: rgba(45,212,167,0.35); }
::selection { background: rgba(45,212,167,0.32); color: #fff; }

.stApp {
  background:
    radial-gradient(1100px 520px at 12% -8%, rgba(45,212,167,0.10), transparent 60%),
    radial-gradient(900px 500px at 92% 4%, rgba(245,200,107,0.07), transparent 55%),
    #0B0F14;
}
header[data-testid="stHeader"] { background: transparent; }
footer, #MainMenu { visibility: hidden; }
.block-container { padding-top: 1.4rem; padding-bottom: 3rem; max-width: 1500px; }

h1, h2, h3 { letter-spacing: -0.01em; }
p, li, span, label { color: var(--text); }
.cm-num, div[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; }

/* ---------- 指标卡 ---------- */
div[data-testid="stMetric"] {
  background: linear-gradient(160deg, rgba(45,212,167,0.10), rgba(45,212,167,0.015));
  border: 1px solid rgba(45,212,167,0.18);
  border-radius: 14px;
  padding: 14px 18px 12px 18px;
  box-shadow: 0 10px 30px rgba(0,0,0,0.35);
}
div[data-testid="stMetricLabel"] p { font-size: 12px !important; color: var(--muted) !important; letter-spacing: .06em; }
div[data-testid="stMetricValue"] { font-size: 30px; }

/* ---------- 表格 ---------- */
div[data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
div[data-testid="stDataFrame"] table { font-variant-numeric: tabular-nums; }

/* ---------- 章节标题 ---------- */
.cm-sec { display:flex; align-items:center; gap:10px; margin: 22px 0 6px 0; }
.cm-sec .bar { width:4px; height:18px; background: var(--accent); border-radius:2px; }
.cm-sec .t { font-size:17px; font-weight:700; letter-spacing:.01em; }
.cm-sec .d { color: var(--muted); font-size:12.5px; margin-left:6px; }

/* ---------- 顶部 hero ---------- */
.cm-hero {
  position: relative; overflow: hidden;
  border: 1px solid var(--line); border-radius: 20px;
  padding: 26px 30px 22px 30px; margin-bottom: 18px;
  background:
    linear-gradient(rgba(255,255,255,0.025) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,0.025) 1px, transparent 1px),
    linear-gradient(135deg, rgba(45,212,167,0.14), rgba(11,15,20,0.2) 45%, rgba(245,200,107,0.08));
  background-size: 34px 34px, 34px 34px, auto;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.05), 0 18px 50px rgba(0,0,0,0.45);
}
.cm-hero .k { color: var(--accent); font-size: 12px; letter-spacing: .22em; text-transform: uppercase; }
.cm-hero h1 { font-size: 34px; margin: 8px 0 6px 0; line-height: 1.15; }
.cm-hero p { color: var(--muted); margin: 0; font-size: 14.5px; }
.cm-chip { display:inline-block; margin: 12px 8px 0 0; padding: 4px 11px; border-radius: 999px;
  font-size: 12px; color: var(--text); border: 1px solid rgba(45,212,167,0.35);
  background: rgba(45,212,167,0.10); }
.cm-chip.ok   { border-color: rgba(45,212,167,0.55); background: rgba(45,212,167,0.14); color:#9DEFD3; }
.cm-chip.warn { border-color: rgba(245,200,107,0.55); background: rgba(245,200,107,0.12); color:#F8D98F; }
.cm-chip.bad  { border-color: rgba(255,122,122,0.55); background: rgba(255,122,122,0.12); color:#FFB3B3; }
.cm-chip.skip { border-color: rgba(138,151,166,0.45); background: rgba(138,151,166,0.10); color:#B7C2CE; }
.cm-chip.info { border-color: rgba(111,177,255,0.50); background: rgba(111,177,255,0.12); color:#AFD2FF; }
.cm-chip.main { border-color: rgba(245,200,107,0.65); background: rgba(245,200,107,0.16); color:#FFE3A0; font-weight:700; }
.cm-chip.coral{ border-color: rgba(255,138,91,0.55); background: rgba(255,138,91,0.12); color:#FFC4A6; }

/* ---------- 结论带 ---------- */
.cm-conclusion { background: rgba(31,58,95,0.12); border-left: 4px solid #1F3A5F;
  padding: 10px 14px; margin-top: 10px; font-size: 14px; color: var(--text); }

/* ---------- 面板（HTML 静态块） ---------- */
.cm-panel { border: 1px solid var(--line); border-radius: 14px; background: var(--panel);
  padding: 14px 16px 12px 16px; margin: 4px 0 10px 0;
  box-shadow: 0 8px 26px rgba(0,0,0,0.28); }
.cm-panel.flat { background: rgba(255,255,255,0.02); box-shadow: none; }
.cm-panel-h { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; margin-bottom: 8px; }
.cm-panel-h .t { font-size:14.5px; font-weight:700; letter-spacing:.01em; }
.cm-panel-h .d { color: var(--muted); font-size:12px; }
.cm-panel-h .r { margin-left:auto; color: var(--muted); font-size:12px; }
.cm-note { color: var(--muted); font-size: 12px; line-height: 1.65; }
.cm-note b { color: var(--text); }
.cm-note .em { color: var(--accent-2); }

/* ---------- 统计块（HTML 网格） ---------- */
.cm-stats { display:grid; gap:10px; margin: 2px 0 6px 0; }
.cm-stat { border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px 10px 14px;
  background: linear-gradient(165deg, rgba(255,255,255,0.035), rgba(255,255,255,0.008)); }
.cm-stat .l { font-size: 11.5px; color: var(--muted); letter-spacing: .05em; }
.cm-stat .v { font-size: 24px; font-weight: 700; font-variant-numeric: tabular-nums; margin-top: 3px; line-height: 1.1; }
.cm-stat .s { font-size: 11.5px; color: var(--muted-2); margin-top: 4px; line-height: 1.5; }
.cm-stat.mint  .v { color: var(--accent); }
.cm-stat.amber .v { color: var(--accent-2); }
.cm-stat.coral .v { color: var(--coral); }
.cm-stat.red   .v { color: var(--danger); }
.cm-stat.info  .v { color: var(--info); }

/* ---------- KV 列表 ---------- */
.cm-kv { width: 100%; border-collapse: collapse; font-size: 13px; }
.cm-kv td { padding: 7px 10px; border-bottom: 1px solid rgba(255,255,255,0.05); vertical-align: top; }
.cm-kv td.k { color: var(--muted); white-space: nowrap; width: 118px; }
.cm-kv td.v { color: var(--text); font-variant-numeric: tabular-nums; }
.cm-kv tr:last-child td { border-bottom: none; }
.cm-kv td.v .big { font-size: 17px; font-weight: 700; }

/* ---------- 公式条 ---------- */
.cm-formula { font-family: var(--mono); font-size: 12.5px; color: var(--text);
  background: rgba(111,177,255,0.07); border: 1px dashed rgba(111,177,255,0.35);
  border-radius: 10px; padding: 9px 12px; margin: 6px 0; line-height: 1.7; }
.cm-formula .hl { color: var(--accent); font-weight: 700; }
.cm-formula .hl2 { color: var(--accent-2); font-weight: 700; }
.cm-formula .dim { color: var(--muted); }

/* ---------- 决策闭环步骤条 ---------- */
.cm-steps { display:flex; align-items:stretch; gap:0; flex-wrap:wrap; margin: 4px 0 8px 0; }
.cm-step { flex: 1; min-width: 120px; text-align: center; padding: 9px 6px 8px 6px;
  border: 1px solid var(--line); border-radius: 10px; background: rgba(255,255,255,0.02);
  font-size: 12px; color: var(--muted); position: relative; }
.cm-step .n { display:block; font-size: 11px; color: var(--muted-2); letter-spacing:.08em; }
.cm-step .t { display:block; font-weight: 700; margin-top: 2px; }
.cm-step.on { border-color: rgba(45,212,167,0.55); background: rgba(45,212,167,0.10); color: var(--text); }
.cm-step.on .n { color: var(--accent); }
.cm-step-arrow { align-self:center; color: var(--muted-2); padding: 0 5px; font-size: 13px; }

/* ---------- 追溯链 ---------- */
.cm-trace { margin: 4px 0; }
.cm-trace .node { display:flex; gap:10px; align-items:flex-start; }
.cm-trace .dot { width: 10px; height: 10px; border-radius: 50%; margin-top: 5px; flex: none;
  background: var(--accent); box-shadow: 0 0 0 4px rgba(45,212,167,0.15); }
.cm-trace .dot.amber { background: var(--accent-2); box-shadow: 0 0 0 4px rgba(245,200,107,0.15); }
.cm-trace .dot.muted { background: var(--muted-2); box-shadow: 0 0 0 4px rgba(138,151,166,0.12); }
.cm-trace .body { font-size: 13px; line-height: 1.6; }
.cm-trace .body .t { font-weight: 700; }
.cm-trace .body .d { color: var(--muted); font-size: 12px; }
.cm-trace .link { width: 2px; height: 14px; background: var(--line-2); margin: 3px 0 3px 4px; }

/* ---------- HTML 表格 ---------- */
.cm-htable { width: 100%; border-collapse: collapse; font-size: 12.5px; font-variant-numeric: tabular-nums; }
.cm-htable th { text-align: left; color: var(--muted); font-weight: 600; padding: 8px 10px;
  border-bottom: 1px solid var(--line-2); white-space: nowrap; }
.cm-htable td { padding: 8px 10px; border-bottom: 1px solid rgba(255,255,255,0.05); vertical-align: top; }
.cm-htable tr:last-child td { border-bottom: none; }
.cm-htable tr.base td { background: rgba(45,212,167,0.07); }
.cm-htable tr.main td { background: rgba(245,200,107,0.07); }
.cm-htable tr.skip td { background: rgba(138,151,166,0.07); }
.cm-htable tr.fail td { background: rgba(255,122,122,0.09); }
.cm-htable td.num, .cm-htable th.num { text-align: right; }
.cm-htable .st-ok { color: var(--accent); font-weight: 700; }
.cm-htable .st-bad { color: var(--danger); font-weight: 700; }
.cm-htable .st-skip { color: var(--muted); }
.cm-htable .st-warn { color: var(--accent-2); font-weight: 700; }

/* ---------- 空状态 ---------- */
.cm-empty { border: 1px dashed var(--line-2); border-radius: 12px; padding: 18px 16px;
  color: var(--muted); font-size: 13px; background: rgba(255,255,255,0.015); }
.cm-empty .t { color: var(--text); font-weight: 700; font-size: 13.5px; margin-bottom: 4px; }
.cm-empty code { color: var(--accent); background: rgba(45,212,167,0.10); padding: 1px 6px; border-radius: 6px; }

/* ---------- FLAG 图例 ---------- */
.cm-flagbar { display:flex; gap:10px; flex-wrap:wrap; margin: 4px 0 10px 0; }
.cm-flag { display:inline-flex; align-items:center; gap:7px; padding: 5px 11px;
  border:1px solid var(--line); border-radius: 999px; font-size:12px; color: var(--muted);
  background: rgba(255,255,255,0.02); }
.cm-flag .dot { width:9px; height:9px; border-radius:3px; display:inline-block; }
.cm-flag .est { border:2px solid #F5C86B; background: transparent; }

/* ---------- 估算值黄框表 ---------- */
.cm-est-wrap { border:1px solid rgba(245,200,107,0.28); border-radius:14px;
  padding: 12px 14px 6px 14px; margin: 6px 0 12px 0;
  background: linear-gradient(180deg, rgba(245,200,107,0.06), rgba(245,200,107,0.01)); }
.cm-est-head { font-size:13px; color:#F5C86B; font-weight:700; letter-spacing:.02em; }
.cm-est-head span { color: var(--muted); font-weight:400; margin-left:8px; }
.cm-est-table { width:100%; border-collapse:collapse; margin-top:10px; font-size:12.5px; }
.cm-est-table th { text-align:left; color: var(--muted); font-weight:600;
  padding: 7px 9px; border-bottom:1px solid var(--line); }
.cm-est-table td { padding: 7px 9px; border-bottom:1px solid rgba(255,255,255,0.05);
  color: var(--text); vertical-align: top; }
.cm-est-table tr.est td { border:2px solid #F5C86B; background: rgba(245,200,107,0.10); }
.cm-est-table td.reason { color:#FFE7A3; }
.cm-est-foot { color: var(--muted); font-size: 11.5px; margin: 8px 0 4px 0; }

/* ---------- 演示数据提示条 ---------- */
.cm-demo { border:1px solid rgba(245,200,107,0.35); background: rgba(245,200,107,0.08);
  border-radius: 12px; padding: 10px 14px; margin: 0 0 14px 0; font-size: 13px;
  color: var(--text); }
.cm-demo b { color:#F5C86B; }

/* ---------- 闸门条目 ---------- */
.cm-gate-ok { color:#2DD4A7; font-weight:700; }
.cm-gate-fail { color:#FF7A7A; font-weight:700; }
.cm-gate-skip { color: var(--muted); font-weight:700; }

/* ---------- Streamlit 控件适配 ---------- */
div[data-testid="stTabs"] { margin-top: 4px; }
button[data-testid="stTab"] { font-size: 13px; color: var(--muted); border-radius: 8px 8px 0 0; }
button[data-testid="stTab"][aria-selected="true"] { color: var(--accent); }
div[data-testid="stExpander"] details { border: 1px solid var(--line); border-radius: 12px;
  background: var(--panel); }
div[data-testid="stExpander"] summary { font-size: 13px; color: var(--text); }
div[data-testid="stExpander"] summary:hover { color: var(--accent); }
button[data-testid="stDownloadButton"] > div,
button[data-testid="baseButton-secondary"] > div { border-radius: 10px !important; }
div[data-testid="stSlider"] [role="slider"] { accent-color: var(--accent); }
section[data-testid="stSidebar"] { background: rgba(13,18,25,0.92); border-right: 1px solid var(--line); }
section[data-testid="stSidebar"] .block-container { padding-top: 1.2rem; }
div[data-testid="stMultiSelect"] span[data-baseweb="tag"] {
  background: rgba(45,212,167,0.16); border: 1px solid rgba(45,212,167,0.35); }
div[data-testid="stNumberInput"] input { font-variant-numeric: tabular-nums; }
.stProgress { border-radius: 999px; }
</style>
"""


def inject() -> None:
    """注入全局样式。每个页面在 setup() 之后调用一次，重复调用无副作用。"""
    if st.session_state.get("_cm_css_injected"):
        return
    st.markdown(CSS, unsafe_allow_html=True)
    st.session_state["_cm_css_injected"] = True


def hero(title: str, subtitle: str, kicker: str = "CARBON LEDGER", badges: Optional[Iterable[str]] = None) -> None:
    chips = "".join("<span class='cm-chip'>" + str(b) + "</span>" for b in (badges or []))
    st.markdown(
        "<div class='cm-hero'>"
        + "<div class='k'>" + kicker + "</div>"
        + "<h1>" + title + "</h1>"
        + "<p>" + subtitle + "</p>"
        + chips
        + "</div>",
        unsafe_allow_html=True,
    )


def section(title: str, desc: str = "") -> None:
    st.markdown(
        "<div class='cm-sec'><span class='bar'></span><span class='t'>"
        + title
        + "</span><span class='d'>" + desc + "</span></div>",
        unsafe_allow_html=True,
    )


def conclusion(text: str) -> None:
    st.markdown(
        "<div class='cm-conclusion'><b>本页结论</b>　" + text + "</div>",
        unsafe_allow_html=True,
    )


def demo_note(reason: str = "") -> None:
    """演示数据提示条：不遮挡内容，但必须一眼看见。"""
    tail = ("　<span style='color:#8A97A6'>" + reason + "</span>") if reason else ""
    st.markdown(
        "<div class='cm-demo'><b>演示数据</b>　当前结果表由内置合成样本生成，"
        "用于前端联调与答辩兜底，不代表真数据口径。真数据放入 <code>data/raw/</code> 后"
        "重跑 <code>python -m src.pipeline</code> 自动切换。" + tail + "</div>",
        unsafe_allow_html=True,
    )
