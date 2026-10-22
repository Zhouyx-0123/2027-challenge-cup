# -*- coding: utf-8 -*-
"""视觉系统：Carbon Tide 电影感数据驾驶舱。

设计原则（答辩现场约束）：
  1. 纯内联 CSS，不加载任何外部字体/图片——断网演示不掉链子；
  2. 数字一律 tabular-nums，对拍表格才能逐位对齐；
  3. 语义色：tide=系统/碳，amber=估算/警示，coral=高排放热点，
     red=未通过，blue=信息，灰=已去重；
  4. 所有类名带 cm- 前缀，避免与 Streamlit 内部类冲突；
  5. 前端只做展示，不新增任何业务计算。
"""

from __future__ import annotations

import html
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote

import streamlit as st

CSS = """
<style>
:root {
  --ink: #07080A;
  --ink-2: #0B0D12;
  --surface: rgba(16, 19, 25, 0.72);
  --surface-2: rgba(22, 26, 34, 0.92);
  --panel: rgba(15, 18, 24, 0.78);
  --panel-2: rgba(22, 26, 34, 0.96);
  --line: rgba(255, 255, 255, 0.085);
  --line-2: rgba(255, 255, 255, 0.16);
  --text: #F3F5F8;
  --muted: #98A3B3;
  --muted-2: #5E6A7A;
  --tide: #5EE7FF;
  --tide-2: #2DD4A7;
  --foam: #B8F7FF;
  --amber: #FFC46B;
  --coral: #FF7A5C;
  --danger: #FF6B6B;
  --info: #8FB8FF;
  --accent: var(--tide);
  --accent-dim: rgba(94, 231, 255, 0.12);
  --accent-2: var(--amber);
  --display: "Bricolage Grotesque", "Segoe UI Variable Display", "Segoe UI", "Microsoft YaHei", sans-serif;
  --sans: "Inter", "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei", sans-serif;
  --mono: "Space Mono", "Cascadia Code", "SFMono-Regular", Consolas, monospace;
  --radius: 22px;
}

* { scrollbar-width: thin; scrollbar-color: rgba(94,231,255,0.22) transparent; }
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-thumb { background: rgba(94,231,255,0.20); border-radius: 8px; }
::-webkit-scrollbar-thumb:hover { background: rgba(94,231,255,0.42); }
::selection { background: rgba(94,231,255,0.30); color: #fff; }

html, body, [data-testid="stAppViewContainer"] {
  background: var(--ink) !important;
  color: var(--text);
  font-family: var(--sans);
}
.stApp {
  background:
    radial-gradient(1000px 560px at 7% -8%, rgba(255, 122, 92, 0.13), transparent 62%),
    radial-gradient(1100px 620px at 94% 0%, rgba(94, 231, 255, 0.12), transparent 60%),
    radial-gradient(900px 600px at 50% 110%, rgba(45, 212, 167, 0.08), transparent 65%),
    #07080A !important;
}
.stApp:before {
  content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 0;
  background-image: radial-gradient(rgba(255,255,255,0.045) 0.7px, transparent 0.8px);
  background-size: 3px 3px; opacity: 0.22; mix-blend-mode: soft-light;
}
header[data-testid="stHeader"] { background: transparent !important; }
footer, #MainMenu { visibility: hidden; }
[data-testid="stToolbar"] { display: none; }
.block-container { padding-top: 0.4rem; padding-bottom: 5rem; max-width: 1390px; }

h1, h2, h3, h4 { font-family: var(--display); letter-spacing: -0.03em; }
p, li, span, label { color: var(--text); }
.cm-num, div[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; }

/* ---------- 顶部公告 + 导航 ---------- */
.cm-announce {
  position: relative; overflow: hidden; display: flex; align-items: center; gap: 12px;
  margin: 0 0 0 0; padding: 8px 16px; border-radius: 0 0 14px 14px;
  background: linear-gradient(100deg, #5EE7FF 0%, #7FB6FF 48%, #B8F7FF 100%);
  color: #04121F; font-family: var(--mono); font-size: 10.5px; letter-spacing: 0.06em;
  text-transform: uppercase; box-shadow: 0 10px 40px rgba(94,231,255,0.16);
}
.cm-announce .a-dot { width: 6px; height: 6px; border-radius: 50%; background: #04121F; animation: cmPulse 2.2s infinite; }
.cm-announce .a-mid { margin-left: auto; opacity: 0.72; }
.cm-announce .a-right { font-weight: 700; }
.cm-nav {
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  padding: 15px 0 14px 0; margin-bottom: 16px;
  border-bottom: 1px solid var(--line);
}
.cm-brand { display: flex; align-items: center; gap: 10px; text-decoration: none !important; }
.cm-brand .mark {
  width: 34px; height: 34px; border-radius: 11px; display: flex; align-items: center; justify-content: center;
  color: #04121F; font-family: var(--display); font-weight: 800; font-size: 12px;
  background: linear-gradient(135deg, var(--tide), var(--tide-2));
  box-shadow: 0 0 0 1px rgba(255,255,255,0.18), 0 8px 30px rgba(94,231,255,0.22);
}
.cm-brand .word { font-family: var(--display); font-weight: 800; font-size: 17px; letter-spacing: -0.04em; color: var(--text); }
.cm-brand .word em { font-style: normal; color: var(--tide); }
.cm-brand .sub { display: block; font-family: var(--mono); font-size: 8px; letter-spacing: 0.16em; color: var(--muted-2); margin-top: 1px; }
.cm-nav-links { display: flex; align-items: center; gap: 1px; flex-wrap: wrap; margin-left: auto; }
.cm-nav-link {
  display: inline-flex; align-items: center; padding: 7px 9px; border-radius: 999px;
  font-size: 11px; color: var(--muted); text-decoration: none !important; white-space: nowrap;
  transition: color .18s, background .18s;
}
.cm-nav-link:hover { color: var(--text); background: rgba(255,255,255,0.05); }
.cm-nav-link.on { color: #04121F; background: var(--tide); font-weight: 700; }
.cm-nav-status {
  display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px; border: 1px solid var(--line-2);
  border-radius: 999px; color: var(--muted); font-family: var(--mono); font-size: 9px; white-space: nowrap;
}
.cm-nav-status:before { content: ""; width: 5px; height: 5px; border-radius: 50%; background: var(--tide-2); box-shadow: 0 0 8px var(--tide-2); }

/* ---------- Hero ---------- */
.cm-hero {
  position: relative; overflow: hidden; isolation: isolate;
  border: 1px solid var(--line); border-radius: 30px;
  padding: 62px 54px 46px 54px; margin: 2px 0 22px 0; min-height: 390px;
  background: #090B0F;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.06), 0 30px 90px rgba(0,0,0,0.45);
}
.cm-hero .cm-aurora {
  position: absolute; inset: -35%; z-index: -2; opacity: 0.70; filter: blur(70px);
  background:
    radial-gradient(closest-side at 30% 42%, rgba(255,122,92,0.36), transparent 72%),
    radial-gradient(closest-side at 72% 34%, rgba(94,231,255,0.34), transparent 72%),
    radial-gradient(closest-side at 58% 90%, rgba(45,212,167,0.22), transparent 75%);
  animation: cmDrift 18s ease-in-out infinite alternate;
}
.cm-hero .cm-mesh {
  position: absolute; inset: 0; z-index: -1; opacity: 0.55;
  background-image:
    linear-gradient(rgba(255,255,255,0.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,0.045) 1px, transparent 1px);
  background-size: 56px 56px;
  mask-image: radial-gradient(ellipse at 52% 48%, #000 0%, transparent 72%);
  -webkit-mask-image: radial-gradient(ellipse at 52% 48%, #000 0%, transparent 72%);
}
.cm-hero .cm-shine {
  position: absolute; width: 320px; height: 320px; border-radius: 50%; right: 8%; top: -16%;
  background: radial-gradient(circle, rgba(255,255,255,0.10), transparent 68%);
  filter: blur(4px); z-index: -1; animation: cmFloat 9s ease-in-out infinite;
}
.cm-hero-inner { position: relative; z-index: 2; max-width: 1000px; animation: cmReveal .7s ease both; }
.cm-kicker {
  display: inline-flex; align-items: center; gap: 9px; color: var(--tide);
  font-family: var(--mono); font-size: 10px; letter-spacing: 0.22em; text-transform: uppercase;
}
.cm-kicker .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--tide); box-shadow: 0 0 12px var(--tide); animation: cmPulse 2.2s infinite; }
.cm-display {
  font-family: var(--display); font-weight: 800; letter-spacing: -0.045em;
  font-size: clamp(40px, 5.3vw, 76px); line-height: 0.98; margin: 17px 0 18px 0;
  max-width: 1040px; color: var(--text); text-wrap: balance;
}
.cm-display.long { font-size: clamp(34px, 4.0vw, 64px); letter-spacing: -0.035em; }
.cm-display .cm-grad {
  background: linear-gradient(100deg, var(--tide) 0%, #8FB8FF 52%, var(--tide-2) 100%);
  -webkit-background-clip: text; background-clip: text; color: transparent;
}
.cm-sub { max-width: 760px; color: var(--muted); font-size: 15px; line-height: 1.75; letter-spacing: 0.005em; }
.cm-hero-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 24px; }
.cm-btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 9px;
  padding: 11px 18px; border-radius: 999px; font-size: 12px; font-weight: 700;
  text-decoration: none !important; transition: transform .2s, background .2s, border-color .2s;
}
.cm-btn:hover { transform: translateY(-1px); }
.cm-btn.primary { background: var(--text); color: #07080A; box-shadow: 0 10px 36px rgba(255,255,255,0.12); }
.cm-btn.primary:hover { background: #fff; }
.cm-btn.ghost { color: var(--text); border: 1px solid var(--line-2); background: rgba(255,255,255,0.02); }
.cm-btn.ghost:hover { border-color: rgba(94,231,255,0.6); background: rgba(94,231,255,0.06); }
.cm-hero-meta {
  position: absolute; right: 34px; bottom: 24px; z-index: 2; text-align: right;
  color: var(--muted-2); font-family: var(--mono); font-size: 8.5px; line-height: 1.8; letter-spacing: 0.10em;
}
.cm-hero-meta b { color: var(--muted); font-weight: 400; }
.cm-hero-index {
  position: absolute; right: 24px; top: 10px; z-index: 1; pointer-events: none;
  font-family: var(--display); font-weight: 800; font-size: clamp(80px, 12vw, 150px);
  line-height: 1; letter-spacing: -0.08em; color: rgba(255,255,255,0.035);
}
.cm-hero.compact { min-height: 0; padding: 30px 38px 28px 38px; border-radius: 24px; margin-bottom: 16px; }
.cm-hero.compact .cm-display { font-size: clamp(30px, 3.2vw, 44px); margin: 9px 0 8px 0; }
.cm-hero.compact .cm-sub { font-size: 13px; max-width: 860px; }
.cm-hero.compact .cm-hero-index { font-size: 90px; top: -6px; }
.cm-hero.compact .cm-hero-meta { display: none; }

/* ---------- Chips ---------- */
.cm-chip { display: inline-flex; align-items: center; margin: 10px 7px 0 0; padding: 5px 10px;
  border-radius: 999px; font-size: 10.5px; color: var(--text); border: 1px solid rgba(94,231,255,0.28);
  background: rgba(94,231,255,0.07); font-family: var(--mono); letter-spacing: 0.02em; }
.cm-chip.ok, .cm-chip.mint { border-color: rgba(45,212,167,0.48); background: rgba(45,212,167,0.10); color: #A7F3DE; }
.cm-chip.warn, .cm-chip.amber { border-color: rgba(255,196,107,0.5); background: rgba(255,196,107,0.10); color: #FFE0A0; }
.cm-chip.bad, .cm-chip.red { border-color: rgba(255,107,107,0.5); background: rgba(255,107,107,0.10); color: #FFB3B3; }
.cm-chip.skip, .cm-chip.plain { border-color: rgba(152,163,179,0.36); background: rgba(152,163,179,0.08); color: #B7C2CE; }
.cm-chip.info, .cm-chip.blue { border-color: rgba(143,184,255,0.45); background: rgba(143,184,255,0.10); color: #C9DCFF; }
.cm-chip.main { border-color: rgba(255,196,107,0.62); background: rgba(255,196,107,0.14); color: #FFE3A0; font-weight: 700; }
.cm-chip.coral { border-color: rgba(255,122,92,0.5); background: rgba(255,122,92,0.10); color: #FFC4B3; }

/* ---------- 章节标题 ---------- */
.cm-sec { display: flex; align-items: flex-end; gap: 14px; flex-wrap: wrap;
  margin: 40px 0 14px 0; padding-bottom: 12px; border-bottom: 1px solid var(--line); }
.cm-sec .bar { display: none; }
.cm-sec .t { font-family: var(--display); font-size: 25px; font-weight: 700; letter-spacing: -0.035em; }
.cm-sec .d { color: var(--muted); font-size: 11.5px; margin-left: auto; max-width: 56%; text-align: right; line-height: 1.6; }

/* ---------- 结论带 ---------- */
.cm-conclusion {
  position: relative; overflow: hidden; margin-top: 26px; padding: 24px 28px 22px 28px;
  border: 1px solid rgba(94,231,255,0.18); border-radius: var(--radius);
  background: linear-gradient(110deg, rgba(94,231,255,0.10), rgba(45,212,167,0.04) 55%, rgba(255,122,92,0.05));
  font-size: 14px; line-height: 1.8; color: var(--text);
}
.cm-conclusion:before { content: "CONCLUSION"; display: block; margin-bottom: 7px;
  font-family: var(--mono); font-size: 8.5px; letter-spacing: 0.22em; color: var(--tide); }
.cm-conclusion b { color: var(--foam); }

/* ---------- 面板 ---------- */
.cm-panel { border: 1px solid var(--line); border-radius: 18px; background: var(--panel);
  padding: 16px 18px 14px 18px; margin: 4px 0 12px 0;
  box-shadow: 0 18px 60px rgba(0,0,0,0.26); backdrop-filter: blur(12px); }
.cm-panel.flat { background: rgba(255,255,255,0.018); box-shadow: none; }
.cm-panel-h { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
.cm-panel-h .t { font-size: 14px; font-weight: 700; letter-spacing: -0.01em; }
.cm-panel-h .d { color: var(--muted); font-size: 11.5px; }
.cm-panel-h .r { margin-left: auto; color: var(--muted); font-size: 11px; font-family: var(--mono); }
.cm-note { color: var(--muted); font-size: 11.5px; line-height: 1.75; }
.cm-note b { color: var(--text); }
.cm-note .em { color: var(--amber); }

/* ---------- 统计卡 ---------- */
.cm-stats { display: grid; gap: 11px; margin: 2px 0 8px 0; }
.cm-stat {
  position: relative; overflow: hidden; border: 1px solid var(--line); border-radius: 18px;
  padding: 17px 17px 15px 17px; min-height: 122px;
  background: linear-gradient(155deg, rgba(255,255,255,0.045), rgba(255,255,255,0.012) 58%, rgba(255,255,255,0.02));
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.04);
  transition: border-color .2s, transform .2s;
}
.cm-stat:hover { border-color: rgba(94,231,255,0.28); transform: translateY(-2px); }
.cm-stat:after { content: ""; position: absolute; right: -20px; bottom: -34px; width: 100px; height: 100px;
  border-radius: 50%; background: radial-gradient(circle, rgba(94,231,255,0.12), transparent 65%); }
.cm-stat .l { font-family: var(--mono); font-size: 9px; letter-spacing: 0.13em; color: var(--muted); text-transform: uppercase; }
.cm-stat .v { font-family: var(--display); font-size: 31px; font-weight: 700; font-variant-numeric: tabular-nums;
  margin-top: 7px; line-height: 1.05; letter-spacing: -0.03em; }
.cm-stat .s { font-size: 10.5px; color: var(--muted-2); margin-top: 6px; line-height: 1.5; }
.cm-stat.mint .v, .cm-stat.ok .v { color: var(--tide-2); }
.cm-stat.amber .v, .cm-stat.warn .v { color: var(--amber); }
.cm-stat.coral .v { color: var(--coral); }
.cm-stat.red .v, .cm-stat.bad .v { color: var(--danger); }
.cm-stat.info .v, .cm-stat.blue .v { color: var(--info); }

/* ---------- KV / 公式 / 步骤 / 追溯 ---------- */
.cm-kv { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.cm-kv td { padding: 8px 10px; border-bottom: 1px solid rgba(255,255,255,0.055); vertical-align: top; }
.cm-kv td.k { color: var(--muted); white-space: nowrap; width: 118px; }
.cm-kv td.v { color: var(--text); font-variant-numeric: tabular-nums; }
.cm-kv tr:last-child td { border-bottom: none; }
.cm-kv td.v .big { font-size: 17px; font-weight: 700; }
.cm-formula { font-family: var(--mono); font-size: 11.5px; color: var(--text);
  background: rgba(94,231,255,0.055); border: 1px dashed rgba(94,231,255,0.28);
  border-radius: 12px; padding: 10px 13px; margin: 7px 0; line-height: 1.8; }
.cm-formula .hl { color: var(--tide); font-weight: 700; }
.cm-formula .hl2 { color: var(--amber); font-weight: 700; }
.cm-formula .dim { color: var(--muted); }
.cm-steps { display: flex; align-items: stretch; gap: 0; flex-wrap: wrap; margin: 4px 0 8px 0; }
.cm-step { flex: 1; min-width: 120px; text-align: center; padding: 11px 7px 10px 7px;
  border: 1px solid var(--line); border-radius: 12px; background: rgba(255,255,255,0.018);
  font-size: 11.5px; color: var(--muted); position: relative; }
.cm-step .n { display: block; font-family: var(--mono); font-size: 9px; color: var(--muted-2); letter-spacing: 0.08em; }
.cm-step .t { display: block; font-weight: 700; margin-top: 3px; color: var(--text); }
.cm-step.on { border-color: rgba(94,231,255,0.45); background: rgba(94,231,255,0.08); }
.cm-step.on .n { color: var(--tide); }
.cm-step-arrow { align-self: center; color: var(--muted-2); padding: 0 5px; font-size: 12px; }
.cm-trace { margin: 4px 0; }
.cm-trace .node { display: flex; gap: 10px; align-items: flex-start; }
.cm-trace .dot { width: 8px; height: 8px; border-radius: 50%; margin-top: 6px; flex: none;
  background: var(--tide); box-shadow: 0 0 0 4px rgba(94,231,255,0.12); }
.cm-trace .dot.amber { background: var(--amber); box-shadow: 0 0 0 4px rgba(255,196,107,0.12); }
.cm-trace .dot.muted { background: var(--muted-2); box-shadow: 0 0 0 4px rgba(152,163,179,0.10); }
.cm-trace .body { font-size: 12.5px; line-height: 1.65; }
.cm-trace .body .t { font-weight: 700; }
.cm-trace .body .d { color: var(--muted); font-size: 11.5px; }
.cm-trace .link { width: 1px; height: 14px; background: var(--line-2); margin: 3px 0 3px 3px; }

/* ---------- HTML 表格 ---------- */
div[data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 16px; overflow: hidden; background: rgba(255,255,255,0.012); }
div[data-testid="stDataFrame"] table { font-variant-numeric: tabular-nums; }
.cm-htable { width: 100%; border-collapse: collapse; font-size: 11.5px; font-variant-numeric: tabular-nums; }
.cm-htable th { text-align: left; color: var(--muted); font-family: var(--mono); font-size: 9px;
  letter-spacing: 0.08em; text-transform: uppercase; font-weight: 400; padding: 9px 10px;
  border-bottom: 1px solid var(--line-2); white-space: nowrap; background: rgba(255,255,255,0.018); }
.cm-htable td { padding: 9px 10px; border-bottom: 1px solid rgba(255,255,255,0.045); vertical-align: top; }
.cm-htable tr:last-child td { border-bottom: none; }
.cm-htable tr:hover td { background: rgba(94,231,255,0.035); }
.cm-htable tr.base td { background: rgba(94,231,255,0.055); }
.cm-htable tr.main td { background: rgba(255,196,107,0.055); }
.cm-htable tr.skip td { background: rgba(152,163,179,0.05); }
.cm-htable tr.fail td { background: rgba(255,107,107,0.07); }
.cm-htable td.num, .cm-htable th.num { text-align: right; }
.cm-htable .st-ok { color: var(--tide-2); font-weight: 700; }
.cm-htable .st-bad { color: var(--danger); font-weight: 700; }
.cm-htable .st-skip { color: var(--muted); }
.cm-htable .st-warn { color: var(--amber); font-weight: 700; }

/* ---------- empty / flags / estimate / demo ---------- */
.cm-empty { border: 1px dashed var(--line-2); border-radius: 14px; padding: 20px 18px;
  color: var(--muted); font-size: 12px; background: rgba(255,255,255,0.014); }
.cm-empty .t { color: var(--text); font-weight: 700; font-size: 13px; margin-bottom: 4px; }
.cm-empty code { color: var(--tide); background: rgba(94,231,255,0.08); padding: 1px 6px; border-radius: 6px; }
.cm-flagbar { display: flex; gap: 9px; flex-wrap: wrap; margin: 4px 0 10px 0; }
.cm-flag { display: inline-flex; align-items: center; gap: 7px; padding: 5px 11px;
  border: 1px solid var(--line); border-radius: 999px; font-size: 11px; color: var(--muted);
  background: rgba(255,255,255,0.018); }
.cm-flag .dot { width: 8px; height: 8px; border-radius: 3px; display: inline-block; }
.cm-flag .est { border: 2px solid var(--amber); background: transparent; }
.cm-est-wrap { border: 1px solid rgba(255,196,107,0.26); border-radius: 16px;
  padding: 13px 15px 7px 15px; margin: 6px 0 12px 0;
  background: linear-gradient(180deg, rgba(255,196,107,0.055), rgba(255,196,107,0.008)); }
.cm-est-head { font-size: 12px; color: var(--amber); font-weight: 700; letter-spacing: 0.02em; }
.cm-est-head span { color: var(--muted); font-weight: 400; margin-left: 8px; }
.cm-est-table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 11.5px; }
.cm-est-table th { text-align: left; color: var(--muted); font-weight: 600; padding: 7px 9px; border-bottom: 1px solid var(--line); }
.cm-est-table td { padding: 7px 9px; border-bottom: 1px solid rgba(255,255,255,0.045); color: var(--text); vertical-align: top; }
.cm-est-table tr.est td { border: 2px solid var(--amber); background: rgba(255,196,107,0.08); }
.cm-est-table td.reason { color: #FFE7A3; }
.cm-est-foot { color: var(--muted); font-size: 10.5px; margin: 8px 0 4px 0; }
.cm-demo { border: 1px solid rgba(255,196,107,0.30); background: rgba(255,196,107,0.06);
  border-radius: 14px; padding: 11px 15px; margin: 0 0 14px 0; font-size: 12px; color: var(--text); }
.cm-demo b { color: var(--amber); }
.cm-gate-ok { color: var(--tide-2); font-weight: 700; }
.cm-gate-fail { color: var(--danger); font-weight: 700; }
.cm-gate-skip { color: var(--muted); font-weight: 700; }

/* ---------- 首页 marquee / bento / CTA ---------- */
.cm-marquee { overflow: hidden; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line);
  margin: 16px 0 6px 0; padding: 10px 0; mask-image: linear-gradient(90deg, transparent, #000 8%, #000 92%, transparent);
  -webkit-mask-image: linear-gradient(90deg, transparent, #000 8%, #000 92%, transparent); }
.cm-marquee-track { display: flex; width: max-content; gap: 0; animation: cmMarquee 34s linear infinite; }
.cm-marquee-track span { display: inline-flex; align-items: center; gap: 16px; padding: 0 18px;
  font-family: var(--mono); font-size: 9.5px; letter-spacing: 0.1em; color: var(--muted); text-transform: uppercase; white-space: nowrap; }
.cm-marquee-track b { color: var(--text); font-weight: 400; }
.cm-marquee-track i { color: var(--tide); font-style: normal; }
.cm-bento { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 13px; margin: 6px 0 8px 0; }
.cm-bento-card { position: relative; display: flex; flex-direction: column; min-height: 286px; overflow: hidden;
  border: 1px solid var(--line); border-radius: 21px; background: #0B0D12; text-decoration: none !important;
  transition: transform .25s ease, border-color .25s ease, box-shadow .25s ease; }
.cm-bento-card { animation: cmReveal .6s ease both; }
.cm-bento-card:nth-child(2) { animation-delay: .04s; }
.cm-bento-card:nth-child(3) { animation-delay: .08s; }
.cm-bento-card:nth-child(4) { animation-delay: .12s; }
.cm-bento-card:nth-child(5) { animation-delay: .16s; }
.cm-bento-card:nth-child(6) { animation-delay: .20s; }
.cm-bento-card:nth-child(7) { animation-delay: .24s; }
.cm-bento-card:nth-child(8) { animation-delay: .28s; }
.cm-bento-card:hover { transform: translateY(-4px); border-color: rgba(94,231,255,0.34); box-shadow: 0 24px 70px rgba(0,0,0,0.48); }
.cm-bento-card.span2 { grid-column: span 2; }
.cm-card-visual { position: relative; height: 152px; overflow: hidden; border-bottom: 1px solid var(--line);
  background: radial-gradient(ellipse at 30% 20%, rgba(94,231,255,0.18), transparent 58%), #0A0C10; }
.cm-bento-card.span2 .cm-card-visual { height: 175px; }
.cm-card-visual:after { content: ""; position: absolute; inset: 0; background: linear-gradient(180deg, transparent 45%, rgba(7,8,10,0.75)); }
.cm-card-visual .cm-v-tag { position: absolute; left: 14px; top: 13px; z-index: 2; color: var(--foam);
  font-family: var(--mono); font-size: 8px; letter-spacing: 0.12em; padding: 4px 7px; border: 1px solid rgba(94,231,255,0.28); border-radius: 999px; background: rgba(7,8,10,0.45); }
.cm-card-body { position: relative; z-index: 2; display: flex; flex-direction: column; flex: 1; padding: 15px 17px 14px 17px; }
.cm-card-eyebrow { color: var(--tide); font-family: var(--mono); font-size: 8.5px; letter-spacing: 0.13em; text-transform: uppercase; }
.cm-card-title { display: flex; align-items: center; gap: 8px; margin: 7px 0 5px 0;
  font-family: var(--display); font-size: 21px; font-weight: 700; letter-spacing: -0.03em; color: var(--text); }
.cm-card-title span { color: var(--tide); font-size: 15px; margin-left: auto; opacity: 0; transform: translateX(-5px); transition: all .2s; }
.cm-bento-card:hover .cm-card-title span { opacity: 1; transform: translateX(0); }
.cm-card-desc { color: var(--muted); font-size: 10.5px; line-height: 1.65; }
.cm-card-foot { margin-top: auto; padding-top: 10px; color: var(--muted-2); font-family: var(--mono); font-size: 8px; letter-spacing: 0.08em; }
.cm-v-bars { position: absolute; left: 22px; right: 22px; bottom: 20px; display: flex; align-items: flex-end; gap: 8px; height: 70px; z-index: 1; }
.cm-v-bars i { display: block; flex: 1; border-radius: 4px 4px 1px 1px; background: linear-gradient(180deg, rgba(94,231,255,0.85), rgba(94,231,255,0.14)); }
.cm-v-bars i:nth-child(2n) { background: linear-gradient(180deg, rgba(45,212,167,0.8), rgba(45,212,167,0.10)); }
.cm-v-line { position: absolute; left: 0; right: 0; bottom: 34px; height: 1px; background: rgba(255,255,255,0.08); }
.cm-v-route { position: absolute; inset: 8% 12%; z-index: 1; }
.cm-v-route path { fill: none; stroke: url(#cmGrad); stroke-width: 3.4; stroke-dasharray: 7 4; stroke-linecap: round;
  filter: drop-shadow(0 0 7px rgba(94,231,255,0.55)); animation: cmDash 12s linear infinite; }
.cm-v-route circle { fill: var(--tide); filter: drop-shadow(0 0 8px var(--tide)); }
.cm-v-grid { position: absolute; inset: 16px; z-index: 1; display: grid; grid-template-columns: repeat(6,1fr); gap: 4px; opacity: 0.9; }
.cm-v-grid i { border-radius: 3px; background: rgba(255,122,92,0.18); }
.cm-v-grid i:nth-child(3n) { background: rgba(255,196,107,0.5); }
.cm-v-grid i:nth-child(5n) { background: rgba(255,122,92,0.55); }
.cm-v-rows { position: absolute; left: 18px; right: 18px; top: 34px; z-index: 1; display: flex; flex-direction: column; gap: 6px; }
.cm-v-rows i { display: block; height: 7px; border-radius: 3px; background: rgba(255,255,255,0.10); }
.cm-v-rows i:nth-child(1) { width: 85%; background: rgba(94,231,255,0.55); }
.cm-v-rows i:nth-child(2) { width: 62%; }
.cm-v-rows i:nth-child(3) { width: 74%; background: rgba(45,212,167,0.38); }
.cm-v-rows i:nth-child(4) { width: 48%; }
.cm-v-rows i:nth-child(5) { width: 68%; }
.cm-v-compare { position: absolute; left: 22px; right: 22px; bottom: 23px; z-index: 1; display: flex; align-items: flex-end; gap: 12px; height: 80px; }
.cm-v-compare i { flex: 1; border-radius: 5px 5px 1px 1px; background: rgba(255,255,255,0.12); }
.cm-v-compare i:nth-child(1) { height: 80%; background: rgba(255,122,92,0.42); }
.cm-v-compare i:nth-child(2) { height: 38%; background: rgba(94,231,255,0.65); }
.cm-v-compare i:nth-child(3) { height: 55%; background: rgba(255,196,107,0.5); }
.cm-v-compare i:nth-child(4) { height: 28%; background: rgba(45,212,167,0.6); }
.cm-cta { position: relative; overflow: hidden; margin: 34px 0 10px 0; padding: 46px 44px; border: 1px solid var(--line);
  border-radius: 26px; background: radial-gradient(800px 300px at 18% 10%, rgba(94,231,255,0.16), transparent 60%),
  radial-gradient(600px 260px at 90% 90%, rgba(255,122,92,0.10), transparent 60%), #0A0C10; }
.cm-cta .k { color: var(--tide); font-family: var(--mono); font-size: 9px; letter-spacing: 0.2em; }
.cm-cta h2 { font-size: clamp(30px, 3.4vw, 46px); line-height: 1.02; margin: 12px 0 10px 0; max-width: 760px; }
.cm-cta p { color: var(--muted); font-size: 12.5px; max-width: 640px; line-height: 1.7; }
.cm-cta .cm-hero-actions { margin-top: 20px; }

/* ---------- 侧栏控制台 ---------- */
section[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] { display: none !important; }
div[data-testid="stExpander"] { margin: -4px 0 16px 0; }
div[data-testid="stExpander"] details { border-radius: 15px; background: rgba(255,255,255,0.022); }
div[data-testid="stExpander"] summary { padding: 9px 14px; font-family: var(--mono); font-size: 10px; letter-spacing: 0.02em; color: var(--muted); }
div[data-testid="stExpander"] summary:hover { color: var(--tide); }
section[data-testid="stSidebar"] .block-container { padding: 20px 18px 28px 18px; }
section[data-testid="stSidebar"] [data-testid="stSidebarNav"],
section[data-testid="stSidebar"] [data-testid="stSidebarNavItems"] { display: none !important; }
.cm-side-brand { display: flex; align-items: center; gap: 10px; padding: 2px 0 18px 0; border-bottom: 1px solid var(--line); margin-bottom: 18px; }
.cm-side-brand .mark { width: 33px; height: 33px; border-radius: 10px; display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, var(--tide), var(--tide-2)); color: #04121F; font-family: var(--display); font-weight: 800; font-size: 11px; }
.cm-side-brand .n { font-family: var(--display); font-size: 14px; font-weight: 700; letter-spacing: -0.03em; }
.cm-side-brand .s { font-family: var(--mono); font-size: 7.5px; letter-spacing: 0.14em; color: var(--muted-2); margin-top: 1px; }
.cm-side-label { color: var(--muted-2); font-family: var(--mono); font-size: 8px; letter-spacing: 0.18em; text-transform: uppercase; margin: 2px 0 8px 0; }
.cm-side-card { border: 1px solid var(--line); border-radius: 14px; padding: 11px 12px; background: rgba(255,255,255,0.018); margin-bottom: 12px; }
.cm-side-card .k { color: var(--muted-2); font-family: var(--mono); font-size: 7.5px; letter-spacing: 0.12em; }
.cm-side-card .v { color: var(--text); font-family: var(--mono); font-size: 11px; margin-top: 3px; }
.cm-side-card .v.ok { color: var(--tide-2); }

/* ---------- Streamlit 控件适配 ---------- */
div[data-testid="stTabs"] { margin-top: 4px; }
button[data-testid="stTab"] { font-size: 12px; color: var(--muted); border-radius: 8px 8px 0 0; }
button[data-testid="stTab"][aria-selected="true"] { color: var(--tide); }
div[data-testid="stExpander"] details { border: 1px solid var(--line); border-radius: 14px; background: var(--panel); }
div[data-testid="stExpander"] summary { font-size: 12px; color: var(--text); }
div[data-testid="stExpander"] summary:hover { color: var(--tide); }
div[data-testid="stMetric"] { background: linear-gradient(155deg, rgba(255,255,255,0.045), rgba(255,255,255,0.01));
  border: 1px solid var(--line); border-radius: 18px; padding: 17px 18px 15px 18px; }
div[data-testid="stMetricLabel"] p { font-family: var(--mono); font-size: 9px !important; color: var(--muted) !important; letter-spacing: 0.1em; text-transform: uppercase; }
div[data-testid="stMetricValue"] { font-family: var(--display); font-size: 30px; letter-spacing: -0.03em; }
button[data-testid="stBaseButton-primary"], button[kind="primary"] {
  border-radius: 999px !important; background: var(--text) !important; color: #07080A !important; border: none !important; font-weight: 700 !important;
}
button[data-testid="stBaseButton-secondary"], button[kind="secondary"] {
  border-radius: 999px !important; border: 1px solid var(--line-2) !important; background: rgba(255,255,255,0.02) !important; color: var(--text) !important;
}
button[data-testid="stBaseButton-secondary"]:hover { border-color: rgba(94,231,255,0.5) !important; }
div[data-testid="stSlider"] [role="slider"] { accent-color: var(--tide); }
div[data-testid="stNumberInput"] input, div[data-testid="stTextInput"] input,
div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
  background: rgba(255,255,255,0.025) !important; border-color: var(--line) !important; border-radius: 11px !important; color: var(--text) !important;
}
div[data-testid="stRadio"] label, div[data-testid="stSelectbox"] label { color: var(--muted) !important; }
div[data-testid="stMultiSelect"] span[data-baseweb="tag"] { background: rgba(94,231,255,0.12); border: 1px solid rgba(94,231,255,0.3); }
[data-testid="stSidebar"] div[data-testid="stNumberInput"] input,
[data-testid="stSidebar"] div[data-testid="stTextInput"] input { background: rgba(255,255,255,0.04) !important; }
[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"] { width: 100%; }
[data-testid="stMarkdownContainer"] a { color: var(--tide); }
[data-testid="stMarkdownContainer"] code { color: var(--foam); background: rgba(94,231,255,0.08); border-radius: 5px; padding: 1px 5px; }

/* ---------- 动效 ---------- */
@keyframes cmDrift { 0% { transform: translate3d(-3%, -2%, 0) scale(1); } 100% { transform: translate3d(4%, 3%, 0) scale(1.08); } }
@keyframes cmFloat { 0%,100% { transform: translateY(0); } 50% { transform: translateY(16px); } }
@keyframes cmPulse { 0%,100% { opacity: 1; } 50% { opacity: .35; } }
@keyframes cmReveal { from { opacity: 0; transform: translateY(16px); } to { opacity: 1; transform: none; } }
@keyframes cmMarquee { from { transform: translateX(0); } to { transform: translateX(-50%); } }
@keyframes cmDash { to { stroke-dashoffset: -100; } }
@media (prefers-reduced-motion: reduce) {
  *, *:before, *:after { animation-duration: .001ms !important; animation-iteration-count: 1 !important; transition-duration: .001ms !important; }
}

/* ---------- 响应式 ---------- */
@media (max-width: 1180px) {
  .cm-nav-links .cm-nav-link { padding: 6px 7px; font-size: 10px; }
  .cm-hero { padding: 46px 34px 38px 34px; }
  .cm-bento { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .cm-bento-card.span2 { grid-column: span 2; }
}
@media (max-width: 760px) {
  .block-container { padding-left: 1rem; padding-right: 1rem; }
  .cm-announce { font-size: 8px; gap: 7px; padding: 7px 10px; }
  .cm-announce .a-mid, .cm-announce .a-right { display: none; }
  div[data-testid="stExpander"] summary { font-size: 9px; padding: 7px 10px; }
  div[data-testid="stExpander"] summary p { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .cm-nav { gap: 8px; }
  .cm-nav-links { order: 3; width: 100%; overflow-x: auto; flex-wrap: nowrap; margin-left: 0; padding-bottom: 2px; }
  .cm-nav-status { margin-left: auto; }
  .cm-hero { padding: 32px 22px 30px 22px; border-radius: 22px; }
  .cm-display { font-size: clamp(34px, 10vw, 50px); }
  .cm-sub { font-size: 13px; }
  .cm-hero-meta { display: none; }
  .cm-sec .d { max-width: 100%; text-align: left; margin-left: 0; }
  .cm-bento { grid-template-columns: 1fr; }
  .cm-bento-card.span2 { grid-column: span 1; }
  .cm-cta { padding: 30px 24px; }
  section[data-testid="stSidebar"] { width: 250px !important; min-width: 250px !important; }
}
</style>
"""


PAGES: List[Dict[str, str]] = [
    {"id": "0", "label": "概览", "href": "/"},
    {"id": "1", "label": "核算", "href": "/基准核算"},
    {"id": "2", "label": "诊断", "href": "/高排放诊断"},
    {"id": "3", "label": "方案", "href": "/替代方案"},
    {"id": "4", "label": "成本", "href": "/成本效益"},
    {"id": "5", "label": "台账", "href": "/碳会计台账"},
    {"id": "6", "label": "路线", "href": "/路线实验室"},
    {"id": "7", "label": "测算", "href": "/电动化测算"},
    {"id": "8", "label": "TCO", "href": "/车队TCO沙盘"},
]


def inject() -> None:
    """注入全局样式。每个页面在 setup() 之后调用一次，重复调用无副作用。"""
    if st.session_state.get("_cm_css_injected"):
        return
    st.markdown(CSS, unsafe_allow_html=True)
    st.session_state["_cm_css_injected"] = True


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _href(path: str) -> str:
    return quote(path, safe="/")


def top_nav(current: str = "", source_name: str = "", gate: Optional[Dict[str, Any]] = None) -> None:
    """顶部公告条 + 品牌导航；替代 Streamlit 默认侧栏导航。"""
    current = str(current or "0")
    links = []
    for item in PAGES:
        on = " on" if item["id"] == current else ""
        links.append(
            "<a class='cm-nav-link" + on + "' href='" + _href(item["href"]) + "' target='_top'>"
            + _esc(item["label"]) + "</a>"
        )
    source = "REAL DATA" if source_name == "real" else "DEMO DATA"
    if gate and gate.get("passed"):
        gate_text = "GATE %d/%d" % (len(gate.get("targets", [])), len(gate.get("targets", [])))
        gate_text = "GATE PASS"
    elif gate:
        gate_text = "GATE CHECK"
    else:
        gate_text = "GATE —"
    announce = (
        "<div class='cm-announce'>"
        "<span class='a-dot'></span><span>2027 CHALLENGE CUP · 主体赛 A 类</span>"
        "<span class='a-mid'>CARBON TIDE / DECISION OS</span>"
        "<span class='a-right'>" + source + " · " + gate_text + "</span>"
        "</div>"
    )
    nav = (
        "<div class='cm-nav'>"
        "<a class='cm-brand' href='/' target='_top'>"
        "<span class='mark'>CT</span>"
        "<span><span class='word'>carbon <em>tide</em></span>"
        "<span class='sub'>CARBON DECISION SYSTEM</span></span>"
        "</a>"
        "<div class='cm-nav-links'>" + "".join(links) + "</div>"
        "<span class='cm-nav-status'>READ-ONLY</span>"
        "</div>"
    )
    st.markdown(announce + nav, unsafe_allow_html=True)


def hero(title: str, subtitle: str, kicker: str = "CARBON LEDGER",
         badges: Optional[Iterable[str]] = None,
         highlight: Optional[str] = None,
         actions: Optional[Iterable[Dict[str, Any]]] = None,
         index: Optional[str] = None) -> None:
    """电影感页头：超大标题 + 潮汐光晕 + 网格 + 行动按钮。"""
    safe_title = _esc(title)
    if highlight:
        safe_highlight = _esc(highlight)
        if safe_highlight in safe_title:
            safe_title = safe_title.replace(
                safe_highlight, "<span class='cm-grad'>" + safe_highlight + "</span>", 1
            )
    chips = "".join("<span class='cm-chip'>" + _esc(b) + "</span>" for b in (badges or []))
    acts = ""
    if actions:
        parts = []
        for item in actions:
            primary = bool(item.get("primary"))
            parts.append(
                "<a class='cm-btn " + ("primary" if primary else "ghost") + "' href='"
                + _href(str(item.get("href", "/"))) + "' target='_top'>" + _esc(item.get("label", "进入"))
                + " <span>→</span></a>"
            )
        acts = "<div class='cm-hero-actions'>" + "".join(parts) + "</div>"
    idx = index
    if idx is None:
        first = str(kicker).split("/")[0].strip()
        idx = first if first.isdigit() else "00"
    st.markdown(
        "<section class='cm-hero'>"
        "<div class='cm-aurora'></div><div class='cm-mesh'></div><div class='cm-shine'></div>"
        "<div class='cm-hero-index'>" + _esc(idx) + "</div>"
        "<div class='cm-hero-inner'>"
        "<div class='cm-kicker'><span class='dot'></span>" + _esc(kicker) + "</div>"
        "<div class='cm-display" + (" long" if len(str(title)) >= 14 else "") + "'>" + safe_title + "</div>"
        "<div class='cm-sub'>" + _esc(subtitle) + "</div>"
        + chips + acts
        + "</div>"
        "<div class='cm-hero-meta'><b>READ-ONLY DATA PLANE</b><br>SCROLL TO EXPLORE ↓</div>"
        "</section>",
        unsafe_allow_html=True,
    )


def hero_compact(title: str, subtitle: str, kicker: str = "CARBON LEDGER",
                 badges: Optional[Iterable[str]] = None) -> None:
    """紧凑页头：给地图/实验台留出首屏高度，视觉与 hero 同源。"""
    safe_title = _esc(title)
    chips = "".join("<span class='cm-chip'>" + _esc(b) + "</span>" for b in (badges or []))
    first = str(kicker).split("/")[0].strip()
    idx = first if first.isdigit() else "00"
    st.markdown(
        "<section class='cm-hero compact'>"
        "<div class='cm-aurora'></div><div class='cm-mesh'></div>"
        "<div class='cm-hero-index'>" + _esc(idx) + "</div>"
        "<div class='cm-hero-inner'>"
        "<div class='cm-kicker'><span class='dot'></span>" + _esc(kicker) + "</div>"
        "<div class='cm-display'>" + safe_title + "</div>"
        "<div class='cm-sub'>" + _esc(subtitle) + "</div>"
        + chips
        + "</div></section>",
        unsafe_allow_html=True,
    )


def section(title: str, desc: str = "") -> None:
    st.markdown(
        "<div class='cm-sec'><span class='t'>" + _esc(title) + "</span>"
        + ("<span class='d'>" + _esc(desc) + "</span>" if desc else "")
        + "</div>",
        unsafe_allow_html=True,
    )


def conclusion(text: str) -> None:
    st.markdown("<div class='cm-conclusion'>" + text + "</div>", unsafe_allow_html=True)


def demo_note(reason: str = "") -> None:
    """演示数据提示条：不遮挡内容，但必须一眼看见。"""
    tail = ("　<span style='color:#98A3B3'>" + _esc(reason) + "</span>") if reason else ""
    st.markdown(
        "<div class='cm-demo'><b>演示数据</b>　当前结果表由内置合成样本生成，"
        "用于前端联调与答辩兜底，不代表真数据口径。真数据放入 <code>data/raw/</code> 后"
        "重跑 <code>python -m src.pipeline</code> 自动切换。" + tail + "</div>",
        unsafe_allow_html=True,
    )


def marquee(items: Iterable[str]) -> None:
    """横向滚动数据带（纯 CSS，无 JS）。"""
    seq = "".join("<span><b>" + _esc(x) + "</b><i>✦</i></span>" for x in items)
    st.markdown(
        "<div class='cm-marquee'><div class='cm-marquee-track'>" + seq + seq + "</div></div>",
        unsafe_allow_html=True,
    )


def cta(title: str, subtitle: str, primary: Dict[str, str],
        secondary: Optional[Dict[str, str]] = None) -> None:
    actions = [
        {"label": primary.get("label", "开始"), "href": primary.get("href", "/基准核算"), "primary": True}
    ]
    if secondary:
        actions.append({"label": secondary.get("label", "路线实验室"),
                        "href": secondary.get("href", "/路线实验室"), "primary": False})
    act = "".join(
        "<a class='cm-btn " + ("primary" if a["primary"] else "ghost") + "' href='"
        + _href(a["href"]) + "' target='_top'>" + _esc(a["label"]) + " <span>→</span></a>" for a in actions
    )
    st.markdown(
        "<div class='cm-cta'><div class='k'>READY FOR DEFENSE</div>"
        "<h2>" + _esc(title) + "</h2><p>" + _esc(subtitle) + "</p>"
        "<div class='cm-hero-actions'>" + act + "</div></div>",
        unsafe_allow_html=True,
    )
