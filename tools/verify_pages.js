/**
 * 逐页视觉验收：导航到每个路由，各截一张图，并回读关键指标。
 *
 * 用法：
 *   node tools/verify_pages.js http://localhost:8501 _shots
 *
 * 依赖：Edge/Chrome 可执行文件 + Node 18+（用内置 WebSocket，无需 puppeteer）。
 * 输出：每页一行 —— 截图字节数 / block-container 高度 / 可见元素数 / 异常文本。
 *
 * 判据（踩过坑，务必记住）：
 *   · 截图字节数是最快的判据：整页空白约 140 KB，有内容约 1.3–1.9 MB；
 *   · 若某页明显偏小，先看 err 字段，再查 .stApp 高度链（见 docs/47 §6）。
 */
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const EDGE_CANDIDATES = [
  // 浏览器路径可用环境变量指定（CI / Linux）：CHROME_PATH=/usr/bin/google-chrome
  process.env.CHROME_PATH,
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  // Linux 常见路径（GitHub Actions ubuntu-latest 自带 Google Chrome）
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
].filter(Boolean);
const PORT = Number(process.env.CDP_PORT || 9336);
const base = (process.argv[2] || 'http://localhost:8501').replace(/\/$/, '');
const outDir = process.argv[3] || '_shots';
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const ROUTES = [
  ['dashboard', '/'],
  ['invoice', '/invoice'],
  ['sensor', '/sensor'],
  ['routes', '/routes'],
  ['ledger', '/ledger'],
];

function findBrowser() {
  const found = EDGE_CANDIDATES.find((p) => fs.existsSync(p));
  if (!found) throw new Error('未找到 Edge/Chrome');
  return found;
}

(async () => {
  fs.mkdirSync(outDir, { recursive: true });
  const browser = findBrowser();
  const userDir = path.join(process.env.TEMP || 'C:\\Temp', 'cdp-verify-' + Date.now());
  const proc = spawn(browser, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
    '--window-size=1600,1200', `--remote-debugging-port=${PORT}`,
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
  let id = 0;
  const pending = new Map();
  ws.addEventListener('message', (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  });
  await new Promise((r) => ws.addEventListener('open', r));
  const send = (method, params = {}) => new Promise((res) => {
    const mid = ++id; pending.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  await send('Page.enable');
  await send('Runtime.enable');

  let failed = 0;
  for (const [name, route] of ROUTES) {
    await send('Page.navigate', { url: base + route });
    await wait(9000);
    const info = await send('Runtime.evaluate', {
      expression: `(() => {
        const bc = document.querySelector('.block-container');
        const err = document.querySelector('[data-testid="stException"]');
        const app = document.querySelector('.stApp');
        const painted = [...document.querySelectorAll('.block-container *')]
          .filter(e => { const r = e.getBoundingClientRect(); return r.width > 3 && r.height > 3; }).length;
        return JSON.stringify({
          h: bc ? Math.round(bc.getBoundingClientRect().height) : 0,
          painted,
          appH: app ? getComputedStyle(app).height : null,
          err: err ? err.innerText.slice(0, 200) : null,
          url: location.pathname,
        });
      })()`,
      returnByValue: true,
    });
    const shot = await send('Page.captureScreenshot', { format: 'png' });
    const file = path.join(outDir, `page-${name}.png`);
    fs.writeFileSync(file, Buffer.from(shot.result.data, 'base64'));
    const meta = JSON.parse(info.result?.result?.value || '{}');
    const bytes = fs.statSync(file).size;
    const ok = !meta.err && bytes > 400000;
    if (!ok) failed += 1;
    console.log(
      `${ok ? 'OK  ' : 'FAIL'} ${name.padEnd(10)} ${String(bytes).padStart(8)} bytes | ` +
      `h=${meta.h} painted=${meta.painted} appH=${meta.appH}` +
      (meta.err ? ` | ERR: ${meta.err.replace(/\s+/g, ' ').slice(0, 120)}` : ''));
  }
  ws.close();
  proc.kill();
  console.log(failed ? `\n${failed} 页未通过` : '\n全部页面通过');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('ERR', e); process.exit(1); });
