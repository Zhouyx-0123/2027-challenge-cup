# -*- coding: utf-8 -*-
"""D1 · 路线碳基线 CLI：坐标 → OSRM → 逐 step 表 + summary。

用法（docs/35 §8 验收命令）
--------------------------
    python -m tools.route_baseline \
        --origin 121.315087,31.014995 \
        --dest   116.664264,39.909698 \
        --coord gcj02 --vehicle 30t_tractor --load 30

产出
----
* ``data/processed/route_baseline_sh_bj_steps.parquet``（D2，逐 step）
* ``data/processed/route_baseline_sh_bj_summary.json``（D3，汇总）
* ``outputs/route_baseline/raw_osrm.json``（原始响应，复核用，**不进 Git**）

纪律
----
* 不读 ``config/amap_*``，不调 AMap，不爬瓦片；
* 公共 OSRM 仅 spike 用，正式版换本地 GraphHopper（改一个常量）；
* toll_yuan 恒为空（OSRM 无金额，不编）；
* traffic_mode 恒 ``freeflow``（OSRM 无实时路况）。

参考：docs/35_上海北京基线_任务书.md。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.routing import carbon_baseline, coord, osrm_client  # noqa: E402
from src.routing.road_class import summarize_road_class  # noqa: E402

PROCESSED = ROOT / "data" / "processed"
RAW_DIR = ROOT / "outputs" / "route_baseline"

#: 默认输出文件名（docs/35 §3 D2/D3）
STEPS_NAME = "route_baseline_sh_bj_steps.parquet"
SUMMARY_NAME = "route_baseline_sh_bj_summary.json"

#: docs/35 §5.1 的字段契约（顺序即 parquet 列序）
STEP_COLUMNS = [
    "od_key", "provider", "traffic_mode", "step_index", "distance_km",
    "duration_min", "speed_kmh", "road_name", "road_ref", "road_class",
    "road_class_source", "road_class_confidence", "toll_flag", "toll_yuan",
    "traffic_lights", "carbon_kg", "factor_version", "factor_mode", "vehicle",
    "load_t", "geometry_geojson",
]


def parse_point(text: str) -> tuple:
    """解析 "lng,lat"。"""
    parts = str(text).replace("，", ",").split(",")
    if len(parts) < 2:
        raise argparse.ArgumentTypeError("坐标格式应为 'lng,lat'，收到: %r" % text)
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        raise argparse.ArgumentTypeError("坐标必须是数字，收到: %r" % text)


def build_steps_frame(result: osrm_client.RouteResult, od_key: str,
                      vehicle: str, load_t: float) -> pd.DataFrame:
    """RouteResult → steps.parquet 的 DataFrame（严格按 STEP_COLUMNS）。"""
    rows: List[Dict[str, Any]] = []
    for step in result.steps:
        rows.append({
            "od_key": od_key,
            "provider": result.provider,
            "traffic_mode": result.traffic_mode,
            "step_index": int(step.step_index),
            "distance_km": round(float(step.distance_km), 6),
            "duration_min": round(float(step.duration_min), 6),
            "speed_kmh": round(float(step.speed_kmh), 4),
            "road_name": step.road_name,
            "road_ref": step.road_ref,
            "road_class": step.road_class,
            "road_class_source": step.road_class_source,
            "road_class_confidence": step.road_class_confidence,
            # OSRM 无过路费金额与信号灯数据 —— 留空，不编（docs/35 §9 第 4/5 条）
            "toll_flag": step.toll_flag,
            "toll_yuan": step.toll_yuan,
            "traffic_lights": step.traffic_lights,
            "carbon_kg": round(carbon_baseline.step_carbon_kg(step.distance_km, vehicle, load_t), 6),
            "factor_version": carbon_baseline.FACTOR_VERSION,
            "factor_mode": carbon_baseline.FACTOR_MODE,
            "vehicle": vehicle,
            "load_t": float(load_t),
            "geometry_geojson": step.geometry_geojson,
        })
    df = pd.DataFrame(rows, columns=STEP_COLUMNS)
    if not df.empty:
        df["toll_flag"] = df["toll_flag"].astype("Int64")
        df["toll_yuan"] = df["toll_yuan"].astype("Float64")
        df["traffic_lights"] = df["traffic_lights"].astype("Int64")
    return df


def build_summary(result: osrm_client.RouteResult, steps: pd.DataFrame,
                  od_key: str, vehicle: str, load_t: float,
                  origin_in: tuple, dest_in: tuple, coord_in: str) -> Dict[str, Any]:
    """RouteResult → summary.json（docs/35 §5.2 字段）。"""
    step_km = float(steps["distance_km"].sum()) if not steps.empty else 0.0
    step_min = float(steps["duration_min"].sum()) if not steps.empty else 0.0
    carbon_kg = float(steps["carbon_kg"].sum()) if not steps.empty else 0.0
    road = summarize_road_class(result.steps)

    unknown_share = float(road["share_pct"].get("unknown", 0.0))
    return {
        "od_key": od_key,
        "distance_km": round(float(result.distance_km), 3),
        "duration_min": round(float(result.duration_min), 1),
        "carbon_kg": round(carbon_kg, 3),
        "carbon_t": round(carbon_kg / 1000.0, 6),
        "road_class_share": road["share_pct"],
        "road_class_count": road["count"],
        "road_class_distance_km": road["distance_km"],
        "road_class_unknown_pct": round(unknown_share, 3),
        "step_count": int(len(steps)),
        "provider": result.provider,
        "traffic_mode": result.traffic_mode,
        "factor_version": carbon_baseline.FACTOR_VERSION,
        "factor_mode": carbon_baseline.FACTOR_MODE,
        "vehicle": vehicle,
        "load_t": float(load_t),
        "ef_base_gkm": carbon_baseline.EF_BASE_GKM,
        "base_speed_kmh": carbon_baseline.BASE_SPEED_KMH,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "points": int(result.points),
        # ---- 坐标审计信息（复核用；记录基准，避免"基准不明"） ----
        "input_coord": coord_in,
        "input_origin": {"lng": origin_in[0], "lat": origin_in[1]},
        "input_dest": {"lng": dest_in[0], "lat": dest_in[1]},
        "wgs_origin": {"lng": round(result.origin_wgs[0], 6), "lat": round(result.origin_wgs[1], 6)},
        "wgs_dest": {"lng": round(result.dest_wgs[0], 6), "lat": round(result.dest_wgs[1], 6)},
        "snap_distance_m": {k: round(v, 2) for k, v in result.snap_distance_m.items()},
        # ---- 求和自检（docs/35 §8：求和偏差 < 0.5%） ----
        "sum_check": {
            "step_distance_km": round(step_km, 3),
            "route_distance_km": round(float(result.distance_km), 3),
            "distance_delta_pct": round(
                (step_km - float(result.distance_km)) / float(result.distance_km) * 100.0, 4)
            if result.distance_km else 0.0,
            "step_duration_min": round(step_min, 1),
            "route_duration_min": round(float(result.duration_min), 1),
            "duration_delta_pct": round(
                (step_min - float(result.duration_min)) / float(result.duration_min) * 100.0, 4)
            if result.duration_min else 0.0,
        },
        # ---- 非实时/固定因子声明（页面与文档必须照抄） ----
        "disclaimer": {
            "real_time": False,
            "fixed_factor": True,
            "road_class_is_proxy": True,
            "note": "非实时 · 固定因子（fixed_50）· 道路等级为 proxy，不代表实测碳账。",
        },
        "attribution": "路线几何与道路等级来自 OpenStreetMap（© OpenStreetMap contributors），"
                       "经 OSRM 公共服务计算。",
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.route_baseline",
        description="上海—北京 OSM 路线碳基线（docs/35 D1）")
    parser.add_argument("--origin", required=True, type=parse_point,
                        help="起点坐标 'lng,lat'")
    parser.add_argument("--dest", required=True, type=parse_point,
                        help="终点坐标 'lng,lat'")
    parser.add_argument("--coord", default="gcj02", choices=["gcj02", "wgs84"],
                        help="输入坐标基准（默认 gcj02 —— 现有业务数据是 GCJ-02）")
    parser.add_argument("--vehicle", default=carbon_baseline.VEHICLE,
                        help="车型标识（第一版只支持 30t_tractor）")
    parser.add_argument("--load", default=30.0, type=float, help="载重（吨）")
    parser.add_argument("--od-key", default="sh_chedun__bj_tongzhou",
                        help="OD 标识，写进表里做键")
    parser.add_argument("--alternatives", default=2, type=int,
                        help="候选路线数（公共实例最多 3）")
    parser.add_argument("--steps-out", default=str(PROCESSED / STEPS_NAME),
                        help="逐 step 表输出路径")
    parser.add_argument("--summary-out", default=str(PROCESSED / SUMMARY_NAME),
                        help="summary 输出路径")
    parser.add_argument("--raw-out", default=str(RAW_DIR / "raw_osrm.json"),
                        help="原始响应落盘路径（不进 Git）")
    parser.add_argument("--base-url", default=osrm_client.OSRM_PUBLIC_URL,
                        help="OSRM 实例地址（正式版换本地实例）")
    args = parser.parse_args(argv)

    # ---- 坐标转 WGS-84 ----
    if args.coord == "gcj02":
        origin_wgs = coord.gcj02_to_wgs84(*args.origin)
        dest_wgs = coord.gcj02_to_wgs84(*args.dest)
    else:
        origin_wgs = tuple(args.origin)
        dest_wgs = tuple(args.dest)

    print("输入基准   : %s" % args.coord)
    print("起点 WGS-84: %.6f, %.6f" % origin_wgs)
    print("终点 WGS-84: %.6f, %.6f" % dest_wgs)

    provider = osrm_client.OsrmProvider(base_url=args.base_url)
    try:
        result = provider.route(origin_wgs, dest_wgs, alternatives=args.alternatives)
    except RuntimeError as exc:
        print("取路失败: %s" % exc, file=sys.stderr)
        return 2

    osrm_client.save_raw(result.raw, Path(args.raw_out))
    print("原始响应   : %s（不进 Git）" % args.raw_out)

    steps = build_steps_frame(result, args.od_key, args.vehicle, args.load)
    summary = build_summary(result, steps, args.od_key, args.vehicle, args.load,
                            args.origin, args.dest, args.coord)

    steps_path = Path(args.steps_out)
    steps_path.parent.mkdir(parents=True, exist_ok=True)
    steps.to_parquet(steps_path, index=False)

    summary_path = Path(args.summary_out)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    # ---- 控制台摘要（可直接抄进交接材料） ----
    print("")
    print("=== %s ===" % args.od_key)
    print("provider     : %s（traffic_mode=%s，非实时）" % (summary["provider"], summary["traffic_mode"]))
    print("距离         : %.2f km（step 求和 %.2f km，偏差 %+.3f%%）"
          % (summary["distance_km"], summary["sum_check"]["step_distance_km"],
             summary["sum_check"]["distance_delta_pct"]))
    print("时长         : %.1f min（step 求和 %.1f min，偏差 %+.3f%%）"
          % (summary["duration_min"], summary["sum_check"]["step_duration_min"],
             summary["sum_check"]["duration_delta_pct"]))
    print("碳排         : %.1f kg（%.4f t，%s / %s）"
          % (summary["carbon_kg"], summary["carbon_t"],
             summary["factor_version"], summary["factor_mode"]))
    print("step 数      : %d" % summary["step_count"])
    print("端点吸附     : 起点 %.1f m / 终点 %.1f m（容差 %.0f m）"
          % (summary["snap_distance_m"]["origin"], summary["snap_distance_m"]["dest"],
             osrm_client.SNAP_TOLERANCE_M))
    print("道路等级占比 :")
    for klass, pct in summary["road_class_share"].items():
        print("    %-10s %6.2f%%  (%d steps)"
              % (klass, pct, summary["road_class_count"].get(klass, 0)))
    print("unknown 占比 : %.2f%%（验收要求 < 2%%）" % summary["road_class_unknown_pct"])
    print("")
    print("输出         : %s" % steps_path)
    print("输出         : %s" % summary_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
