/**
 * 首页布局验收：查方块重叠与间距。
 *
 * 用法：
 *   node tools/verify_dashboard_layout.js http://localhost:8501 _shots
 *
 * 判据（都是真实踩过的坑，别删）：
 *   1. 悬浮徽章（.dh-float）不得压住实时卡里的任何内容（迷你卡片栅格）；
 *      允许它压住卡片底部的留白——那是刻意的「挂边」效果。
 *   2. 决策面板（.dh-decision）底边与步骤卡顶边之间必须有 ≥24px 间距；
 *      两者紧贴时，大圆角底边 + 阴影会把步骤卡顶角「咬」掉，视觉上等同于重叠。
 *   3. 四张 KPI 卡与四张步骤卡彼此不得重叠。
 *   4. 任何一块都不得横向溢出页面内容区。
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
const PORT = Number(process.env.CDP_PORT || 9353);
const base = (process.argv[2] || 'http://localhost:8501').replace(/\/$/, '');
const outDir = process.argv[3] || '_shots';
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function findBrowser() {
  const found = EDGE_CANDIDATES.find((p) => fs.existsSync(p));
  if (!found) throw new Error('未找到 Edge/Chrome');
  return found;
}

(async () => {
  fs.mkdirSync(outDir, { recursive: true });
  const browser = findBrowser();
  const userDir = path.join(process.env.TEMP || 'C:\\Temp', 'cdp-dash-' + Date.now());
  const proc = spawn(browser, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
    '--window-size=1600,1900', `--remote-debugging-port=${PORT}`,
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
  await send('Page.enable');
  await send('Runtime.enable');

  await send('Page.navigate', { url: base + '/' });
  await wait(15000);

  const probe = await send('Runtime.evaluate', {
    expression: `(() => {
      const R = (sel) => { const e = document.querySelector(sel); if (!e) return null;
        const r = e.getBoundingClientRect();
        return {t: r.top, b: r.bottom, l: r.left, r: r.right, h: r.height, w: r.width}; };
      const overlap = (a, b) => (!a || !b) ? 0 : {
        ox: Math.min(a.r, b.r) - Math.max(a.l, b.l),
        oy: Math.min(a.b, b.b) - Math.max(a.t, b.t)};
      const kpis = [...document.querySelectorAll('[class*="st-key-dh_kpi_"]')].map(e => {
        const r = e.getBoundingClientRect();
        return {t: r.top, b: r.bottom, l: r.left, r: r.right};
      });
      const steps = [...document.querySelectorAll('[class*="st-key-dh_step_"]')].map(e => {
        const r = e.getBoundingClientRect();
        return {t: r.top, b: r.bottom, l: r.left, r: r.right};
      });
      const decision = R('.dh-decision');
      const float = R('.dh-float');
      const mini = R('.dh-mini-grid');
      const container = R('.block-container');
      const o = overlap(float, mini);
      const gap = steps.length ? Math.round(steps[0].t - decision.b) : null;
      // 同排方块之间不得重叠
      let rowOverlap = 0;
      const row = (arr) => { for (let i=0;i<arr.length;i++) for (let j=i+1;j<arr.length;j++){
        const x = Math.min(arr[i].r, arr[j].r) - Math.max(arr[i].l, arr[j].l);
        const y = Math.min(arr[i].b, arr[j].b) - Math.max(arr[i].t, arr[j].t);
        if (x > 1 && y > 1) rowOverlap += 1; } };
      row(kpis); row(steps);
      return JSON.stringify({
        floatOverMini: {ox: Math.round(o.ox), oy: Math.round(o.oy)},
        decisionToStepsGap: gap,
        rowOverlap,
        countKpi: kpis.length, countSteps: steps.length,
        overflowRight: container ? Math.round(Math.max(
          ...kpis.map(k=>k.r), ...steps.map(s=>s.r)) - container.r) : null,
        err: (document.querySelector('[data-testid="stException"]')||{}).innerText || null,
      });
    })()`,
    returnByValue: true,
  });
  const meta = JSON.parse(probe.result?.result?.value || '{}');

  const shot = await send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(path.join(outDir, 'dashboard-layout.png'),
    Buffer.from(shot.result.data, 'base64'));

  const checks = [
    ['浮标未压住迷你卡片', meta.floatOverMini && meta.floatOverMini.oy <= 0],
    ['决策面板与步骤卡间距 ≥24px', meta.decisionToStepsGap !== null && meta.decisionToStepsGap >= 24],
    ['同排方块互不重叠', meta.rowOverlap === 0],
    ['四张 KPI 卡都在', meta.countKpi === 4],
    ['四张步骤卡都在', meta.countSteps === 4],
    ['无横向溢出', meta.overflowRight !== null && meta.overflowRight <= 1],
    ['页面无异常', !meta.err],
  ];
  let failed = 0;
  for (const [name, ok] of checks) {
    if (!ok) failed += 1;
    console.log(`${ok ? 'OK  ' : 'FAIL'} ${name}`);
  }
  console.log('   细节：', JSON.stringify(meta));
  ws.close();
  proc.kill();
  console.log(failed ? `\n${failed} 项未通过` : '\n首页布局全部通过');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('ERR', e); process.exit(1); });
