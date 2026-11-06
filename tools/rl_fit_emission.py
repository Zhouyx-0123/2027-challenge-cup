# -*- coding: utf-8 -*-
"""标定路段级排放因子系数（占位标定，不是 PEMS 实测）。

做什么
------
用户给定的 EF 公式有 8 个系数（A..D 速度项 + a..d 功率耦合项）。仓库里没有
PEMS 数据，只有「燃料守恒 + 功率模型」这条物理链，于是本工具：

1. 用功率模型在 15–90 km/h × 三档载重上生成**目标 EF 曲线**（含低速起停惩罚，
   这是功率模型本身没有的项，必须由 A/C/D 吸收）；
2. 把既有因子表在 50 km/h 的锚点（重型 30 t ≈ 1055.95 g/km，来自
   ``src/factor_model.FACTOR_BY_CLASS_LOAD``）作为高权重样本并入，保证新公式
   与主碳账**同量级**（差 15% 内报警，docs/59 §3.4）；
3. 非负最小二乘拟合 7 个可辨识系数，输出 R²、RMSE、锚点偏差。

**这个结果是占位系数**：接入 PEMS/OBD 实测后，用
``src.rl.emission.calibrate_coefficients`` 重跑本脚本，替换
``config/rl_policy.json`` 的 ``emission.coefficients`` 并 bump 版本。

用法
----
    python tools/rl_fit_emission.py               # 打印 + 落 outputs/rl/emission_fit.json
    python tools/rl_fit_emission.py --vclass 3
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rl import emission  # noqa: E402

#: 慢速起停惩罚：城市工况低速段每公里多耗的比例（35 km/h 以上不再加罚）
STOPGO_SPEED_KMH = 45.0
STOPGO_PENALTY = 0.80
#: 拟合用载重档
FIT_LOADS = (4.5, 18.0, 30.0)
#: 目标曲线速度网格
FIT_SPEEDS = tuple(range(15, 95, 5))
#: 既有因子表锚点（50 km/h，各车型取各自的代表载重档）
#: 数值来自 src/factor_model.FACTOR_BY_CLASS_LOAD
ANCHOR_SPEED = 50.0
ANCHORS = {
    1: {"load_t": 4.5, "g_per_km": 394.560},
    2: {"load_t": 12.0, "g_per_km": 544.752},
    3: {"load_t": 30.0, "g_per_km": 1055.952},
}
ANCHOR_WEIGHT = 6


#: 发动机有效热效率（柴油重卡常用 0.38–0.42，取中值）。用它把「每升柴油的碳」
#: 折算成「每 kWh 机械功的碳」，即目标曲线的物理主力项。
ENGINE_EFFICIENCY = 0.40
#: 燃料守恒碳强度：3.16 kg CO₂/L ÷ (9.94 kWh/L × 0.40) ≈ 795 g CO₂/kWh
A_PHYS_G_PER_KWH = (emission.DIESEL_KG_CO2_PER_L * 1000.0
                    / (emission.DIESEL_KWH_PER_L * ENGINE_EFFICIENCY))


def _physical_ef(v: float, load: float, vclass: int) -> float:
    """目标曲线：燃料守恒 + 功率模型，叠加速度相关的起停惩罚。

    这里**不读 DEFAULT_COEFFICIENTS.a**，否则「拟合结果喂回目标曲线」会自我循环，
    换一次默认系数就得到新一套系数。物理常量必须钉死在工具里。
    """
    p = emission.tractive_power_kw(v, load, vclass=vclass)
    ef = A_PHYS_G_PER_KWH * p / max(v, emission.MIN_SPEED_KMH)
    penalty = 1.0 + STOPGO_PENALTY * max(0.0, (STOPGO_SPEED_KMH - v) / STOPGO_SPEED_KMH)
    return ef * penalty


def build_samples(vclass: int):
    anchor_spec = ANCHORS[int(vclass)]
    rows = []
    for load in FIT_LOADS:
        for v in FIT_SPEEDS:
            rows.append({
                "speed_kmh": float(v),
                "load_t": float(load),
                "power_kw": emission.tractive_power_kw(v, load, vclass=vclass),
                "ef_g_per_km": _physical_ef(v, load, vclass),
            })
    anchor = {
        "speed_kmh": ANCHOR_SPEED,
        "load_t": anchor_spec["load_t"],
        "power_kw": emission.tractive_power_kw(
            ANCHOR_SPEED, anchor_spec["load_t"], vclass=vclass),
        "ef_g_per_km": anchor_spec["g_per_km"],
    }
    rows.extend([anchor] * ANCHOR_WEIGHT)
    return rows, [1.0] * (len(rows) - ANCHOR_WEIGHT) + [3.0] * ANCHOR_WEIGHT


def main() -> int:
    parser = argparse.ArgumentParser(description="标定路段级排放因子系数")
    parser.add_argument("--vclass", type=int, default=3, choices=(1, 2, 3))
    parser.add_argument("--out", default=str(ROOT / "outputs" / "rl" / "emission_fit.json"))
    args = parser.parse_args()

    samples, weights = build_samples(args.vclass)
    fit = emission.calibrate_coefficients(
        samples, vclass=args.vclass, min_samples=12, weights=weights)
    coeff = emission.EmissionCoefficients.from_vector(
        [fit["coefficients"][k] for k in ("A", "a", "b", "C", "c", "D", "d")])
    anchor_spec = ANCHORS[int(args.vclass)]

    check_load = anchor_spec["load_t"]
    rows = []
    for v in (20.0, 50.0, 80.0):
        p = emission.tractive_power_kw(v, check_load, vclass=args.vclass)
        rows.append({
            "speed_kmh": v,
            "power_kw": round(p, 2),
            "ef_fit_g_per_km": round(coeff.ef_g_per_km(v, p, 1.0), 2),
        })
    anchor_p = emission.tractive_power_kw(ANCHOR_SPEED, check_load, vclass=args.vclass)
    anchor_ef = coeff.ef_g_per_km(ANCHOR_SPEED, anchor_p, 1.0)
    anchor_dev = (anchor_ef - anchor_spec["g_per_km"]) / anchor_spec["g_per_km"] * 100.0

    payload = {
        "vclass": args.vclass,
        "note": "占位标定：燃料守恒+功率模型生成目标曲线，锚定既有因子表；非 PEMS 实测",
        "coefficients": fit["coefficients"],
        "r2": fit["r2"],
        "rmse_g_per_km": fit["rmse_g_per_km"],
        "samples": fit["samples"],
        "anchor": {
            "speed_kmh": ANCHOR_SPEED, "load_t": check_load,
            "repo_factor_g_per_km": anchor_spec["g_per_km"],
            "fit_ef_g_per_km": round(anchor_ef, 3),
            "deviation_pct": round(anchor_dev, 3),
        },
        "curve": rows,
        "physical": {
            "kwh_per_l": emission.DIESEL_KWH_PER_L,
            "kg_co2_per_l": emission.DIESEL_KG_CO2_PER_L,
        },
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("\n已写入 %s" % out)
    if abs(anchor_dev) > 15.0:
        print("警告：锚点偏差 %.1f%% 超过 15%%，不要直接上线" % anchor_dev)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
