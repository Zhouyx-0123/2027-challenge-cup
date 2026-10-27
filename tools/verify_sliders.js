/**
 * 滑条验收：截图 + 真实拖拽 + 值回传校验。
 *
 * 用法：
 *   node tools/verify_sliders.js http://localhost:8501 D:\oceanbase01\_shots
 *
 * 判据：
 *   · 自绘滑条与联动图必须同时存在（iframe 内 .sl-track / #chSvg 有内容）；
 *   · 载体滑条必须被隐藏（opacity 0）但仍保留可测量宽度；
 *   · 在 iframe 内模拟一次真实拖拽后，父页载体滑条的 input.value 必须改变，
 *     且等于拖拽位置换算出的期望值 —— 这是「自定义滑条 → Streamlit 值」
 *     桥接是否成立的唯一硬判据；
 *   · iframe 里的读数气泡必须与载体滑条的值一致。
 */
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const EDGE_CANDIDATES = [
  // 浏览器路径可用环境变量指定（CI / Linux）：CHROME_PATH=/usr/bin/google-chrome
  process.env.CHROME_PATH,
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  // Linux 常见路径（GitHub Actions ubuntu-latest 自带 Google Chrome）
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
].filter(Boolean);
const PORT = Number(process.env.CDP_PORT || 9345);
const base = (process.argv[2] || 'http://localhost:8501').replace(/\/$/, '');
const outDir = process.argv[3] || '_shots';
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const CASES = [
  { name: 'sensor', route: '/sensor', key: 'sensor_window_n', ratio: 0.72 },
  { name: 'routes', route: '/routes', key: 'route_carbon_price', ratio: 0.35 },
];

function findBrowser() {
  const found = EDGE_CANDIDATES.find((p) => fs.existsSync(p));
  if (!found) throw new Error('未找到 Edge/Chrome');
  return found;
}

(async () => {
  fs.mkdirSync(outDir, { recursive: true });
  const browser = findBrowser();
  const userDir = path.join(process.env.TEMP || 'C:\\Temp', 'cdp-slider-' + Date.now());
  const proc = spawn(browser, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
    '--window-size=1600,1100', `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${userDir}`, 'about:blank',
  ], { stdio: 'ignore' });

  let list = null;
  for (let i = 0; i < 40; i++) {
    await wait(500);
    try {
      list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      if (list.length) break;
    } catch (e) { /* 还没起来 */ }
  }
  if (!list || !list.length) throw new Error('CDP 不可达');

  const target = await (await fetch(
    `http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(base + '/')}`, { method: 'PUT' })).json();
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  let id = 0; const pending = new Map();
  ws.addEventListener('message', (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  });
  await new Promise((r) => ws.addEventListener('open', r));
  const send = (method, params = {}) => new Promise((res) => {
    const mid = ++id; pending.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  const evaluate = async (expression) => {
    const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: false });
    if (r.result?.exceptionDetails) {
      return { error: r.result.exceptionDetails.text + ' ' +
        (r.result.exceptionDetails.exception?.description || '') };
    }
    try {
      const v = r.result?.result?.value;
      if (v !== null && typeof v === 'object') return v;
      return JSON.parse(v || 'null');
    } catch (e) { return { raw: r.result?.result?.value }; }
  };
  await send('Page.enable');
  await send('Runtime.enable');

  let failed = 0;
  for (const c of CASES) {
    await send('Page.navigate', { url: base + c.route });
    await wait(11000);

    const before = await evaluate(`(() => {
      const frames = [...document.querySelectorAll('iframe')];
      const f = frames.find(x => x.getBoundingClientRect().width > 250 && x.getBoundingClientRect().height > 150);
      if (!f) return {err: 'no lab iframe', frames: frames.length};
      const d = f.contentDocument, w = f.contentWindow;
      const track = d.getElementById('slTrack');
      const holder = document.querySelector('[class*="st-key-slc_${c.key}"]');
      const carrier = holder ? holder.querySelector('input[type="range"]') : null;
      const cs = carrier ? getComputedStyle(carrier.closest('[data-testid="stSlider"]')) : null;
      const svg = d.getElementById('chSvg');
      return {
        hasTrack: !!track,
        trackRect: track ? track.getBoundingClientRect().toJSON() : null,
        svgNodes: svg ? svg.childElementCount : 0,
        carrierValue: carrier ? Number(carrier.value) : null,
        carrierMin: carrier ? Number(carrier.min) : null,
        carrierMax: carrier ? Number(carrier.max) : null,
        carrierStep: carrier ? Number(carrier.step) : null,
        carrierOpacity: cs ? cs.opacity : null,
        carrierWidth: carrier ? Math.round(carrier.closest('[data-testid="stSlider"]').getBoundingClientRect().width) : null,
        readNum: (d.getElementById('slNum')||{}).textContent,
        readCursor: (d.getElementById('chRead')||{}).textContent,
        err: (document.querySelector('[data-testid="stException"]')||{}).innerText,
      };
    })()`);

    const shot1 = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(outDir, `slider-${c.name}-before.png`),
      Buffer.from(shot1.result.data, 'base64'));
    if (before.error || before.raw) {
      console.log(`DEBUG ${c.name}:`, JSON.stringify(before).slice(0, 600));
    }

    // 真实拖拽：在 iframe 的自绘滑条上 pointerdown → pointermove → pointerup
    const dragged = await evaluate(`(() => {
      const frames = [...document.querySelectorAll('iframe')];
      const f = frames.find(x => x.getBoundingClientRect().width > 250 && x.getBoundingClientRect().height > 150);
      if (!f) return {err: 'no lab iframe'};
      const d = f.contentDocument, w = f.contentWindow;
      const track = d.getElementById('slTrack');
      if (!track) return {err: 'no track'};
      const r = track.getBoundingClientRect();
      const x = r.left + r.width * ${c.ratio};
      const y = r.top + r.height / 2;
      const W = w;
      const opts = {bubbles:true, cancelable:true, pointerId:1, pointerType:'mouse',
                    isPrimary:true, button:0, buttons:1, clientX:x, clientY:y};
      track.dispatchEvent(new W.PointerEvent('pointerdown', opts));
      w.dispatchEvent(new W.PointerEvent('pointermove', opts));
      w.dispatchEvent(new W.PointerEvent('pointerup', {...opts, buttons:0}));
      const num = d.getElementById('slNum');
      return {afterDragNum: num ? num.textContent : null, ratio: ${c.ratio}};
    })()`);

    await wait(4500);

    const after = await evaluate(`(() => {
      const frames = [...document.querySelectorAll('iframe')];
      const f = frames.find(x => x.getBoundingClientRect().width > 250 && x.getBoundingClientRect().height > 150);
      const holder = document.querySelector('[class*="st-key-slc_${c.key}"]');
      const carrier = holder ? holder.querySelector('input[type="range"]') : null;
      const d = f ? f.contentDocument : null;
      return {
        carrierValue: carrier ? Number(carrier.value) : null,
        readNum: d && d.getElementById('slNum') ? d.getElementById('slNum').textContent : null,
        readCursor: d && d.getElementById('chRead') ? d.getElementById('chRead').textContent : null,
        svgNodes: d && d.getElementById('chSvg') ? d.getElementById('chSvg').childElementCount : 0,
        err: (document.querySelector('[data-testid="stException"]')||{}).innerText,
      };
    })()`);

    const shot2 = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(outDir, `slider-${c.name}-after.png`),
      Buffer.from(shot2.result.data, 'base64'));

    // 期望值：线性映射 + 步长对齐
    let expected = null;
    if (before.carrierValue !== null && before.carrierMax !== null) {
      const raw = before.carrierMin + (before.carrierMax - before.carrierMin) * c.ratio;
      expected = Number((Math.round(raw / before.carrierStep) * before.carrierStep).toFixed(6));
    }
    const changed = after.carrierValue !== null && before.carrierValue !== null &&
      after.carrierValue !== before.carrierValue;
    const matched = expected !== null && after.carrierValue === expected;
    const ok = !before.err && !after.err && before.hasTrack && before.svgNodes > 5 &&
      changed && matched && before.carrierOpacity === '0';

    if (!ok) failed += 1;
    console.log(
      `${ok ? 'OK  ' : 'FAIL'} ${c.name.padEnd(7)} ` +
      `carrier ${before.carrierValue} → ${after.carrierValue} (期望 ${expected}) ` +
      `| 气泡 ${before.readNum} → ${after.readNum} | 图节点 ${before.svgNodes}/${after.svgNodes} ` +
      `| 隐藏=${before.carrierOpacity} 宽=${before.carrierWidth}` +
      (before.err || after.err ? ` | ERR ${String(before.err || after.err).slice(0, 100)}` : ''));
  }
  ws.close();
  proc.kill();
  console.log(failed ? `\n${failed} 项未通过` : '\n滑条全部通过');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('ERR', e); process.exit(1); });
