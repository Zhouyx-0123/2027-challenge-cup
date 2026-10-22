# -*- coding: utf-8 -*-
"""OpenLayers 地图的真实渲染验证（浏览器级）。

为什么要有这条测试
------------------
Python 侧断言**测不出**浏览器里的问题。本项目实测踩到三个只有真浏览器
才暴露的 bug，而当时 pytest 全绿：

1. **JS 括号错位** —— ``new ol.style.Fill({color:"rgba(0,0,0,0)")}``，
   报 "Unexpected token ')'"，整张地图画不出来；
2. **OL v10 破坏性变更** —— ``ol.control.defaults`` 从函数变成对象
   （默认控件挪到 ``ol.control.defaults.defaults``）；
3. **OL v10 移除 ``ol.geom.GeoJSON``**（v7 起就没了），
   且 ``setStyle`` 只接受 ``ol.style.Style`` 实例，不接受普通对象。

所以这里用 Playwright 真跑一遍浏览器，检查：OL 是否加载、canvas 是否创建、
路线是否真的画出来（截图与背景的像素差异）、声明条/图例/逐 step 列表是否齐全。

降级
----
没装 node / Playwright / Chromium 时**跳过**（不红）。
静态层面另有防线：``test_frontend.py`` 的语法检查会拦住 JS 括号错位导致的
Python 侧问题；但 JS 自身的语法错误仍需 node --check（见 test_ol_js_syntax）。

需要时手动跑：
    python -m tools.baseline_preview
    node tools/verify_ol.js outputs/figures/baseline_sh_bj.html outputs/figures/baseline_sh_bj.png
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PREVIEW_HTML = ROOT / "outputs" / "figures" / "baseline_sh_bj.html"
PREVIEW_PNG = ROOT / "outputs" / "figures" / "baseline_sh_bj.png"
VERIFY_JS = ROOT / "tools" / "verify_ol.js"

#: 常见的 Chromium 位置（Playwright 下载目录 + 系统浏览器）
_CHROME_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright" / "chromium-1234"
    / "chrome-win64" / "chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright" / "chromium-1148"
    / "chrome-win64" / "chrome.exe",
    Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
]


def _find_chrome() -> str:
    """找一个可用的 Chromium。找不到返回空串。"""
    env = os.environ.get("PW_CHROME_PATH")
    if env and Path(env).exists():
        return env
    for candidate in _CHROME_CANDIDATES:
        try:
            if candidate.exists():
                return str(candidate)
        except OSError:
            continue
    # 扫一遍 playwright 缓存目录
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    if root.exists():
        for folder in sorted(root.glob("chromium-*/chrome-win64/chrome.exe")):
            return str(folder)
    return ""


def _find_node() -> str:
    return shutil.which("node") or ""


def test_ol_javascript_has_no_syntax_error():
    """生成的地图 HTML 里的内联 JS 必须语法正确。

    这是**不需要浏览器、也不需要联网**的快速防线：把 HTML 里的 <script> 抠出来，
    交给 node --check 检查。node 没有就跳过。

    为什么重要：JS 语法错误会让整张地图**静默画不出来**，
    而 Python 侧测试全绿 —— 本项目实测踩过（括号错位，报
    "Unexpected token ')'"，页面上只剩一条提示文案）。
    """
    node = _find_node()
    if not node:
        pytest.skip("未找到 node，跳过 JS 语法检查")

    if not PREVIEW_HTML.exists():
        # 先离线生成预览（不联网）
        subprocess.run([sys.executable, "-m", "tools.baseline_preview"],
                       cwd=str(ROOT), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not PREVIEW_HTML.exists():
        pytest.skip("预览 HTML 未生成")

    import re
    doc = PREVIEW_HTML.read_text(encoding="utf-8")
    match = re.search(r"<script>\n(.*?)\n</script>", doc, re.S)
    assert match, "预览 HTML 里找不到内联脚本"
    tmp = ROOT / "outputs" / "figures" / "_inline_check.js"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(match.group(1), encoding="utf-8")
    try:
        proc = subprocess.run([node, "--check", str(tmp)],
                              capture_output=True, text=True)
        assert proc.returncode == 0, "内联 JS 有语法错误：\n%s" % proc.stderr[:1500]
    finally:
        if tmp.exists():
            tmp.unlink()


@pytest.mark.network
def test_page6_three_modes_render_in_browser():
    """第 6 页三模式端到端验证（真浏览器）。

    为什么必须有：Streamlit 的 **Tab 内容在 AppTest 里不渲染**，
    而第 6 页的核心交付全在 Tab 里（OSM 地图、逐 step 表、对照表）。
    Python 测试全绿 ≠ 页面能用。

    验证内容：
      1. 三个 Tab 都能点开；
      2. Tab② 的 OpenLayers 真的画出路线（iframe 内 canvas + 65 个 step + 图例）；
      3. Tab③ 对照表有数据、且写着「不是同一口径」；
      4. **顶部导航仍为 8 页**（不许新开第 9 页）。

    需要先起服务：
        python -m streamlit run app/app.py --server.port 8512
    默认跳过（服务没起时不算失败）。
    """
    node = _find_node()
    if not node:
        pytest.skip("未找到 node，跳过第 6 页端到端验证")
    chrome = _find_chrome()
    if not chrome:
        pytest.skip("未找到 Chromium，跳过第 6 页端到端验证")
    node_path = os.environ.get("NODE_PATH", "")
    candidates = [p for p in node_path.split(os.pathsep) if p] if node_path else []
    candidates += [
        str(ROOT / "node_modules"),
        str(Path.home() / ".workbuddy" / "binaries" / "node" / "workspace" / "node_modules"),
    ]
    if not any((Path(c) / "playwright-core").exists() for c in candidates):
        pytest.skip("未安装 playwright-core，跳过第 6 页端到端验证")

    base = os.environ.get("WB_STREAMLIT_BASE", "http://127.0.0.1:8512")
    try:
        import urllib.request
        urllib.request.urlopen(base + "/", timeout=5)
    except Exception:
        pytest.skip("Streamlit 未启动（%s），跳过第 6 页端到端验证" % base)

    env = dict(os.environ)
    env["PW_CHROME_PATH"] = chrome
    env["NODE_PATH"] = os.pathsep.join(candidates)
    shots = ROOT / "outputs" / "figures"
    shots.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [node, str(ROOT / "tools" / "verify_page6.js"), base, "", str(shots)],
        cwd=str(ROOT), capture_output=True, text=True, env=env, timeout=420)
    assert proc.returncode == 0, (
        "第 6 页三模式验证失败：\n%s\n%s" % (proc.stdout[-2500:], proc.stderr[-2500:]))


@pytest.mark.network
def test_ol_map_renders_in_real_browser():
    """真浏览器里跑一遍：OL 加载、canvas 创建、路线画出、声明条/图例/列表齐全。"""
    node = _find_node()
    if not node:
        pytest.skip("未找到 node，跳过浏览器渲染验证")
    chrome = _find_chrome()
    if not chrome:
        pytest.skip("未找到 Chromium，跳过浏览器渲染验证")

    # playwright-core 的解析路径
    node_path = os.environ.get("NODE_PATH", "")
    candidates = [p for p in node_path.split(os.pathsep) if p] if node_path else []
    candidates += [
        str(ROOT / "node_modules"),
        str(Path.home() / ".workbuddy" / "binaries" / "node" / "workspace" / "node_modules"),
    ]
    if not any((Path(c) / "playwright-core").exists() for c in candidates):
        pytest.skip("未安装 playwright-core，跳过浏览器渲染验证")

    if not PREVIEW_HTML.exists():
        subprocess.run([sys.executable, "-m", "tools.baseline_preview"],
                       cwd=str(ROOT), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    env = dict(os.environ)
    env["PW_CHROME_PATH"] = chrome
    env["NODE_PATH"] = os.pathsep.join(candidates)
    proc = subprocess.run(
        [node, str(VERIFY_JS), str(PREVIEW_HTML), str(PREVIEW_PNG)],
        cwd=str(ROOT), capture_output=True, text=True, env=env, timeout=300)
    assert proc.returncode == 0, (
        "浏览器渲染验证失败：\n%s\n%s" % (proc.stdout[-2000:], proc.stderr[-2000:]))
    assert PREVIEW_PNG.exists() and PREVIEW_PNG.stat().st_size > 5000, "截图未生成或过小"
