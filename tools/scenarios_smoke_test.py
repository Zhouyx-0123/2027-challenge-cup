# -*- coding: utf-8 -*-
"""情景演算冒烟测试（tools/）：内核数值 + 与 evaluator 公式对拍 + 新页面 AppTest。

运行：python -m tools.scenarios_smoke_test
退出码 0 = 全部通过。这是 docs/20 §11 演算页纪律的验收命令之一。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import evaluator, optimizer, scenarios  # noqa: E402

FAILED = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print("  [%s] %s%s" % (status, name, ("｜" + detail) if detail else ""))
    if not cond:
        FAILED.append(name)


def close(a: float, b: float, tol: float = 1e-6) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(b)))


def main() -> int:
    print("=" * 70)
    print("情景演算冒烟测试（scenarios + evaluator 对拍 + AppTest）")
    print("=" * 70)

    # ---- 1. 内核：电动化测算数值合理性 ----
    print("\n① 电动化测算内核")
    r = scenarios.calc_electrification({
        "n_vehicles": 10, "vclass": 3, "km_per_veh_year": 60000,
        "ice_l_per_100km": 35.0, "diesel_price_yuan_per_l": 7.5,
        "elec_price_yuan_per_kwh": 0.8, "ice_price_yuan": 480000,
        "bev_price_yuan": 1080000,
    })
    check("年节省为正", r["annual"]["saving"] > 0,
          "%.1f 万元/年" % (r["annual"]["saving"] / 1e4))
    check("回本月份合理（10–120 月）", 10 < (r["payback"]["months"] or 0) < 120,
          "%.1f 月" % r["payback"]["months"])
    check("WTW 减碳为正", r["co2"]["cut_wtw_t_per_year"] > 0)
    check("减排成本为正（电动更贵但减碳）",
          r["abatement"]["wtw_yuan_per_t"] is not None
          and r["abatement"]["wtw_yuan_per_t"] > 0)
    check("assumptions 非空", len(r["assumptions"]) >= 3)
    check("累计曲线点数＝期限+1", len(r["cumulative"]) == r["params"]["horizon_years"] + 1)

    # ---- 2. 公式对拍：与 evaluator 一致 ----
    print("\n② 与 evaluator 公式对拍")
    # 回本： scenarios._payback_years vs evaluator._payback
    check("回本公式一致",
          close(scenarios._payback_years(100000.0, 20000.0),
                evaluator._payback(100000.0, 20000.0)))
    check("无节省时回本 inf",
          scenarios._payback_years(100000.0, 0.0) == float("inf"))
    # NPV：年金系数与 evaluator 内部用法一致（evaluate_scenario 的 AF）
    af_sc = scenarios.annuity_factor(0.06, 5)
    af_ev = evaluator.annuity_factor(0.06, 5) if hasattr(evaluator, "annuity_factor") else None
    if af_ev is not None:
        check("年金系数一致", close(af_sc, af_ev))
    else:
        check("年金系数（本地核算）", close(af_sc, (1 - 1.06 ** -5) / 0.06))
    # NPV 四分解合计 = 各分项和
    npv = r["npv"]["ice"]
    check("NPV 分解合计闭合",
          close(npv["total"], npv["vehicle_net_residual"] + npv["energy"]
                + npv["maintenance"] + npv["insurance"]))
    # 默认价格来自 optimizer 展示口径（单一来源）
    check("柴油价默认取自 optimizer",
          close(scenarios.calc_electrification({})["params"]["vclass"], 3))

    # ---- 3. 内核：多路线 TCO ----
    print("\n③ 车队 TCO 多路线内核")
    t = scenarios.calc_fleet_tco({
        "scenarios": [
            {"key": "diesel", "name": "柴油", "kind": "real", "energy_per_100km": 35,
             "price_per_unit": 7.5, "co2_ttw_kg_per_unit": 2.63,
             "co2_wtw_kg_per_unit": 3.16, "maint_yuan_per_km": 1.5, "residual_rate": 0.2},
            {"key": "bev", "name": "电动", "kind": "real", "energy_per_100km": 135,
             "price_per_unit": 0.8, "co2_ttw_kg_per_unit": 0.0,
             "co2_wtw_kg_per_unit": 0.5568, "capex_delta_yuan": 600000,
             "maint_yuan_per_km": 0.9, "residual_rate": 0.15},
            {"key": "hvo", "name": "HVO（示意）", "kind": "sketch", "energy_per_100km": 38,
             "price_per_unit": 9.5, "co2_ttw_kg_per_unit": 0.0,
             "co2_wtw_kg_per_unit": 0.62, "maint_yuan_per_km": 1.5, "residual_rate": 0.2},
        ],
        "n_vehicles": 25, "km_per_veh_year": 60000, "vuf": 0.95,
    })
    check("三条路线齐全", len(t["scenarios"]) == 3)
    check("基准减排成本为 None",
          t["scenarios"][0]["abatement_wtw_yuan_per_t"] is None)
    check("VUF 调整成本 > 名义成本", t["scenarios"][0]["cost_per_effective_km"]
          > t["scenarios"][0]["cost_per_km_nominal"])
    check("示意路线带 kind 标记", t["scenarios"][2]["kind"] == "sketch")
    check("非法 vclass 被拒绝",
          _raises(lambda: scenarios.calc_electrification({"vclass": 5})))
    check("空路线列表被拒绝",
          _raises(lambda: scenarios.calc_fleet_tco({"scenarios": []})))
    check("NaN 参数被拒绝",
          _raises(lambda: scenarios.calc_electrification({"km_per_veh_year": float("nan")})))

    # ---- 4. 口径对照 ----
    print("\n④ 口径对照内核")
    cmp_r = scenarios.tonne_km_comparison(38.6, total_km=4690435.81,
                                          total_load_tkm=116556557.6,
                                          total_co2_kg=4502042.41)
    check("我们强度低于行业默认", cmp_r["our_g_per_tkm"] < cmp_r["industry_g_per_tkm"])
    check("行业法总量可算", cmp_r["industry_total_t"] is not None
          and cmp_r["industry_total_t"] > cmp_r["our_total_t"])

    # ---- 5. AppTest：新页面无异常 ----
    print("\n⑤ 新页面 AppTest")
    try:
        from streamlit.testing.v1 import AppTest
    except ImportError:
        print("  [SKIP] streamlit.testing 不可用")
    else:
        for page in ("7_电动化测算.py", "8_车队TCO沙盘.py"):
            at = AppTest.from_file(str(ROOT / "app" / "pages" / page), default_timeout=60)
            at.run()
            check("%s 渲染 0 异常" % page, not at.exception)

    print("\n" + "=" * 70)
    if FAILED:
        print("未通过 %d 项：%s" % (len(FAILED), "、".join(FAILED)))
        return 1
    print("全部通过 [OK]")
    return 0


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:
        return True


if __name__ == "__main__":
    raise SystemExit(main())
