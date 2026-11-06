# -*- coding: utf-8 -*-
"""
rules.py —— 业务规则层（纯函数，无 I/O、无全局状态，可单独单测）

本模块只做四件事：
1. 把“数据质量标识”翻译成“数据层级标签”（绿框 / 黄框）；
2. 算两条互不相加的轨道：配额缺口轨道、影子价格轨道；
3. 黄框的不确定性区间；
4. 守一条底线：两层数据不得跨层级合并，不得出现“总计碳成本”。
"""

from __future__ import annotations

TIER_GREEN = "绿框"
TIER_YELLOW = "黄框"
FORBIDDEN_WORD = "总计碳成本"

# 层级白名单：只精确接受这两种规范写法，防止“绿”“框绿”“率高”之类误升格
TIER_TOKENS = {"绿框": TIER_GREEN, "黄框": TIER_YELLOW}


def judge_tier(quality_flag: str) -> str:
    """把“数据质量标识”判定为数据层级标签。

    规则（唯一入口，不接受人工改判）：
        含“绿框” -> “绿框”（范围一 / OBD 实测 / 直接计量层级）
        含“黄框” -> “黄框”（范围三 / GLEC 估算 / 估计层级）

    保守升格原则：
        · 空白、NaN、无法识别 -> 拒绝判定（由调用方按“不得升格、降为黄框”处理）；
        · 同时含两者 -> 拒绝判定，交由人工复核；
        · 不做模糊匹配，不把“绿”“框中”之类字样认定为绿框。

    Args:
        quality_flag: 输入表“数据质量标识”列的值，如“绿框(高可信)”。

    Returns:
        "绿框" 或 "黄框"。

    Raises:
        ValueError: 值为空、同时含两种、或既不含“绿框”也不含“黄框”。
    """
    if quality_flag is None:
        raise ValueError("数据质量标识为空，无法判定数据层级标签")

    text = str(quality_flag).strip()
    if text == "" or text.lower() == "nan":
        raise ValueError("数据质量标识为空，无法判定数据层级标签")

    has_green = "绿框" in text
    has_yellow = "黄框" in text
    if has_green and has_yellow:
        raise ValueError(f"数据质量标识同时含“绿框”与“黄框”，冲突需人工复核：{text}")
    if has_green:
        return TIER_GREEN
    if has_yellow:
        return TIER_YELLOW
    raise ValueError(f"数据质量标识无法判定（既不含“绿框”也不含“黄框”）：{text}")


def demote_unknown(quality_flag) -> str:
    """无法判定时的保守处理：一律降为黄框（估计层级），绝不升格为绿框。

    用于“无标识不得升格”规则：空白、错拼、模糊标签都不许进入范围一/配额缺口轨道。
    """
    return TIER_YELLOW


def calc_gap(emission_kg: float, distance_km: float, baseline: float) -> float:
    """计算单条记录的模拟配额缺口。

    公式：max(0, 绝对碳排 - 行驶里程 × 假设基准线)
    低于基准线的一律记 0（即“盈余（模拟）”），不产生负缺口。

    Args:
        emission_kg: 绝对碳排，kgCO2e。
        distance_km: 行驶里程，km。
        baseline: 假设基准线，kgCO2e/km。

    Returns:
        模拟缺口，kgCO2e，>= 0。
    """
    return max(0.0, float(emission_kg) - float(distance_km) * float(baseline))


def calc_intensity_deviation(emission_kg: float, distance_km: float,
                             baseline: float) -> float | None:
    """相对演示阈值的强度偏离率；返回小数（0.1 = +10%）。

    里程不大于零时强度不适用，不影响原有缺口计算；偏离率不得加总。
    """
    distance = float(distance_km)
    if distance <= 0:
        return None
    return float(emission_kg) / distance / float(baseline) - 1.0


def calc_shadow_cost(emission_kg: float, price_yuan_per_t: float) -> float:
    """计算影子碳成本。

    公式：排放量(kg) / 1000 × 碳价(元/t) = 元。
    注意单位：入参是 kg 与 元/t，内部除以 1000 换算为 t。

    Args:
        emission_kg: 绝对碳排，kgCO2e。
        price_yuan_per_t: 影子碳价，元/tCO2e。

    Returns:
        影子碳成本，元。
    """
    return float(emission_kg) / 1000.0 * float(price_yuan_per_t)


def calc_uncertainty_range(cost_central: float, range_pct: float) -> tuple:
    """按给定幅度给出不确定性区间（仅黄框使用）。

    Args:
        cost_central: 中枢值（元 或 kg 均可，函数不关心单位）。
        range_pct: 相对幅度，0.30 表示 ±30%。

    Returns:
        (下限, 上限) 二元组。
    """
    central = float(cost_central)
    pct = float(range_pct)
    return (central * (1.0 - pct), central * (1.0 + pct))


def validate_no_cross_tier_sum(df_gap, df_shadow) -> None:
    """校验两张表没有跨层级合并，也没有“总计碳成本”。

    校验项：
        1. 缺口表只含绿框（范围一）；
        2. 影子表同时覆盖绿框与黄框（全量）；
        3. 两表不存在名为“总计碳成本”的列；
        4. 两表任何单元格字符串中不出现“总计碳成本”字样。

    Args:
        df_gap: 缺口表 DataFrame，必须含“数据层级标签”列。
        df_shadow: 影子表 DataFrame，必须含“数据层级标签”列。

    Raises:
        ValueError: 任一校验不通过。
    """
    if df_gap is None or df_shadow is None:
        raise ValueError("校验入参不能为空")

    if "数据层级标签" not in df_gap.columns:
        raise ValueError("缺口表缺少“数据层级标签”列，无法校验是否只含绿框")
    bad_rows = df_gap.loc[df_gap["数据层级标签"] != TIER_GREEN, "原始行号"].tolist()
    if bad_rows:
        raise ValueError(f"缺口表混入非绿框行（原始行号）：{bad_rows}")

    if "数据层级标签" not in df_shadow.columns:
        raise ValueError("影子表缺少“数据层级标签”列，无法校验层级覆盖")
    tiers = set(df_shadow["数据层级标签"].astype(str).tolist())
    if tiers != {TIER_GREEN, TIER_YELLOW}:
        raise ValueError(
            f"影子表层级覆盖不完整：{sorted(tiers)}，应同时包含“绿框”与“黄框”"
        )

    for table_name, df in (("缺口表", df_gap), ("影子表", df_shadow)):
        for col in df.columns:
            if FORBIDDEN_WORD in str(col):
                raise ValueError(f"{table_name}存在名为“{col}”的列，违反分列原则")
            if df[col].dtype == object:
                hit = df[col].astype(str).str.contains(FORBIDDEN_WORD, na=False).any()
                if hit:
                    raise ValueError(
                        f"{table_name}的“{col}”列出现“{FORBIDDEN_WORD}”字样"
                    )
