# -*- coding: utf-8 -*-
"""碳排基线模型（docs/35 §7）。

第一版口径：**固定因子**，不接速度修正、不接 VSP。

    EF_base = 1055.95 g/km     # 30 t 牵引车，50 km/h 基准
    carbon_kg_step = EF_base × distance_km / 1000
    carbon_total  = Σ carbon_kg_step

三个纪律
--------
1. **道路等级不参与碳排计算**（docs/35 §9 第 7 条）。等级只用于解释
   速度/结构差异，**不给碳排乘任何权重**。碳排 = 距离 × 因子，仅此而已。
2. **不把固定因子说成精确碳账**。``factor_mode = "fixed_50"``，
   页面与文档必须写"固定因子基线"，不能写成"实测碳排"。
3. **因子可替换、表结构不变**。等周yx因子表、Mofusand VSP 结果回来，
   只替换 :func:`emission_factor_gkm` 的实现，**不改 steps.parquet 的字段**。

.. note:: EF_base = 1055.95 g/km 从哪来

   这是 30 t 牵引车头在 50 km/h 基准工况下的**有效排放因子**
   （含 TTW，物理量纲 g/(km·辆)），与项目既有因子表同源口径。
   全线 1,191 km × 1055.95 g/km ≈ 1,258 kg，与 docs/35 §7 的预期一致。

   等价形式：30 t 载重 × 1,191 km = 35,730 t·km，
   1258 kg ÷ 35,730 t·km ≈ 35.2 g/t·km —— 这是**含车辆总重的**车公里口径，
   换算成吨公里口径时务必标明用的是哪一个，否则会和赛题的
   段级吨公里口径（14.67 g/s 等）打架。
"""

from __future__ import annotations

from typing import Optional

#: 因子版本号。因子表一改，这个号跟着改（文档与 summary 都要引用它）。
FACTOR_VERSION = "JCB2023-BASE-v1"

#: 因子模式：固定 50 km/h，不做速度修正。
FACTOR_MODE = "fixed_50"

#: 车型标识
VEHICLE = "30t_tractor"

#: 基准速度（km/h），写进文档用
BASE_SPEED_KMH = 50.0

#: 基准因子（g/km）
EF_BASE_GKM = 1055.95


def emission_factor_gkm(vehicle: str = VEHICLE, load_t: float = 30.0,
                        speed_kmh: Optional[float] = None) -> float:
    """返回排放因子（g/km）。

    第一版：**恒定返回** :data:`EF_BASE_GKM`，忽略速度与载重。

    后续接 VSP 时（docs/35 §7「等周yx因子表、Mofusand VSP 结果回来，
    只替换 EF_base 函数，不改表结构」），本函数改为查表/插值，
    上层与表结构不动。
    """
    if vehicle != VEHICLE:
        # 第一版只支持一个车型；换车型必须先补因子表，不能静默套用。
        raise ValueError(
            "第一版只支持 %s，收到 %r——换车型请先补因子表，"
            "不要静默套用 30t 牵引车的因子。" % (VEHICLE, vehicle))
    return EF_BASE_GKM


def step_carbon_kg(distance_km: float, vehicle: str = VEHICLE,
                    load_t: float = 30.0) -> float:
    """单 step 碳排（kg）。"""
    return emission_factor_gkm(vehicle, load_t) * float(distance_km) / 1000.0


def total_carbon_kg(steps, vehicle: str = VEHICLE, load_t: float = 30.0) -> float:
    """整条路线碳排（kg）。= Σ step carbon（逐步求和，保证与表一致）。"""
    return sum(step_carbon_kg(float(getattr(s, "distance_km", 0.0)), vehicle, load_t)
               for s in steps)
