# -*- coding: utf-8 -*-
"""前端 AppTest：首页 + 8 页逐页跑一遍，零异常才算过。

替代原手工点页（README A2/A3 的"AppTest 0 异常"自动化版）。
另加一条弃用 API 防线：事项 9 固化的迁移不许回归。
"""
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


def test_no_deprecated_streamlit_api():
    """app/ 与 tools/ 不许出现已弃用的 Streamlit API。"""
    scan_dirs = [APP_DIR, ROOT / "tools"]
    offenders = []
    for folder in scan_dirs:
        for py in sorted(folder.rglob("*.py")):
            text = py.read_text(encoding="utf-8")
            for pattern in _BANNED_PATTERNS:
                if re.search(pattern, text):
                    offenders.append("%s → %s" % (py.name, pattern))
    assert not offenders, "发现弃用 API：%s" % offenders
