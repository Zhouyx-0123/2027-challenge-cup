# -*- coding: utf-8 -*-
"""首页入口：把渲染委托给 home_view，保持 / 可直达。"""
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from home_view import render

render()
