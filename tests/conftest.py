# -*- coding: utf-8 -*-
"""pytest 配置：把仓库根目录放进 sys.path。

tests/ 下的用例要么 import src（需要根目录在路径里），要么用子进程跑
``python -m src.pipeline``（cwd=仓库根目录）。放这里统一兜底。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
