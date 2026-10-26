/**
 * 导航联动验收：点顶栏、点首页 CTA / KPI 卡 / 流程步骤，验证真的跳到目标页。
 *
 * 用法：node tools/verify_nav.js http://localhost:8501
 *
 * 背景：主应用历史上出过「一次点击打开两个页面」的 bug（自绘 <a> + 隐藏 page_link
 * 双事件源），以及「首页根本不能跳转」的问题。本脚本把这两件事变成可回归的检查。
 */
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const EDGE_CANDIDATES = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
];
const PORT = Number(process.env.CDP_PORT || 9337);
const base = (process.argv[2] || 'http://localhost:8501').replace(/\/$/, '');
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function findBrowser() {
  const found = EDGE_CANDIDATES.find((p) => fs.existsSync(p));
  if (!found) throw new Error('未找到 Edge/Chrome');
  return found;
}

// [用例名, 起点路由, 链接文字, 期望落地路由, 期望页面出现的文字]
const CASES = [
  ['顶栏→发票感知', '/', '发票感知', '/invoice', '发票数据感知'],
  ['顶栏→硬件监控', '/', '硬件监控', '/sensor', '云边端硬件监控'],
  ['顶栏→路线沙盘', '/', '路线沙盘', '/routes', '减碳路线沙盘'],
  ['顶栏→碳台账', '/', '碳台账', '/ledger', '碳核算与台账'],
  ['首页CTA→发票', '/', '感知一张发票', '/invoice', '发票数据感知'],
  ['首页CTA→台账', '/', '打开碳台账', '/ledger', '碳核算与台账'],
  ['首页KPI→监控', '/', '看监控', '/sensor', '云边端硬件监控'],
  ['首页流程→路线', '/', '去路线沙盘', '/routes', '减碳路线沙盘'],
  ['首页流程→台账', '/', '去碳台账', '/ledger', '碳核算与台账'],
];

(async () => {
  const browser = findBrowser();
  const userDir = path.join(process.env.TEMP || 'C:\\Temp', 'cdp-nav-' + Date.now());
  const proc = spawn(browser, ['--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
    '--window-size=1600,1200', `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${userDir}`, 'about:blank'], { stdio: 'ignore' });

  let list = null;
  for (let i = 0; i < 40; i++) {
    await wait(500);
    try { list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json(); if (list.length) break; } catch (e) {}
  }
  const target = await (await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(base + '/')}`, { method: 'PUT' })).json();
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  let id = 0; const pending = new Map();
  ws.addEventListener('message', (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } });
  await new Promise((r) => ws.addEventListener('open', r));
  const send = (method, params = {}) => new Promise((res) => { const mid = ++id; pending.set(mid, res); ws.send(JSON.stringify({ id: mid, method, params })); });
  await send('Page.enable'); await send('Runtime.enable');

  let failed = 0;
  for (const [name, from, text, expectPath, expectText] of CASES) {
    await send('Page.navigate', { url: base + from });
    await wait(9000);
    const clicked = await send('Runtime.evaluate', {
      expression: `(() => {
        const links = [...document.querySelectorAll('[data-testid="stPageLink-NavLink"]')];
        const hit = links.find(a => (a.innerText || '').includes(${JSON.stringify(text)}));
        if (!hit) return 'NO_LINK';
        hit.click();
        return 'CLICKED';
      })()`,
      returnByValue: true,
    });
    await wait(6000);
    const state = await send('Runtime.evaluate', {
      expression: `(() => JSON.stringify({
        path: location.pathname,
        hasText: document.body.innerText.includes(${JSON.stringify(expectText)}),
        err: !!document.querySelector('[data-testid="stException"]'),
      }))()`,
      returnByValue: true,
    });
    const s = JSON.parse(state.result?.result?.value || '{}');
    const ok = clicked.result?.result?.value === 'CLICKED' && s.path === expectPath && s.hasText && !s.err;
    if (!ok) failed += 1;
    console.log(`${ok ? 'OK  ' : 'FAIL'} ${name.padEnd(16)} click=${clicked.result?.result?.value} ` +
      `path=${s.path}(期望 ${expectPath}) hasText=${s.hasText} err=${s.err}`);
  }
  ws.close(); proc.kill();
  console.log(failed ? `\n${failed} 项联动未通过` : '\n联动全部通过');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('ERR', e); process.exit(1); });
