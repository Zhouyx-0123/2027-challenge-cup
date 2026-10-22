/**
 * 第 6 页三模式 · 端到端浏览器验证。
 *
 * 为什么必须真浏览器：Streamlit 的 Tab 内容在 AppTest 里**不渲染**，
 * 而本项目第 6 页的核心交付全在 Tab 里（OSM 地图、逐 step 表、对照表）。
 * Python 测试全绿 ≠ 页面能用。
 *
 * 验证内容：
 *   1. 页面加载无 JS 错误；
 *   2. 三个 Tab 都能点开；
 *   3. Tab② 里 OpenLayers 真的画出路线（iframe 内）；
 *   4. Tab③ 对照表有数据、且写着「不是同一口径」；
 *   5. 顶部没有多出第 9 页导航（8 页结构不变）。
 */
const { chromium } = require('playwright-core');
const path = require('path');

const base = process.argv[2] || 'http://127.0.0.1:8511';
// 第 6 页路径。
// 注意：本项目的路由是**中文路径**（/路线实验室），不是 Streamlit 默认的 /6_路线实验室 ——
// 顶部导航 theme.top_nav 里手写了中文 href。用错路径 Streamlit 会静默重定向到首页。
let pagePath = process.argv[3];
if (!pagePath || !pagePath.startsWith('/')) {
  pagePath = '/' + encodeURIComponent('路线实验室');
}
const shotDir = process.argv[4] || 'outputs/figures';

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.PW_CHROME_PATH,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await ctx.newPage();

  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e && e.message ? e.message : e)));
  const result = { errors, steps: [] };

  // Streamlit 用 websocket 长连接，networkidle 永远不会触发 —— 用 domcontentloaded + 显式等待。
  await page.goto(base + pagePath, { waitUntil: 'domcontentloaded', timeout: 90000 });

  // 等 Streamlit 渲染出 Tab。
  // 注意：Tab 是**延迟渲染**的 —— 页面先渲染上半部分（含高德 iframe），
  // Tab 组件才出现。所以这里要轮询等待，不能只等一次。
  const waitStart = Date.now();
  let tabCount = 0;
  let probeError = null;
  while (Date.now() - waitStart < 60000) {
    try {
      tabCount = await page.evaluate(() => document.querySelectorAll('[data-testid="stTab"]').length);
    } catch (e) {
      probeError = String(e && e.message ? e.message : e);
      tabCount = 0;
    }
    if (tabCount >= 3) break;
    await page.waitForTimeout(1500);
  }
  result.tabWaitMs = Date.now() - waitStart;
  result.tabCountFound = tabCount;
  result.probeError = probeError;
  result.currentUrl = page.url();
  await page.waitForTimeout(3000);

  // 1) 顶部导航：必须还是 8 页
  //    同一个页面在 DOM 里有三种 URL 形态（绝对 URL / 相对路径 / 百分号编码），
  //    先归一化成"中文路径名"再去重，否则会数出 25 个。
  const nav = await page.evaluate(() => {
    const links = Array.from(document.querySelectorAll('a[href]'))
      .map((a) => a.getAttribute('href'))
      .filter((h) => h && h.length > 1 && h !== '/' && !h.startsWith('#'));
    const names = new Set();
    for (const h of links) {
      let p = h.split('?')[0].split('#')[0];
      try { p = decodeURIComponent(p); } catch (e) { /* 保持原样 */ }
      p = p.replace(/^https?:\/\/[^/]+/, '').replace(/^\/+/, '');
      if (!p || p.includes('_') || p === 'app.py') continue;
      names.add(p);
    }
    return [...names].sort();
  });
  result.navPages = nav;
  result.navCount = nav.length;

  // 2) 找三个 Tab
  //    Streamlit 1.56 的 Tab 是 [data-testid="stTab"]（不是 stBaseButton-primary）。
  //    用 evaluate 直接读，避免 Playwright 的 hasText 在多层子节点上匹配不到。
  const tabLabels = await page.evaluate(() => {
    const tabs = Array.from(document.querySelectorAll('[data-testid="stTab"]'));
    return tabs.map((b) => (b.innerText || b.textContent || '').trim()).filter(Boolean);
  });
  result.tabLabels = tabLabels;

  const clickTab = async (pattern, name) => {
    // 在页面上下文里找并点击，避免 locator 的文本匹配问题
    const clicked = await page.evaluate((pat) => {
      const re = new RegExp(pat);
      const tabs = Array.from(document.querySelectorAll('[data-testid="stTab"]'));
      const hit = tabs.find((b) => re.test((b.innerText || b.textContent || '').trim()));
      if (!hit) return false;
      hit.click();
      return true;
    }, pattern.source || pattern);
    if (!clicked) { result.steps.push({ tab: name, ok: false, why: '找不到 Tab' }); return null; }
    await page.waitForTimeout(9000);
    await page.screenshot({ path: path.join(shotDir, 'p6_' + name + '.png') });
    const text = await page.evaluate(() => document.body.innerText);
    result.steps.push({ tab: name, ok: true, textLen: text.length });
    return text;
  };

  // 切到 Tab② 后，OL 的 iframe 要时间初始化（CDN 拉 JS + 渲染）。
  // 用 page.frames() 统一探测 —— window.frames 的可见性与 Playwright 的
  // frame 列表不一定一致，用它会漏掉 srcdoc iframe。
  //
  // 为什么要重试：OL 走 CDN，公共 CDN 偶发慢/失败是常态。
  // **组件本身会降级**（显示可读提示，不留灰块），
  // 所以这里要区分"CDN 没拉到"与"组件坏了"：
  // 连续重试若干次仍拉不到，才判失败。
  const waitForOl = async (attempts = 3) => {
    for (let round = 0; round < attempts; round++) {
      for (let i = 0; i < 25; i++) {
        for (const f of page.frames()) {
          const ok = await f.evaluate(() => {
            if (typeof document === 'undefined') return false;
            return !!(document.querySelector('#banner')
              && document.querySelectorAll('#map canvas').length);
          }).catch(() => false);
          if (ok) return true;
        }
        await page.waitForTimeout(1000);
      }
      // 重试前把 OL 的 iframe 重新挂一次：切走 Tab① 再切回来，
      // 触发 Streamlit 重渲染 iframe。
      result.olRetryRound = (result.olRetryRound || 0) + 1;
      await clickTab(/① 实时路线/, 'tab1_amap_retry');
      await clickTab(/碳核算基线/, 'tab2_baseline');
    }
    return false;
  };

  // Tab① 高德
  const t1 = await clickTab(/实时路线/, 'tab1_amap');
  result.tab1_has_od = !!(t1 && /OD（网点级）/.test(t1));

  // Tab② OSM 基线
  const t2 = await clickTab(/碳核算基线/, 'tab2_baseline');
  if (t2) {
    result.tab2_has_disclaimer = /非实时/.test(t2) && /固定因子/.test(t2) && /proxy/.test(t2);
    result.tab2_has_roadclass = /道路等级/.test(t2) && /高速/.test(t2);
    result.tab2_has_step_table = /逐 step 明细/.test(t2);
    result.olReady = await waitForOl();
    // iframe 里的地图。
    // 注意：Streamlit 的 iframe 走 srcdoc，frame.url() 是 "about:srcdoc"，
    // 不能按 url 前缀筛；而且 Tab① 的高德 iframe 与 Tab② 的 OL iframe 可能同时存在，
    // 要挨个 frame 试，找**有 #banner 的那个**（OL 组件的独有标记）。
    let mapInfo = null;
    const framesInfo = [];
    for (const f of page.frames()) {
      const r = await f.evaluate(() => {
        if (typeof document === 'undefined') return null;
        const c = document.querySelectorAll('#map canvas');
        const banner = document.querySelector('#banner');
        if (!c.length && !banner) return null;
        return {
          canvas: c.length,
          size: c.length ? [c[0].width, c[0].height] : null,
          banner: banner ? banner.innerText.replace(/\s+/g, ' ').trim() : null,
          listItems: document.querySelectorAll('#lst .it').length,
          legend: document.querySelector('#legend')
            ? document.querySelector('#legend').innerText.replace(/\s+/g, ' ').trim() : null,
          foot: document.querySelector('#foot')
            ? document.querySelector('#foot').innerText.replace(/\s+/g, ' ').trim() : null,
          hasOl: typeof ol !== 'undefined',
        };
      }).catch(() => null);
      if (r) {
        framesInfo.push(r);
        // **必须选带 #banner 的那个** —— 高德 iframe 也可能有 canvas，
        // 但它没有 OL 组件的 #banner 声明条。先按 banner 选，再退回 canvas。
        if (r.banner && r.hasOl && !mapInfo) mapInfo = r;
      }
    }
    if (!mapInfo) {
      const withCanvas = framesInfo.filter((r) => r.canvas && r.listItems > 0);
      if (withCanvas.length) mapInfo = withCanvas[0];
    }
    result.mapInfo = mapInfo;
    result.framesInfo = framesInfo;
  }

  // Tab③ 对照
  const t3 = await clickTab(/对照/, 'tab3_compare');
  if (t3) {
    result.tab3_has_table = /差异原因/.test(t3);
    result.tab3_says_not_same = /不是同一口径/.test(t3);
    result.tab3_has_dimensions = /距离/.test(t3) && /碳排/.test(t3) && /道路结构/.test(t3);
  }

  await browser.close();

  const fail = [];
  if (result.navCount !== 8) fail.push('顶部导航页数异常: ' + result.navCount + ' (应为 8)');
  if (result.tabLabels.length < 3) fail.push('三个 Tab 未齐: ' + JSON.stringify(result.tabLabels));
  if (!result.tab1_has_od) fail.push('Tab① 缺共用 OD 选择器');
  if (!result.tab2_has_disclaimer) fail.push('Tab② 缺「非实时/固定因子/proxy」声明');
  if (!result.tab2_has_roadclass) fail.push('Tab② 缺道路等级结构');
  if (!result.mapInfo || !result.mapInfo.canvas) fail.push('Tab② 的 OpenLayers 未渲染');
  if (result.mapInfo && result.mapInfo.listItems < 60) fail.push('地图内逐 step 列表项数异常: ' + result.mapInfo.listItems);
  if (result.mapInfo && !/非实时/.test(result.mapInfo.banner || '')) fail.push('地图声明条缺少「非实时」');
  if (result.mapInfo && !/高速/.test(result.mapInfo.legend || '')) fail.push('地图图例缺少高速项');
  if (!result.tab3_has_table) fail.push('Tab③ 缺对照表');
  if (!result.tab3_says_not_same) fail.push('Tab③ 缺「不是同一口径」声明');
  if (errors.length) fail.push('JS 错误: ' + errors.join(' | '));

  console.log(JSON.stringify(result, null, 2));
  if (fail.length) {
    console.error('\n验证失败:\n- ' + fail.join('\n- '));
    process.exit(1);
  }
  console.error('\n验证通过：三个 Tab 均正常，OSM 地图已渲染，对照表齐全，顶部导航仍为 8 页。');
})();
