# -*- coding: utf-8 -*-
"""已废弃：请改用 python -m src.pipeline。

保留此文件只为兼容旧命令；它现在直接调用正式流水线，
避免旧版 mock 生成器写出与结果表契约不一致的列。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pipeline import main  # noqa: E402

if __name__ == "__main__":
    print("提示：tools/make_mock_results.py 已废弃，等价于 python -m src.pipeline")
    raise SystemExit(main(sys.argv[1:]))
