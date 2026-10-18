"""视觉系统：暗色指挥中心风格。所有页面共用，页面里不写内联样式。"""

from __future__ import annotations

from typing import Iterable, Optional

import streamlit as st

CSS = """
<style>
:root {
  --bg: #0B0F14;
  --panel: rgba(19, 26, 35, 0.72);
  --line: rgba(255, 255, 255, 0.08);
  --accent: #2DD4A7;
  --accent-2: #F5C86B;
  --text: #E6EDF3;
  --muted: #8A97A6;
}
.stApp {
  background:
    radial-gradient(1100px 520px at 12% -8%, rgba(45,212,167,0.10), transparent 60%),
    radial-gradient(900px 500px at 92% 4%, rgba(245,200,107,0.07), transparent 55%),
    #0B0F14;
}
header[data-testid="stHeader"] { background: transparent; }
footer, #MainMenu { visibility: hidden; }
.block-container { padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1500px; }

h1, h2, h3 { letter-spacing: -0.01em; }
p, li, span, label { color: var(--text); }

/* 指标卡 */
div[data-testid="stMetric"] {
  background: linear-gradient(160deg, rgba(45,212,167,0.10), rgba(45,212,167,0.015));
  border: 1px solid rgba(45,212,167,0.18);
  border-radius: 14px;
  padding: 14px 18px 12px 18px;
  box-shadow: 0 10px 30px rgba(0,0,0,0.35);
}
div[data-testid="stMetricLabel"] p { font-size: 12px !important; color: var(--muted) !important; letter-spacing: .06em; }
div[data-testid="stMetricValue"] { font-size: 30px; font-variant-numeric: tabular-nums; }

/* 表格 */
div[data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }

/* 章节标题 */
.cm-sec { display:flex; align-items:center; gap:10px; margin: 22px 0 6px 0; }
.cm-sec .bar { width:4px; height:18px; background: var(--accent); border-radius:2px; }
.cm-sec .t { font-size:17px; font-weight:700; letter-spacing:.01em; }
.cm-sec .d { color: var(--muted); font-size:12.5px; margin-left:6px; }

/* 顶部 hero */
.cm-hero {
  position: relative; overflow: hidden;
  border: 1px solid var(--line); border-radius: 20px;
  padding: 26px 30px 22px 30px; margin-bottom: 18px;
  background: linear-gradient(135deg, rgba(45,212,167,0.14), rgba(11,15,20,0.2) 45%, rgba(245,200,107,0.08));
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.05), 0 18px 50px rgba(0,0,0,0.45);
}
.cm-hero .k { color: var(--accent); font-size: 12px; letter-spacing: .22em; text-transform: uppercase; }
.cm-hero h1 { font-size: 34px; margin: 8px 0 6px 0; line-height: 1.15; }
.cm-hero p { color: var(--muted); margin: 0; font-size: 14.5px; }
.cm-chip { display:inline-block; margin: 12px 8px 0 0; padding: 4px 11px; border-radius: 999px;
  font-size: 12px; color: var(--text); border: 1px solid rgba(45,212,167,0.35);
  background: rgba(45,212,167,0.10); }

/* 结论带 */
.cm-conclusion { background: rgba(31,58,95,0.12); border-left: 4px solid #1F3A5F;
  padding: 10px 14px; margin-top: 10px; font-size: 14px; color: var(--text); }

/* FLAG 图例 */
.cm-flagbar { display:flex; gap:10px; flex-wrap:wrap; margin: 4px 0 10px 0; }
.cm-flag { display:inline-flex; align-items:center; gap:7px; padding: 5px 11px;
  border:1px solid var(--line); border-radius: 999px; font-size:12px; color: var(--muted);
  background: rgba(255,255,255,0.02); }
.cm-flag .dot { width:9px; height:9px; border-radius:3px; display:inline-block; }
.cm-flag .est { border:2px solid #F5C86B; background: transparent; }

/* 估算值黄框表（HTML 版，保证边框一定能看见） */
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

/* 演示数据提示条 */
.cm-demo { border:1px solid rgba(245,200,107,0.35); background: rgba(245,200,107,0.08);
  border-radius: 12px; padding: 10px 14px; margin: 0 0 14px 0; font-size: 13px;
  color: var(--text); }
.cm-demo b { color:#F5C86B; }

/* 闸门条目 */
.cm-gate-ok { color:#2DD4A7; font-weight:700; }
.cm-gate-fail { color:#FF7A7A; font-weight:700; }
.cm-gate-skip { color: var(--muted); font-weight:700; }
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
