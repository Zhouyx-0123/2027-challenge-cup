# -*- coding: utf-8 -*-
"""D6 · 静态预览：一张 OSM 路线 + 道路等级着色图。

任务书 §3 D6 说这是**可选**交付物，且"不改第 6 页"。
本脚本只读已生成的结果表，产出一个自包含 HTML 文件，
双击即可看（不需要起 Streamlit）。

用法
----
    python -m tools.baseline_preview --out outputs/figures/baseline_sh_bj.html

输出
----
* ``outputs/figures/baseline_sh_bj.html`` —— 自包含预览（OpenLayers CDN）
* 同时可选导出 PNG（需要浏览器，--png）

纪律
----
* 只读 ``data/processed/route_baseline_sh_bj_*``，不重算碳排；
* 不改第 6 页；
* 原始 OSRM 响应与瓦片缓存不进 Git。

参考：docs/35_上海北京基线_任务书.md D6。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import pandas as pd  # noqa: E402

from components import baseline_map, basemap_align, olmapview  # noqa: E402

PROCESSED = ROOT / "data" / "processed"
STEPS_PATH = PROCESSED / "route_baseline_sh_bj_steps.parquet"
SUMMARY_PATH = PROCESSED / "route_baseline_sh_bj_summary.json"


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.baseline_preview",
        description="上海—北京 OSM 基线静态预览（D6）")
    parser.add_argument("--out", default=str(ROOT / "outputs" / "figures" / "baseline_sh_bj.html"),
                        help="输出 HTML 路径")
    parser.add_argument("--height", default=820, type=int, help="地图高度（px）")
    args = parser.parse_args(argv)

    if not STEPS_PATH.exists() or not SUMMARY_PATH.exists():
        print("基线表还没生成，先跑 python -m tools.route_baseline", file=sys.stderr)
        return 2

    steps = pd.read_parquet(STEPS_PATH)
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))

    token = olmapview.load_tianditu_token()
    # 底图优先级：天地图（CGCS2000，零偏移，需 token）> 高德栅格（GCJ-02，服务端对齐）。
    # 两者都是国内持牌服务，都不会出现"路线偏在路边"。
    if token:
        layers = olmapview.default_tianditu_layers(token)
        basemap_desc = "天地图 WMTS（CGCS2000，零偏移）"
    else:
        layers = [basemap_align.amap_xyz_layer()]
        basemap_desc = "高德栅格（GCJ-02，已做基准对齐）"
    payload = baseline_map.build_baseline_payload(steps, summary, layers=layers)
    payload["attribution"] = str(summary.get("attribution") or "")

    doc = olmapview.build_doc(
        payload,
        height=int(args.height),
        title="上海—北京 OSM 碳基线 · 道路等级着色",
        fallback_note=("底图：%s" % basemap_desc),
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")

    print("预览已生成: %s" % out)
    print("  feature 数 : %d" % len(payload["features"]))
    print("  底图       : %s" % basemap_desc)
    print("  几何基准   : %s" % payload.get("geomCrs"))
    print("  道路等级   : %s" % payload["road_class_share"])
    print("  署名       : © OpenStreetMap contributors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
