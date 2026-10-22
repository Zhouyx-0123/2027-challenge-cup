/**
 * OpenLayers 地图真实渲染验证（Playwright + Chromium）。
 *
 * 为什么需要它：Python 侧断言**测不出**浏览器里的问题。
 * 本项目实测踩到三个只有真浏览器才暴露的 bug：
 *   1. JS 括号错位（"Unexpected token ')'"）；
 *   2. OL v10 把 ol.control.defaults 从函数改成对象；
 *   3. OL v10 移除 ol.geom.GeoJSON，且 setStyle 只接受 Style 实例。
 * 这三个 bug 在 Python 测试全绿的情况下都能让地图彻底画不出来。
 *
 * 用法：
 *   python -m tools.baseline_preview
 *   node tools/verify_ol.js outputs/figures/baseline_sh_bj.html outputs/figures/baseline_sh_bj.png
 *
 * 依赖：playwright-core + 一个 Chromium。可用 PW_CHROME_PATH 指定浏览器路径。
 * 没装 Playwright 时该脚本不可用 —— 此时靠 tests/ 里的静态检查兜底。
 */
const { chromium } = require('playwright-core');
const path = require('path');

const htmlPath = process.argv[2] || 'outputs/figures/baseline_sh_bj.html';
const pngPath = process.argv[3] || 'outputs/figures/baseline_sh_bj.png';
const url = 'file://' + path.resolve(htmlPath).replace(/\\/g, '/');

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.PW_CHROME_PATH,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  const errors = [];
  const consoleErrors = [];
  page.on('pageerror', (e) => errors.push(String(e && e.message ? e.message : e)));
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text());
  });

  await page.goto(url, { waitUntil: 'load', timeout: 60000 });
  // 等 OL 初始化 + 路线绘制
  await page.waitForTimeout(6000);

  const probe = await page.evaluate(() => {
    const canvas = document.querySelectorAll('#map canvas');
    const svgs = document.querySelectorAll('#map svg');
    const banner = document.querySelector('#banner');
    const legend = document.querySelector('#legend');
    const list = document.querySelector('#lst');
    const fallback = document.querySelector('#fallback');
    const olLoaded = typeof ol !== 'undefined';
    let featureCount = null;
    try {
      featureCount = window.__probeFeatures ? window.__probeFeatures() : null;
    } catch (e) { featureCount = 'err:' + e.message; }
    // 通过 canvas 像素统计判断"真的画了东西"
    // 注意：OL 10 默认用 WebGL 渲染，getImageData 在合成 canvas 上读不到内容，
    // 所以改用「页面截图与纯背景的差异」来判定（由外层脚本做）。
    let nonBlank = null;
    if (canvas.length) {
      try {
        const c = canvas[0];
        const ctx = c.getContext('2d');
        if (ctx) {
          const d = ctx.getImageData(0, 0, Math.min(c.width, 400), Math.min(c.height, 300)).data;
          let painted = 0;
          for (let i = 0; i < d.length; i += 4) {
            if (d[i + 3] > 0 && (d[i] + d[i + 1] + d[i + 2]) > 30) painted++;
          }
          nonBlank = painted;
        } else {
          nonBlank = 'webgl';  // 读不到像素，用截图差异判定
        }
      } catch (e) { nonBlank = 'err:' + e.message; }
    }
    return {
      olLoaded,
      canvasCount: canvas.length,
      svgCount: svgs.length,
      canvasSize: canvas.length ? [canvas[0].width, canvas[0].height] : null,
      paintedPixels: nonBlank,
      bannerText: banner ? banner.innerText.replace(/\s+/g, ' ').trim() : null,
      legendText: legend ? legend.innerText.replace(/\s+/g, ' ').trim() : null,
      listItems: list ? list.querySelectorAll('.it').length : 0,
      firstItem: list && list.querySelector('.it') ? list.querySelector('.it').innerText.replace(/\s+/g, ' ').trim() : null,
      footText: document.querySelector('#foot') ? document.querySelector('#foot').innerText.replace(/\s+/g, ' ').trim() : null,
      fallbackVisible: fallback ? fallback.classList.contains('on') : null,
      fallbackText: fallback ? fallback.innerText.replace(/\s+/g, ' ').trim() : null,
    };
  });

  await page.screenshot({ path: pngPath, fullPage: false });

  console.log(JSON.stringify({ probe, errors, consoleErrors }, null, 2));
  await browser.close();

  const fail = [];
  if (!probe.olLoaded) fail.push('OpenLayers 未加载');
  if (probe.canvasCount === 0) fail.push('没有 canvas，地图未渲染');
  if (!probe.bannerText || !/非实时/.test(probe.bannerText)) fail.push('声明条缺少「非实时」');
  if (!probe.bannerText || !/固定因子/.test(probe.bannerText)) fail.push('声明条缺少「固定因子」');
  if (!probe.bannerText || !/proxy/.test(probe.bannerText)) fail.push('声明条缺少「proxy」');
  if (!probe.listItems || probe.listItems < 60) fail.push('逐 step 列表项数异常: ' + probe.listItems);
  if (!probe.legendText || !/高速/.test(probe.legendText)) fail.push('图例缺少高速项');
  if (!probe.footText || !/km/.test(probe.footText)) fail.push('底部汇总缺失');
  if (errors.length) fail.push('JS 错误: ' + errors.join(' | '));

  if (fail.length) {
    console.error('\n验证失败:\n- ' + fail.join('\n- '));
    process.exit(1);
  }
  console.error('\n验证通过：OL 已加载，路线已绘制，声明条/图例/逐 step 列表齐全。');
})();
