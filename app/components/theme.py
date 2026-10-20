"""视觉系统：企业级碳决策台（深色、克制、留白）。

设计原则：
  1. 纯内联 CSS，不加载外部字体/图片，断网演示不依赖 CDN；
  2. 一屏只允许一个主色（青绿）+ 一个强调色（香槟金）；红只表示失败；
  3. 所有数字 tabular-nums，表格逐位对齐；
  4. 类名统一 cm- 前缀，避免和 Streamlit 内部类冲突。
"""

from __future__ import annotations

from typing import Iterable, Optional

import streamlit as st

CSS = """
<style>
:root {
  --bg: #070A0E;
  --bg-2: #0A0F15;
  --panel: #0E151C;
  --panel-2: #111A23;
  --line: rgba(255,255,255,0.075);
  --line-2: rgba(255,255,255,0.14);
  --accent: #6FD3BF;
  --accent-dim: rgba(111,211,191,0.10);
  --accent-2: #D2B36F;
  --coral: #C98A6B;
  --danger: #D97A7A;
  --info: #8AA9C9;
  --text: #E9EEF4;
  --muted: #94A1AD;
  --muted-2: #63707C;
  --mono: "SFMono-Regular", Consolas, "Cascadia Code", "Liberation Mono", monospace;
  --radius: 12px;
}
html, body, [class*="css"] {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
               "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}
* { scrollbar-width: thin; scrollbar-color: rgba(255,255,255,0.14) transparent; }
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.12); border-radius: 8px; }
::-webkit-scrollbar-thumb:hover { background: rgba(111,211,191,0.30); }
::selection { background: rgba(111,211,191,0.24); color: #fff; }

.stApp {
  background:
    radial-gradient(980px 560px at 8% -12%, rgba(111,211,191,0.055), transparent 60%),
    radial-gradient(760px 480px at 96% 2%, rgba(210,179,111,0.035), transparent 55%),
    var(--bg);
  color: var(--text);
}
header[data-testid="stHeader"] { background: transparent; }
div[data-testid="stToolbar"],
div[data-testid="stAppDeployButton"],
button[data-testid="stAppDeployButton"] { display: none !important; }
footer, #MainMenu { visibility: hidden; }
.block-container { padding-top: 1.35rem; padding-bottom: 3.5rem; max-width: 1360px; }

h1, h2, h3 { letter-spacing: -0.02em; font-weight: 600; color: var(--text); }
p, li, label { color: var(--text); line-height: 1.65; }
.cm-num, div[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; }

/* ---------- 指标卡 ---------- */
div[data-testid="stMetric"] {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 16px 18px 14px 18px;
  box-shadow: none;
  min-height: 96px;
}
div[data-testid="stMetricLabel"] p {
  font-size: 10.5px !important;
  color: var(--muted) !important;
  letter-spacing: .14em;
  text-transform: uppercase;
}
div[data-testid="stMetricValue"] {
  font-size: 31px;
  font-weight: 600;
  letter-spacing: -0.03em;
  color: var(--text);
}
div[data-testid="stMetricDelta"] { font-size: 12px; }

/* ---------- 表格 ---------- */
div[data-testid="stDataFrame"] {
  border: 1px solid var(--line);
  border-radius: 10px;
  overflow: hidden;
  background: var(--panel);
}
div[data-testid="stDataFrame"] table { font-variant-numeric: tabular-nums; }

/* ---------- 章节标题 ---------- */
.cm-sec {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin: 32px 0 10px 0;
  padding-top: 9px;
  border-top: 1px solid var(--line);
}
.cm-sec .bar { width: 18px; height: 1px; background: var(--accent); border-radius: 1px; align-self: center; }
.cm-sec .t { font-size: 17px; font-weight: 600; letter-spacing: -0.01em; }
.cm-sec .d { color: var(--muted); font-size: 12px; margin-left: 4px; }

/* ---------- Hero ---------- */
.cm-hero {
  position: relative;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 30px 34px 26px 34px;
  margin-bottom: 20px;
  background:
    linear-gradient(135deg, rgba(111,211,191,0.085), transparent 48%),
    linear-gradient(180deg, #0E161E, #0B1117);
}
.cm-hero::before {
  content: "";
  position: absolute;
  top: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, var(--accent), transparent 55%, var(--accent-2));
  opacity: .55;
}
.cm-hero .k {
  color: var(--accent);
  font-size: 10.5px;
  letter-spacing: .22em;
  text-transform: uppercase;
  font-weight: 600;
}
.cm-hero h1 {
  font-size: 36px;
  line-height: 1.14;
  margin: 10px 0 8px 0;
  font-weight: 600;
  letter-spacing: -0.03em;
}
.cm-hero p {
  color: var(--muted);
  margin: 0;
  font-size: 14.5px;
  max-width: 820px;
  line-height: 1.7;
}
.cm-hero-chips { margin-top: 16px; }

/* ---------- Chips ---------- */
.cm-chip {
  display: inline-block;
  margin: 0 7px 6px 0;
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 11.5px;
  color: var(--muted);
  border: 1px solid var(--line);
  background: rgba(255,255,255,0.018);
  letter-spacing: .01em;
}
.cm-chip.ok   { border-color: rgba(111,211,191,.35); color: #8DE3D2; background: rgba(111,211,191,.07); }
.cm-chip.warn { border-color: rgba(210,179,111,.38); color: #E4CB97; background: rgba(210,179,111,.07); }
.cm-chip.bad  { border-color: rgba(217,122,122,.40); color: #EDB0B0; background: rgba(217,122,122,.07); }
.cm-chip.skip { border-color: var(--line); color: var(--muted); background: transparent; }
.cm-chip.info { border-color: rgba(138,169,201,.35); color: #AEC5DD; background: rgba(138,169,201,.07); }
.cm-chip.main { border-color: rgba(210,179,111,.55); color: #F0DFB9; background: rgba(210,179,111,.10); font-weight: 600; }
.cm-chip.coral{ border-color: rgba(201,138,107,.40); color: #DDB29C; background: rgba(201,138,107,.07); }

/* ---------- 结论带 ---------- */
.cm-conclusion {
  display: flex;
  gap: 14px;
  align-items: flex-start;
  background: var(--panel);
  border: 1px solid var(--line);
  border-left: 2px solid var(--accent);
  padding: 13px 16px;
  margin-top: 14px;
  border-radius: 0 8px 8px 0;
  font-size: 13.5px;
  color: var(--text);
  line-height: 1.7;
}
.cm-conclusion .label {
  flex: none;
  color: var(--accent);
  font-size: 10.5px;
  letter-spacing: .14em;
  font-weight: 600;
  padding-top: 2px;
}

/* ---------- 面板 ---------- */
.cm-panel {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
  padding: 16px 18px 14px 18px;
  margin: 4px 0 12px 0;
}
.cm-panel.flat { background: transparent; }
.cm-panel-h {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.cm-panel-h .t { font-size: 14px; font-weight: 600; letter-spacing: -0.01em; }
.cm-panel-h .d { color: var(--muted); font-size: 11.5px; }
.cm-panel-h .r { margin-left: auto; color: var(--muted-2); font-size: 10.5px; letter-spacing: .08em; text-transform: uppercase; }
.cm-note { color: var(--muted); font-size: 12px; line-height: 1.75; }
.cm-note b { color: var(--text); font-weight: 600; }
.cm-note .em { color: var(--accent-2); }

/* ---------- 统计块 ---------- */
.cm-stats { display: grid; gap: 12px; margin: 4px 0 8px 0; }
.cm-stat {
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 14px 16px 12px 16px;
  background: var(--panel);
}
.cm-stat .l {
  font-size: 10.5px;
  color: var(--muted);
  letter-spacing: .12em;
  text-transform: uppercase;
}
.cm-stat .v {
  font-size: 25px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  margin-top: 5px;
  line-height: 1.1;
  letter-spacing: -0.02em;
}
.cm-stat .s { font-size: 11.5px; color: var(--muted-2); margin-top: 5px; line-height: 1.55; }
.cm-stat.mint  .v { color: var(--accent); }
.cm-stat.amber .v { color: var(--accent-2); }
.cm-stat.coral .v { color: var(--coral); }
.cm-stat.red   .v { color: var(--danger); }
.cm-stat.info  .v { color: var(--info); }

/* ---------- KV ---------- */
.cm-kv { width: 100%; border-collapse: collapse; font-size: 13px; }
.cm-kv td { padding: 9px 10px; border-bottom: 1px solid rgba(255,255,255,0.055); vertical-align: top; }
.cm-kv td.k { color: var(--muted); white-space: nowrap; width: 132px; }
.cm-kv td.v { color: var(--text); font-variant-numeric: tabular-nums; }
.cm-kv tr:last-child td { border-bottom: none; }
.cm-kv td.v .big { font-size: 18px; font-weight: 600; }

/* ---------- 公式/口径条 ---------- */
.cm-formula {
  font-family: var(--mono);
  font-size: 12px;
  color: var(--text);
  background: #0A1117;
  border: 1px solid var(--line);
  border-left: 2px solid var(--accent);
  border-radius: 0 8px 8px 0;
  padding: 11px 14px;
  margin: 8px 0;
  line-height: 1.8;
}
.cm-formula .hl { color: var(--accent); font-weight: 600; }
.cm-formula .hl2 { color: var(--accent-2); font-weight: 600; }
.cm-formula .dim { color: var(--muted); }

/* ---------- 步骤条 ---------- */
.cm-steps { display: flex; align-items: stretch; gap: 6px; flex-wrap: wrap; margin: 6px 0 10px 0; }
.cm-step {
  flex: 1;
  min-width: 96px;
  padding: 9px 8px 8px 8px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: transparent;
  font-size: 11.5px;
  color: var(--muted-2);
  position: relative;
}
.cm-step .n { display: block; font-size: 10px; color: var(--muted-2); letter-spacing: .12em; }
.cm-step .t { display: block; font-weight: 600; margin-top: 2px; color: var(--muted); }
.cm-step.on { border-color: rgba(111,211,191,.38); background: rgba(111,211,191,.055); }
.cm-step.on .n, .cm-step.on .t { color: var(--accent); }
.cm-step-arrow { align-self: center; color: var(--muted-2); padding: 0 2px; font-size: 11px; }

/* ---------- 追溯链 ---------- */
.cm-trace { margin: 4px 0; }
.cm-trace .node { display: flex; gap: 10px; align-items: flex-start; }
.cm-trace .dot { width: 7px; height: 7px; border-radius: 50%; margin-top: 7px; flex: none;
  background: var(--accent); box-shadow: 0 0 0 3px rgba(111,211,191,0.10); }
.cm-trace .dot.amber { background: var(--accent-2); box-shadow: 0 0 0 3px rgba(210,179,111,0.10); }
.cm-trace .dot.muted { background: var(--muted-2); box-shadow: 0 0 0 3px rgba(99,112,124,0.10); }
.cm-trace .body { font-size: 12.5px; line-height: 1.65; }
.cm-trace .body .t { font-weight: 600; }
.cm-trace .body .d { color: var(--muted); font-size: 11.5px; }
.cm-trace .link { width: 1px; height: 12px; background: var(--line-2); margin: 3px 0 3px 3px; }

/* ---------- HTML 表格 ---------- */
.cm-htable { width: 100%; border-collapse: collapse; font-size: 12.5px; font-variant-numeric: tabular-nums; }
.cm-htable th {
  text-align: left;
  color: var(--muted-2);
  font-weight: 600;
  font-size: 10.5px;
  letter-spacing: .09em;
  text-transform: uppercase;
  padding: 9px 10px;
  border-bottom: 1px solid var(--line-2);
  white-space: nowrap;
}
.cm-htable td { padding: 10px 10px; border-bottom: 1px solid rgba(255,255,255,0.05); vertical-align: top; }
.cm-htable tbody tr:hover td { background: rgba(255,255,255,0.018); }
.cm-htable tr:last-child td { border-bottom: none; }
.cm-htable tr.base td { background: rgba(111,211,191,0.045); }
.cm-htable tr.main td { background: rgba(210,179,111,0.055); }
.cm-htable tr.skip td { background: rgba(99,112,124,0.06); }
.cm-htable tr.fail td { background: rgba(217,122,122,0.06); }
.cm-htable td.num, .cm-htable th.num { text-align: right; }
.cm-htable .st-ok { color: var(--accent); font-weight: 600; }
.cm-htable .st-bad { color: var(--danger); font-weight: 600; }
.cm-htable .st-skip { color: var(--muted); }
.cm-htable .st-warn { color: var(--accent-2); font-weight: 600; }

/* ---------- 空状态 ---------- */
.cm-empty {
  border: 1px dashed var(--line-2);
  border-radius: 10px;
  padding: 20px 18px;
  color: var(--muted);
  font-size: 13px;
  background: transparent;
}
.cm-empty .t { color: var(--text); font-weight: 600; font-size: 13.5px; margin-bottom: 4px; }
.cm-empty code { color: var(--accent); background: rgba(111,211,191,.08); padding: 1px 6px; border-radius: 4px; }

/* ---------- FLAG 图例 ---------- */
.cm-flagbar { display: flex; gap: 8px; flex-wrap: wrap; margin: 6px 0 12px 0; }
.cm-flag {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 5px 10px;
  border: 1px solid var(--line);
  border-radius: 6px;
  font-size: 11px;
  color: var(--muted);
  background: transparent;
}
.cm-flag .dot { width: 7px; height: 7px; border-radius: 2px; display: inline-block; }
.cm-flag .est { border: 1px solid var(--accent-2); background: transparent; }

/* ---------- 估算值 ---------- */
.cm-est-wrap {
  border: 1px solid rgba(210,179,111,.26);
  border-radius: 10px;
  padding: 14px 16px 8px 16px;
  margin: 8px 0 14px 0;
  background: rgba(210,179,111,.025);
}
.cm-est-head { font-size: 12px; color: var(--accent-2); font-weight: 600; letter-spacing: .02em; }
.cm-est-head span { color: var(--muted); font-weight: 400; margin-left: 8px; }
.cm-est-table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 12px; }
.cm-est-table th { text-align: left; color: var(--muted-2); font-weight: 600; font-size: 10.5px;
  padding: 7px 9px; border-bottom: 1px solid var(--line); text-transform: uppercase; letter-spacing: .08em; }
.cm-est-table td { padding: 8px 9px; border-bottom: 1px solid rgba(255,255,255,0.05); color: var(--text); vertical-align: top; }
.cm-est-table tr.est td { border-top: 1px solid rgba(210,179,111,.45); border-bottom: 1px solid rgba(210,179,111,.45); background: rgba(210,179,111,.04); }
.cm-est-table td.reason { color: #E4CB97; }
.cm-est-foot { color: var(--muted); font-size: 11px; margin: 9px 0 4px 0; line-height: 1.6; }

/* ---------- 演示数据 ---------- */
.cm-demo {
  border: 1px solid rgba(210,179,111,.28);
  border-left: 2px solid var(--accent-2);
  background: rgba(210,179,111,.045);
  border-radius: 0 8px 8px 0;
  padding: 11px 14px;
  margin: 0 0 16px 0;
  font-size: 12.5px;
  color: var(--text);
}
.cm-demo b { color: var(--accent-2); }

/* ---------- 闸门 ---------- */
.cm-gate-ok { color: var(--accent); font-weight: 600; }
.cm-gate-fail { color: var(--danger); font-weight: 600; }
.cm-gate-skip { color: var(--muted); font-weight: 600; }

/* ---------- 侧边栏品牌/导航 ---------- */
section[data-testid="stSidebar"] div[data-testid="stSidebarNav"],
section[data-testid="stSidebar"] nav[data-testid="stSidebarNav"],
section[data-testid="stSidebar"] div[data-testid="stSidebarNavItems"],
div[data-testid="stSidebarNav"] {
  display: none !important;
  visibility: hidden !important;
  height: 0 !important;
  overflow: hidden !important;
}
section[data-testid="stSidebar"] {
  background: #080C11;
  border-right: 1px solid var(--line);
}
section[data-testid="stSidebar"] > div { padding-top: 1.1rem; }
section[data-testid="stSidebar"] .block-container { padding: 1.1rem 1rem 2rem 1rem; }
section[data-testid="stSidebar"] hr { border-color: var(--line); margin: 14px 0; }
section[data-testid="stSidebar"] a {
  color: var(--muted);
  font-size: 12.5px;
  text-decoration: none;
  border-radius: 6px;
}
section[data-testid="stSidebar"] a:hover { color: var(--text); background: rgba(255,255,255,0.035); }
.cm-brand { display: flex; gap: 11px; align-items: center; padding: 4px 2px 16px 2px; }
.cm-brand-mark {
  width: 34px; height: 34px; flex: none;
  border: 1px solid rgba(111,211,191,.45);
  border-radius: 8px;
  color: var(--accent);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: .02em;
  display: flex; align-items: center; justify-content: center;
  background: rgba(111,211,191,.05);
}
.cm-brand-text .eyebrow { color: var(--muted-2); font-size: 8.5px; letter-spacing: .19em; text-transform: uppercase; }
.cm-brand-text .name { color: var(--text); font-size: 14px; font-weight: 600; margin-top: 2px; letter-spacing: -0.01em; }
.cm-brand-text .sub { color: var(--muted-2); font-size: 9.5px; margin-top: 1px; letter-spacing: .06em; }
.cm-navlabel {
  color: var(--muted-2);
  font-size: 9px;
  letter-spacing: .19em;
  text-transform: uppercase;
  margin: 18px 2px 7px 2px;
}
.cm-navitem {
  color: var(--muted);
  font-size: 12.5px;
  padding: 7px 9px;
  border-radius: 6px;
  margin: 1px 0;
}
.cm-navitem:hover { color: var(--text); background: rgba(255,255,255,.035); }
.cm-side-status {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px 11px;
  margin: 10px 0 2px 0;
  background: rgba(255,255,255,.018);
  font-size: 11.5px;
  color: var(--muted);
  line-height: 1.7;
}
.cm-side-status b { color: var(--text); font-weight: 600; }
.cm-side-status .ok { color: var(--accent); }
.cm-side-status .warn { color: var(--accent-2); }

/* ---------- Streamlit 控件 ---------- */
div[data-testid="stTabs"] { margin-top: 2px; }
button[data-testid="stTab"] { font-size: 12.5px; color: var(--muted); border-radius: 0; }
button[data-testid="stTab"][aria-selected="true"] { color: var(--accent); }
button[data-testid="stTab"][aria-selected="true"]::after { background: var(--accent); }
div[data-testid="stExpander"] details {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
}
div[data-testid="stExpander"] summary { font-size: 12.5px; color: var(--text); }
div[data-testid="stExpander"] summary:hover { color: var(--accent); }
button[data-testid="baseButton-secondary"] {
  background: var(--panel) !important;
  border: 1px solid var(--line) !important;
  color: var(--text) !important;
  border-radius: 7px !important;
  font-size: 12.5px !important;
}
button[data-testid="baseButton-secondary"]:hover { border-color: rgba(111,211,191,.35) !important; }
button[kind="primary"] {
  background: var(--accent) !important;
  color: #06110D !important;
  border: none !important;
  border-radius: 7px !important;
  font-weight: 600 !important;
}
button[data-testid="stDownloadButton"] button {
  background: transparent !important;
  border: 1px solid var(--line) !important;
  color: var(--text) !important;
  border-radius: 7px !important;
  font-size: 12px !important;
}
div[data-testid="stSlider"] [role="slider"] { accent-color: var(--accent); }
section[data-testid="stSidebar"] div[data-testid="stNumberInput"] input,
section[data-testid="stSidebar"] div[data-testid="stTextInput"] input {
  background: rgba(255,255,255,.025);
  border-color: var(--line);
  color: var(--text);
  font-variant-numeric: tabular-nums;
}
div[data-testid="stMultiSelect"] span[data-baseweb="tag"] {
  background: rgba(111,211,191,.10);
  border: 1px solid rgba(111,211,191,.25);
  color: #A8E8DB;
  font-size: 11px;
}
div[data-testid="stSelectbox"] div[data-baseweb="select"] > div,
div[data-testid="stNumberInput"] input {
  border-color: var(--line);
}
.stProgress { border-radius: 999px; }
</style>
"""


def inject() -> None:
    """每次页面运行都注入样式。

    Streamlit 多页导航会重建页面 DOM，但 session_state 会保留；如果这里
    按 session_state 只注入一次，第 2 页起就只剩自定义导航、默认导航重新
    出现，表现为左侧目录重合。CSS 重复注入无副作用。
    """
    st.markdown(CSS, unsafe_allow_html=True)


def hero(title: str, subtitle: str, kicker: str = "CARBON LEDGER",
         badges: Optional[Iterable[str]] = None) -> None:
    chips = "".join("<span class='cm-chip'>" + str(b) + "</span>" for b in (badges or []))
    st.markdown(
        "<div class='cm-hero'>"
        "<div class='k'>" + kicker + "</div>"
        "<h1>" + title + "</h1>"
        "<p>" + subtitle + "</p>"
        + ("<div class='cm-hero-chips'>" + chips + "</div>" if chips else "")
        + "</div>",
        unsafe_allow_html=True,
    )


def section(title: str, desc: str = "") -> None:
    st.markdown(
        "<div class='cm-sec'><span class='bar'></span><span class='t'>"
        + title + "</span><span class='d'>" + desc + "</span></div>",
        unsafe_allow_html=True,
    )


def conclusion(text: str) -> None:
    st.markdown(
        "<div class='cm-conclusion'><span class='label'>结论</span>"
        "<span>" + text + "</span></div>",
        unsafe_allow_html=True,
    )


def demo_note(reason: str = "") -> None:
    """演示数据提示条：不遮挡内容，但必须一眼看见。"""
    tail = ("　<span style='color:#94A1AD'>" + reason + "</span>") if reason else ""
    st.markdown(
        "<div class='cm-demo'><b>演示数据</b>　当前结果表由内置合成样本生成，"
        "用于前端联调与答辩兜底，不代表真数据口径。真数据放入 <code>data/raw/</code> 后"
        "重跑 <code>python -m src.pipeline</code> 自动切换。" + tail + "</div>",
        unsafe_allow_html=True,
    )
