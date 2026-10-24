# -*- coding: utf-8 -*-
"""概览页：与 / 共用 home_view.render，走客户端路由回首页。"""
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from home_view import render

render()
