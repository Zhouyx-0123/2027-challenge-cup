# -*- coding: utf-8 -*-
"""路段级碳排因子：功率-速度二元模型（分段可覆盖系数）。

公式（用户给定，见 ``docs/59`` §3）：

    EF(g/km) = [A + a·(kW_t/v) + B + b·kW_t + (C + c·kW_t)·v + (D + d·kW_t)·v²] · k_{i,j}

符号口径
--------
v       路段平均车速，km/h（不是瞬时车速；瞬时量已由 ``kW_t`` 吸收）
kW_t    该路段的平均发动机输出功率，kW。两条来源：
        ① OBD 实测 ``rpm × 负荷率 × 最大扭矩``（工科单片机通道，最硬）；
        ② 功率模型 ``tractive_power_kw()``（只有 GPS 时速/坡度/载重时的回退）
A..D    与功率无关的速度曲线项，单位 g/km（B 与 A 同为常数项，默认 B=0，见下）
a..d    功率耦合项；``a`` 的量纲是 g CO₂/kWh，即「每公里机械功的碳强度」
k_{i,j} 道路等级 i × 载重分箱 j 的修正系数（k-means 分箱后按偏差中位数标定，
        与 ``docs/45`` §5「多车样本在同一条路上的偏差中位数 = 路段系数」同一纪律）

为什么是这一组基函数
--------------------
把 ``P/v`` 展开：巡航时 ``P ≈ F·v``，于是 ``a·P/v = a·F`` —— 这一项直接等于
「阻力 × 碳强度」，是燃料守恒法的物理主干；``P_aux/v`` 自然给出低速段上扬的
1/v 形状；``v²`` 与 ``P·v²`` 描述高速风阻段。A..D 则是功率模型没覆盖到的
道路条件偏差（停车怠速、制动损耗、路面），由 PEMS/OBD 数据回归得到。

**A 与 B 共线**：两项都是常数，同时拟合不可辨识。默认 ``B = 0``，
标定时把常数项全部并进 ``A``（``calibrate_coefficients`` 只拟合 A）。

边界处理
--------
v < 5 km/h 时公式的 1/v 发散且物理意义失真（停车段应走「时长 × 怠速油耗」通道，
不是「每公里」通道）。本模块统一把 v 夹到 ``MIN_SPEED_KMH``，并在返回里置
``low_speed_clamped=True`` 供上游打标；这是**假设**，不是实测。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

#: 公式中 v 的下限（km/h）。低于此值夹住，避免 1/v 发散。
MIN_SPEED_KMH = 5.0
#: 公式中 v 的上限（km/h）。
MAX_SPEED_KMH = 130.0
#: 速度分段边界（km/h）；用于「分段系数」覆盖，默认各段共用一套系数。
SPEED_BIN_EDGES: Tuple[float, ...] = (0.0, 20.0, 40.0, 60.0, 80.0, 130.0)
#: 重力加速度 / 空气密度
GRAVITY = 9.80665
AIR_DENSITY = 1.225

#: 车型中文名（与 src/invoice.py 的 vclass 口径一致：1 轻 / 2 中 / 3 重）
VCLASS_NAMES: Dict[int, str] = {1: "轻型", 2: "中型", 3: "重型"}


@dataclass(frozen=True)
class VehicleParams:
    """整车参数：决定「同样的速度与坡度，需要多少 kW」。"""

    curb_mass_kg: float
    cd_a_m2: float          # 风阻系数 × 迎风面积
    crr: float              # 滚动阻力系数
    driveline_eff: float    # 传动效率
    aux_kw: float           # 附件功率（空调/取力/电器），停车也耗
    max_power_kw: float     # 额定功率，用于截断
    idle_kw: float = 8.0    # 怠速/极低速时的等效机械功率下界（见 tractive_power_kw）

    def mass_kg(self, load_t: float) -> float:
        return self.curb_mass_kg + max(float(load_t), 0.0) * 1000.0


#: 车型默认参数（假设值，页面上应打黄框；换实测整车参数时只改这里或 config）
DEFAULT_VEHICLES: Dict[int, VehicleParams] = {
    1: VehicleParams(curb_mass_kg=2500.0, cd_a_m2=3.2, crr=0.010, driveline_eff=0.90,
                     aux_kw=1.0, max_power_kw=120.0, idle_kw=4.0),
    2: VehicleParams(curb_mass_kg=8000.0, cd_a_m2=5.0, crr=0.008, driveline_eff=0.89,
                     aux_kw=2.0, max_power_kw=200.0, idle_kw=8.0),
    3: VehicleParams(curb_mass_kg=15000.0, cd_a_m2=6.8, crr=0.007, driveline_eff=0.88,
                     aux_kw=3.0, max_power_kw=330.0, idle_kw=12.0),
}

#: 柴油能量口径：1 L ≈ 9.94 kWh 热值；出账碳强度 3.16 kg CO₂/L（TTW+WTT，与
#: config/calc_factors.json 的 2.63+0.53 一致）。两者相除即 g CO₂/kWh 热值。
DIESEL_KWH_PER_L = 9.94
DIESEL_KG_CO2_PER_L = 3.16


@dataclass(frozen=True)
class EmissionCoefficients:
    """EF 公式的 8 个系数（B 默认 0，见模块 docstring）。"""

    A: float = 0.0
    a: float = 794.0
    B: float = 0.0
    b: float = 0.0
    C: float = 0.0
    c: float = 0.0
    D: float = 0.0
    d: float = 0.0

    def ef_g_per_km(self, speed_kmh: float, power_kw: float, k_factor: float = 1.0) -> float:
        """按公式算 g/km；输入非法时夹到物理可解释区间，不做静默兜底。"""
        v = float(speed_kmh)
        p = float(power_kw)
        k = float(k_factor)
        if not math.isfinite(v) or not math.isfinite(p) or not math.isfinite(k):
            raise ValueError("EF 输入必须是有限数值")
        if k <= 0:
            raise ValueError("k_{i,j} 必须为正数")
        v = min(max(v, MIN_SPEED_KMH), MAX_SPEED_KMH)
        p = max(p, 0.0)
        base = (self.A + self.a * (p / v) + self.B + self.b * p
                + (self.C + self.c * p) * v + (self.D + self.d * p) * v * v)
        return max(base * k, 0.0)

    def basis(self, speed_kmh: float, power_kw: float) -> np.ndarray:
        """设计矩阵的一行 ``[1, P/v, P, v, P·v, v², P·v²]``（B 并入 A，不单列）。"""
        v = min(max(float(speed_kmh), MIN_SPEED_KMH), MAX_SPEED_KMH)
        p = max(float(power_kw), 0.0)
        return np.array([1.0, p / v, p, v, p * v, v * v, p * v * v], dtype=float)

    @classmethod
    def from_vector(cls, vector: Sequence[float]) -> "EmissionCoefficients":
        """``[A, a, b, C, c, D, d]`` → 系数对象（B 恒为 0）。"""
        values = [float(x) for x in vector]
        if len(values) != 7:
            raise ValueError("系数向量长度必须为 7：[A, a, b, C, c, D, d]")
        return cls(A=values[0], a=values[1], b=values[2], C=values[3],
                   c=values[4], D=values[5], d=values[6])

    def to_dict(self) -> Dict[str, float]:
        return {"A": self.A, "a": self.a, "B": self.B, "b": self.b,
                "C": self.C, "c": self.c, "D": self.D, "d": self.d}


#: 默认系数（占位值）：用「燃料守恒 + 功率模型」在 15–90 km/h × 三档载重上生成
#: 目标曲线，再把既有因子表 50 km/h 的锚点加权并入，做最小二乘拟合
#: （R² = 0.971 / 0.994 / 0.993；锚点偏差 −13.6% / +1.6% / −1.1%）。
#: 复算脚本：``python tools/rl_fit_emission.py``。**不是 PEMS 实测**，
#: 接入实测数据后必须用 ``calibrate_coefficients()`` 重标并 bump config 版本。
DEFAULT_COEFFICIENTS: Dict[int, EmissionCoefficients] = {
    1: EmissionCoefficients(A=388.582, a=803.566, C=-9.3645, D=0.05493),
    2: EmissionCoefficients(A=488.822, a=895.000, C=-17.5691, D=0.12395),
    3: EmissionCoefficients(A=592.210, a=870.885, C=-20.0778, D=0.14009),
}


def vehicle_for(vclass: int) -> VehicleParams:
    key = int(vclass)
    if key not in DEFAULT_VEHICLES:
        raise ValueError("车型 vclass 必须为 1、2 或 3，收到 %r" % (vclass,))
    return DEFAULT_VEHICLES[key]


def coefficients_for(vclass: int,
                     table: Optional[Dict[int, EmissionCoefficients]] = None
                     ) -> EmissionCoefficients:
    key = int(vclass)
    source = table or DEFAULT_COEFFICIENTS
    if key not in source:
        raise ValueError("车型 vclass 必须为 1、2 或 3，收到 %r" % (vclass,))
    return source[key]


def tractive_power_kw(speed_kmh: float, load_t: float, vclass: int = 3,
                      grade_pct: float = 0.0, accel_ms2: float = 0.0,
                      vehicle: Optional[VehicleParams] = None) -> float:
    """功率模型：坡道/加速/风阻/滚阻 → 发动机输出功率（kW，含附件）。

    这是 OBD 缺失时的回退通道；OBD 在位时以实测为准（``power_from_obd``）。
    制动力不回收（柴油车无能量回收），负加速度的惯性功截断为 0。
    """
    params = vehicle or vehicle_for(vclass)
    v_ms = min(max(float(speed_kmh), 0.0), MAX_SPEED_KMH) / 3.6
    mass = params.mass_kg(load_t)
    grade = float(grade_pct) / 100.0
    accel = max(float(accel_ms2), 0.0)
    rolling = params.crr * mass * GRAVITY
    aero = 0.5 * AIR_DENSITY * params.cd_a_m2 * v_ms * v_ms
    grade_force = mass * GRAVITY * grade
    inertia = mass * accel
    wheel_w = (rolling + aero + grade_force + inertia) * v_ms
    idle_kw = float(params.idle_kw)
    if v_ms <= 1e-9:
        return float(max(idle_kw, params.aux_kw))
    engine_kw = wheel_w / max(params.driveline_eff, 1e-6) / 1000.0 + params.aux_kw
    # 下坡/滑行时按"零负荷"算会让重卡在 1% 下坡上变成 70 g/km，明显违背实测。
    # 真实发动机在怠速/滑行时仍烧掉相当于 idle_kw 的机械功（摩擦泵损），故设下界。
    return float(min(max(engine_kw, idle_kw), params.max_power_kw))


def power_from_obd(rpm: float, engine_load_pct: float, max_torque_nm: float,
                   aux_kw: float = 0.0, max_power_kw: Optional[float] = None) -> float:
    """OBD 通道：``P(kW) = 扭矩(N·m) × 角速度(rad/s) / 1000``。

    扭矩用「负荷率 × 最大扭矩」近似（OBD 的 Load_pct 就是当前扭矩占最大扭矩的比例，
    这是 ISO 15031 的定义，不是油门开度）。怠速无负荷时仍输出附件功率。
    """
    load = min(max(float(engine_load_pct), 0.0), 100.0) / 100.0
    omega = max(float(rpm), 0.0) * 2.0 * math.pi / 60.0
    torque = load * max(float(max_torque_nm), 0.0)
    kw = torque * omega / 1000.0 + max(float(aux_kw), 0.0)
    if max_power_kw is not None:
        kw = min(kw, float(max_power_kw))
    return float(max(kw, 0.0))


def speed_bin_index(speed_kmh: float,
                    edges: Sequence[float] = SPEED_BIN_EDGES) -> int:
    """速度所属分段下标（左闭右开，最后一箱右闭）。"""
    v = float(speed_kmh)
    for index in range(len(edges) - 1):
        if v < edges[index + 1]:
            return index
    return len(edges) - 2


def fuel_from_carbon(co2_kg: float, kg_co2_per_l: float = DIESEL_KG_CO2_PER_L) -> float:
    """碳 → 油量（L）：燃料守恒法反推，用于与 OBD 实测油量对拍。"""
    if kg_co2_per_l <= 0:
        raise ValueError("柴油因子必须为正")
    return float(co2_kg) / float(kg_co2_per_l)


def segment_carbon(length_km: float, speed_kmh: float, vclass: int = 3,
                   load_t: float = 18.0, k_factor: float = 1.0,
                   power_kw: Optional[float] = None,
                   grade_pct: float = 0.0,
                   coefficients: Optional[EmissionCoefficients] = None,
                   vehicle: Optional[VehicleParams] = None,
                   speed: str = "avg") -> Dict[str, Any]:
    """一段路的碳排：``EF × 里程``，并回吐中间量供审计与前端解释。

    ``speed`` 取 ``avg`` 时用路段平均车速（正常口径）；取 ``obd`` 表示功率由 OBD
    实测提供（此时 ``power_kw`` 必填，v 仍用平均车速）。
    """
    if length_km <= 0:
        raise ValueError("路段长度必须大于 0")
    coeff = coefficients or coefficients_for(vclass)
    params = vehicle or vehicle_for(vclass)
    v = float(speed_kmh)
    clamped = v < MIN_SPEED_KMH
    if power_kw is None:
        p = tractive_power_kw(v, load_t, vclass=vclass, grade_pct=grade_pct, vehicle=params)
        power_source = "model"
    else:
        if speed == "obd" and float(power_kw) < 0:
            raise ValueError("OBD 功率不能为负")
        p = float(power_kw)
        power_source = "obd"
    ef = coeff.ef_g_per_km(v, p, k_factor)
    co2_kg = ef * float(length_km) / 1000.0
    v_eff = min(max(v, MIN_SPEED_KMH), MAX_SPEED_KMH)
    return {
        "length_km": round(float(length_km), 4),
        "speed_kmh": round(v_eff, 3),
        "power_kw": round(p, 3),
        "power_source": power_source,
        "ef_g_per_km": round(ef, 4),
        "co2_kg": round(co2_kg, 6),
        "fuel_l": round(fuel_from_carbon(co2_kg), 6),
        "energy_kwh_per_km": round(p / v_eff, 6),
        "k_factor": round(float(k_factor), 6),
        "speed_bin": speed_bin_index(v_eff),
        "low_speed_clamped": bool(clamped),
    }


def lookup_k_factor(table: Optional[Dict[str, Any]], road_class: int, load_t: float,
                    load_bins: Optional[Sequence[float]] = None) -> float:
    """``k_{i,j}``：``table[str(road_class)][j]``；缺表时返回 1.0 并让上游打标。

    ``load_bins`` 是 k-means 载重分箱的**上界**（升序，长度 = 分箱数），
    由 ``clustering.LoadBinClusterer`` 产出后落进 config，保证训练与推理同一套分箱。
    """
    if not table:
        return 1.0
    bins = list(load_bins) if load_bins else []
    if bins:
        index = int(np.searchsorted(np.asarray(bins, dtype=float), float(load_t),
                                    side="left"))
        # bins 是"分箱上界"，k 个边界 → k+1 个箱；越界落到最后一箱
        index = min(index, len(bins))
    else:
        index = 0
    row = table.get(str(int(road_class)))
    if not row:
        return 1.0
    try:
        return float(row[index])
    except (IndexError, TypeError, ValueError):
        return 1.0


def _nnls(design: np.ndarray, target: np.ndarray, max_rounds: int = 60) -> np.ndarray:
    """非负最小二乘（活动集迭代）；系数物理上不能为负，负值一律钳 0。"""
    x = np.linalg.lstsq(design, target, rcond=None)[0]
    for _ in range(max_rounds):
        negative = x < 0
        if not negative.any():
            break
        x[negative] = 0.0
        free = ~negative
        if not free.any():
            break
        residual = target - design[:, ~free] @ x[~free]
        x[free] = np.linalg.lstsq(design[:, free], residual, rcond=None)[0]
    return np.maximum(x, 0.0)


#: 可辨识的基函数：项名 → 生成函数。A 与 B 都是常数，同时拟合不可辨识。
TERM_BUILDERS: Dict[str, Any] = {
    "A": lambda v, p: 1.0,
    "a": lambda v, p: p / v,
    "B": lambda v, p: 1.0,
    "b": lambda v, p: p,
    "C": lambda v, p: v,
    "c": lambda v, p: p * v,
    "D": lambda v, p: v * v,
    "d": lambda v, p: p * v * v,
}

#: 默认只拟合 4 个可辨识项：常数项、功率项、两个速度项。
#: ``b/c/d`` 描述「功率与速度的交互」，物理模型里假设不存在这种交互，
#: 必须有 PEMS 实测（不同载重 × 不同速度的组合）才能辨识，故默认 0。
DEFAULT_FIT_TERMS: Tuple[str, ...] = ("A", "a", "C", "D")


def calibrate_coefficients(observations: Iterable[Dict[str, Any]],
                           vclass: int = 3, vehicle: Optional[VehicleParams] = None,
                           min_samples: int = 12,
                           terms: Sequence[str] = DEFAULT_FIT_TERMS,
                           weights: Optional[Sequence[float]] = None,
                           nonnegative: bool = False) -> Dict[str, Any]:
    """用实测样本回归系数。

    ``observations`` 每条形如 ``{"speed_kmh": 42.0, "power_kw": 88.0,
    "ef_g_per_km": 1120.0}`` 或 ``{"speed_kmh":…, "power_kw":…, "length_km":…,
    "co2_kg":…}``。样本太少会拒绝拟合——宁可报错，也不拿 3 个点编一套系数。

    ``terms`` 默认只含可辨识的 4 项（见 ``DEFAULT_FIT_TERMS``）；拿到 PEMS
    数据后可传全 7 项，但要注意 ``P`` 与 ``v`` 高度相关，全项拟合容易过拟合，
    这也是本模块默认不给 ``b/c/d`` 编数字的原因。

    ``nonnegative=True`` 时用非负最小二乘。默认放开符号：低速起停段的弯曲
    必须靠 ``C < 0, D > 0`` 这类经验项表达，硬性非负会把曲线拉平（实测 R²
    从 0.79 掉到 0.98 的差别）。放开符号后仍会校验：拟合域内 EF 不得为负。
    """
    chosen = [str(t) for t in terms]
    unknown = [t for t in chosen if t not in TERM_BUILDERS]
    if unknown:
        raise ValueError("未知基函数项：%s" % ",".join(unknown))
    if "A" in chosen and "B" in chosen:
        raise ValueError("A 与 B 同为常数项，不可同时拟合（默认 B=0）")
    rows: List[Tuple[float, float, float]] = []
    for obs in observations:
        v = float(obs["speed_kmh"])
        p = float(obs["power_kw"])
        if "ef_g_per_km" in obs:
            ef = float(obs["ef_g_per_km"])
        elif "co2_kg" in obs and "length_km" in obs:
            length = float(obs["length_km"])
            if length <= 0:
                continue
            ef = float(obs["co2_kg"]) * 1000.0 / length
        else:
            raise ValueError("样本需要 ef_g_per_km 或 (co2_kg, length_km)")
        if ef < 0 or v <= 0:
            continue
        rows.append((v, p, ef))
    if len(rows) < int(min_samples):
        raise ValueError("样本不足：%d < %d，拒绝拟合" % (len(rows), int(min_samples)))
    matrix = np.array([[TERM_BUILDERS[t](min(max(v, MIN_SPEED_KMH), MAX_SPEED_KMH), p)
                        for t in chosen] for v, p, _ in rows], dtype=float)
    target = np.array([ef for _, _, ef in rows], dtype=float)
    sample_weight = (np.ones(len(rows)) if weights is None
                     else np.asarray(list(weights), dtype=float))
    if sample_weight.shape != target.shape:
        raise ValueError("权重数量必须等于样本数")
    root_w = np.sqrt(np.maximum(sample_weight, 0.0))
    design = matrix * root_w[:, None]
    response = target * root_w
    coef = _nnls(design, response) if nonnegative else np.linalg.lstsq(
        design, response, rcond=None)[0]
    predicted = matrix @ coef
    if predicted.min() < 0:
        raise ValueError("拟合结果在样本域内出现负排放因子（最小值 %.3f g/km），"
                         "改用 nonnegative=True 或检查样本" % predicted.min())
    ss_res = float(np.sum((target - predicted) ** 2))
    ss_tot = float(np.sum((target - target.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    payload = EmissionCoefficients().to_dict()
    payload.update({name: 0.0 for name in ("A", "a", "B", "b", "C", "c", "D", "d")})
    for name, value in zip(chosen, coef):
        payload[name] = round(float(value), 6)
    return {
        "vclass": int(vclass),
        "samples": len(rows),
        "terms": chosen,
        "coefficients": payload,
        "r2": round(r2, 6),
        "rmse_g_per_km": round(float(np.sqrt(ss_res / len(rows))), 4),
        "provenance": "由 %d 条实测样本%s拟合（项：%s）"
                      % (len(rows), "非负最小二乘" if nonnegative else "最小二乘",
                         ",".join(chosen)),
    }


def self_check() -> Dict[str, Any]:
    """模块自检：三车型在 50 km/h / 满载 30 t 的 EF 与既有因子表量级对照。"""
    out: Dict[str, Any] = {}
    for vclass in (1, 2, 3):
        p = tractive_power_kw(50.0, 30.0 if vclass == 3 else 18.0, vclass=vclass)
        ef = coefficients_for(vclass).ef_g_per_km(50.0, p, 1.0)
        out[VCLASS_NAMES[vclass]] = {"power_kw": round(p, 2), "ef_g_per_km": round(ef, 2)}
    return out


__all__ = [
    "MIN_SPEED_KMH", "MAX_SPEED_KMH", "SPEED_BIN_EDGES", "GRAVITY", "AIR_DENSITY",
    "VCLASS_NAMES", "VehicleParams", "DEFAULT_VEHICLES", "EmissionCoefficients",
    "DEFAULT_COEFFICIENTS", "DIESEL_KWH_PER_L", "DIESEL_KG_CO2_PER_L",
    "vehicle_for", "coefficients_for", "tractive_power_kw", "power_from_obd",
    "speed_bin_index", "fuel_from_carbon", "segment_carbon", "lookup_k_factor",
    "calibrate_coefficients", "self_check",
]
