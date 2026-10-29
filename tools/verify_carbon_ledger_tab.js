/**
 * 碳会计 A0–A6 审计轨 tab 的视觉与内容验收。
 *
 * 用法：
 *   node tools/verify_carbon_ledger_tab.js http://localhost:8501 _shots
 *
 * 依赖：Edge/Chrome 可执行文件 + Node 18+（用内置 WebSocket，无需 puppeteer）。
 *
 * 判据（与 verify_pages.js / verify_obd_tab.js 同源）：
 *   · 切到「碳会计口径 A0–A6（审计轨）」tab 后，截图字节数必须 > 400 KB；
 *   · 页面必须出现六块内容的标题与两条边界声明：
 *     「正式汇总资格判定链」「配额台账」「证据链索引」
 *     「活动数据 51 字段就绪度」「规则与自证」「口径边界」
 *     「WTW」「TTW」「模拟数据」；
 *   · 页面必须**不得**出现「排放为零」这种把 0 行说成零排放的表述；
 *   · 出现 Streamlit 异常元素即判 FAIL。
 */
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const EDGE_CANDIDATES = [
  process.env.CHROME_PATH,
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
].filter(Boolean);
const PORT = Number(process.env.CDP_PORT || 9341);
const base = (process.argv[2] || 'http://localhost:8501').replace(/\/$/, '');
const outDir = process.argv[3] || '_shots';
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const REQUIRED_TEXTS = [
  '正式汇总资格判定链',
  '配额台账',
  '证据链索引',
  '活动数据 51 字段就绪度',
  '规则与自证',
  '口径边界',
  'WTW',
  'TTW',
  '模拟数据',
  'NO_APPROVED_ROWS',
];

function findBrowser() {
  const found = EDGE_CANDIDATES.find((p) => fs.existsSync(p));
  if (!found) throw new Error('未找到 Edge/Chrome');
  return found;
}

(async () => {
  fs.mkdirSync(outDir, { recursive: true });
  const browser = findBrowser();
  const userDir = path.join(process.env.TEMP || 'C:\\Temp', 'cdp-ledger-' + Date.now());
  const proc = spawn(browser, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
    // 审计轨区块多（六块 + 明细表），窗口要够高才能一次截全
    '--window-size=1600,4600', `--remote-debugging-port=${PORT}`,
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
    `http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(base + '/ledger')}`,
    { method: 'PUT' })).json();
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

  await wait(9000);

  // ---- 切到碳会计审计轨 tab ----
  const clicked = await send('Runtime.evaluate', {
    expression: `(() => {
      const tabs = [...document.querySelectorAll('[data-testid="stTab"], [role="tab"]')];
      const hit = tabs.find(t => (t.innerText || '').includes('碳会计'));
      if (!hit) return 'no-tab';
      hit.click();
      return 'clicked:' + hit.innerText.replace(/\\s+/g, ' ').trim().slice(0, 40);
    })()`,
    returnByValue: true,
  });
  const clickResult = clicked.result?.result?.value || '';
  await wait(7000);

  const info = await send('Runtime.evaluate', {
    expression: `(() => {
      const bc = document.querySelector('.block-container');
      const err = document.querySelector('[data-testid="stException"]');
      const text = document.body.innerText || '';
      const painted = [...document.querySelectorAll('.block-container *')]
        .filter(e => { const r = e.getBoundingClientRect(); return r.width > 3 && r.height > 3; }).length;
      const gates = document.querySelectorAll('.cl-gate').length;
      const cells = document.querySelectorAll('.cl-cell').length;
      const tables = document.querySelectorAll('[data-testid="stDataFrame"]').length;
      const tabs = [...document.querySelectorAll('[data-testid="stTab"], [role="tab"]')]
        .map(t => (t.innerText || '').replace(/\\s+/g, ' ').trim());
      return JSON.stringify({
        h: bc ? Math.round(bc.getBoundingClientRect().height) : 0,
        painted, gates, cells, tables, tabs,
        err: err ? err.innerText.slice(0, 300) : null,
        text: text.slice(0, 9000),
      });
    })()`,
    returnByValue: true,
  });
  const meta = JSON.parse(info.result?.result?.value || '{}');
  const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
  const file = path.join(outDir, 'carbon-ledger-tab.png');
  fs.writeFileSync(file, Buffer.from(shot.result.data, 'base64'));
  const bytes = fs.statSync(file).size;

  const body = meta.text || '';
  const missing = REQUIRED_TEXTS.filter((t) => !body.includes(t));
  // 「排放为零」不能出现（正确表述是「不等于实际排放为零」）
  const zeroClaim = /(?<!不等于实际)排放为零/.test(body);
  const ok = !meta.err && bytes > 400000 && missing.length === 0
    && meta.gates >= 9 && meta.cells >= 6 && !zeroClaim;

  console.log(`tab 切换: ${clickResult}`);
  console.log(`tabs    : ${JSON.stringify(meta.tabs)}`);
  console.log(`判定链  : ${meta.gates} 条｜指标卡 ${meta.cells} 个｜表格 ${meta.tables} 张｜painted=${meta.painted}｜h=${meta.h}`);
  console.log(`文案缺失: ${missing.length ? missing.join(' / ') : '无'}`);
  if (zeroClaim) console.log('零排放表述: 页面出现了「排放为零」，判 FAIL');
  if (meta.err) console.log(`异常    : ${meta.err.replace(/\s+/g, ' ').slice(0, 200)}`);
  console.log(`${ok ? 'OK  ' : 'FAIL'} 碳账审计轨 tab  ${String(bytes).padStart(8)} bytes  → ${file}`);

  ws.close();
  proc.kill();
  process.exit(ok ? 0 : 1);
})().catch((e) => { console.error('ERR', e); process.exit(1); });
