# -*- coding: utf-8 -*-
"""高级滑条 + 联动可操作图（Slider Lab）。

为什么不用 st.slider 原生控件：
  · Streamlit 1.4x 的滑条是 react-aria 实现，DOM 里没有 [role="slider"]，
    theme.py 里旧的滑条样式选择器全部落空（这正是它看起来"土"的根因）；
  · react-aria 的原生渲染是「细线 + 小圆点 + 文字气泡」，与本项目的暗色
    电影感语言（无边界玻璃、发丝环、潮汐高光）不匹配；
  · Streamlit 的滑条只在 pointerup 时提交（onChangeEnd），拖动过程中无法
    驱动旁边的图做实时联动。

本组件的做法：
  1. 页面里保留一个「载体滑条」——它是 Streamlit 认识的值载体，
     被 CSS 隐藏（opacity:0 + 绝对定位，保留可测量的布局宽度）；
  2. 可见部分是一段自绘滑条（iframe 内，HTML/CSS/JS，无任何外部依赖）：
     玻璃轨道 + 渐变填充 + 发丝环发光旋钮 + 跟随数值气泡 + 刻度尺；
  3. 旁边的图是同 iframe 内的手写 SVG：hover 十字线与浮层、点击写值、
     游标随滑条实时移动——拖动时 60fps 本地更新，不走服务端往返；
  4. 松手时用**真实 pointer 事件**把值写回载体滑条（pointerdown+pointerup
     落在轨道对应位置），Streamlit 的滑条按原生逻辑收到值并触发一次 rerun。

第 4 步不依赖任何 React 内部 API：事件走标准 DOM 冒泡，与用户真实拖拽
等价，因此不脆弱。写回后会回读 input.value 做校验，不一致时用割线法
二次校准（见 JS 里的 aimAt）。

设计参考（调研结论）：
  · Radix / shadcn：track + indicator + thumb 三段式，thumb 用 ring 而非 border；
  · Carbon Design：刻度尺 + 当前值标签同层；
  · 玻璃滑块实践：backdrop-filter 只用在滑块自身、不放在轨道父级，
    模糊半径 ≤ 10px，thumb 用 will-change:transform 提升到独立合成层。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import streamlit as st
from streamlit.components import v1 as components
# 暗色电影感调色板（与 theme.py 的 :root 令牌同源，这里是 iframe 内的副本）
PALETTE = {
    "ink": "#07080A",
    "ink2": "#0B0D10",
    "surface": "#101318",
    "bone": "#F3F5F8",
    "mist": "#9AA2AD",
    "dim": "#5B636E",
    "foam": "#8BF3E6",
    "tide": "#46B7FF",
    "coral": "#FF6B6B",
    "amber": "#F5B942",
}

# 载体滑条隐藏 + 容器定位（写在页面文档里，作用域限定到本组件的 st-key）
#
# 关键：不能 display:none / visibility:hidden —— 那样滑条没有布局盒，
# 轨道宽度为 0，坐标→数值的映射就失真了。这里用「绝对定位 + 宽度撑满 +
# 透明」的方式：既不占位、不可见、不接收指针，又保留可测量的布局宽度。
CARRIER_CSS = """
<style>
div[class*="st-key-{ckey}"], .st-key-{ckey}{{ position:relative; }}
div[class*="st-key-{ckey}"] [data-testid="stSlider"],
.st-key-{ckey} [data-testid="stSlider"]{{
  position:absolute !important; left:0; right:0; top:0;
  height:0 !important; overflow:hidden !important;
  opacity:0 !important; pointer-events:none !important;
  margin:0 !important; padding:0 !important;
}}
</style>
"""

LAB_CSS = """
<style>
*{{ box-sizing:border-box; }}
html,body{{ margin:0; padding:0; background:transparent; }}
body{{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
              "Hiragino Sans GB","Microsoft YaHei",sans-serif;
  color:{bone}; -webkit-font-smoothing:antialiased;
}}
.lab{{ display:grid; grid-template-columns:minmax(240px,300px) 1fr;
       gap:26px; align-items:stretch; height:100%; }}
.lab-narrow{{ grid-template-columns:1fr; grid-template-rows:auto 1fr; }}

/* ---------------- 滑条 ---------------- */
.sl{{ display:flex; flex-direction:column; justify-content:center; gap:20px;
      padding:20px 22px; border-radius:18px;
      background:linear-gradient(155deg,rgba(255,255,255,.055),rgba(255,255,255,.012));
      -webkit-backdrop-filter:blur(10px); backdrop-filter:blur(10px);
      box-shadow:0 18px 44px rgba(0,0,0,.45), 0 0 0 1px rgba(255,255,255,.08); }}
.sl-head{{ display:flex; align-items:flex-end; justify-content:space-between; gap:12px; }}
.sl-title{{ font-size:11px; font-weight:700; letter-spacing:.18em;
            text-transform:uppercase; color:{dim}; }}
.sl-read{{ display:flex; align-items:baseline; gap:5px; }}
.sl-read b{{ font-size:30px; font-weight:700; letter-spacing:-.02em; line-height:1;
             color:{bone}; font-variant-numeric:tabular-nums;
             text-shadow:0 0 26px rgba(70,183,255,.35); }}
.sl-read i{{ font-style:normal; font-size:12px; color:{mist}; }}
.sl-sub{{ font-size:11.5px; color:{dim}; line-height:1.6; }}

.sl-track{{ position:relative; height:34px; cursor:grab; touch-action:none;
            outline:none; }}
.sl-track:active{{ cursor:grabbing; }}
.sl-rail{{ position:absolute; left:0; right:0; top:50%; height:8px;
           transform:translateY(-50%); border-radius:999px;
           background:rgba(255,255,255,.07);
           box-shadow:inset 0 1px 2px rgba(0,0,0,.55), 0 0 0 1px rgba(255,255,255,.05); }}
.sl-fill{{ position:absolute; left:0; top:50%; height:8px; transform:translateY(-50%);
           border-radius:999px; width:0;
           background:linear-gradient(90deg,{tide},{foam});
           box-shadow:0 0 18px rgba(70,183,255,.45), 0 0 0 1px rgba(139,243,230,.22); }}
.sl-glow{{ position:absolute; top:50%; width:120px; height:120px; border-radius:50%;
           transform:translate(-50%,-50%); pointer-events:none; opacity:0;
           background:radial-gradient(circle,rgba(70,183,255,.30),rgba(70,183,255,0) 68%);
           transition:opacity .22s cubic-bezier(.22,1,.36,1); }}
.sl-track.drag .sl-glow{{ opacity:1; }}
.sl-tick{{ position:absolute; top:50%; width:2px; height:2px; margin-top:9px;
           border-radius:50%; background:rgba(255,255,255,.26); transform:translateX(-1px); }}
.sl-knob{{ position:absolute; top:50%; left:0; width:22px; height:22px;
           transform:translate(-50%,-50%); border-radius:50%;
           background:radial-gradient(circle at 34% 30%,#FFFFFF 0%,#DDE7F0 46%,#9FC4DE 100%);
           box-shadow:0 0 0 1px rgba(255,255,255,.55), 0 0 0 6px rgba(70,183,255,.16),
                      0 6px 18px rgba(0,0,0,.55), 0 0 26px rgba(70,183,255,.45);
           transition:transform .2s cubic-bezier(.22,1,.36,1),
                      box-shadow .2s cubic-bezier(.22,1,.36,1);
           will-change:transform; }}
.sl-track:hover .sl-knob{{ transform:translate(-50%,-50%) scale(1.10); }}
.sl-track.drag .sl-knob{{ transform:translate(-50%,-50%) scale(1.18);
           box-shadow:0 0 0 1px rgba(255,255,255,.7), 0 0 0 9px rgba(70,183,255,.20),
                      0 8px 22px rgba(0,0,0,.6), 0 0 34px rgba(70,183,255,.65); }}
.sl-track:focus-visible .sl-knob{{ box-shadow:0 0 0 1px #fff, 0 0 0 7px rgba(139,243,230,.55),
           0 0 30px rgba(70,183,255,.7); }}
.sl-bubble{{ position:absolute; bottom:26px; left:50%; transform:translateX(-50%) scale(.9);
            padding:4px 10px; border-radius:999px; white-space:nowrap;
            font-size:12px; font-weight:700; font-variant-numeric:tabular-nums;
            color:{ink}; background:linear-gradient(140deg,{foam},{tide});
            box-shadow:0 6px 18px rgba(0,0,0,.5), 0 0 0 1px rgba(255,255,255,.28);
            opacity:0; transition:opacity .2s cubic-bezier(.22,1,.36,1),
                                 transform .2s cubic-bezier(.22,1,.36,1); }}
.sl-track:hover .sl-bubble,.sl-track.drag .sl-bubble,.sl-track:focus-visible .sl-bubble{{
            opacity:1; transform:translateX(-50%) scale(1); }}
.sl-scale{{ display:flex; justify-content:space-between; margin-top:-8px;
            font-size:10.5px; color:{dim}; font-variant-numeric:tabular-nums; }}
.sl-hint{{ font-size:11px; color:{dim}; line-height:1.7; }}

/* ---------------- 图 ---------------- */
.ch{{ position:relative; display:flex; flex-direction:column; min-width:0;
      padding:18px 20px 14px; border-radius:18px;
      background:linear-gradient(155deg,rgba(255,255,255,.05),rgba(255,255,255,.01));
      -webkit-backdrop-filter:blur(10px); backdrop-filter:blur(10px);
      box-shadow:0 18px 44px rgba(0,0,0,.45), 0 0 0 1px rgba(255,255,255,.08); }}
.ch-head{{ display:flex; align-items:center; justify-content:space-between; gap:10px;
          flex-wrap:wrap; margin-bottom:8px; }}
.ch-title{{ font-size:12.5px; font-weight:700; color:{bone}; letter-spacing:.01em; }}
.ch-sub{{ font-size:11px; color:{dim}; }}
.ch-read{{ font-size:11.5px; font-weight:700; font-variant-numeric:tabular-nums;
           color:{foam}; padding:3px 10px; border-radius:999px;
           background:rgba(139,243,230,.10); box-shadow:0 0 0 1px rgba(139,243,230,.24); }}
.ch-body{{ position:relative; flex:1; min-height:0; }}
.ch-body svg{{ display:block; width:100%; height:100%; overflow:visible; }}
.ch-tip{{ position:absolute; pointer-events:none; opacity:0; transform:translate(-50%,-120%);
          padding:7px 10px; border-radius:10px; white-space:nowrap;
          font-size:11.5px; line-height:1.5; font-variant-numeric:tabular-nums;
          color:{bone}; background:rgba(11,13,16,.94);
          box-shadow:0 0 0 1px rgba(255,255,255,.14), 0 10px 26px rgba(0,0,0,.55);
          transition:opacity .14s ease; z-index:5; }}
.ch-tip.on{{ opacity:1; }}
.ch-empty{{ position:absolute; inset:0; display:flex; align-items:center;
            justify-content:center; font-size:12px; color:{dim}; }}
.ch-legend{{ display:flex; gap:14px; flex-wrap:wrap; margin-top:8px; }}
.ch-legend span{{ display:inline-flex; align-items:center; gap:6px;
                  font-size:10.5px; color:{mist}; }}
.ch-legend i{{ width:14px; height:3px; border-radius:2px; display:block; }}
.ch-grid{{ stroke:rgba(255,255,255,.055); stroke-width:1; }}
.ch-axis-line{{ stroke:rgba(255,255,255,.12); stroke-width:1; }}
.ch-tick{{ fill:{dim}; font-size:10px; font-variant-numeric:tabular-nums; }}
.ch-axis-name{{ fill:{dim}; font-size:10px; letter-spacing:.08em; }}
.ch-series{{ fill:none; stroke-linecap:round; stroke-linejoin:round; }}
.ch-label{{ font-size:10px; font-variant-numeric:tabular-nums; }}
.ch-hit{{ fill:transparent; cursor:crosshair; }}
</style>
"""

LAB_JS = r"""
(function () {
  const P = __PAYLOAD__;
  const C = P.chart || {};
  const $ = (id) => document.getElementById(id);
  const root = $('lab');

  const fmt = (v, d) => {
    const n = Number(v);
    return n.toLocaleString('zh-CN', {minimumFractionDigits: d, maximumFractionDigits: d});
  };
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const snap = (v) => {
    const step = P.step || 1;
    const snapped = Math.round((v - P.min) / step) * step + P.min;
    return clamp(Number(snapped.toFixed(6)), P.min, P.max);
  };

  /* ============ 滑条 ============ */
  const track = $('slTrack'), fill = $('slFill'), knob = $('slKnob');
  const bubble = $('slBubble'), numEl = $('slNum'), glow = $('slGlow');
  const scaleEl = $('slScale'), ticksEl = $('slTicks');

  // 刻度：主刻度 5 个 + 次刻度按 step
  const tickCount = Math.min(6, Math.max(2, Math.round((P.max - P.min) / (P.step * 5)) + 1));
  for (let i = 0; i < tickCount; i++) {
    const v = P.min + (P.max - P.min) * (i / (tickCount - 1));
    const d = document.createElement('div');
    d.className = 'sl-tick';
    d.style.left = (i / (tickCount - 1) * 100) + '%';
    ticksEl.appendChild(d);
  }
  // 刻度尺：五个刻度值对齐到步长，避免出现 132.5 / 377.5 这种碎数字
  const quarter = (P.max - P.min) / 4;
  const align = (P.step > 0 && P.step <= quarter) ? P.step : 1;
  const scaleVals = [0, 1, 2, 3, 4].map((i) => {
    const v = P.min + quarter * i;
    return clamp(Math.round(v / align) * align, P.min, P.max);
  });
  scaleVals.forEach((v) => {
    const s = document.createElement('span');
    s.textContent = fmt(v, P.decimals);
    scaleEl.appendChild(s);
  });

  let value = Number(P.value);

  function paint() {
    const ratio = (value - P.min) / (P.max - P.min);
    const pct = ratio * 100;
    fill.style.width = pct + '%';
    knob.style.left = pct + '%';
    glow.style.left = pct + '%';
    bubble.textContent = fmt(value, P.decimals) + (P.unit ? ' ' + P.unit : '');
    numEl.textContent = fmt(value, P.decimals);
    if (typeof onValue === 'function') onValue(value, ratio);
  }

  function valueFromClientX(x) {
    const r = track.getBoundingClientRect();
    const ratio = clamp((x - r.left) / r.width, 0, 1);
    return snap(P.min + ratio * (P.max - P.min));
  }

  let dragging = false;
  function onDown(e) {
    dragging = true;
    track.classList.add('drag');
    track.setPointerCapture && e.pointerId !== undefined &&
      track.setPointerCapture(e.pointerId);
    value = valueFromClientX(e.clientX);
    paint();
    e.preventDefault();
  }
  function onMove(e) {
    if (!dragging) return;
    value = valueFromClientX(e.clientX);
    paint();
  }
  function onUp() {
    if (!dragging) return;
    dragging = false;
    track.classList.remove('drag');
    commit(value);
  }
  track.addEventListener('pointerdown', onDown);
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
  window.addEventListener('pointercancel', onUp);
  track.addEventListener('keydown', (e) => {
    const big = (P.max - P.min) / 10;
    let v = value, hit = true;
    if (e.key === 'ArrowRight' || e.key === 'ArrowUp') v = value + P.step;
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') v = value - P.step;
    else if (e.key === 'PageUp') v = value + big;
    else if (e.key === 'PageDown') v = value - big;
    else if (e.key === 'Home') v = P.min;
    else if (e.key === 'End') v = P.max;
    else hit = false;
    if (!hit) return;
    e.preventDefault();
    value = snap(v); paint(); commit(value);
  });

  /* ============ 写回载体滑条（真实 pointer 事件） ============ */
  function carrier() {
    const doc = window.parent.document;
    const holder = doc.querySelector('[class*="st-key-' + P.ckey + '"]');
    const scope = holder || doc;
    const slider = scope.querySelector('[data-testid="stSlider"]');
    if (!slider) return null;
    const input = slider.querySelector('input[type="range"]');
    const wrap = slider.querySelector('div[style*="position: relative"]');
    if (!input || !wrap) return null;
    // 轨道元素：取 wrap 下宽度最大的子元素（react-aria 用它算坐标）
    let rail = null, railRect = null;
    for (const kid of Array.from(wrap.children)) {
      const r = kid.getBoundingClientRect();
      if (r.width > 20 && (!railRect || r.width > railRect.width)) { rail = kid; railRect = r; }
    }
    if (!rail) { rail = wrap.firstElementChild || wrap; railRect = rail.getBoundingClientRect(); }
    // 旋钮：react-aria 用它的绝对定位渲染当前值
    let thumb = null;
    for (const kid of Array.from(wrap.children)) {
      const style = kid.getAttribute('style') || '';
      if (style.indexOf('position: absolute') >= 0) { thumb = kid; break; }
    }
    return {doc, slider, input, wrap, rail, railRect, thumb};
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function fireAt(target, x, y, type, buttons) {
    const doc = window.parent.document;
    const W = doc.defaultView;
    target.dispatchEvent(new W.PointerEvent(type, {
      bubbles: true, cancelable: true, pointerId: 1, pointerType: 'mouse',
      isPrimary: true, button: 0, buttons: buttons, clientX: x, clientY: y, composed: true,
    }));
  }

  /* 值 → 像素坐标。
     优先用旋钮的实际渲染位置反推映射比例：react-aria 正是用它把值画到
     轨道上的，所以这样得到的落点与 react-aria 自己的换算完全一致；
     退而求其次才用轨道宽度线性换算。 */
  function xForValue(car, v) {
    const railRect = car.rail.getBoundingClientRect();
    const min = Number(car.input.min), max = Number(car.input.max);
    if (!isFinite(min) || !isFinite(max) || max <= min) return null;
    const ratio = (v - min) / (max - min);
    if (car.thumb) {
      const tr = car.thumb.getBoundingClientRect();
      const curRatio = (Number(car.input.value) - min) / (max - min);
      if (curRatio > 0.002 && tr.width > 0) {
        const cx = tr.left + tr.width / 2;
        const scale = (cx - railRect.left) / curRatio;
        if (isFinite(scale) && scale > 0) {
          return {x: railRect.left + ratio * scale, y: tr.top + tr.height / 2};
        }
      }
    }
    return {x: railRect.left + ratio * railRect.width,
            y: railRect.top + railRect.height / 2};
  }

  async function commit(v) {
    try {
      const car = carrier();
      if (!car) return;
      const pos = xForValue(car, v);
      if (!pos) return;
      fireAt(car.rail, pos.x, pos.y, 'pointerdown', 1);
      fireAt(car.rail, pos.x, pos.y, 'pointerup', 0);
      // React 18 是异步批处理：必须等一拍才读得到新值
      await sleep(180);
      const fresh = carrier();
      const got = fresh ? Number(fresh.input.value) : Number(car.input.value);
      if (Math.abs(got - v) > 1e-6) await recalibrate(v);
    } catch (err) { /* 载体缺失时静默降级：图仍可交互 */ }
  }

  async function probeAt(x) {
    const car = carrier();
    if (!car) return null;
    const rect = car.rail.getBoundingClientRect();
    const y = rect.top + rect.height / 2;
    fireAt(car.rail, x, y, 'pointerdown', 1);
    fireAt(car.rail, x, y, 'pointerup', 0);
    await sleep(160);
    const fresh = carrier();
    return fresh ? Number(fresh.input.value) : Number(car.input.value);
  }

  async function recalibrate(target) {
    try {
      const car0 = carrier();
      if (!car0) return;
      const rect = car0.rail.getBoundingClientRect();
      const w = rect.width || 1;
      const x0 = rect.left + 0.05 * w, x1 = rect.left + 0.95 * w;
      const v0 = await probeAt(x0), v1 = await probeAt(x1);
      if (v0 === null || v1 === null || v1 === v0) return;
      await probeAt(x0 + (target - v0) * (x1 - x0) / (v1 - v0));
    } catch (e) { /* 标定失败就保留当前值，不阻断交互 */ }
  }

  /* ============ 图（手写 SVG） ============ */
  const svg = $('chSvg'), tip = $('chTip'), emptyEl = $('chEmpty');
  const series = (C.series || []).filter((s) => s && s.points && s.points.length);
  const PAD = {l: 60, r: 18, t: 24, b: 40};
  let geo = null;

  const yDiv = C.y_divisor || 1;
  const ySuf = C.y_suffix || '';
  const yTick = (v) => fmt(v / yDiv, C.y_decimals === undefined ? 0 : C.y_decimals) + ySuf;

  function domain(points, idx) {
    let lo = Infinity, hi = -Infinity;
    points.forEach((p) => { const v = p[idx]; if (v < lo) lo = v; if (v > hi) hi = v; });
    if (!isFinite(lo)) { lo = 0; hi = 1; }
    if (lo === hi) { lo -= 1; hi += 1; }
    return [lo, hi];
  }

  function build() {
    if (!svg) return;
    const w = svg.clientWidth || svg.parentNode.clientWidth || 600;
    const h = svg.clientHeight || svg.parentNode.clientHeight || 240;
    svg.setAttribute('viewBox', '0 0 ' + w + ' ' + h);
    svg.innerHTML = '';
    if (!series.length) {
      if (emptyEl) { emptyEl.style.display = 'flex'; emptyEl.textContent = C.empty || '暂无可视化数据'; }
      return;
    }
    if (emptyEl) emptyEl.style.display = 'none';

    let xd = C.x_domain || domain(series[0].points, 0);
    let yd = C.y_domain ? C.y_domain.slice() : domain(series.map((s) => s.points).flat(), 1);
    // 阈值线必须落在可视范围内，否则预警线会被裁掉
    (C.hlines || []).forEach((h) => {
      if (typeof h.y === 'number') { yd[0] = Math.min(yd[0], h.y); yd[1] = Math.max(yd[1], h.y); }
    });
    const xPad = (xd[1] - xd[0]) * .04;
    xd = [xd[0] - xPad, xd[1] + xPad];
    const ySpan = yd[1] - yd[0];
    const yPad = ySpan * .12;
    // 全为非负时不画到 0 以下，避免出现 "-10.8万" 这种负刻度
    const allNonNeg = series.map((s) => s.points).flat().every((p) => p[1] >= 0);
    yd = [allNonNeg ? 0 : yd[0] - yPad, yd[1] + yPad];

    const iw = Math.max(10, w - PAD.l - PAD.r), ih = Math.max(10, h - PAD.t - PAD.b);
    const sx = (v) => PAD.l + ((v - xd[0]) / (xd[1] - xd[0])) * iw;
    const sy = (v) => PAD.t + ih - ((v - yd[0]) / (yd[1] - yd[0])) * ih;
    geo = {sx, sy, xd, yd, w, h, iw, ih};

    const NS = 'http://www.w3.org/2000/svg';
    const mk = (tag, attrs, text) => {
      const el = document.createElementNS(NS, tag);
      for (const k in attrs) el.setAttribute(k, attrs[k]);
      if (text !== undefined) el.textContent = text;
      svg.appendChild(el);
      return el;
    };

    // 窗口高亮（如：最近 N 条）
    if (C.window) {
      const x0 = sx(C.window.from), x1 = sx(C.window.to);
      const g = mk('linearGradient', {id: 'winGrad', x1: '0', x2: '1', y1: '0', y2: '0'});
      mk('stop', {offset: '0%', 'stop-color': P.colors.tide, 'stop-opacity': '0.02'}, undefined);
      mk('stop', {offset: '100%', 'stop-color': P.colors.tide, 'stop-opacity': '0.26'}, undefined);
      g.setAttribute('gradientUnits', 'objectBoundingBox');
      mk('rect', {x: Math.min(x0, x1), y: PAD.t, width: Math.abs(x1 - x0), height: ih,
                   fill: 'url(#winGrad)'});
      mk('line', {x1: x1, y1: PAD.t, x2: x1, y2: PAD.t + ih,
                  stroke: P.colors.tide, 'stroke-width': 1, 'stroke-dasharray': '3 3', opacity: .75});
    }

    // 网格
    const gy = 4, gx = 4;
    for (let i = 0; i <= gy; i++) {
      const y = PAD.t + ih * (i / gy);
      mk('line', {x1: PAD.l, y1: y, x2: PAD.l + iw, y2: y, class: 'ch-grid'});
      const val = yd[1] - (yd[1] - yd[0]) * (i / gy);
      mk('text', {x: PAD.l - 9, y: y + 3.5, class: 'ch-tick', 'text-anchor': 'end'},
         yTick(val));
    }
    for (let i = 0; i <= gx; i++) {
      const x = PAD.l + iw * (i / gx);
      mk('line', {x1: x, y1: PAD.t, x2: x, y2: PAD.t + ih, class: 'ch-grid'});
    }
    mk('line', {x1: PAD.l, y1: PAD.t + ih, x2: PAD.l + iw, y2: PAD.t + ih, class: 'ch-axis-line'});

    // x 轴刻度标签
    const xLabels = C.x_labels;
    for (let i = 0; i <= gx; i++) {
      const xv = xd[0] + (xd[1] - xd[0]) * (i / gx);
      let text = fmt(xv, C.x_decimals === undefined ? 0 : C.x_decimals);
      if (xLabels && xLabels.length) {
        const idx = nearestIndex(series[0].points, xv);
        if (xLabels[idx]) text = xLabels[idx];
      }
      mk('text', {x: PAD.l + iw * (i / gx), y: PAD.t + ih + 15,
                  class: 'ch-tick', 'text-anchor': i === 0 ? 'start' : (i === gx ? 'end' : 'middle')}, text);
    }
    if (C.x_name) mk('text', {x: PAD.l, y: PAD.t + ih + 32, class: 'ch-axis-name',
                                'text-anchor': 'start'}, C.x_name);
    if (C.y_name) mk('text', {x: 2, y: 11, class: 'ch-axis-name'}, C.y_name);

    // 阈值横线
    (C.hlines || []).forEach((h) => {
      if (h.y < yd[0] || h.y > yd[1]) return;
      const y = sy(h.y);
      mk('line', {x1: PAD.l, y1: y, x2: PAD.l + iw, y2: y,
                  stroke: h.color || P.colors.coral, 'stroke-width': 1,
                  'stroke-dasharray': '5 4', opacity: .8});
      if (h.label) mk('text', {x: PAD.l + 6, y: y - 5, class: 'ch-label',
                  fill: h.color || P.colors.coral}, h.label);
    });

    // 数据线
    series.forEach((s, si) => {
      const pts = s.points;
      let d = '';
      pts.forEach((p, i) => {
        d += (i ? 'L' : 'M') + sx(p[0]).toFixed(2) + ' ' + sy(p[1]).toFixed(2) + ' ';
      });
      if (s.area) {
        const base = PAD.t + ih;
        const gid = 'ar' + si;
        let ad = d + 'L' + sx(pts[pts.length - 1][0]).toFixed(2) + ' ' + base +
                 ' L' + sx(pts[0][0]).toFixed(2) + ' ' + base + ' Z';
        const grad = mk('linearGradient', {id: gid, x1: '0', y1: '0', x2: '0', y2: '1'});
        mk('stop', {offset: '0%', 'stop-color': s.color, 'stop-opacity': .34}, undefined);
        mk('stop', {offset: '100%', 'stop-color': s.color, 'stop-opacity': 0}, undefined);
        mk('path', {d: ad, fill: 'url(#' + gid + ')', stroke: 'none'});
      }
      mk('path', {d: d, class: 'ch-series', stroke: s.color,
                  'stroke-width': s.width || 1.8,
                  'stroke-dasharray': s.dashed ? '5 4' : null,
                  opacity: s.faded ? .35 : 1});
    });

    // 游标
    const cur = mk('g', {id: 'chCursor'});
    mk('line', {x1: 0, y1: PAD.t, x2: 0, y2: PAD.t + ih, stroke: P.colors.foam,
                'stroke-width': 1, opacity: .55, id: 'chCursorLine'});
    const dot = mk('circle', {r: 4.5, fill: P.colors.foam, stroke: P.colors.ink,
                               'stroke-width': 2, id: 'chCursorDot', opacity: 0});

    // 命中区 + 十字线
    const hit = mk('rect', {x: PAD.l, y: PAD.t, width: iw, height: ih, class: 'ch-hit'});
    const cross = mk('line', {x1: 0, y1: PAD.t, x2: 0, y2: PAD.t + ih,
                              stroke: 'rgba(255,255,255,.30)', 'stroke-width': 1,
                              'stroke-dasharray': '2 3', opacity: 0, id: 'chCross'});

    hit.addEventListener('pointermove', (e) => {
      const box = svg.getBoundingClientRect();
      const scale = geo.w / box.width;
      const x = (e.clientX - box.left) * scale;
      const y = (e.clientY - box.top) * scale;
      const xv = xd[0] + ((x - PAD.l) / iw) * (xd[1] - xd[0]);
      const idx = nearestIndex(series[0].points, xv);
      const p = series[0].points[idx];
      cross.setAttribute('x1', sx(p[0])); cross.setAttribute('x2', sx(p[0]));
      cross.setAttribute('opacity', .8);
      tip.classList.add('on');
      tip.style.left = (sx(p[0]) / scale) + 'px';
      tip.style.top = (sy(p[1]) / scale) + 'px';
      tip.innerHTML = (C.hover_prefix || '') + '<b>' + fmt(p[1], C.hover_decimals === undefined ? 1 : C.hover_decimals)
        + '</b> ' + (C.hover_suffix || '') +
        (C.x_labels && C.x_labels[idx] ? '<br><span style="color:' + P.colors.mist + '">'
          + C.x_labels[idx] + '</span>' : '');
    });
    hit.addEventListener('pointerleave', () => {
      tip.classList.remove('on');
      cross.setAttribute('opacity', 0);
    });
    if (C.interact === 'set_x') {
      hit.style.cursor = 'pointer';
      hit.addEventListener('click', (e) => {
        const box = svg.getBoundingClientRect();
        const scale = geo.w / box.width;
        const x = (e.clientX - box.left) * scale;
        const xv = xd[0] + ((x - PAD.l) / iw) * (xd[1] - xd[0]);
        let v = xv;
        if (C.interact_transform === 'reverse_count' && C.count !== undefined) {
          v = C.count - xv;
        }
        value = snap(v);
        paint();
        commit(value);
      });
    }
  }

  function nearestIndex(points, xv) {
    let best = 0, bd = Infinity;
    for (let i = 0; i < points.length; i++) {
      const d = Math.abs(points[i][0] - xv);
      if (d < bd) { bd = d; best = i; }
    }
    return best;
  }

  /* 游标跟随滑条 */
  function onValue(v) {
    if (!geo) return;
    const line = $('chCursorLine'), dot = $('chCursorDot');
    const cur = C.cursor || {};
    let x = cur.x;
    if (cur.follow === 'value') x = v;
    else if (cur.follow === 'window_end') x = (C.count || 0);
    else if (cur.follow === 'window_start') x = (C.count || 0) - v;
    if (x === undefined || x === null) return;
    const px = geo.sx(x);
    if (line) { line.setAttribute('x1', px); line.setAttribute('x2', px); }
    if (dot) {
      let yy = null;
      const sIdx = Math.min(Math.max(0, cur.series || 0), series.length - 1);
      const s0 = series[sIdx];
      if (s0) {
        const idx = nearestIndex(s0.points, x);
        yy = geo.sy(s0.points[idx][1]);
      }
      if (yy !== null) { dot.setAttribute('cx', px); dot.setAttribute('cy', yy); dot.setAttribute('opacity', 1); }
    }
    if (cur.label) {
      const el = $('chRead');
      if (el) {
        const txt = String(cur.label);
        el.textContent = txt.indexOf('{v}') >= 0
          ? txt.replace('{v}', fmt(v, P.decimals))
          : txt;
      }
    }
    if (C.window) {
      const x0 = geo.sx(C.count - v), x1 = geo.sx(C.count);
      const rect = svg.querySelector('rect[fill="url(#winGrad)"]');
      if (rect) { rect.setAttribute('x', Math.min(x0, x1)); rect.setAttribute('width', Math.abs(x1 - x0)); }
    }
  }

  /* 启动 */
  build();
  paint();
  if (window.ResizeObserver) {
    new ResizeObserver(() => { build(); paint(); }).observe($('chBody'));
  } else {
    window.addEventListener('resize', () => { build(); paint(); });
  }
  if (window.innerWidth < 720) root.classList.add('lab-narrow');
})();
"""

LAB_HTML = """
<div class="lab" id="lab">
  <div class="sl">
    <div class="sl-head">
      <div>
        <div class="sl-title">__LABEL__</div>
        <div class="sl-sub" id="slSub">__SUB__</div>
      </div>
      <div class="sl-read"><b id="slNum">0</b><i>__UNIT__</i></div>
    </div>
    <div class="sl-track" id="slTrack" tabindex="0" role="slider"
         aria-label="__LABEL__" aria-valuemin="__MIN__" aria-valuemax="__MAX__">
      <div class="sl-rail"></div>
      <div class="sl-fill" id="slFill"></div>
      <div class="sl-ticks" id="slTicks"></div>
      <div class="sl-glow" id="slGlow"></div>
      <div class="sl-knob" id="slKnob"><span class="sl-bubble" id="slBubble">0</span></div>
    </div>
    <div class="sl-scale" id="slScale"></div>
    <div class="sl-hint">__HINT__</div>
  </div>
  <div class="ch">
    <div class="ch-head">
      <div>
        <div class="ch-title">__CHART_TITLE__</div>
        <div class="ch-sub">__CHART_SUB__</div>
      </div>
      <div class="ch-read" id="chRead"></div>
    </div>
    <div class="ch-body" id="chBody">
      <svg id="chSvg" xmlns="http://www.w3.org/2000/svg"></svg>
      <div class="ch-tip" id="chTip"></div>
      <div class="ch-empty" id="chEmpty"></div>
    </div>
    <div class="ch-legend" id="chLegend"></div>
  </div>
</div>
"""


def _legend_html(chart: Dict[str, Any]) -> str:
    items = chart.get("legend") or []
    if not items:
        return ""
    parts = []
    for it in items:
        style = it.get("style", "")
        parts.append(
            "<span><i style=\"background:%s;%s\"></i>%s</span>"
            % (it.get("color", PALETTE["tide"]), style, it.get("name", ""))
        )
    return "".join(parts)


#: 嵌入 HTML 的统一入口。
#: Streamlit 1.66 起官方推荐 st.iframe（components.v1.html 已标记弃用、
#: 将在后续版本移除）。st.iframe 的文档明确保证：HTML 字符串会以
#: "允许 JavaScript 执行 + 允许同源访问 Streamlit 应用" 的方式嵌入 ——
#: 这正是本组件的桥接所依赖的能力，所以优先用它，旧版本回退到 components.html。
def _embed_html(html: str, height: int) -> None:
    if hasattr(st, "iframe"):
        st.iframe(html, width="stretch", height=height)
    else:  # pragma: no cover - 兼容 Streamlit < 1.66
        components.html(html, height=height, scrolling=False)


def render_lab(
    *,
    key: str,
    label: str,
    min_value: float,
    max_value: float,
    step: float,
    value: float,
    unit: str = "",
    decimals: int = 0,
    sub: str = "",
    hint: str = "",
    chart: Optional[Dict[str, Any]] = None,
    height: int = 340,
) -> float:
    """渲染「高级滑条 + 联动图」，返回当前值（Streamlit 侧的真实值）。

    载体滑条以 key 命名并被 CSS 隐藏；自绘滑条松手时通过真实 pointer
    事件把值写回载体，Streamlit 收到后触发一次 rerun，本函数返回新值。
    """
    chart = chart or {}
    container_key = "slc_" + key
    # 先取当前值：载体滑条上一轮提交的值就存在 session_state 里，
    # payload 必须带它，否则 rerun 后 iframe 会显示初始默认值而不是新值。
    if key not in st.session_state:
        st.session_state[key] = float(value)
    current = max(float(min_value), min(float(max_value), float(st.session_state.get(key, value))))
    payload = {
        "key": key,
        "ckey": container_key,
        "label": label,
        "min": min_value,
        "max": max_value,
        "step": step,
        "value": current,
        "unit": unit,
        "decimals": decimals,
        "colors": PALETTE,
        "chart": chart,
    }

    st.markdown(CARRIER_CSS.format(ckey=container_key), unsafe_allow_html=True)
    with st.container(key=container_key):
        # 值载体：Streamlit 只认这个控件；视觉上被隐藏，宽度保留以便映射坐标。
        # 先写 session_state 再建控件，Streamlit 会以 session_state 为准，
        # 这样既不会触发「重复设置默认值」告警，也能在 rerun 后保留新值。
        st.slider(label, min_value=min_value, max_value=max_value,
                  step=step, key=key, label_visibility="collapsed")

    body = (LAB_HTML
            .replace("__LABEL__", label)
            .replace("__SUB__", sub)
            .replace("__UNIT__", unit)
            .replace("__HINT__", hint)
            .replace("__CHART_TITLE__", chart.get("title", ""))
            .replace("__CHART_SUB__", chart.get("subtitle", ""))
            .replace("__MIN__", str(min_value))
            .replace("__MAX__", str(max_value)))
    body = body.replace('id="chLegend"></div>', 'id="chLegend">%s</div>' % _legend_html(chart))

    style = (LAB_CSS
             .replace("{{", "{").replace("}}", "}")
             .replace("{bone}", PALETTE["bone"])
             .replace("{mist}", PALETTE["mist"])
             .replace("{dim}", PALETTE["dim"])
             .replace("{tide}", PALETTE["tide"])
             .replace("{foam}", PALETTE["foam"])
             .replace("{ink}", PALETTE["ink"]))
    js = LAB_JS.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False))

    _embed_html(
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>%s</style></head><body>%s<script>%s</script></body></html>"
        % (style, body, js),
        height)
    # 载体滑条的值：rerun 后即为最新（夹紧到合法区间，坏值不让页面崩）
    current = float(st.session_state.get(key, current))
    return max(float(min_value), min(float(max_value), current))


__all__ = ["render_lab", "PALETTE"]
