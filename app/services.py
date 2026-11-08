"""只读结果层：主页面的唯一数据入口。

边界：
1. 只读 data/processed/ 的宽表与结果表，只从 src/schema.py 取契约常量；
2. 不导入、不调用任何核心算法或联网模型，页面不在请求线程里做数据准备；
3. 文件缺失/损坏返回空表或静态快照，不向用户抛业务异常；
4. 用户触发的 OCR、传感器和路线场景走独立的 ``live_services``，
   且结果不进入 E₀、KPI 或闸门。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
if str(ROOT) not in sys.path:  # noqa: E402
    sys.path.insert(0, str(ROOT))

from src import schema  # noqa: E402  ← 只取常量（文件名/列名），不调算法

# ---- 结果文件清单 / 列契约：唯一来源是 src/schema.py（此处只做别名） ----
RESULT_FILES: Dict[str, str] = schema.RESULT_FILES

# ---- 冻结的列名：只增不改 ----
SEGMENT_COLUMNS: List[str] = schema.SEGMENT_COLUMNS

# 页面能不能出数，只看这四列在不在（缺任何一列，页面停在数据检查，不编数字）
CORE_COLUMNS: List[str] = schema.CORE_COLUMNS

SCENARIO_COLUMNS: List[str] = schema.SCENARIO_COLUMNS

#: O6 约束结论列：唯一来源是 src/schema.py（页面 3 按契约渲染，不猜列名）
CONSTRAINT_COLUMNS: List[str] = schema.CONSTRAINT_COLUMNS

#: 方案 × 线路约束表契约列（scenario_route_constraint.parquet）
ROUTE_CONSTRAINT_COLUMNS: List[str] = schema.ROUTE_CONSTRAINT_COLUMNS

LEDGER_COLUMNS: List[str] = schema.LEDGER_COLUMNS

DISCLOSURE_KEYS: List[str] = schema.DISCLOSURE_KEYS


def path_of(name: str) -> Path:
    return PROCESSED / RESULT_FILES.get(name, "")


def _has(df: pd.DataFrame, col: str) -> bool:
    return df is not None and not df.empty and col in df.columns


def column_ok(df: pd.DataFrame, cols: List[str]) -> List[str]:
    """返回缺失的列名；页面据此显示“该表还没就绪”。"""
    if df is None or df.empty:
        return list(cols)
    return [c for c in cols if c not in df.columns]


@st.cache_data(show_spinner=False)
def available() -> Dict[str, bool]:
    out: Dict[str, bool] = {}
    for key, filename in RESULT_FILES.items():
        path = PROCESSED / filename
        sidecar = path.with_suffix(".json") if filename.endswith(".parquet") else None
        out[key] = path.exists() or bool(sidecar and sidecar.exists() and key.startswith("carbon_ledger_"))
    return out


def mtime_token(name: str) -> float:
    """结果文件的修改时间，作为缓存键的一部分。

    内层读取函数是**永久缓存**（口径冻结的主表依赖它）。实测通道是活的，
    只在外层加 TTL 没用 —— 外层过期后仍会拿到内层那份旧对象。
    需要跟文件的调用方把 mtime 传进来，让缓存键随文件变化而失效。
    """
    try:
        return (PROCESSED / RESULT_FILES.get(name, name)).stat().st_mtime
    except OSError:
        return 0.0


@st.cache_data(show_spinner=False)
def _read_parquet(filename: str, token: float = 0.0) -> pd.DataFrame:
    p = PROCESSED / filename
    sidecar = p.with_suffix(".json") if filename.startswith("carbon_ledger_") and filename.endswith(".parquet") else None
    # 使用适配器显式写出的 manifest 决定结果格式，避免旧 Parquet 文件遮蔽新 JSON 侧车。
    manifest_path = PROCESSED / "carbon_ledger_export_manifest.json"
    manifest = {}
    if sidecar and manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    use_json_sidecar = bool(sidecar and sidecar.exists() and
        manifest.get("output_formats", {}).get(filename) == "json_sidecar_no_parquet_engine")
    if use_json_sidecar:
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return pd.DataFrame(payload)
        except Exception:
            return pd.DataFrame()
    if p.exists():
        try:
            return pd.read_parquet(p)
        except Exception:
            pass
    if sidecar and sidecar.exists():
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return pd.DataFrame(payload)
        except Exception:
            return pd.DataFrame()
    # 其他表没有通用格式回退，缺失/损坏时返回空表。
    return pd.DataFrame()


@st.cache_data(show_spinner=False)
def _read_json(filename: str, token: float = 0.0) -> Dict[str, Any]:
    p = PROCESSED / filename
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


@st.cache_data(show_spinner=False)
def current_run() -> Dict[str, Any]:
    """当前**生效版本**的 run 信息（企业级 P0 · docs/50 §2）。

    读 ``data/processed/current_run.json``——由 ``src/pipeline.py`` 在闸门通过时写入，
    闸门失败时**不动**（所以这里读到的永远是"对外生效的那一版"）。

    返回 ``{"published": bool, "run_id": str, "published_at": str,
    "gate_counts": {...}, "source": str, "code_version": str, ...}``；
    没有指针时 ``published=False``，页面据此显示"未发布"。
    """
    payload = _read_json("current_run.json")
    if not payload or not payload.get("run_id"):
        return {"published": False, "run_id": "", "published_at": "",
                "gate_counts": {}, "source": "", "code_version": ""}
    out = dict(payload)
    out["published"] = True
    out.setdefault("gate_counts", {})
    return out


@st.cache_data(show_spinner=False)
def run_history(limit: int = 8) -> list:
    """最近的 run 记录（读 ``data/processed/_runs/index.jsonl``）。

    只读审计视图：谁在什么时候跑过、过了没有。文件缺失时返回空列表。
    """
    path = PROCESSED / "_runs" / "index.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-int(limit):]:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return list(reversed(out))


# ---------------- 主表 ----------------

def wide() -> pd.DataFrame:
    return _read_parquet(RESULT_FILES["wide"])


def segments() -> pd.DataFrame:
    return _read_parquet(RESULT_FILES["segments"])


def scenarios() -> pd.DataFrame:
    return _read_parquet(RESULT_FILES["scenarios"])


# ---------------- 运营约束（O6 契约式读取，可选） ----------------
# 规则文本见 docs/20 §7：C1 续航衰减 / C2 14h 窗口 / C3 运力匹配 / C4 补能可达 / C5 冬季山区。
# O6 未接表前，页面 3 只展示规则框架；约束结论列写进结果表后，页面按约定自动渲染。


def constraint_contract() -> Dict[str, Any]:
    """探测结果表里的约束结论列。返回 {available, columns, rows}。"""
    df = scenarios()
    if df.empty or "scenario" not in df.columns:
        return {"available": False, "columns": [], "rows": []}
    cols = [c for c in CONSTRAINT_COLUMNS if c in df.columns]
    if not cols:
        return {"available": False, "columns": [], "rows": []}
    rows = df[["scenario"] + cols].to_dict(orient="records")
    return {"available": True, "columns": cols, "rows": rows}


def route_constraints() -> pd.DataFrame:
    """方案 × 哪条线：scenario_route_constraint.parquet（逐线路 C1–C5 结论）。

    一行一个 (方案, 类别, 线路)；只有参数化方案才有行。缺失/旧结果表返回空表。
    """
    return _read_parquet(RESULT_FILES["route_constraints"])


def route_constraint_contract() -> Dict[str, Any]:
    """线路级约束表契约探测：{available, columns, rows, summary}。

    summary 由段表级 verdict 列聚合不出（它在 final_report 的 note 里），
    页面直接展示逐线表 + 表级状态分布即可，不在这里做二次统计口径。
    """
    df = route_constraints()
    if df.empty:
        return {"available": False, "columns": [], "rows": [], "summary": {}}
    cols = [c for c in ROUTE_CONSTRAINT_COLUMNS if c in df.columns]
    if not cols:
        return {"available": False, "columns": [], "rows": [], "summary": {}}
    summary: Dict[str, Any] = {"rows": int(len(df))}
    if "deploy_tier" in df.columns:
        summary["by_tier"] = {str(k): int(v) for k, v in
                              df["deploy_tier"].value_counts().items()}
    if "constraint_status" in df.columns:
        summary["by_status"] = {str(k): int(v) for k, v
                                in df["constraint_status"].value_counts().items()}
    return {"available": True, "columns": cols,
            "rows": df[cols].to_dict(orient="records"), "summary": summary}


def hotspots(dim: str | None = None) -> pd.DataFrame:
    df = _read_parquet(RESULT_FILES["hotspots"])
    if dim and _has(df, "dimension"):
        df = df[df["dimension"] == dim]
    return df


def od_band() -> pd.DataFrame:
    return _read_parquet(RESULT_FILES["od_band"])


def od_vehicle_mix(od_site: Optional[str] = None) -> pd.DataFrame:
    """可比较 OD 的逐车型构成（od_vehicle_mix.parquet，D4 预计算）。

    一行一个 (od_site, vehicle_type)；占比 = 该车型碳排占该 OD 碳排的比例。
    页面按 od_site 查表显示，不再在页面上做 groupby / mean / 占比。
    """
    df = _read_parquet(RESULT_FILES["od_vehicle_mix"])
    if od_site and not df.empty and "od_site" in df.columns:
        df = df[df["od_site"].astype(str) == str(od_site)]
    return df


def route_carbon_od(od_site: Optional[str] = None) -> pd.DataFrame:
    """OD 级路线口径碳合计（route_carbon_od.parquet，D4 预计算）。

    一行一个 od_site；列 = 段数 + kg_co2_route_legal/phys + route_km 合计。
    浏览器端不再对段表 sum 碳排——那是口径计算，只能在这里读行。
    仅在 --with-routes 跑过 optimizer.build_routes 后存在。
    """
    df = _read_parquet(RESULT_FILES["route_carbon_od"])
    if od_site and not df.empty and "od_site" in df.columns:
        df = df[df["od_site"].astype(str) == str(od_site)]
    return df


# ---------------- D5 · λ 敏感性曲线（预计算，页面只读） ----------------
# 公式全项目唯一一份在 src/evaluator.build_lambda_curve（与 evaluate_scenario 的
# NPV 口径逐点一致：年节省 = 运营节省 + 年度减排量 × λ）。页面不得自建第二套。


def lambda_curve() -> pd.DataFrame:
    """λ × 回本年限全网格表（0–3000 元/t，步长 5）。缺失返回空表。"""
    return _read_parquet(RESULT_FILES["lambda_curve"])


def lambda_grid() -> Dict[str, Any]:
    """网格量程信息 {min, max, step, points}，页面据此注明“当前 λ 取最近网格点”。"""
    df = lambda_curve()
    if df.empty or "lambda_yuan_t" not in df.columns:
        return {}
    values = sorted(float(v) for v in
                    pd.to_numeric(df["lambda_yuan_t"], errors="coerce").dropna().unique())
    if len(values) < 2:
        return {}
    return {"min": values[0], "max": values[-1],
            "step": float(values[1] - values[0]), "points": len(values)}


def lambda_curve_at(lam: float) -> pd.DataFrame:
    """当前 λ 的最近网格行（只做最近点选取，不做任何换算）。"""
    df = lambda_curve()
    if df.empty or "lambda_yuan_t" not in df.columns:
        return df
    values = [float(v) for v in
              pd.to_numeric(df["lambda_yuan_t"], errors="coerce").dropna().unique()]
    if not values:
        return df
    nearest = min(values, key=lambda v: abs(v - float(lam)))
    return df[df["lambda_yuan_t"] == nearest]


def ledger() -> pd.DataFrame:
    return _read_parquet(RESULT_FILES["ledger"])


def disclosure() -> Dict[str, Any]:
    return _read_json(RESULT_FILES["disclosure"])


def composition() -> Dict[str, Any]:
    """E₀ 构成预计算表（第 5 页下钻）：combos[月份键|车型键] → 该组合的构成。

    键规则与 src/pipeline._subset_key 一致：全集 = "all"，子集 = 升序逗号连接
    （如 "1,2|1,3" = 1/2 月 × 轻型+重型）。页面只查表，不做聚合。
    """
    return _read_json(RESULT_FILES["composition"])


def composition_combo(months: Optional[List[int]] = None,
                      vclasses: Optional[List[int]] = None) -> Dict[str, Any]:
    """按页面筛选取预计算组合；查不到返回 {}（页面显示提示，不自行聚合）。"""
    data = composition()
    combos = data.get("combos") or {}
    all_months = [int(m) for m in (data.get("months") or [])]
    all_classes = [int(v) for v in (data.get("vclasses") or [])]
    picked_m = sorted(int(m) for m in (months or []))
    picked_v = sorted(int(v) for v in (vclasses or []))
    m_key = "all" if (not picked_m or picked_m == all_months) else ",".join(str(m) for m in picked_m)
    v_key = "all" if (not picked_v or picked_v == all_classes) else ",".join(str(v) for v in picked_v)
    return combos.get(m_key + "|" + v_key) or {}


def quality() -> Dict[str, Any]:
    return _read_json(RESULT_FILES["quality"])


def gate() -> Dict[str, Any]:
    return _read_json(RESULT_FILES["gate"])


def run_log() -> Dict[str, Any]:
    return _read_json(RESULT_FILES["run_log"])


def fleet_stats() -> Dict[str, Any]:
    """车队真实统计 fleet_stats.json（第 7/8 页计算器默认值）。缺失返回 {}。"""
    return _read_json(RESULT_FILES["fleet_stats"])


# 实时演示通道已拆分到 live_services.py；本层只读结果表。


def source() -> Dict[str, Any]:
    return _read_json(RESULT_FILES["source"])


def source_name() -> str:
    return str(source().get("source", "unknown"))


# ---------------- T7 · 路线情景（只读） ----------------

def routes() -> pd.DataFrame:
    """路线表 route_od.parquet（高德真路线 + 离线兜底）。缺失返回空表。"""
    return _read_parquet(RESULT_FILES["routes"])


def route_carbon() -> pd.DataFrame:
    """路线情景段表 route_carbon.parquet（L1 双轨碳排）。缺失返回空表。"""
    return _read_parquet(RESULT_FILES["route_carbon"])


def route_select() -> pd.DataFrame:
    """λ 推荐表 route_select.parquet（L2 联合决策）。缺失返回空表。"""
    return _read_parquet(RESULT_FILES["route_select"])


def route_candidates() -> pd.DataFrame:
    """候选路线表 route_candidates.parquet（OD × 4 策略）。缺失返回空表。"""
    return _read_parquet(RESULT_FILES["route_candidates"])


def route_candidate_carbon() -> pd.DataFrame:
    """候选路线碳账 route_candidate_carbon.parquet（OD × 策略 × 车型）。缺失返回空表。"""
    return _read_parquet(RESULT_FILES["route_candidate_carbon"])


@st.cache_data(show_spinner=False)
def route_lab_candidates() -> Dict[str, Dict[str, Dict[str, Any]]]:
    """路线实验室的策略碳账：od_key → {strategy: 指标}。

    只做单位换算（kg → t）和字段搬运，不做碳/成本公式；缺失返回 {}。
    """
    df = route_candidate_carbon()
    if df.empty or "od_key" not in df.columns or "strategy" not in df.columns:
        return {}
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for _, r in df.iterrows():
        try:
            od = str(r.get("od_key") or "")
            strategy = str(int(r.get("strategy")))
        except (TypeError, ValueError):
            continue
        if not od:
            continue
        def _f(key: str) -> float:
            value = pd.to_numeric(pd.Series([r.get(key)]), errors="coerce").iloc[0]
            return float(value) if pd.notna(value) else 0.0

        kg = pd.to_numeric(pd.Series([r.get("kg_co2_route")]), errors="coerce").iloc[0]
        out.setdefault(od, {})[strategy] = {
            "km": _f("route_km"),
            "min": _f("route_min"),
            "toll": _f("toll_yuan"),
            "co2": (float(kg) / 1000.0) if pd.notna(kg) else None,
            "opex": _f("opex_yuan"),
            "cc": _f("carbon_cost_yuan"),
            "score": _f("score_yuan"),
            "veh": str(r.get("vehicle_type") or ""),
            "load": _f("load_t"),
            "name": str(r.get("strategy_name") or ""),
        }
    return out


def route_available() -> bool:
    """路线表是否就绪（决定第 3 页显示动态路线面板还是提示）。"""
    return not routes().empty


# ---------------- 上海—北京 OSM 碳基线（docs/35 D2/D3） ----------------
# 纪律：**只读不算**。summary 里的所有数字都是 tools/route_baseline.py
# 预先算好的；页面不重算碳排、不重算占比、不做坐标转换。


def route_baseline_steps() -> pd.DataFrame:
    """OSM 基线逐 step 表 route_baseline_sh_bj_steps.parquet。缺失返回空表。"""
    return _read_parquet(RESULT_FILES["route_baseline_steps"])


def route_baseline_summary() -> Dict[str, Any]:
    """OSM 基线汇总 route_baseline_sh_bj_summary.json。缺失返回 {}。

    含 disclaimer（非实时 / 固定因子 / 道路等级 proxy），页面必须照着显示。
    """
    return _read_json(RESULT_FILES["route_baseline_summary"])


def route_baseline_available() -> bool:
    """基线是否就绪（steps 与 summary 同时存在才算）。"""
    if not route_baseline_summary():
        return False
    return not route_baseline_steps().empty


# ---------------- HYC 物理行程审计（docs/39，只读，不替代 E0） ----------------

def vsp_audit() -> Dict[str, Any]:
    """物理行程审计汇总 vsp_audit.json。缺失返回 {}。"""
    return _read_json(RESULT_FILES["vsp_audit"])


def vsp_physical_trips() -> pd.DataFrame:
    """12,167 条物理行程（已去车牌/运单/客户），供审计表与下载。"""
    return _read_parquet(RESULT_FILES["vsp_physical_trips"])


def vsp_vehicle_groups() -> pd.DataFrame:
    """HYC 车型组汇总（6 组），供审计下钻。"""
    return _read_parquet(RESULT_FILES["vsp_vehicle_groups"])


def vsp_available() -> bool:
    """审计层是否就绪；旧数据目录没有这三张表时页面自动隐藏。"""
    return bool(vsp_audit()) and not vsp_physical_trips().empty


@st.cache_data(show_spinner=False)
def route_lab_rows() -> pd.DataFrame:
    """路线实验室的展示表：route_od 的几何/端点 + route_select 的碳账与推荐车型。

    只做左连接与列改名，不做碳/成本计算；页面拿到的是已经算好的数字。
    缺失 route_select 时仍返回 route_od，实验室自动隐藏碳账区块。
    """
    route = routes()
    if route.empty:
        return route
    out = route.copy()
    select = route_select()
    if not select.empty and "od_key" in select.columns:
        keep = ["od_key", "vehicle_type", "load_t", "route_km", "route_min",
                "toll_yuan", "kg_co2_route", "opex_yuan", "carbon_cost_yuan",
                "score_yuan", "flag_route_est"]
        sub = select[[c for c in keep if c in select.columns]].copy()
        sub = sub.rename(columns={
            "route_km": "sel_km", "route_min": "sel_min",
            "toll_yuan": "sel_toll_yuan", "kg_co2_route": "sel_kg_co2",
            "flag_route_est": "sel_flag_route_est",
        })
        out = out.merge(sub, on="od_key", how="left")

    for col in ("distance_km", "duration_min", "toll_yuan", "sel_km", "sel_min",
                "sel_toll_yuan", "sel_kg_co2", "opex_yuan", "carbon_cost_yuan",
                "score_yuan", "load_t", "flag_route_est", "sel_flag_route_est"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    if "distance_km" in out.columns and "sel_km" in out.columns:
        out["distance_km"] = out["distance_km"].fillna(out["sel_km"])
    if "duration_min" in out.columns and "sel_min" in out.columns:
        out["duration_min"] = out["duration_min"].fillna(out["sel_min"])
    if "toll_yuan" in out.columns and "sel_toll_yuan" in out.columns:
        out["toll_yuan"] = out["toll_yuan"].fillna(out["sel_toll_yuan"])
    if "flag_route_est" in out.columns and "sel_flag_route_est" in out.columns:
        out["flag_route_est"] = out["flag_route_est"].fillna(out["sel_flag_route_est"])
    if "flag_route_est" in out.columns:
        out["flag_route_est"] = out["flag_route_est"].fillna(0).astype(int)

    if "sel_kg_co2" in out.columns:
        out["co2_t"] = out["sel_kg_co2"] / 1000.0
    return out


def is_mock() -> bool:
    """演示数据：_source.json 说 demo，或旧版 _mock.flag 还在。"""
    if source_name() == "demo":
        return True
    return (PROCESSED / "_mock.flag").exists() and source_name() != "real"


# ---------------- OBD + GPS 实测碳排（docs/52，独立轨道） ----------------
# 纪律：只读不算。本层只从 data/processed/ 读由 `python -m src.obd_pipeline`
# 预计算的结果表；**不 import src.obd_gps 的算法**，不在页面里做守恒法。
# 该轨道**不进 E₀ 主口径、不参与主闸门对拍**（口径冻结，见 docs/52 §2）。
#
# 缓存策略：这一层带 **30 s TTL**，不像主表那样永久缓存 —— 实测通道是活的，
# 设备持续上报 → 重跑流水线 → 页面应当自己跟上，不该要求重启应用。
# 主表的永久缓存保持原样（口径冻结，不动既有行为）。

OBD_CACHE_TTL = 30


@st.cache_data(show_spinner=False, ttl=OBD_CACHE_TTL)
def _read_obd_points() -> pd.DataFrame:
    """逐点工况 + 碳排表 obd_points.parquet（一次读入，页面按车切片）。"""
    return _read_parquet(RESULT_FILES["obd_points"], token=mtime_token("obd_points"))


@st.cache_data(show_spinner=False, ttl=OBD_CACHE_TTL)
def _read_obd_vehicles() -> pd.DataFrame:
    """车级碳排聚合表。"""
    return _read_parquet(RESULT_FILES["obd_vehicle_carbon"], token=mtime_token("obd_vehicle_carbon"))


@st.cache_data(show_spinner=False, ttl=OBD_CACHE_TTL)
def _read_obd_trips() -> pd.DataFrame:
    """行程级聚合表。"""
    return _read_parquet(RESULT_FILES["obd_trips"], token=mtime_token("obd_trips"))


@st.cache_data(show_spinner=False, ttl=OBD_CACHE_TTL)
def _read_obd_summary() -> Dict[str, Any]:
    """实测碳排汇总（来源、条数、因子、数据质量）。"""
    return _read_json(RESULT_FILES["obd_summary"], token=mtime_token("obd_summary"))


def obd_summary() -> Dict[str, Any]:
    """实测碳排汇总 obd_summary.json（来源、条数、因子、数据质量）。缺失返回 {}。"""
    return _read_obd_summary()


def obd_vehicles() -> pd.DataFrame:
    """车级碳排聚合表 obd_vehicle_carbon.parquet。**每台车（每台发动机）的碳排放**。"""
    return _read_obd_vehicles()


def obd_trips() -> pd.DataFrame:
    """行程级聚合表 obd_trips.parquet。缺失返回空表。"""
    return _read_obd_trips()


def obd_points(vehicle_id: str = "", limit: int = 4000) -> pd.DataFrame:
    """逐点表（可选按车过滤、按时间倒序截断）。

    只做筛选与截断，不做任何换算与计算。
    """
    frame = _read_obd_points()
    if frame.empty:
        return frame
    if vehicle_id:
        frame = frame[frame["vehicle_id"].astype(str) == str(vehicle_id)]
    if "ts" in frame.columns:
        frame = frame.sort_values("ts")
    if limit and len(frame) > limit:
        frame = frame.tail(limit)
    return frame.reset_index(drop=True)


def obd_available() -> bool:
    """实测碳排通道是否就绪（车级表非空即可）。"""
    return not obd_vehicles().empty


def obd_is_demo() -> bool:
    """当前结果表是不是演示数据（前端据此显式标注，不冒充真实读数）。"""
    return bool((obd_summary() or {}).get("is_demo"))


# ---------------- VSP 模型 × OBD 实测 对照验证（docs/57） ----------------
# 判据（老师原话）：「你测的和这些估的能不能对得上？对不上，那估的可能就不用了。」
# 本层只读 ``src/obd_vsp_crosscheck.py`` 预计算的对拍结果表：
# 实测轨 = 碳平衡法（守恒法）；模型轨 = VSP（速度剖面积分 / 平均速度法）。
# 两者互不替代、也不相加 —— 这是**对照**，不是新口径。
CARBON_LEDGER_CACHE_TTL = 30


@st.cache_data(show_spinner=False, ttl=CARBON_LEDGER_CACHE_TTL)
def _read_obd_vsp_crosscheck() -> pd.DataFrame:
    """逐趟 × 载重档的模型 vs 实测偏差表。"""
    return _read_parquet(RESULT_FILES["obd_vsp_crosscheck"])


@st.cache_data(show_spinner=False, ttl=CARBON_LEDGER_CACHE_TTL)
def _read_obd_vsp_crosscheck_points() -> pd.DataFrame:
    """最佳载重档下的逐点排放率对照（实测 vs 模型）。"""
    return _read_parquet(RESULT_FILES["obd_vsp_crosscheck_points"])


@st.cache_data(show_spinner=False, ttl=CARBON_LEDGER_CACHE_TTL)
def _read_obd_vsp_crosscheck_summary() -> Dict[str, Any]:
    """对拍汇总与判据结论。"""
    return _read_json(RESULT_FILES["obd_vsp_crosscheck_summary"])


def obd_vsp_crosscheck() -> pd.DataFrame:
    """模型 vs 实测偏差表。缺失返回空表。"""
    return _read_obd_vsp_crosscheck()


def obd_vsp_crosscheck_points() -> pd.DataFrame:
    """逐点排放率对照表。缺失返回空表。"""
    return _read_obd_vsp_crosscheck_points()


def obd_vsp_crosscheck_summary() -> Dict[str, Any]:
    """对拍汇总与判据结论。缺失返回 {}。"""
    return _read_obd_vsp_crosscheck_summary()


# ---------------- 碳会计 A0–A6 交付轨（docs/54，审计轨） ----------------
# 纪律：只读不算。本层只从 data/processed/ 读由 `python -m src.carbon_ledger_export`
# 预计算的结果表；**不 import 适配器、不在页面里做判定、不写 Excel**。
#
# 这条轨道与工程侧 E₀ 主口径**不是同一口径**：
#   碳会计轨 = WTW 全生命周期（柴油 3.16 kgCO₂e/L）
#   工程 E₀  = TTW 尾管燃烧（柴油 2.63 kgCO₂/L）
# 因此这里**只读碳账自己的数**，绝不把两边的总量放在一起比大小。
#
# 缓存策略与 OBD 轨一致：带 **30 s TTL**，交付方更新工作簿后重跑适配器即可刷新。
CARBON_LEDGER_CACHE_TTL = 30


@st.cache_data(show_spinner=False, ttl=CARBON_LEDGER_CACHE_TTL)
def _read_carbon_ledger_trips() -> pd.DataFrame:
    """主账 Trip_Detail 全列 + 门禁谓词拆解。"""
    return _read_parquet(RESULT_FILES["carbon_ledger_trips"])


@st.cache_data(show_spinner=False, ttl=CARBON_LEDGER_CACHE_TTL)
def _read_carbon_ledger_evidence() -> pd.DataFrame:
    """证据索引 + 凭证文件存在性（适配器实时判定的事实）。"""
    return _read_parquet(RESULT_FILES["carbon_ledger_evidence"])


@st.cache_data(show_spinner=False, ttl=CARBON_LEDGER_CACHE_TTL)
def _read_carbon_ledger_summary() -> Dict[str, Any]:
    """账本状态条 / 月年汇总 / 正式汇总 / 口径声明。"""
    return _read_json(RESULT_FILES["carbon_ledger_summary"])


@st.cache_data(show_spinner=False, ttl=CARBON_LEDGER_CACHE_TTL)
def _read_carbon_ledger_gates() -> Dict[str, Any]:
    """正式汇总资格判定链（9 项谓词）。"""
    return _read_json(RESULT_FILES["carbon_ledger_gates"])


@st.cache_data(show_spinner=False, ttl=CARBON_LEDGER_CACHE_TTL)
def _read_carbon_ledger_quota() -> Dict[str, Any]:
    """配额台账。"""
    return _read_json(RESULT_FILES["carbon_ledger_quota"])


@st.cache_data(show_spinner=False, ttl=CARBON_LEDGER_CACHE_TTL)
def _read_carbon_ledger_readiness() -> Dict[str, Any]:
    """活动数据 51 字段就绪度。"""
    return _read_json(RESULT_FILES["carbon_ledger_activity_readiness"])


@st.cache_data(show_spinner=False, ttl=CARBON_LEDGER_CACHE_TTL)
def _read_carbon_ledger_rules() -> Dict[str, Any]:
    """控制规则 / 用例 / 压力场景 / 规则卡。"""
    return _read_json(RESULT_FILES["carbon_ledger_rules"])


def carbon_ledger_trips() -> pd.DataFrame:
    """碳账主账明细。缺失返回空表。"""
    return _read_carbon_ledger_trips()


def carbon_ledger_evidence() -> pd.DataFrame:
    """碳账证据索引。缺失返回空表。"""
    return _read_carbon_ledger_evidence()


def carbon_ledger_summary() -> Dict[str, Any]:
    """碳账汇总（状态条数据）。缺失返回 {}。"""
    return _read_carbon_ledger_summary()


def carbon_ledger_gates() -> Dict[str, Any]:
    """正式汇总资格判定链。缺失返回 {}。"""
    return _read_carbon_ledger_gates()


def carbon_ledger_quota() -> Dict[str, Any]:
    """配额台账。缺失返回 {}。"""
    return _read_carbon_ledger_quota()


def carbon_ledger_readiness() -> Dict[str, Any]:
    """活动数据就绪度。缺失返回 {}。"""
    return _read_carbon_ledger_readiness()


def carbon_ledger_rules() -> Dict[str, Any]:
    """控制规则与自证数据。缺失返回 {}。"""
    return _read_carbon_ledger_rules()


def carbon_ledger_available() -> bool:
    """碳账轨是否就绪（主账明细非空即可）。"""
    return not carbon_ledger_trips().empty


def carbon_ledger_integration_status() -> Dict[str, Any]:
    """面向前端的接入元数据：只读适配器产物，不执行核算或审批。"""
    summary = carbon_ledger_summary()
    if not summary:
        return {"ready": False, "reason": "carbon_ledger_summary.json 缺失或不可读"}
    formal = summary.get("formal") or {}
    return {
        "ready": bool(summary.get("trip_rows")),
        "source_workbook": summary.get("ledger_file", ""),
        "source_sha256_16": summary.get("ledger_sha256_16", ""),
        "generated_at": summary.get("generated_at", ""),
        "trip_rows": int(summary.get("trip_rows") or 0),
        "simulated_rows": int(summary.get("simulated_rows") or 0),
        "actual_rows": int(summary.get("actual_rows") or 0),
        "approved_rows": int(formal.get("approved_row_count") or 0),
        "formal_gate_status": formal.get("gate_status", ""),
        "reason": "metadata from read-only adapter output",
    }


# ---------------- 汇总指标 ----------------

@st.cache_data(show_spinner=False)
def kpis() -> Dict[str, Any]:
    """kpi.json 是唯一来源（pipeline 预计算，口径只有这一份）。

    D4：原函数在缺 kpi.json 时从段表自算 sum/mean/占比——那正是“前端第二套口径”，
    已删除。缺文件时返回空 dict，页面显示“—”并提示重跑 pipeline，不编数字。
    """
    return dict(_read_json(RESULT_FILES["kpi"]))


def flag_counts() -> Dict[str, int]:
    """四类 FLAG 的计数：唯一来源是 data_quality.json（pipeline 预计算）。

    D4：原实现在缺键时回退到段表 sum——前端第二套口径，已删除；
    缺键返回 0，页面显示空态而不是自算。
    """
    q = quality()
    k = kpis()

    def pick(qkey: str, kkey: str, default: int = 0) -> int:
        for value in (q.get(qkey), k.get(kkey)):
            try:
                if value is not None:
                    return int(value)
            except (TypeError, ValueError):
                pass
        return default

    return {
        "rows_wide": pick("rows_wide", "rows_wide"),
        "rows_dedup": pick("rows_dedup", "segments"),
        "dup_rows": pick("dup_rows", "dup_rows"),
        "anomaly_rows": pick("anomaly_rows", "anomaly_rows"),
        "basis_rows": pick("basis_rows", "basis_rows"),
        "estimated_rows": pick("estimated_rows", "estimated_rows"),
    }


# ---------------- 文件状态 ----------------

@st.cache_data(show_spinner=False)
def file_status() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for key, filename in RESULT_FILES.items():
        p = PROCESSED / filename
        exists = p.exists()
        count: Optional[int] = None
        if exists and filename.endswith(".parquet"):
            count = len(_read_parquet(filename))
        updated = "—"
        if exists:
            try:
                updated = datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M:%S")
            except Exception:
                updated = "—"
        rows.append({
            "结果表": key,
            "文件": filename,
            "状态": "已就绪" if exists else "未生成",
            "行数": count if count is not None else "—",
            "更新时间": updated,
        })
    return rows


def fmt(value: Any, digits: int = 0) -> str:
    """把数字变好看；None 显示为横杠。"""
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if v != v:
        return "—"
    if digits == 0:
        return "{:,.0f}".format(v)
    return "{:,.{}f}".format(v, digits)


def pct(value: Any, digits: int = 1) -> str:
    try:
        return "{:.{}f}%".format(float(value) * 100.0, digits)
    except (TypeError, ValueError):
        return "—"


# ---------------- 收口⑤ · 数据链总览（只读取证，不参与 E₀ / 闸门） ----------------
# 这一层只做两件事：把设备实流的落地文件读成事实、把各路结果的状态对齐到一张快照。
# 纪律与上面一致：不 import 算法、不写 data/processed、缺文件就返回空态让页面说明。
RAW_DIR = ROOT / "data" / "raw" / "obd_gps"
RAW_STREAM_PATH = RAW_DIR / "obd_stream.csv"
RAW_REJECTS_PATH = RAW_DIR / "obd_rejects.csv"
FLEET_EFFICIENCY_PATH = ROOT / "outputs" / "fleet_vehicle_efficiency.csv"
FLEET_AUDIT_PATH = ROOT / "outputs" / "fleet_audit_trail.json"
RL_POLICY_PATH = ROOT / "config" / "rl_policy_real.json"

#: 实流是设备随时写入的文件：缓存只压连续 rerun 的解析开销，不做分钟级冻结。
STREAM_CACHE_TTL = 15
FLEET_CACHE_TTL = 60


def _utc_iso(stamp: Any) -> str:
    """时间戳 → YYYY-MM-DDTHH:MM:SSZ；坏值返回空串（由页面写空态）。"""
    if stamp is None or stamp == "":
        return ""
    try:
        parsed = pd.to_datetime(stamp, errors="coerce", utc=True)
    except (TypeError, ValueError):
        return ""
    if parsed is None or pd.isna(parsed):
        return ""
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def _mtime_text(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        return ""


@st.cache_data(show_spinner=False, ttl=STREAM_CACHE_TTL)
def latest_raw_stream() -> Dict[str, Any]:
    """设备实流落盘文件（data/raw/obd_gps/obd_stream.csv）的最近一次接收事实。

    返回 {path, rows, columns, first_ts, last_ts, window_s, vehicles,
    n_vehicles, mtime, mtime_ts, bytes}；文件不存在/不可解析返回 {}。

    注意：该文件**没有进 data/processed**，因此不参与 E₀、KPI 或闸门；
    页面 OBD 图表读的仍是 demo 基线（obd_summary.json），两者在界面上分开写。
    """
    path = RAW_STREAM_PATH
    if not path.exists():
        return {}
    try:
        stat = path.stat()
        frame = pd.read_csv(path)
    except Exception:
        return {}
    if frame.empty:
        return {}
    stamps = pd.Series([], dtype="datetime64[ns, UTC]")
    if "ts" in frame.columns:
        stamps = pd.to_datetime(frame["ts"], errors="coerce", utc=True).dropna()
    vehicles: List[str] = []
    for column in ("vehicle_id", "plate"):
        if column in frame.columns:
            values = [str(v).strip() for v in frame[column].dropna().tolist()]
            values = [v for v in values if v]
            if values:
                vehicles = sorted(set(values))
                break
    window = float((stamps.max() - stamps.min()).total_seconds()) if len(stamps) else 0.0
    return {
        "path": "data/raw/obd_gps/obd_stream.csv",
        "rows": int(len(frame)),
        "columns": int(frame.shape[1]),
        "first_ts": _utc_iso(stamps.min()) if len(stamps) else "",
        "last_ts": _utc_iso(stamps.max()) if len(stamps) else "",
        "window_s": window,
        "vehicles": vehicles[:8],
        "n_vehicles": len(vehicles),
        "mtime": _mtime_text(path),
        "mtime_ts": float(stat.st_mtime),
        "bytes": int(stat.st_size),
    }


@st.cache_data(show_spinner=False, ttl=STREAM_CACHE_TTL)
def obd_reject_stats() -> Dict[str, Any]:
    """拒收台账（data/raw/obd_gps/obd_rejects.csv）：行数 + 最近 3 条原因。

    接收端没启用拒收时文件不存在 —— 返回 {exists: False, rows: 0, ...}，
    页面据此写「文件未生成」，而不是把「没有台账」说成「零拒收」。
    """
    path = RAW_REJECTS_PATH
    if not path.exists():
        return {"exists": False, "rows": 0, "reasons": [], "columns": [], "mtime": ""}
    try:
        frame = pd.read_csv(path)
    except Exception:
        return {"exists": False, "rows": 0, "reasons": [], "columns": [], "mtime": _mtime_text(path)}
    reason_column = next(
        (c for c in ("reason", "reject_reason", "error", "why", "message", "note")
         if c in frame.columns),
        None,
    )
    reasons: List[str] = []
    if reason_column is not None:
        series = frame[reason_column].dropna().astype(str).str.strip()
        series = series[series != ""]
        reasons = list(series.tail(3))[::-1][:3]
    return {
        "exists": True,
        "rows": int(len(frame)),
        "reasons": reasons,
        "columns": [str(c) for c in frame.columns][:12],
        "mtime": _mtime_text(path),
    }


@st.cache_data(show_spinner=False, ttl=FLEET_CACHE_TTL)
def fleet_efficiency() -> pd.DataFrame:
    """车队油耗/效率审计表（outputs/fleet_vehicle_efficiency.csv）。

    一行一个 (车牌, 月)。当前实测覆盖为 0（segments_covered 全 0），
    所以这张表只能证明「7,712 个车月都在等发票」，不能给出实测油耗偏差。
    """
    path = FLEET_EFFICIENCY_PATH
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


@st.cache_data(show_spinner=False, ttl=FLEET_CACHE_TTL)
def fleet_audit() -> Dict[str, Any]:
    """发票/审计缺口（outputs/fleet_audit_trail.json）。缺文件返回 {}。"""
    path = FLEET_AUDIT_PATH
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


@st.cache_data(show_spinner=False, ttl=FLEET_CACHE_TTL)
def route_policy() -> Dict[str, Any]:
    """路线 Q 的真实数据卡（config/rl_policy_real.json，**只读**）。缺文件返回 {}。"""
    path = RL_POLICY_PATH
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


@st.cache_data(show_spinner=False, ttl=STREAM_CACHE_TTL)
def chain_snapshot() -> Dict[str, Any]:
    """六条链的现状快照：只搬运文件里的事实，不做任何派生计算。

    契约键：e0_kg / gate_pass / gate_total / run_id / code_version /
    a4_rows / a4_status / obd_demo_n / obd_raw / fleet_rows；
    其余键是同一次读取里顺手对齐的旁证（覆盖期、票数、Q 表规模），
    全部来自 data/processed、data/raw 与 outputs 的只读文件。
    """
    kpi = kpis()
    run = current_run()
    gate_report = gate()
    counts = dict(gate_report.get("counts") or {})
    if not counts:
        counts = dict(run.get("gate_counts") or {})
    gate_pass = int(counts.get("pass") or 0)
    gate_fail = int(counts.get("fail") or 0)
    gate_skip = int(counts.get("skip") or 0)
    gate_total = gate_pass + gate_fail + gate_skip

    ledger = carbon_ledger_integration_status()
    ledger_summary = carbon_ledger_summary()
    ledger_gates = carbon_ledger_gates()
    conditions = ledger_gates.get("conditions") or []
    predicate_total = int(ledger_gates.get("predicate_count") or len(conditions) or 0)
    total_rows = int(ledger_gates.get("total_rows") or 0)
    # 一个谓词算「通过」= 它在全部行上都不阻断（status=pass，或 passed>=total_rows）。
    # 4 条结构谓词（唯一趟次/证据号/Run_ID/来源行号）在 12 行上全过；5 条实质谓词
    # （数据状态、Simulated、因子核验、预检、负责人批准）0 行通过 → 这正是正式行 0 的原因。
    predicate_passed = 0
    for item in conditions:
        item = item or {}
        if not isinstance(item, dict):
            continue
        try:
            if total_rows and int(item.get("passed") or 0) >= total_rows:
                predicate_passed += 1
            elif str(item.get("status") or "").strip().lower() == "pass":
                predicate_passed += 1
        except (TypeError, ValueError):
            continue

    seg_audit = _read_json("jd_segment_master_audit.json")
    obd = obd_summary() or {}
    stream = latest_raw_stream()
    fleet = fleet_efficiency()
    audit = fleet_audit()
    real = route_policy().get("real_data") or {}

    covered_rows = 0
    if not fleet.empty and "segments_covered" in fleet.columns:
        try:
            values = pd.to_numeric(fleet["segments_covered"], errors="coerce").fillna(0)
            covered_rows = int((values > 0).sum())
        except Exception:
            covered_rows = 0

    return {
        # —— 契约键 ——
        "e0_kg": kpi.get("total_kg"),
        "gate_pass": gate_pass,
        "gate_total": gate_total,
        "run_id": run.get("run_id", ""),
        "code_version": run.get("code_version", ""),
        "a4_rows": int(ledger.get("trip_rows") or 0),
        "a4_status": ledger.get("formal_gate_status", ""),
        "obd_demo_n": int(obd.get("n_records") or 0),
        "obd_raw": int(stream.get("rows") or 0),
        "fleet_rows": int(len(fleet)),
        # —— 同批旁证 ——
        "e0_t": kpi.get("e0_t"),
        "e0_trips": kpi.get("physical_trips"),
        "e0_km": kpi.get("total_km"),
        "e0_generated": kpi.get("generated_at", ""),
        "gate_fail": gate_fail,
        "published_at": run.get("published_at", ""),
        "a4_t": ledger_summary.get("total_emissions_t"),
        "a4_simulated": int(ledger.get("simulated_rows") or 0),
        "a4_approved": int(ledger.get("approved_rows") or 0),
        "a4_predicates_total": predicate_total,
        "a4_predicates_passed": predicate_passed,
        "seg_total": seg_audit.get("n_rows"),
        "seg_passed": seg_audit.get("n_passed"),
        "seg_km": seg_audit.get("distance_km"),
        "seg_carbon_valid_kg": seg_audit.get("carbon_kg_valid"),
        "obd_demo_vehicles": int(obd.get("n_vehicles") or 0),
        "obd_demo_kg": obd.get("total_kg_co2"),
        "obd_is_demo": bool(obd.get("is_demo")),
        "obd_raw_vehicles": int(stream.get("n_vehicles") or 0),
        "obd_raw_first_ts": stream.get("first_ts", ""),
        "obd_raw_last_ts": stream.get("last_ts", ""),
        "fleet_covered_rows": covered_rows,
        "invoice_records": int(((audit.get("counts") or {}).get("invoice_records") or 0)),
        "route_od": real.get("n_od"),
        "route_updates": real.get("n_updates"),
        "route_clusters": real.get("n_clusters"),
        "snapshot_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
