# -*- coding: utf-8 -*-
"""当前五页主入口与全项目源码的前端质量防线。"""
import ast
import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
ENTRYPOINT = APP_DIR / "app.py"

_BANNED_PATTERNS = [
    r"use_container_width",
    r"streamlit\.components\.v1",
    r"\bst_html\b",
]


def test_main_app_runs_without_exception():
    at = AppTest.from_file(str(ENTRYPOINT), default_timeout=60)
    at.run(timeout=180)
    assert not at.exception, "\n".join(str(e.value) for e in at.exception)


def test_main_app_is_syntactically_valid():
    ast.parse(ENTRYPOINT.read_text(encoding="utf-8"), filename=str(ENTRYPOINT))


def test_no_deprecated_streamlit_api():
    offenders = []
    for folder in (APP_DIR, ROOT / "tools"):
        for py in sorted(folder.rglob("*.py")):
            text = py.read_text(encoding="utf-8")
            for pattern in _BANNED_PATTERNS:
                if re.search(pattern, text):
                    offenders.append("%s → %s" % (py.name, pattern))
    assert not offenders, "发现弃用 API：%s" % offenders


def test_syntax_guard_catches_chinese_quote_trap():
    bad = (
        "# -*- coding: utf-8 -*-\n"
        "def f():\n"
        "    return \"这不是\"OSM 更快\"，是「没算拥堵」。\"\n"
    )
    with pytest.raises(SyntaxError):
        ast.parse(bad)


def test_all_source_files_are_syntactically_valid():
    offenders = []
    for folder in (APP_DIR, ROOT / "src", ROOT / "tools"):
        for py in sorted(folder.rglob("*.py")):
            try:
                ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
            except SyntaxError as exc:
                offenders.append("%s 第 %s 行：%s" % (py.name, exc.lineno, exc.msg))
    assert not offenders, "语法错误：\n%s" % "\n".join(offenders)


def test_navigation_has_no_legacy_javascript_bridge():
    theme = (APP_DIR / "components" / "theme.py").read_text(encoding="utf-8")
    nav = (APP_DIR / "components" / "main_nav.py").read_text(encoding="utf-8")
    for marker in ("NAV_GUARD_JS", "MutationObserver", "cm-nav-veil", "st.iframe"):
        assert marker not in theme
    assert "st.page_link(page" in nav
    assert "cm_nav_source" not in nav
