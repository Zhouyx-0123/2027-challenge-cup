"""数据读取层（A0）：前端唯一的数据入口。

规矩：
1. 只读 data/processed/；只从 src/schema.py 取常量（文件名/列名/校验），
   不调用任何核心算法（A0 规则针对算法，不针对常量——契约必须只有一份）；
2. 缺文件、缺列、文件损坏一律返回空表 + 原因，不抛异常（答辩现场不能红屏）；
3. 页面只能通过本模块取数，不允许直接 pd.read_parquet；
4. 结果表由 python -m src.pipeline 预先生成，页面里没有一行业务公式。

docs/20 §11 增补（情景演算页）：第 7/8 页是"用户输入→即时算"的演算页，
与对拍链路隔离。全 app/ 只在本文件出现一次 ``from src import scenarios``
（演算内核）；``from src import schema`` 只取列名/文件名常量，不调算法。
页面仍只 import services——自证命令见 docs/20 §4。
"""

from __future__ import annotations

import json
import os
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
from src import scenarios as _kernel  # noqa: E402  ← 全 app/ 唯一一处演算内核（docs/20 §11）
                                       # 别名：避免与本文件 scenarios()（结果表读取）撞名
from src import invoice as _invoice  # noqa: E402  ← 发票内核（docs/44）：识别 + 口径，全 app/ 仅此一处
from src import sensor as _sensor  # noqa: E402  ← 云边端协同：传感器读取 + 突发碳成本
from src.routing import amap_client as _amap_client  # noqa: E402  ← 路线沙盘：高德 REST
from src.routing import amap_js as _amap_js  # noqa: E402  ← 路线沙盘：JS API 2.0 凭据
from src.routing import route_carbon as _route_carbon  # noqa: E402  ← 路线沙盘：即时碳核算
from src import disclosure as _disclosure  # noqa: E402  ← DeepSeek 披露附注
from src.routing import evolve_routes as _evolution  # noqa: E402  ← 四维 NSGA-II + Q-learning

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
    return {k: (PROCESSED / f).exists() for k, f in RESULT_FILES.items()}


@st.cache_data(show_spinner=False)
def _read_parquet(filename: str) -> pd.DataFrame:
    p = PROCESSED / filename
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except Exception:
        # 没装 pyarrow 或文件损坏，都退化成空表；页面显示空状态而不是红屏
        return pd.DataFrame()


@st.cache_data(show_spinner=False)
def _read_json(filename: str) -> Dict[str, Any]:
    p = PROCESSED / filename
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


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


# ---------------- 情景演算桥（docs/20 §11） ----------------
# 全 app/ 唯一调用 src 的地方；页面只经此处，不直接 import src。
# 演算结果绝不写回结果表，也不进 kpi/gate（对拍口径与情景推演分列）。

def calc_factors() -> Dict[str, Any]:
    """口径配置 config/calc_factors.json（计算器的假设值来源）。"""
    return _kernel.load_factors()


def calc_electrification(params: Dict[str, Any]) -> Dict[str, Any]:
    """第 7 页：电动车队测算。逻辑在 src/scenarios.py，本函数只转发。"""
    return _kernel.calc_electrification(params)


def calc_fleet_tco(params: Dict[str, Any]) -> Dict[str, Any]:
    """第 8 页：车队 TCO 多路线对比。逻辑在 src/scenarios.py，本函数只转发。"""
    return _kernel.calc_fleet_tco(params)


def tonne_km_comparison(our_g_per_tkm: float, **kwargs: Any) -> Dict[str, Any]:
    """口径对照：行业吨公里法 vs 我们段级自底向上（EcoTransIT 式）。"""
    return _kernel.tonne_km_comparison(our_g_per_tkm, **kwargs)


# ---------------- 发票智能录入桥（docs/44） ----------------
# 识别与口径都在 src/invoice.py；本节只转发，页面不写一行业务公式。
# 纪律同演算桥：结果绝不写回结果表，也不进 kpi/gate（对拍口径与情景推演分列）。

#: DeepSeek key 的读取顺序：环境变量 → config/deepseek_key.txt（永不入库、永不进前端代码）
def invoice_api_key() -> str:
    """读取 DeepSeek API key（服务端读取）。读不到返回空串，页面据此禁用识别。"""
    key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if key:
        return key
    key_file = ROOT / "config" / "deepseek_key.txt"
    if key_file.exists():
        text = key_file.read_text(encoding="utf-8", errors="ignore").strip()
        if text.upper().startswith("DEEPSEEK_API_KEY="):
            text = text.split("=", 1)[1].strip()
        if text and not text.startswith("请至"):
            return text
    return ""


def extract_invoice(image_bytes: bytes, filename: str = "invoice.jpg") -> Dict[str, Any]:
    """第 9 页：识别发票图片 → 归一字段。逻辑在 src/invoice.py，本函数只转发。"""
    return _invoice.extract_invoice_fields(image_bytes, invoice_api_key(), filename)


def invoice_summary(fuel_liters: Optional[float], distance_km: Optional[float],
                    **kwargs: Any) -> Dict[str, Any]:
    """第 9 页：发票碳核算（油量直算轨 + 里程因子表轨 + 交叉校验）。只转发。"""
    return _invoice.invoice_summary(fuel_liters, distance_km, **kwargs)


def calc_carbon(load: float, distance: float, truck_type: Any = "重型") -> float:
    """兼容既有业务入口：载重(t) + 里程(km) + 车型 -> 碳排量(kgCO2)。"""
    return _invoice.calc_carbon(load, distance, truck_type=truck_type)


def amap_key_available() -> bool:
    return bool(_amap_client.load_amap_key())


def amap_driving_routes(origin: str, destination: str) -> List[Dict[str, Any]]:
    """路线沙盘：高德驾车规划 2.0，返回全部备选路线及 polyline。"""
    return _amap_client.fetch_driving_routes(origin, destination)


def amap_js_available() -> bool:
    return _amap_js.js_available()


def amap_js_credentials() -> tuple[str, str]:
    """仅在生成内嵌地图 HTML 时调用；Key 不进入日志或页面正文。"""
    return _amap_js.load_js_credentials()


def calc_carbon_for_route(route: Dict[str, Any], truck_type: str,
                          load: float) -> Dict[str, Any]:
    return _route_carbon.calc_carbon_for_route(route, truck_type, load)


def evaluate_amap_routes(origin: str, destination: str, truck_type: str,
                         load: float, carbon_price: float) -> List[Dict[str, Any]]:
    routes = amap_driving_routes(origin, destination)
    return _route_carbon.evaluate_routes(routes, truck_type, load, carbon_price)


def evaluate_route_records(routes: List[Dict[str, Any]], truck_type: str, load: float,
                           carbon_price: float, energy_cost_per_km: float = 0.0) -> List[Dict[str, Any]]:
    return _route_carbon.evaluate_routes(
        routes, truck_type, load, carbon_price, energy_cost_per_km)


def baseline_route_choices(routes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return _route_carbon.as_route_choices(routes)


def evolve_route_options(routes: List[Dict[str, Any]], **kwargs: Any) -> List[Dict[str, Any]]:
    return _evolution.evolve_routes(routes, **kwargs)


def route_memory() -> pd.DataFrame:
    return _evolution.load_route_memory()


def save_route_memory(route: Dict[str, Any]) -> Dict[str, Any]:
    return _evolution.save_route_to_memory(route)


def invoice_fuel_with_estimation(value: Any) -> tuple[float, bool]:
    return _invoice.fuel_with_estimation(value)


def sensor_data() -> pd.DataFrame:
    """实时读取边缘端 CSV，不缓存，供 2 秒 fragment 刷新。"""
    return _sensor.read_sensor_data(ROOT / "sensor_data.csv")


def sensor_warning_ledger(frame: pd.DataFrame, **kwargs: Any) -> pd.DataFrame:
    return _sensor.build_warning_ledger(frame, **kwargs)


def highlight_sensor_warning(row: pd.Series) -> List[str]:
    return _sensor.highlight_warning(row)


def invoice_carbon(record: Dict[str, Any]) -> Dict[str, Any]:
    """通用财务发票核算桥：活动数据优先、支出法兜底；只转发内核。"""
    return _invoice.calculate_invoice_carbon(record)


def invoice_factors() -> Dict[str, Any]:
    """发票页用的柴油因子（来自 config/calc_factors.json，唯一口径来源）。"""
    return _invoice.diesel_factors()


def invoice_consumption_table() -> Dict[int, float]:
    """车型大类 → 油耗基准（L/100km）假设值。页面据此打黄框。"""
    return dict(_invoice.TRUCK_CONSUMPTION)


def invoice_vclass_names() -> Dict[int, str]:
    """车型大类中文名。"""
    return dict(_invoice.VCLASS_NAME)


def invoice_mock_mode() -> bool:
    """是否处于 mock 模式（不联网、返回演示识别值）。页面据此显式标注。"""
    return bool(_invoice.MOCK_MODE)


def generate_disclosure_note(snapshot: Dict[str, Any]) -> str:
    return _disclosure.generate_disclosure_note(snapshot, invoice_api_key())


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
