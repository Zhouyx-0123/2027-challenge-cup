# -*- coding: utf-8 -*-
"""前端 AppTest：首页 + 8 页逐页跑一遍，零异常才算过。

替代原手工点页（README A2/A3 的"AppTest 0 异常"自动化版）。
另加一条弃用 API 防线：事项 9 固化的迁移不许回归。
"""
import ast
import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
PAGES = [APP_DIR / "app.py"] + sorted((APP_DIR / "pages").glob("*.py"))

# 事项 9：这些 API 已弃用（streamlit 1.66 警告、未来版本移除），再写回来 CI 直接红
_BANNED_PATTERNS = [
    r"use_container_width",
    r"streamlit\.components\.v1",
    r"\bst_html\b",
]


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_page_runs_without_exception(page: Path):
    """逐页渲染：有异常即失败（红屏/Exception 不允许带上答辩现场）。"""
    at = AppTest.from_file(str(page), default_timeout=60)
    at.run(timeout=180)
    assert not at.exception, "\n".join(str(e.value) for e in at.exception)


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_page_is_syntactically_valid(page: Path):
    """页面必须能被 Python 解析。

    为什么单独一条：AppTest 在**脚本编译失败**时不会往 ``at.exception`` 里塞东西
    —— 页面直接空白渲染，测试照样"全绿"。中文文案里一个英文双引号
    （例如 "这不是"OSM 更快""）就会截断字符串、触发这个盲区。
    """
    try:
        ast.parse(page.read_text(encoding="utf-8"), filename=str(page))
    except SyntaxError as exc:
        pytest.fail("%s 第 %s 行语法错误：%s" % (page.name, exc.lineno, exc.msg))


def test_no_deprecated_streamlit_api():
    """app/ 与 tools/ 不许出现已弃用的 Streamlit API。"""
    scan_dirs = [APP_DIR, ROOT / "tools"]
    offenders = []
    for folder in scan_dirs:
        for py in sorted(folder.rglob("*.py")):
            text = py.read_text(encoding="utf-8")
            for pattern in _BANNED_PATTERNS:
                # 导航遮罩/外链桥接需要执行一小段同源 JS；st.html 会剥掉 script，
                # 只有 components.html 能可靠运行。仅允许 theme.py 这一处。
                if pattern == r"streamlit\.components\.v1" and py == APP_DIR / "components" / "theme.py":
                    continue
                if re.search(pattern, text):
                    offenders.append("%s → %s" % (py.name, pattern))
    assert not offenders, "发现弃用 API：%s" % offenders


def test_syntax_guard_catches_chinese_quote_trap():
    """自测：语法防线确实能抓到"中文文案里的英文双引号"这个坑。

    背景：这个坑在中文项目里极易复发（作者想强调，却顺手打了英文双引号），
    而且 **AppTest 抓不到** —— 脚本编译失败时它不往 ``at.exception`` 里塞东西，
    页面直接空白渲染，测试照样"全绿"。

    所以本条不是重复检查，而是**证明防线本身有效**：
    用真实的坏样例跑一遍 AST 解析，必须抛 SyntaxError。
    """
    bad = (
        "# -*- coding: utf-8 -*-\n"
        "def f():\n"
        "    return \"这不是\"OSM 更快\"，是「没算拥堵」。\"\n"
    )
    with pytest.raises(SyntaxError):
        ast.parse(bad)

    good = (
        "# -*- coding: utf-8 -*-\n"
        "def f():\n"
        "    return \"地区/特别行政区\" + \"无对照\"\n"
    )
    ast.parse(good)  # 成对的英文引号是合法用法，不该误报


def test_all_source_files_are_syntactically_valid():
    """app/ src/ tools/ 下所有 .py 都必须能被解析。

    比只查页面更宽：组件与工具模块同样会进浏览器或 CLI，
    一个引号就能让整块功能静默消失。
    """
    offenders = []
    for folder in (APP_DIR, ROOT / "src", ROOT / "tools"):
        for py in sorted(folder.rglob("*.py")):
            try:
                ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
            except SyntaxError as exc:
                offenders.append("%s 第 %s 行：%s" % (py.name, exc.lineno, exc.msg))
    assert not offenders, "语法错误：\n%s" % "\n".join(offenders)
