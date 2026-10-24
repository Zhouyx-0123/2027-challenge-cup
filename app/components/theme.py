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
from urllib.parse import quote, urlencode

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

/* 同源整页切换时用浏览器 View Transition 做交叉淡入，避免旧页面闪回 */
@view-transition { navigation: auto; }
::view-transition-old(root), ::view-transition-new(root) { animation-duration: .32s; animation-timing-function: cubic-bezier(.22,.61,.36,1); }

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
.cm-brand .sub { display: block; font-family: var(--mono); font-size: 7.5px; letter-spacing: 0.10em; color: var(--muted-2); margin-top: 1px; white-space: nowrap; }
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
.cm-hero-inner { position: relative; z-index: 2; max-width: 1000px; animation: cmReveal .34s ease both; }
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
/* 第 6 页模式切换：segmented_control，只渲染当前模式，避免双地图同时挂载 */
div[data-testid="stButtonGroup"] { gap: 6px; margin: 2px 0 8px 0; }
div[data-testid="stButtonGroup"] button[role="radio"] {
  border: 1px solid var(--line); border-radius: 999px;
  background: rgba(255,255,255,0.018); color: var(--muted);
  font-size: 11.5px; min-height: 0; padding: 6px 13px;
}
div[data-testid="stButtonGroup"] button[role="radio"]:hover { border-color: rgba(94,231,255,0.35); color: var(--text); }
div[data-testid="stButtonGroup"] button[role="radio"][aria-checked="true"] {
  background: linear-gradient(135deg, rgba(94,231,255,0.20), rgba(255,196,107,0.16));
  border-color: rgba(94,231,255,0.55); color: var(--text);
  box-shadow: inset 0 0 0 1px rgba(94,231,255,0.10);
}
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

/* ---------- 数据台账卡（替代 st.dataframe） ---------- */
.cm-table { border: 1px solid var(--line); border-radius: 20px; overflow: hidden;
  background: linear-gradient(155deg, rgba(255,255,255,0.035), rgba(255,255,255,0.012));
  margin: 6px 0 14px 0; box-shadow: 0 18px 50px rgba(0,0,0,0.20); }
.cm-table-top { display: flex; align-items: baseline; justify-content: space-between; gap: 12px;
  padding: 14px 17px 11px 17px; border-bottom: 1px solid var(--line); }
.cm-table-top .t { font-family: var(--display); font-size: 14px; font-weight: 700; letter-spacing: -0.02em; }
.cm-table-top .d { color: var(--muted); font-size: 10.5px; margin-left: 8px; }
.cm-table-top .n { color: var(--muted-2); font-family: var(--mono); font-size: 9px; white-space: nowrap; }
.cm-table-scroll { overflow: auto; max-height: 540px; }
.cm-table-grid { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 11.5px; font-variant-numeric: tabular-nums; }
.cm-table-grid thead th { position: sticky; top: 0; z-index: 2; background: #11151C;
  color: var(--muted); font-family: var(--mono); font-size: 8.5px; letter-spacing: 0.08em;
  text-transform: uppercase; font-weight: 400; text-align: left; padding: 10px 12px;
  border-bottom: 1px solid var(--line-2); white-space: nowrap; }
.cm-table-grid thead th.num { text-align: right; }
.cm-table-grid tbody td { padding: 10px 12px; border-bottom: 1px solid rgba(255,255,255,0.045);
  color: var(--text); vertical-align: top; }
.cm-table-grid tbody td.num { text-align: right; font-variant-numeric: tabular-nums; }
.cm-table-grid tbody tr:last-child td { border-bottom: none; }
.cm-table-grid tbody tr:hover td { background: rgba(94,231,255,0.038); }
.cm-table-grid tbody tr.main td { background: rgba(255,196,107,0.065); }
.cm-table-grid tbody tr.main td:first-child { box-shadow: inset 2px 0 0 var(--amber); }
.cm-table-grid tbody tr.fail td { background: rgba(255,107,107,0.06); }
.cm-table-foot { padding: 8px 16px; border-top: 1px solid var(--line); color: var(--muted-2); font-size: 9.5px; }
.cm-pill { display: inline-flex; align-items: center; padding: 2px 8px; border-radius: 999px;
  font-family: var(--mono); font-size: 8.5px; font-weight: 700; letter-spacing: 0.02em; }
.cm-pill.ok { color: #A7F3DE; background: rgba(45,212,167,0.12); border: 1px solid rgba(45,212,167,0.35); }
.cm-pill.bad { color: #FFB3B3; background: rgba(255,107,107,0.12); border: 1px solid rgba(255,107,107,0.35); }
.cm-pill.skip { color: #B7C2CE; background: rgba(152,163,179,0.10); border: 1px solid rgba(152,163,179,0.28); }
.cm-pill.warn { color: #FFE0A0; background: rgba(255,196,107,0.12); border: 1px solid rgba(255,196,107,0.35); }
.cm-mut { color: var(--muted-2); }

/* 兼容旧 cards.htable 的台账表：同样走圆角玻璃卡 */
.cm-table .cm-htable { border: none; border-radius: 0; }
.cm-table-plain { margin-top: 6px; }
.cm-htable { border-collapse: separate; border-spacing: 0; width: 100%; overflow: hidden;
  border: 1px solid var(--line); border-radius: 16px; background: rgba(255,255,255,0.018); font-size: 11.5px; }
.cm-htable thead th { background: #11151C; color: var(--muted); font-family: var(--mono);
  font-size: 8.5px; letter-spacing: 0.08em; text-transform: uppercase; font-weight: 400;
  padding: 10px 12px; border-bottom: 1px solid var(--line-2); }
.cm-htable tbody td { padding: 10px 12px; border-bottom: 1px solid rgba(255,255,255,0.045); }
.cm-htable tbody tr:last-child td { border-bottom: none; }
.cm-htable tbody tr:hover td { background: rgba(94,231,255,0.038); }
.cm-htable tbody tr.base td { background: rgba(94,231,255,0.06); }
.cm-htable tbody tr.main td { background: rgba(255,196,107,0.065); }
.cm-htable tbody tr.main td:first-child { box-shadow: inset 2px 0 0 var(--amber); }
.cm-htable tbody tr.fail td { background: rgba(255,107,107,0.06); }

/* ---------- 计算器结果图 ---------- */
.cm-savings { border: 1px solid var(--line); border-radius: 18px; background: rgba(255,255,255,0.022); padding: 16px 18px 14px 18px; margin: 8px 0 14px 0; }
.cm-savings-h { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; margin-bottom: 10px;
  font-family: var(--mono); font-size: 9px; letter-spacing: 0.10em; color: var(--muted); text-transform: uppercase; }
.cm-savings-row { display: grid; grid-template-columns: 90px 1fr 74px; align-items: center; gap: 12px; margin: 7px 0; }
.cm-savings-row .lbl { color: var(--muted); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cm-savings-row .track { height: 14px; border-radius: 999px; background: rgba(255,255,255,0.045); overflow: hidden; }
.cm-savings-row .track i { display: block; height: 100%; border-radius: 999px; }
.cm-savings-row .track i.ice { background: linear-gradient(90deg, rgba(255,122,92,0.42), var(--coral)); }
.cm-savings-row .track i.bev { background: linear-gradient(90deg, rgba(94,231,255,0.35), var(--tide)); }
.cm-savings-row .val { text-align: right; font-family: var(--display); font-size: 17px; font-variant-numeric: tabular-nums; }
.cm-savings-delta { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--line); font-size: 11px; color: var(--muted); }
.cm-savings-delta b { color: var(--amber); font-size: 14px; }
.cm-savings-delta span { color: var(--muted-2); font-size: 9.5px; margin-left: 8px; }

.cm-npv { border: 1px solid var(--line); border-radius: 20px; background: linear-gradient(155deg, rgba(255,255,255,0.035), rgba(255,255,255,0.012)); padding: 18px 20px 14px 20px; margin: 6px 0 12px 0; }
.cm-npv-h { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; margin-bottom: 9px; }
.cm-npv-h span:first-child { font-family: var(--display); font-size: 16px; font-weight: 700; letter-spacing: -0.02em; }
.cm-npv-h span:last-child { font-family: var(--mono); font-size: 8.5px; color: var(--muted-2); }
.cm-npv-legend { display: flex; flex-wrap: wrap; gap: 14px; margin: 4px 0 14px 0; font-size: 10px; color: var(--muted); }
.cm-npv-legend span { display: inline-flex; align-items: center; gap: 6px; }
.cm-npv-legend i { width: 8px; height: 8px; border-radius: 2px; display: inline-block; }
.cm-npv-row { display: grid; grid-template-columns: 64px 1fr 74px; align-items: center; gap: 12px; margin: 9px 0; }
.cm-npv-name { color: var(--muted); font-size: 11px; white-space: nowrap; }
.cm-npv-track { height: 28px; border-radius: 999px; background: rgba(255,255,255,0.04); overflow: hidden; display: flex; }
.cm-npv-seg { display: flex; align-items: center; justify-content: center; min-width: 0; height: 100%;
  color: #04121F; font-family: var(--mono); font-size: 8px; font-weight: 700; overflow: hidden; white-space: nowrap;
  border-right: 1px solid rgba(7,8,10,0.18); }
.cm-npv-seg:last-child { border-right: none; }
.cm-npv-total { text-align: right; font-family: var(--display); font-size: 19px; font-weight: 700; font-variant-numeric: tabular-nums; }
.cm-npv-foot { margin-top: 11px; padding-top: 9px; border-top: 1px solid var(--line); color: var(--muted-2); font-size: 9.5px; line-height: 1.6; }
.cm-line { border: 1px solid var(--line); border-radius: 20px; background: linear-gradient(155deg, rgba(255,255,255,0.035), rgba(255,255,255,0.012)); padding: 18px 20px 14px 20px; margin: 6px 0 12px 0; }
.cm-line-h { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; margin-bottom: 6px; }
.cm-line-h span:first-child { font-family: var(--display); font-size: 16px; font-weight: 700; letter-spacing: -0.02em; }
.cm-line-h span:last-child { font-family: var(--mono); font-size: 8.5px; color: var(--muted-2); }
.cm-line-legend { display: flex; flex-wrap: wrap; gap: 14px; margin: 2px 0 2px 0; font-size: 10px; color: var(--muted); }
.cm-line-legend span { display: inline-flex; align-items: center; gap: 6px; }
.cm-line-legend i { width: 8px; height: 8px; border-radius: 2px; display: inline-block; }
.cm-line-svg { width: 100%; height: auto; display: block; overflow: visible; margin: 4px 0 0 0; }
.cm-line-grid { stroke: rgba(255,255,255,0.06); stroke-width: 1; }
.cm-line-ytxt, .cm-line-xtxt { fill: var(--muted-2); font-family: var(--mono); font-size: 9px; }
.cm-line-path { fill: none; stroke-width: 2.6; stroke-linecap: round; stroke-linejoin: round; }
.cm-line-area { opacity: 0.10; }
.cm-line-dot { stroke: #07080A; stroke-width: 1.2; }
.cm-line-last { font-family: var(--mono); font-size: 9px; font-weight: 700; }
.cm-line-foot { margin-top: 6px; padding-top: 9px; border-top: 1px solid var(--line); color: var(--muted-2); font-size: 9.5px; }
@media (max-width: 760px) {
  .cm-savings-row { grid-template-columns: 62px 1fr 56px; gap: 8px; }
  .cm-npv-row { grid-template-columns: 52px 1fr 56px; gap: 8px; }
  .cm-npv-seg { font-size: 0; }
  .cm-npv { padding: 14px 14px 12px 14px; }
}

/* ---------- 浮动产品顶栏（Coolors 式卡片） ---------- */
div[class*="st-key-cm_header_card"] {
  position: relative; isolation: isolate; overflow: hidden;
  margin: 8px 0 16px 0; padding: 11px 16px 10px 16px;
  border: 1px solid rgba(255,255,255,0.10); border-radius: 22px;
  background:
    radial-gradient(650px 130px at 10% 0%, rgba(94,231,255,0.10), transparent 70%),
    linear-gradient(180deg, rgba(255,255,255,0.055), rgba(255,255,255,0.018)),
    rgba(10,12,17,0.82);
  box-shadow: 0 20px 60px rgba(0,0,0,0.36), inset 0 1px 0 rgba(255,255,255,0.06);
  backdrop-filter: blur(18px);
}
div[class*="st-key-cm_header_card"]:before {
  content: ""; position: absolute; left: 0; right: 0; top: 0; height: 3px; z-index: 0;
  background: linear-gradient(90deg, #5EE7FF 0%, #2DD4A7 24%, #FFC46B 55%, #FF7A5C 76%, #8FB8FF 100%);
  opacity: 0.92;
}
div[class*="st-key-cm_header_card"] [data-testid="stHorizontalBlock"] { position: relative; z-index: 1; align-items: center; }
div[class*="st-key-cm_header_card"] div[class*="st-key-cm_nav_row"] {
  border: none; background: transparent; padding: 0; border-radius: 0;
}
.cm-head-status { display: flex; justify-content: flex-end; gap: 6px; flex-wrap: wrap; }
.cm-head-pill { display: inline-flex; align-items: center; gap: 5px; padding: 4px 8px; border-radius: 999px;
  border: 1px solid var(--line); color: var(--muted); font-family: var(--mono); font-size: 8px;
  letter-spacing: 0.04em; white-space: nowrap; background: rgba(255,255,255,0.022); }
.cm-head-pill.live { color: var(--tide); border-color: rgba(94,231,255,0.24); }
.cm-head-pill.ok { color: var(--tide-2); border-color: rgba(45,212,167,0.28); }

/* ---------- 按钮 / 导航 / 控制台精修 ---------- */
/* 原生 page_link 导航：保留胶囊视觉，但走 Streamlit 客户端路由 */
div[data-testid="stPageLink"] { display: inline-flex; margin: 0 !important; }
div[data-testid="stPageLink"] > div { margin: 0 !important; }
div[data-testid="stPageLink"] a[data-testid="stPageLink-NavLink"] {
  display: inline-flex; align-items: center; padding: 6px 10px; border-radius: 999px;
  border: 1px solid transparent; text-decoration: none !important; transition: all .18s ease;
}
div[data-testid="stPageLink"] a p { margin: 0 !important; font-size: 11.5px !important; font-weight: 600; color: var(--muted); }
div[data-testid="stPageLink"] a:hover { background: rgba(255,255,255,0.05); }
div[data-testid="stPageLink"] a:hover p { color: var(--text); }
div[data-testid="stPageLink"] a[aria-disabled="true"],
div[data-testid="stPageLink"] a[disabled] {
  background: linear-gradient(100deg, var(--tide), #8FB8FF) !important;
  box-shadow: 0 0 20px rgba(94,231,255,0.26); cursor: default;
}
div[data-testid="stPageLink"] a[aria-disabled="true"] p,
div[data-testid="stPageLink"] a[disabled] p { color: #04121F !important; font-weight: 700; }
div[class*="st-key-cm_nav_row"] { gap: 2px; align-items: center; width: fit-content; max-width: 100%;
  padding: 3px; border: 1px solid var(--line); border-radius: 999px; background: rgba(255,255,255,0.018); }
.cm-nav-rule { border-bottom: 1px solid var(--line); margin: -2px 0 14px 0; }

.cm-nav-links { gap: 3px; padding: 3px; border: 1px solid var(--line); border-radius: 999px; background: rgba(255,255,255,0.018); }
.cm-nav-link { font-size: 11.5px; font-weight: 600; padding: 7px 11px; border: 1px solid transparent; }
.cm-nav-link.on { background: linear-gradient(100deg, var(--tide), #8FB8FF); color: #04121F; box-shadow: 0 0 20px rgba(94,231,255,0.26); }

/* 控制台收成一枚右上角胶囊；点开是浮层参数卡 */
div[data-testid="stLayoutWrapper"]:has(div[data-testid="stPopover"]) { margin-left: auto; }
div[data-testid="stElementContainer"]:has(div[data-testid="stPopover"]) { display: flex; justify-content: flex-end; }
div[data-testid="stPopover"] { display: flex; justify-content: flex-end; margin: -2px 0 18px auto !important; width: fit-content !important; }
div[data-testid="stPopover"] > button {
  border-radius: 999px !important; border: 1px solid rgba(94,231,255,0.28) !important;
  background: rgba(94,231,255,0.065) !important; color: var(--foam) !important;
  padding: 8px 16px !important; min-height: 36px; font-family: var(--mono);
  font-size: 9.5px !important; letter-spacing: 0.03em; box-shadow: 0 0 22px rgba(94,231,255,0.08);
}
div[data-testid="stPopover"] > button:hover { border-color: rgba(94,231,255,0.55) !important; background: rgba(94,231,255,0.10) !important; }
div[data-testid="stPopoverBody"] div[data-testid="stButton"] { display: flex; justify-content: flex-end; }
div[data-testid="stPopoverBody"] button { width: auto !important; }
div[data-testid="stPopoverBody"] {
  background: #11151C !important; border: 1px solid rgba(94,231,255,0.24) !important;
  border-radius: 18px !important; box-shadow: 0 24px 70px rgba(0,0,0,0.55) !important;
  padding: 16px !important; min-width: 330px;
}
div[data-testid="stExpander"] { max-width: 100%; margin: 0 0 14px 0; }
div[data-testid="stExpander"] details { border-radius: 999px; background: rgba(94,231,255,0.055); border: 1px solid rgba(94,231,255,0.22); overflow: hidden; }
div[data-testid="stExpander"] summary { padding: 8px 15px; color: var(--foam); font-size: 9.5px; letter-spacing: 0.03em; }
div[data-testid="stExpander"] summary:hover { color: #fff; background: rgba(94,231,255,0.06); }
div[data-testid="stExpander"] details[open] { border-radius: 18px; background: var(--panel); border-color: rgba(94,231,255,0.26); }
div[data-testid="stExpander"] details[open] summary { border-bottom: 1px solid var(--line); border-radius: 18px 18px 0 0; }

/* 所有 Streamlit 按钮统一胶囊；主按钮走潮汐渐变 */
button[data-testid="stBaseButton-primary"], button[kind="primary"],
button[data-testid="stBaseButton-secondary"], button[kind="secondary"],
div[data-testid="stDownloadButton"] button {
  min-height: 38px; border-radius: 999px !important; padding: 8px 18px !important;
  font-size: 12px !important; font-weight: 700 !important; letter-spacing: 0.01em;
  transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease;
}
button[data-testid="stBaseButton-primary"], button[kind="primary"] {
  background: linear-gradient(100deg, var(--tide), var(--tide-2)) !important;
  color: #04121F !important; border: none !important;
  box-shadow: 0 8px 28px rgba(94,231,255,0.24) !important;
}
button[data-testid="stBaseButton-primary"]:hover, button[kind="primary"]:hover {
  transform: translateY(-1px); box-shadow: 0 12px 34px rgba(94,231,255,0.34) !important;
}
button[data-testid="stBaseButton-secondary"], button[kind="secondary"], div[data-testid="stDownloadButton"] button {
  background: rgba(255,255,255,0.035) !important; border: 1px solid var(--line-2) !important; color: var(--text) !important;
}
div[data-testid="stDownloadButton"] { display: flex; }
div[data-testid="stDownloadButton"] button { width: auto !important; min-width: 168px; max-width: 240px; }
button[data-testid="stBaseButton-secondary"]:hover, div[data-testid="stDownloadButton"] button:hover {
  transform: translateY(-1px); border-color: rgba(94,231,255,0.55) !important; background: rgba(94,231,255,0.07) !important;
}

/* radio 做成胶囊分段控件 */
div[data-testid="stRadio"] div[role="radiogroup"] { gap: 7px; flex-wrap: wrap; }
div[data-testid="stRadio"] label {
  margin: 0 !important; padding: 6px 11px 6px 9px; border: 1px solid var(--line);
  border-radius: 999px; background: rgba(255,255,255,0.028); transition: all .18s ease;
}
div[data-testid="stRadio"] label:has(input:checked) {
  border-color: rgba(94,231,255,0.55); background: rgba(94,231,255,0.11);
  box-shadow: 0 0 18px rgba(94,231,255,0.12);
}
div[data-testid="stRadio"] label p { font-size: 11.5px !important; }
div[data-testid="stRadio"] label:hover { border-color: rgba(94,231,255,0.35); }

/* tabs 也做成小胶囊 */
button[data-testid="stTab"] { padding: 7px 13px !important; border-radius: 999px !important; border: 1px solid transparent !important; }
button[data-testid="stTab"][aria-selected="true"] { background: rgba(94,231,255,0.10) !important; border-color: rgba(94,231,255,0.30) !important; }

/* Streamlit 运行态：顶部 2px 潮汐进度条，切页时给即时反馈 */
div[data-testid="stStatusWidget"] { position: fixed !important; top: 0 !important; left: 0 !important;
  width: 100% !important; height: 2px !important; z-index: 9999 !important; padding: 0 !important;
  background: linear-gradient(90deg, var(--tide), var(--tide-2), #8FB8FF, var(--tide));
  background-size: 300% 100%; animation: cmLoading 1.2s linear infinite; }
div[data-testid="stStatusWidget"] * { display: none !important; }
@keyframes cmLoading { from { background-position: 0% 0; } to { background-position: 300% 0; } }

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
  .cm-nav-status, .cm-head-status { display: none; }
  div[class*="st-key-cm_header_card"] { padding: 10px 12px 9px 12px; border-radius: 18px; }
  div[class*="st-key-cm_nav_row"] { flex-wrap: nowrap; overflow-x: auto; width: 100%; border-radius: 16px; }
  div[class*="st-key-cm_nav_row"]::-webkit-scrollbar { display: none; }
  div[data-testid="stPageLink"] { flex: 0 0 auto; }
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
    {"id": "0", "label": "概览", "href": "/", "path": "app.py"},
    {"id": "1", "label": "核算", "href": "/基准核算", "path": "pages/1_基准核算.py"},
    {"id": "2", "label": "诊断", "href": "/高排放诊断", "path": "pages/2_高排放诊断.py"},
    {"id": "3", "label": "方案", "href": "/替代方案", "path": "pages/3_替代方案.py"},
    {"id": "4", "label": "成本", "href": "/成本效益", "path": "pages/4_成本效益.py"},
    {"id": "5", "label": "台账", "href": "/碳会计台账", "path": "pages/5_碳会计台账.py"},
    {"id": "6", "label": "路线", "href": "/路线实验室", "path": "pages/6_路线实验室.py"},
    {"id": "7", "label": "测算", "href": "/电动化测算", "path": "pages/7_电动化测算.py"},
    {"id": "8", "label": "TCO", "href": "/车队TCO沙盘", "path": "pages/8_车队TCO沙盘.py"},
]


def inject() -> None:
    """注入全局样式。

    不做 session_state 去重：浏览器刷新/重连后 DOM 会重建，但 session_state
    可能还在；一旦跳过注入，首屏就会退化成无 CSS 的裸 HTML。CSS 每次 rerun
    重新注入的成本很低，稳定优先。
    """
    st.markdown(CSS, unsafe_allow_html=True)


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _href(path: str) -> str:
    """页面链接；把 λ / 成本口径带在 query 上，target=_top 全量刷新也不丢参数。"""
    qs = ""
    try:
        lam = st.session_state.get("lam")
        basis = st.session_state.get("basis")
        payload = {}
        if lam is not None:
            payload["lam"] = "%g" % float(lam)
        if basis:
            payload["basis"] = str(basis)
        qs = urlencode(payload)
    except Exception:
        qs = ""
    return quote(path, safe="/") + (("?" + qs) if qs else "")


def top_nav(current: str = "", source_name: str = "", gate: Optional[Dict[str, Any]] = None) -> None:
    """浮动产品顶栏：品牌 + 原生 page_link 导航 + 实时状态。

    不用通栏公告条；整块收进一张玻璃卡片，顶部用一条多色 palette 条
    做 Coolors 式产品视觉。页面链接走 st.page_link，客户端路由不重载。
    """
    current = str(current or "0")
    source = "REAL" if source_name == "real" else "DEMO"
    gate_text = "GATE PASS" if gate and gate.get("passed") else ("GATE CHECK" if gate else "GATE —")

    with st.container(key="cm_header_card"):
        c_brand, c_links, c_status = st.columns([0.18, 0.68, 0.14], gap="small",
                                                vertical_alignment="center")
        with c_brand:
            st.markdown(
                "<div class='cm-brand'>"
                "<span class='mark'>碳</span>"
                "<span><span class='word'>碳慧<em>智算</em></span>"
                "<span class='sub'>CARBON INTELLIGENCE</span></span>"
                "</div>", unsafe_allow_html=True,
            )
        with c_links:
            with st.container(horizontal=True, gap="small", key="cm_nav_row"):
                for item in PAGES:
                    if item["id"] == "0":
                        cls = "cm-nav-link on" if current == "0" else "cm-nav-link"
                        st.markdown(
                            "<a class='" + cls + "' href='/' target='_top'>概览</a>",
                            unsafe_allow_html=True,
                        )
                        continue
                    try:
                        st.page_link(
                            item["path"],
                            label=item["label"],
                            disabled=(item["id"] == current),
                        )
                    except Exception:
                        on = " on" if item["id"] == current else ""
                        st.markdown(
                            "<a class='cm-nav-link" + on + "' href='"
                            + _href(item["href"]) + "' target='_top'>"
                            + _esc(item["label"]) + "</a>",
                            unsafe_allow_html=True,
                        )
        with c_status:
            st.markdown(
                "<div class='cm-head-status'>"
                "<span class='cm-head-pill live'>● " + source + "</span>"
                "<span class='cm-head-pill ok'>" + gate_text + "</span>"
                "</div>", unsafe_allow_html=True,
            )


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
