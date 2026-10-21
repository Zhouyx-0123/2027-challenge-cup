# -*- coding: utf-8 -*-
"""候选路线预取：310 个热点 OD × 4 条高德策略 → route_candidates.parquet。

定位（docs/28 §8 下一步）：让第 6 页切换策略时不只换几何，还能换同一策略的
碳账/成本/推荐车型。本脚本只做**路网 I/O**，不写任何业务口径：

    高德 REST（strategy 0/4/1/6）→ route_candidates.parquet
        ↓
    src/optimizer.route_candidate_select()（L2 展示口径）
        ↓
    route_candidate_carbon.parquet
        ↓
    第 6 页只读；浏览器不算碳

四条策略（与前端 pills 的近似映射）：

    0 综合  ↔ JS policy 0
    4 避堵  ↔ JS policy 4
    1 避费  ↔ JS policy 1
    6 不走高速 ↔ JS policy 6

REST 的 strategy 与 JS 的 policy 使用同一套 0/1/4/6 语义；两边分别请求，
距离/时长可能有几个百分点的差异；页面同时展示“实时几何”和“同策略预计算
碳账”，并标注口径。

用法：
    python -m tools.route_candidates --limit 5     # 小样本
    python -m tools.route_candidates                # 全量，可断点续跑
    python -m tools.route_candidates --refresh      # 忽略缓存
"""

from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from tools import route_fetch as rf

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
BASE_PATH = PROCESSED / "route_od.parquet"
CAND_PATH = PROCESSED / "route_candidates.parquet"

STRATEGIES = [(0, "综合"), (4, "躲避拥堵"), (1, "避开收费"), (6, "不走高速")]

CAND_COLUMNS = [
    "od_key", "od_site", "od_pair", "origin", "dest",
    "o_lng", "o_lat", "d_lng", "d_lat",
    "strategy", "strategy_name",
    "distance_km", "duration_min", "toll_yuan", "traffic_lights",
    "source", "flag_route_est", "fetched_at",
]


def load_base(limit: Optional[int] = None) -> pd.DataFrame:
    if not BASE_PATH.exists():
        return pd.DataFrame()
    df = pd.read_parquet(BASE_PATH)
    if df.empty:
        return df
    need = ["od_key", "origin", "dest", "o_lng", "o_lat", "d_lng", "d_lat"]
    if any(col not in df.columns for col in need):
        return pd.DataFrame()
    if limit:
        df = df.head(int(limit)).copy()
    return df


def _load_cache(refresh: bool) -> Dict[str, Dict[str, Any]]:
    if refresh or not CAND_PATH.exists():
        return {}
    try:
        old = pd.read_parquet(CAND_PATH)
    except Exception:
        return {}
    cache: Dict[str, Dict[str, Any]] = {}
    for _, row in old.iterrows():
        try:
            key = "%s|%d" % (str(row["od_key"]), int(row["strategy"]))
        except (KeyError, TypeError, ValueError):
            continue
        cache[key] = row.to_dict()
    return cache


def _save(cache: Dict[str, Dict[str, Any]]) -> pd.DataFrame:
    if not cache:
        return pd.DataFrame(columns=CAND_COLUMNS)
    out = pd.DataFrame(list(cache.values()))
    for col in CAND_COLUMNS:
        if col not in out.columns:
            out[col] = None
    out = out[CAND_COLUMNS].sort_values(["od_key", "strategy"]).reset_index(drop=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    out.to_parquet(CAND_PATH, index=False)
    return out


def _record(row: Any, strategy: int, name: str,
            result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not result:
        return None
    try:
        o_lng, o_lat = float(row["o_lng"]), float(row["o_lat"])
        d_lng, d_lat = float(row["d_lng"]), float(row["d_lat"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "od_key": str(row["od_key"]),
        "od_site": str(row.get("od_site") or row["od_key"]),
        "od_pair": str(row.get("od_pair") or (str(row["origin"]) + " → " + str(row["dest"]))),
        "origin": str(row["origin"]),
        "dest": str(row["dest"]),
        "o_lng": o_lng, "o_lat": o_lat,
        "d_lng": d_lng, "d_lat": d_lat,
        "strategy": int(strategy), "strategy_name": name,
        "distance_km": result.get("distance_km"),
        "duration_min": result.get("duration_min"),
        "toll_yuan": result.get("toll_yuan"),
        "traffic_lights": result.get("traffic_lights"),
        "source": result.get("source") or "amap_rest",
        "flag_route_est": int(result.get("flag_route_est") or 0),
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def build(limit: Optional[int] = None, refresh: bool = False,
          workers: int = 3) -> pd.DataFrame:
    base = load_base(limit)
    if base.empty:
        print("  [C1] route_od.parquet 缺失或没有坐标 — 跳过候选路线预取")
        return pd.DataFrame(columns=CAND_COLUMNS)

    cache = _load_cache(refresh)
    todo: List[Any] = []
    for _, row in base.iterrows():
        for strategy, _name in STRATEGIES:
            key = "%s|%d" % (str(row["od_key"]), strategy)
            if key not in cache:
                todo.append((row, strategy))

    print("  [C1] 候选路线：待抓 %d 条 / 缓存命中 %d 条（%d 个 OD × %d 策略，%d 并发）"
          % (len(todo), len(cache), len(base), len(STRATEGIES), max(1, workers)))
    if todo and not rf.key_available():
        print("       ⚠ 未检测到 Web 服务 key，候选路线需要真实 key 才能生成")
        return pd.DataFrame(columns=CAND_COLUMNS)

    def work(item: Any):
        row, strategy = item
        name = dict(STRATEGIES)[strategy]
        try:
            result = rf.fetch_driving(
                (float(row["o_lng"]), float(row["o_lat"])),
                (float(row["d_lng"]), float(row["d_lat"])),
                strategy=strategy, with_polyline=False,
            )
        except Exception:
            result = None
        return row, strategy, _record(row, strategy, name, result)

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        for row, strategy, record in pool.map(work, todo):
            if record is None and strategy == 0:
                # 综合策略失败时，至少用 route_od 的既有指标兜底，保证默认策略有账可看
                record = {
                    "od_key": str(row["od_key"]),
                    "od_site": str(row.get("od_site") or row["od_key"]),
                    "od_pair": str(row.get("od_pair") or ""),
                    "origin": str(row["origin"]), "dest": str(row["dest"]),
                    "o_lng": float(row["o_lng"]), "o_lat": float(row["o_lat"]),
                    "d_lng": float(row["d_lng"]), "d_lat": float(row["d_lat"]),
                    "strategy": 0, "strategy_name": "综合",
                    "distance_km": row.get("distance_km"),
                    "duration_min": row.get("duration_min"),
                    "toll_yuan": row.get("toll_yuan"),
                    "traffic_lights": row.get("traffic_lights"),
                    "source": "route_od_fallback",
                    "flag_route_est": int(row.get("flag_route_est") or 1),
                    "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            if record is not None:
                cache["%s|%d" % (str(row["od_key"]), strategy)] = record
            done += 1
            if done % 20 == 0 or done == len(todo):
                _save(cache)
                print("       %d/%d（候选 %d 条）" % (done, len(todo), len(cache)))

    out = _save(cache)
    n_ok = int((out["source"] == "amap_rest").sum()) if not out.empty else 0
    print("  [C1] 候选路线落盘：%s（%d 行，真路线 %d）" % (CAND_PATH.name, len(out), n_ok))
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="候选路线预取：OD × 4 策略")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 个 OD")
    parser.add_argument("--refresh", action="store_true", help="忽略缓存重抓")
    parser.add_argument("--workers", type=int, default=3, help="并发线程数（默认 3）")
    args = parser.parse_args(argv)
    print("=" * 78)
    print("路线候选预取 route_candidates.py · 4 策略")
    print("=" * 78)
    out = build(limit=args.limit, refresh=args.refresh, workers=args.workers)
    print("-" * 78)
    print("完成：%d 行" % len(out))
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
