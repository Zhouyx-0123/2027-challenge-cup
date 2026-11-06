# -*- coding: utf-8 -*-
"""
main.py —— 车辆碳排放 → 财务数据 → 披露底稿（演示原型主流程）

执行链：
    config.yaml → input_data.xlsx → 层级判定 → 00/01/02/03/04 五张表
    → 全量硬校验 → output/*.xlsx + output/披露底稿_一页纸.docx

边界：
    1. 本程序只做“转化层”，绝对碳排是输入值，不重新核算排放量；
    2. 缺口轨道与影子轨道分列，永不合并为一个“碳成本”总数；
    3. 输出定位为披露底稿/演示原型，不构成法定披露结论，不做鉴证。

运行：python main.py
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName

import charts
import scenario
from math import isfinite
import rules
from rules import (
    FORBIDDEN_WORD,
    TIER_GREEN,
    TIER_YELLOW,
    calc_intensity_deviation,
    calc_shadow_cost,
    calc_uncertainty_range,
    judge_tier,
    validate_no_cross_tier_sum,
)

# ============================================================
# 路径与常量
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"
INPUT_PATH = BASE_DIR / "input_data.xlsx"
OUT_DIR = BASE_DIR / "output"
OUT_XLSX = OUT_DIR / "排放-财务-披露底稿_演示包.xlsx"
OUT_DOCX = OUT_DIR / "披露底稿_一页纸.docx"

INPUT_SHEET = "原始数据"
INPUT_COLUMNS = [
    "日期",
    "车牌号",
    "燃料类型",
    "行驶里程(km)",
    "消耗量(L/kWh)",
    "绝对碳排(kgCO₂e)",
    "核算方法",
    "排放范围",
    "数据质量标识",
]

SHEET_PARAM = "00_参数"
SHEET_RAW = "01_原始数据"
SHEET_GAP = "02_配额缺口模拟"
SHEET_SHADOW = "03_影子碳成本"
SHEET_TEXT = "04_披露文本生成"
SHEET_ORDER = [SHEET_PARAM, SHEET_RAW, SHEET_GAP, SHEET_SHADOW, SHEET_TEXT]

ACCOUNTING_NOTE = "参照政策结构的管理情景；非主管部门核定，不作会计确认"
PROJECT_ROW_GREEN = "营业成本—运输费用（管理用参考）"
PROJECT_ROW_YELLOW = "营业成本—运输费用/外包运输（管理用参考，估算）"
GAP_TAIL_NOTE = "黄框范围三数据不适用配额缺口轨道，未进入本表。"
SHADOW_SUMMARY_LABEL = "影子碳成本合计（管理用，不跨层级合并排放量）"

BOUNDARY_STATEMENT = (
    "本文件为模拟披露稿／管理用底稿，不构成法定披露结论，不得直接用于对外法定披露。"
    "缺口轨道仅参照北京地方试点政策的移动设施结构，不代表企业实际履约；"
    "影子成本为内部管理用虚拟成本，不产生实际会计分录；"
    "GLEC黄框数据属估计层级，其区间为本演示设定的假设带宽，非经量化的不确定性；"
    "OBD绿框数据为一手实测，但尚未纳入法定MRV体系；样例范围标签沿用输入映射，电力记录的范围归属待复核，不能作为完整企业核算。"
    "所有数字仅用于验证“排放量→财务数据→模拟披露文本”的转化链路。"
)

# ------------------------------------------------------------
# 可配置参数（config.yaml 可覆盖；缺失时用这里的默认值并告警）
# ------------------------------------------------------------
DEFAULT_CONFIG = {
    # 碳价
    "shadow_carbon_price_central": 80.0,
    "shadow_carbon_price_low": 50.0,
    "shadow_carbon_price_high": 120.0,
    "gap_carbon_price": 100.0,
    "shadow_price_source": "内部碳价演示假设；50/80/120元/t用于情景分析，不代表CEA现价，未进行市场校准",
    "shadow_price_anchor_date": "2026-11-06",
    # 缺口
    "baseline_intensity": 0.55,  # 假设历史强度，非企业历史统计结果
    "scenario_control_factor": 0.99,
    "scenario_intensity_low": 0.50,
    "scenario_intensity_mid": 0.52,
    "scenario_intensity_stress": 0.45,
    "scenario_policy_source": scenario.POLICY_SOURCE,
    "scenario_policy_url": scenario.POLICY_URL,
    "gap_price_source": "情景假设价（非 CEA 挂牌价、非履约成交价），用于演示传导，无政策依据",
    # 不确定性与因子
    "uncertainty_range": 0.30,
    "gl_framework_version": "ISO 14083:2023（GLEC Framework 框架线）",
    "gl_factor_set_version": "GLEC 中国默认排放系数集 v1.1（2024-12）",
    "wtw_diesel": 96.175,
    "wtw_petrol": 92.046,
    "biofuel_declaration": "输入数据未申报生物燃料/HVO 掺混比例，按 0% 掺混处理；"
                           "若实际存在掺混，黄框排放与因子一致性结论均需重算",
    # 报告期与口径
    "report_period_start": "2026-11-01",
    "report_period_end": "2026-11-10",
    "scope2_declaration": "本演示未完成范围二单独核算；样例电力记录须复核范围归属，不能认定范围二不适用",
    # 体例与留档
    "doc_version": "V0.9",
    "data_cutoff": "2026-11-10",
    "prepared_by": "（待填）",
    "reviewed_by": "（待填）",
    "retention_years": 5,
}

# 数值型参数（做 float 转换与取值校验）
NUMERIC_KEYS = (
    "shadow_carbon_price_central",
    "shadow_carbon_price_low",
    "shadow_carbon_price_high",
    "gap_carbon_price",
    "baseline_intensity",
    "scenario_control_factor",
    "scenario_intensity_low",
    "scenario_intensity_mid",
    "scenario_intensity_stress",
    "uncertainty_range",
    "wtw_diesel",
    "wtw_petrol",
)

# 参数中文说明（写进 00 表 C 列）
PARAM_DESC = {
    "shadow_carbon_price_central": "影子碳价中枢，元/tCO2e；为管理用前瞻价格，明确区别于合规碳价",
    "shadow_carbon_price_low": "影子碳价低情景，元/tCO2e；仅用于敏感性展示",
    "shadow_carbon_price_high": "影子碳价高情景，元/tCO2e；仅用于敏感性展示",
    "shadow_price_source": "内部碳价情景的设定依据；未经市场校准",
    "shadow_price_anchor_date": "情景参数设定日期，非市场成交日",
    "gap_carbon_price": "缺口模拟碳价，元/tCO2e；情景假设价，非 CEA 履约价",
    "gap_price_source": "缺口碳价的性质说明（有无政策依据）",
    "baseline_intensity": "假设历史强度，kgCO₂e/km；无三年历史数据，非历史统计结果、非官方基准",
    "scenario_control_factor": "参照北京方案第3.3节控排系数99%；不是排放因子",
    "scenario_intensity_low": "敏感性情景一的假设历史强度，kgCO₂e/km",
    "scenario_intensity_mid": "敏感性情景二的假设历史强度，kgCO₂e/km",
    "scenario_intensity_stress": "压力情景的假设历史强度，kgCO₂e/km；非行业推荐值",
    "scenario_policy_source": "仅借用移动设施配额结构；不等于完整复现政策",
    "scenario_policy_url": "官方通知及附件入口",
    "uncertainty_range": "黄框假设带宽（0.30 表示 ±30%）；演示假设，非量化不确定性",
    "gl_framework_version": "核算框架版本（GLEC Framework / ISO 14083）",
    "gl_factor_set_version": "内置排放因子集版本（与框架版本分开表述）",
    "wtw_diesel": "柴油 WTW 因子，gCO2e/MJ（用于一致性校验）",
    "wtw_petrol": "汽油 WTW 因子，gCO2e/MJ（用于一致性校验）",
    "biofuel_declaration": "生物燃料/HVO 掺混口径声明",
    "report_period_start": "报告期起（含），格式 YYYY-MM-DD",
    "report_period_end": "报告期止（含），格式 YYYY-MM-DD",
    "scope2_declaration": "范围二口径声明",
    "doc_version": "文件版本号",
    "data_cutoff": "数据截止日",
    "prepared_by": "编制人",
    "reviewed_by": "复核人",
    "retention_years": "数据与底稿保留年限",
}

# 因子一致性校验用的燃料密度范围（kg/L）取中值，用于给出差异率
FUEL_DENSITY = {"柴油": 0.835, "汽油": 0.745}
# 每 MJ 对应的低位热值（MJ/L），用于把 gCO2e/MJ 换成 kgCO2e/L
LHV_MJ_PER_L = {"柴油": 35.8, "汽油": 32.2}

# 关键参数 -> 命名单元格名称
NAMED_CELLS = {
    "shadow_carbon_price_central": "shadow_central",
    "shadow_carbon_price_low": "shadow_low",
    "shadow_carbon_price_high": "shadow_high",
    "gap_carbon_price": "gap_price",
    "baseline_intensity": "baseline",
    "scenario_control_factor": "scenario_factor",
    "uncertainty_range": "uncertainty",
}

# ============================================================
# 样式
# ============================================================
FONT_NAME = "微软雅黑"
NAVY = "1F3864"
HEADER_FILL = PatternFill("solid", fgColor=NAVY)
HEADER_FONT = Font(name=FONT_NAME, size=10, bold=True, color="FFFFFF")
BODY_FONT = Font(name=FONT_NAME, size=10)
BOLD_FONT = Font(name=FONT_NAME, size=10, bold=True)
NOTE_FONT = Font(name=FONT_NAME, size=9, italic=True, color="C00000")
GREEN_FILL = PatternFill("solid", fgColor="E2EFDA")
YELLOW_FILL = PatternFill("solid", fgColor="FFF2CC")
SUM_FILL = PatternFill("solid", fgColor="DEEBF7")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)

FMT_KG = "#,##0.00"
FMT_MONEY = "#,##0.00"
FMT_PRICE = "#,##0.00"
FMT_RATE = "0.00"


# ============================================================
# 通用工具
# ============================================================
def setup_logging() -> None:
    """配置日志：信息进控制台，格式含时间与级别。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def abort(message: str) -> None:
    """打印错误并以非零码退出（用于所有硬性校验失败）。"""
    logging.error(message)
    sys.exit(1)


def money(value: float) -> str:
    """金额统一保留两位小数的字符串。"""
    return f"{float(value):,.2f}"


def kg(value: float) -> str:
    """排放量统一保留两位小数的字符串。"""
    return f"{float(value):,.2f}"


# ============================================================
# 1. 读取配置
# ============================================================
def _parse_simple_yaml(text: str) -> dict:
    """PyYAML 缺失时的兜底解析器：仅支持本项目 config.yaml 的平铺 `key: value` 格式。

    支持：整行注释、行尾注释、单/双引号字符串、整数/浮点。
    不支持：嵌套结构、列表、多行字符串——本项目 config.yaml 也用不到。
    """
    data = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.split("#", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            try:
                value = float(value)
            except ValueError:
                pass
        data[key.strip()] = value
    return data


def _read_config_file(path: Path) -> dict:
    """读取 YAML 配置：优先用 PyYAML，未安装时退回内置最小解析器。"""
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # 延迟导入：未安装也不影响其余功能
    except ImportError:
        logging.warning(
            "未安装 PyYAML，改用内置最小解析器（仅支持平铺 key: value）。"
            "当前平铺配置可直接运行，无需另装依赖。"
        )
        return _parse_simple_yaml(text)
    return yaml.safe_load(text) or {}


def load_config(path: Path) -> dict:
    """读取 config.yaml；缺失的键用默认值并告警。

    Args:
        path: 配置文件路径。

    Returns:
        合并后的参数字典。

    Raises:
        SystemExit: 参数类型/取值非法。
    """
    config = dict(DEFAULT_CONFIG)

    if not path.exists():
        logging.warning("未找到 config.yaml，全部参数使用默认值")
    else:
        loaded = _read_config_file(path)
        for key, default in DEFAULT_CONFIG.items():
            if key not in loaded or loaded[key] is None:
                logging.warning("config.yaml 缺少参数 %s，使用默认值 %s", key, default)
            else:
                config[key] = loaded[key]

    # 类型与取值校验
    for key in NUMERIC_KEYS:
        try:
            config[key] = float(config[key])
        except (TypeError, ValueError):
            abort(f"参数 {key} 必须为数值，当前值：{config[key]!r}")

    if not all(isfinite(config[k]) for k in NUMERIC_KEYS):
        abort("数值参数必须为有限数值，不能为NaN或无穷大")
    if not 0 < config["scenario_control_factor"] <= 1:
        abort("scenario_control_factor 必须在(0,1]之间")
    if min(config["scenario_intensity_low"], config["scenario_intensity_mid"], config["scenario_intensity_stress"]) <= 0:
        abort("情景强度必须大于0")

    try:
        config["retention_years"] = int(config["retention_years"])
    except (TypeError, ValueError):
        abort(f"参数 retention_years 必须为整数，当前值：{config['retention_years']!r}")

    if config["shadow_carbon_price_central"] <= 0 or config["gap_carbon_price"] <= 0:
        abort("碳价参数必须大于 0")
    if config["baseline_intensity"] <= 0:
        abort("baseline_intensity 必须大于 0")
    if not 0 <= config["uncertainty_range"] < 1:
        abort("uncertainty_range 必须在 [0, 1) 之间，例如 0.30 表示 ±30%")

    for key in ("report_period_start", "report_period_end", "data_cutoff", "shadow_price_anchor_date"):
        try:
            config[key] = datetime.strptime(str(config[key]), "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            abort(f"参数 {key} 必须为 YYYY-MM-DD 格式，当前值：{config[key]!r}")

    if config["prepared_by"].strip() in ("", "（待填）") or config["reviewed_by"].strip() in ("", "（待填）"):
        logging.warning("编制人/复核人未填写（见 config.yaml 的 prepared_by / reviewed_by），底稿将标注“待填”")

    logging.info(
        "参数载入完成：影子碳价 %s/%s/%s 元/t，缺口情景价 %s 元/t，假设历史强度 %s kg/km，区间 ±%s%%",
        config["shadow_carbon_price_low"],
        config["shadow_carbon_price_central"],
        config["shadow_carbon_price_high"],
        config["gap_carbon_price"],
        config["baseline_intensity"],
        int(config["uncertainty_range"] * 100),
    )
    logging.warning("政策参照仅为情景演示：假设历史强度 %.4f，控排系数 %.2f%%，有效情景强度 %.4f；非核定配额",
                    config["baseline_intensity"], config["scenario_control_factor"]*100,
                    config["baseline_intensity"]*config["scenario_control_factor"])
    return config


# ============================================================
# 2. 读取并校验输入
# ============================================================
def read_input(path: Path) -> pd.DataFrame:
    """读取 input_data.xlsx 的“原始数据”表并做入口校验。

    校验：列是否齐全、绝对碳排是否为空/非数值、行驶里程是否可数值化。
    数据层级标签不在此处判定，由 attach_tier 统一处理。

    Args:
        path: 输入工作簿路径。

    Returns:
        含“原始行号”列（从 2 起，对应 Excel 行号）的 DataFrame。

    Raises:
        SystemExit: 文件不存在、缺列、必填为空或非数值。
    """
    if not path.exists():
        abort(f"未找到输入文件：{path}，请按 README 准备 input_data.xlsx")

    try:
        df = pd.read_excel(path, sheet_name=INPUT_SHEET, dtype=object)
    except ValueError:
        abort(f"输入文件中不存在 Sheet“{INPUT_SHEET}”")

    missing = [col for col in INPUT_COLUMNS if col not in df.columns]
    if missing:
        abort("输入表缺少以下列：" + "、".join(missing))

    df = df[INPUT_COLUMNS].copy().reset_index(drop=True)
    # 原始行号 = 该记录在 Excel 中的行号（表头占第 1 行）
    df.insert(0, "原始行号", range(2, len(df) + 2))

    if df.empty:
        abort("输入表没有任何数据行")

    # 绝对碳排：必填、必须为数值
    bad_emission = []
    for idx, raw in df["绝对碳排(kgCO₂e)"].items():
        row_no = int(df.at[idx, "原始行号"])
        if pd.isna(raw) or str(raw).strip() == "":
            bad_emission.append(row_no)
            continue
        try:
            df.at[idx, "绝对碳排(kgCO₂e)"] = float(raw)
        except (TypeError, ValueError):
            bad_emission.append(row_no)
    if bad_emission:
        abort(
            "绝对碳排为必填，本程序只做转化层，不做排放核算。问题行（Excel 行号）："
            + "、".join(str(x) for x in bad_emission)
        )

    # 行驶里程：缺口轨道必需
    bad_distance = []
    for idx, raw in df["行驶里程(km)"].items():
        try:
            df.at[idx, "行驶里程(km)"] = float(raw)
        except (TypeError, ValueError):
            bad_distance.append(int(df.at[idx, "原始行号"]))
    if bad_distance:
        abort("行驶里程(km) 必须为数值，问题行（Excel 行号）：" + "、".join(map(str, bad_distance)))

    for col in ("绝对碳排(kgCO₂e)", "行驶里程(km)"):
        bad = df.loc[~df[col].map(lambda x: isfinite(float(x)) and float(x) >= 0), "原始行号"].tolist()
        if bad:
            abort(f"{col}必须为有限非负数，问题行（Excel 行号）：{bad}")

    # 消耗量仅保留原值，不参与计算，允许为空
    logging.info("输入读取完成：%d 条记录（原始行号 %d—%d）",
                 len(df), df["原始行号"].min(), df["原始行号"].max())
    return df


def attach_tier(df: pd.DataFrame, strict: bool = False) -> pd.DataFrame:
    """对每行调用 judge_tier，新增“数据层级标签”列。

    两条原则：
        1. 层级由程序自动判定，输入表里任何手工层级列一律忽略并覆盖；
        2. 无标识不得升格——空白/错拼/双标签的行不会默认成绿框：
           strict=False（默认）降为黄框（估计层级）并告警；
           strict=True 直接报错退出。绿框只能来自明确的“绿框”字样。

    Args:
        df: 含“数据质量标识”列的 DataFrame。
        strict: 是否对无法判定的行直接退出。

    Returns:
        新增“数据层级标签”列的 DataFrame 副本，并附加 `.attrs["tier_notes"]`。

    Raises:
        SystemExit: strict=True 且存在无法判定的行。
    """
    df = df.copy()
    tiers, notes, bad_rows, reasons = [], [], [], []

    for idx, flag in df["数据质量标识"].items():
        row_no = int(df.at[idx, "原始行号"])
        try:
            tier = judge_tier(flag)
            tiers.append(tier)
            if str(flag).strip() in ("", "nan") or str(flag).strip() != str(flag):
                notes.append(f"行 {row_no}：标识为空白，按“不得升格”处理")
        except ValueError as exc:
            bad_rows.append(row_no)
            reasons.append(str(exc))
            tiers.append(rules.demote_unknown(flag))
            notes.append(f"行 {row_no}：{exc} → 降为黄框（估计层级），不进入范围一/缺口轨道")

    if bad_rows and strict:
        detail = "\n".join(f"  行 {r}：{m}" for r, m in zip(bad_rows, reasons))
        abort("数据质量标识无法判定（严格模式），问题行（Excel 行号）：\n" + detail)

    if bad_rows:
        for note in notes:
            logging.warning("层级降级：%s", note)

    df["数据层级标签"] = tiers
    df.attrs["tier_notes"] = notes
    n_green = int((df["数据层级标签"] == TIER_GREEN).sum())
    n_yellow = int((df["数据层级标签"] == TIER_YELLOW).sum())
    logging.info("层级判定完成：绿框 %d 条、黄框 %d 条（降级 %d 条）",
                 n_green, n_yellow, len(bad_rows))
    return df


# ============================================================
# 3. 计算两条轨道
# ============================================================
def build_gap_frame(df_all: pd.DataFrame, config: dict) -> pd.DataFrame:
    """仅绿框模拟分组；逐行保留有符号贡献，正缺口在汇总后计算。"""
    sub = df_all[df_all["数据层级标签"] == TIER_GREEN].copy()
    sub["假设基准线"] = config["baseline_intensity"]
    sub["情景系数"] = config["scenario_control_factor"]
    sub["情景额度(kg)"] = [scenario.allowance(d, config["baseline_intensity"], config["scenario_control_factor"])
                          for d in sub["行驶里程(km)"]]
    sub["情景差额(kg)"] = sub["绝对碳排(kgCO₂e)"].astype(float) - sub["情景额度(kg)"]
    sub["数据层级标签"] = TIER_GREEN
    return sub


def build_shadow_frame(df_all: pd.DataFrame, config: dict) -> pd.DataFrame:
    """构建影子碳成本表数据（全部数据，黄框带 ±区间）。

    Args:
        df_all: 全量数据（含数据层级标签）。
        config: 参数字典。

    Returns:
        影子表 DataFrame，含成本下限/中枢/上限三列。
    """
    price = config["shadow_carbon_price_central"]
    pct = config["uncertainty_range"]

    out = df_all.copy()
    out["影子碳价(元/t)"] = price
    out["影子碳成本(元)"] = [
        calc_shadow_cost(e, price) for e in out["绝对碳排(kgCO₂e)"]
    ]

    projects, lows, mids, highs = [], [], [], []
    for _, row in out.iterrows():
        cost = row["影子碳成本(元)"]
        if row["数据层级标签"] == TIER_GREEN:
            projects.append(PROJECT_ROW_GREEN)
            lows.append("—")
            mids.append("—")
            highs.append("—")
        else:
            low, high = calc_uncertainty_range(cost, pct)
            projects.append(PROJECT_ROW_YELLOW)
            lows.append(low)
            mids.append(cost)
            highs.append(high)

    out["报表行项目"] = projects
    out["成本下限(元)"] = lows
    out["成本中枢(元)"] = mids
    out["成本上限(元)"] = highs

    return out[[
        "原始行号", "车牌号", "数据层级标签", "绝对碳排(kgCO₂e)", "影子碳价(元/t)",
        "影子碳成本(元)", "报表行项目", "成本下限(元)", "成本中枢(元)", "成本上限(元)",
    ]]


def summarize(df_gap: pd.DataFrame, df_shadow: pd.DataFrame, config: dict) -> dict:
    """汇总两条轨道的关键数字（供 04 表文本与 Word 使用）。

    注意：区间按各自层级分别计算——范围一绿框与范围三黄框不得互相套用。

    Returns:
        含 n_green/n_yellow/emission_green/emission_yellow/区间上下限/
        gap_kg/gap_cost/shadow_* 的字典。
    """
    green = df_shadow[df_shadow["数据层级标签"] == TIER_GREEN]
    yellow = df_shadow[df_shadow["数据层级标签"] == TIER_YELLOW]
    pct = float(config["uncertainty_range"])

    emission_green = round(float(green["绝对碳排(kgCO₂e)"].sum()), 2)
    emission_yellow = round(float(yellow["绝对碳排(kgCO₂e)"].sum()), 2)
    distance_green = round(gap_distance_sum(df_gap), 2)

    result = scenario.balance(float(df_gap["绝对碳排(kgCO₂e)"].sum()),
                              float(df_gap["情景额度(kg)"].sum()), config["gap_carbon_price"])
    return {
        "scenario_allowance": result["allowance"],
        "scenario_delta": result["delta"],
        "n_green": len(green),
        "n_yellow": len(yellow),
        "emission_green": emission_green,
        "emission_yellow": emission_yellow,
        # 两个层级各自的假设带宽（互不套用）
        "green_low": round(emission_green * (1 - pct), 2),
        "green_high": round(emission_green * (1 + pct), 2),
        "yellow_low": round(emission_yellow * (1 - pct), 2),
        "yellow_high": round(emission_yellow * (1 + pct), 2),
        "distance_green": distance_green,
        "intensity_green": round(emission_green / distance_green, 4) if distance_green else 0.0,
        "gap_kg": round(result["gap"], 2),
        "gap_cost": round(result["cost"], 2),
        "shadow_green": round(float(green["影子碳成本(元)"].sum()), 2),
        "shadow_yellow": round(float(yellow["影子碳成本(元)"].sum()), 2),
        "shadow_total": round(float(df_shadow["影子碳成本(元)"].sum()), 2),
        "shadow_low": round(float(yellow["成本下限(元)"].sum()), 2),
        "shadow_high": round(float(yellow["成本上限(元)"].sum()), 2),
    }


def gap_distance_sum(df_gap: pd.DataFrame) -> float:
    """缺口表里的里程合计（用于算强度）。"""
    return float(df_gap["行驶里程(km)"].sum()) if len(df_gap) else 0.0


def factor_check(df_all: pd.DataFrame, config: dict) -> dict:
    """对黄框行做 GLEC 因子一致性校验，返回差异率（A6 要求量化）。

    做法：用 WTW 因子(gCO₂e/MJ) × 低位热值(MJ/L) 得到 kgCO₂e/L 的官方换算值，
    再与输入数据的反推因子比较，给出差异率。

    Returns:
        {"柴油": {"input_factor":…, "official_factor":…, "diff_pct":…}, ...}
    """
    out = {}
    factors = {"柴油": config["wtw_diesel"], "汽油": config["wtw_petrol"]}
    for fuel, wtw in factors.items():
        sub = df_all[(df_all["燃料类型"] == fuel) & (df_all["数据层级标签"] == TIER_YELLOW)]
        sub = sub[pd.to_numeric(sub["消耗量(L/kWh)"], errors="coerce") > 0]
        if len(sub) == 0:
            continue
        consumed = pd.to_numeric(sub["消耗量(L/kWh)"], errors="coerce")
        emission = sub["绝对碳排(kgCO₂e)"].astype(float)
        input_factor = float(emission.sum() / consumed.sum())
        official_factor = float(wtw) * LHV_MJ_PER_L[fuel] / 1000.0
        diff_pct = (input_factor - official_factor) / official_factor * 100.0
        out[fuel] = {
            "input_factor": round(input_factor, 3),
            "official_factor": round(official_factor, 3),
            "diff_pct": round(diff_pct, 1),
        }
    return out


# ============================================================
# 4. 写工作簿
# ============================================================
def style_header(ws, headers, widths) -> None:
    """写表头行并设置列宽。"""
    for col_idx, (title, width) in enumerate(zip(headers, widths), start=1):
        cell = ws.cell(row=1, column=col_idx, value=title)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = CENTER
        cell.border = BORDER
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    ws.row_dimensions[1].height = 32
    ws.freeze_panes = "A2"


def write_cell(ws, row, col, value, fmt=None, font=None, fill=None, align=CENTER):
    """写一个单元格并统一套用边框/字体/数字格式。"""
    cell = ws.cell(row=row, column=col, value=value)
    cell.font = font or BODY_FONT
    cell.alignment = align
    cell.border = BORDER
    if fmt:
        cell.number_format = fmt
    if fill:
        cell.fill = fill
    return cell


def build_param_sheet(wb: Workbook, config: dict) -> None:
    """00_参数：每个参数一行（A 参数名 / B 值 / C 说明），并定义命名单元格。"""
    ws = wb.create_sheet(SHEET_PARAM)
    style_header(ws, ["参数名", "值", "说明"], [38, 58, 86])
    ws.freeze_panes = None

    named_refs = {}
    for row_idx, (key, value) in enumerate(config.items(), start=2):
        fmt = FMT_RATE if key == "uncertainty_range" else (
            FMT_PRICE if "price" in key else (FMT_RATE if key == "baseline_intensity" else None)
        )
        write_cell(ws, row_idx, 1, key, font=BOLD_FONT, align=LEFT)
        write_cell(ws, row_idx, 2, value, fmt=fmt, fill=YELLOW_FILL, font=BOLD_FONT)
        write_cell(ws, row_idx, 3, PARAM_DESC.get(key, ""), align=LEFT)
        if isinstance(value, str):
            display_width=sum(2 if ord(ch)>255 else 1 for ch in value)
            ws.row_dimensions[row_idx].height=max(30,((display_width+51)//52)*17+10)
        if key in NAMED_CELLS:
            named_refs[NAMED_CELLS[key]] = f"'{ws.title}'!$B${row_idx}"

    for name, ref in named_refs.items():
        wb.defined_names.add(DefinedName(name, attr_text=ref))

    note_row = len(config) + 3
    write_cell(
        ws, note_row, 1,
        "说明：本表为参数快照，可调入口是 config.yaml。上表 7 个关键参数已定义为命名单元格"
        "（shadow_central / shadow_low / shadow_high / gap_price / baseline / scenario_factor / uncertainty）。"
        "修改 config.yaml 后重新运行 python main.py；直接修改本表不会回写配置。02/03 表为本次计算静态值，不会自动刷新。",
        align=LEFT, font=NOTE_FONT,
    )
    ws.merge_cells(start_row=note_row, start_column=1, end_row=note_row, end_column=3)
    ws.row_dimensions[note_row].height = 60


def build_raw_sheet(wb: Workbook, df_all: pd.DataFrame, stats: dict) -> None:
    """01_原始数据：原始列 + 数据层级标签 + 原始行号，末尾附合计块。"""
    ws = wb.create_sheet(SHEET_RAW)
    headers = INPUT_COLUMNS + ["数据层级标签", "原始行号"]
    widths = [10, 11, 10, 14, 15, 18, 21, 10, 18, 14, 10]
    style_header(ws, headers, widths)

    for i, (_, row) in enumerate(df_all.iterrows()):
        r = 2 + i
        tier = row["数据层级标签"]
        fill = GREEN_FILL if tier == TIER_GREEN else YELLOW_FILL
        for col_idx, col_name in enumerate(INPUT_COLUMNS, start=1):
            value = row[col_name]
            fmt = None
            if col_name == "绝对碳排(kgCO₂e)":
                fmt = FMT_KG
            elif col_name == "行驶里程(km)":
                fmt = "#,##0"
            elif col_name == "消耗量(L/kWh)":
                fmt = "#,##0.0"
            write_cell(ws, r, col_idx, value, fmt=fmt, fill=fill, align=LEFT if col_idx <= 3 or col_idx >= 7 else CENTER)
        write_cell(ws, r, len(INPUT_COLUMNS) + 1, tier, fill=fill, font=BOLD_FONT)
        write_cell(ws, r, len(INPUT_COLUMNS) + 2, int(row["原始行号"]), fmt="0")
        ws.row_dimensions[r].height = 20

    base = len(df_all) + 3
    write_cell(ws, base, 1, "合计块（非原始记录，仅供底稿引用）", font=BOLD_FONT, align=LEFT, fill=SUM_FILL)
    ws.merge_cells(start_row=base, start_column=1, end_row=base, end_column=5)
    write_cell(ws, base, 6, "绝对碳排(kgCO₂e)", font=BOLD_FONT, fill=SUM_FILL)
    write_cell(ws, base + 1, 1, "范围一（绿框）行合计", align=LEFT)
    ws.merge_cells(start_row=base + 1, start_column=1, end_row=base + 1, end_column=5)
    write_cell(ws, base + 1, 6, stats["emission_green"], fmt=FMT_KG, font=BOLD_FONT, fill=GREEN_FILL)
    write_cell(ws, base + 2, 1, "范围三（黄框）行合计", align=LEFT)
    ws.merge_cells(start_row=base + 2, start_column=1, end_row=base + 2, end_column=5)
    write_cell(ws, base + 2, 6, stats["emission_yellow"], fmt=FMT_KG, font=BOLD_FONT, fill=YELLOW_FILL)
    write_cell(
        ws, base + 3, 1,
        "声明：上两行为分列合计，不得相加作为排放总量，本表不设跨层级合计行。",
        font=NOTE_FONT, align=LEFT,
    )
    ws.merge_cells(start_row=base + 3, start_column=1, end_row=base + 3, end_column=9)


def build_gap_sheet(wb: Workbook, df_gap: pd.DataFrame, config: dict) -> int:
    """政策结构情景：明细贡献可追溯，净缺口仅在汇总层取正。"""
    ws = wb.create_sheet(SHEET_GAP)
    headers = ["原始行号", "车牌号", "输入排放(kgCO₂e)", "行驶里程(km)", "假设历史强度(kg/km)",
               "情景额度(kg)\n=里程×假设强度×系数", "有符号差额(kg)\n=排放−情景额度", "参照控排系数", "本行不单独计价", "结果性质", "强度相对情景阈值偏离"]
    style_header(ws, headers, [14,12,20,16,23,25,25,18,22,45,25])
    ws.row_dimensions[1].height = 46
    effective = config["baseline_intensity"] * config["scenario_control_factor"]
    for r, (_, row) in enumerate(df_gap.iterrows(), start=2):
        deviation = calc_intensity_deviation(row["绝对碳排(kgCO₂e)"], row["行驶里程(km)"], effective)
        values = [int(row["原始行号"]), row["车牌号"], row["绝对碳排(kgCO₂e)"], row["行驶里程(km)"],
                  row["假设基准线"], row["情景额度(kg)"], row["情景差额(kg)"], row["情景系数"],
                  "汇总净缺口后计价", "假设历史强度；非核定配额", deviation if deviation is not None else "不适用（零里程）"]
        formats = ["0",None,FMT_KG,FMT_KG,"0.0000",FMT_KG,FMT_KG,"0.00%",None,None,"+0.00%;-0.00%;0.00%"]
        for c,(v,f) in enumerate(zip(values,formats),start=1):
            write_cell(ws,r,c,v,fmt=f,fill=GREEN_FILL,align=LEFT if c in (2,9,10) else CENTER)
        ws.row_dimensions[r].height = 32
    total = len(df_gap)+2
    e = float(df_gap["绝对碳排(kgCO₂e)"].sum())
    a = float(df_gap["情景额度(kg)"].sum())
    result = scenario.balance(e,a,config["gap_carbon_price"])
    for c,v in enumerate(["合计（仅绿框）",f"{len(df_gap)}条",e,gap_distance_sum(df_gap),"—",a,result["delta"],"—","—","先汇总有符号差额，后取正","—"],1):
        write_cell(ws,total,c,v,fmt=FMT_KG if c in (3,4,6,7) else None,font=BOLD_FONT,fill=SUM_FILL)
    for r,label,value,unit in [(total+1,"绿框模拟净缺口",result["gap"],"kgCO₂e = max(0, 合计差额)"),
                               (total+2,"缺口情景金额",result["cost"],f"元；假设价{config['gap_carbon_price']:g}元/t，非会计确认")]:
        write_cell(ws,r,1,label,font=BOLD_FONT,align=LEFT)
        ws.merge_cells(start_row=r,start_column=1,end_row=r,end_column=2)
        write_cell(ws,r,3,value,fmt=FMT_MONEY,font=BOLD_FONT)
        write_cell(ws,r,4,unit,align=LEFT)
        ws.merge_cells(start_row=r,start_column=4,end_row=r,end_column=11)
    note = total+4
    for r,text in [(note,GAP_TAIL_NOTE),(note+1,scenario.MODEL_NOTE),(note+2,scenario.POLICY_SOURCE+"；"+scenario.POLICY_URL)]:
        write_cell(ws,r,1,text,font=NOTE_FONT,align=LEFT,fill=YELLOW_FILL)
        ws.merge_cells(start_row=r,start_column=1,end_row=r,end_column=11)
        ws.row_dimensions[r].height = 62 if r==note+1 else 36
    head=note+4
    for c,v in enumerate(["假设强度(kg/km)","参照控排系数","有效情景阈值","模拟额度(kg)","净差额(kg)","模拟净缺口(kg)","情景金额(元)"],1):
        write_cell(ws,head,c,v,font=HEADER_FONT,fill=HEADER_FILL)
    ws.row_dimensions[head].height=35
    for r,intensity in enumerate([config["baseline_intensity"],config["scenario_intensity_mid"],config["scenario_intensity_low"],config["scenario_intensity_stress"]],head+1):
        alt_a=sum(scenario.allowance(d,intensity,config["scenario_control_factor"]) for d in df_gap["行驶里程(km)"])
        alt=scenario.balance(e,alt_a,config["gap_carbon_price"])
        vals=[intensity,config["scenario_control_factor"],intensity*config["scenario_control_factor"],alt_a,alt["delta"],alt["gap"],alt["cost"]]
        for c,v in enumerate(vals,1):
            write_cell(ws,r,c,v,fmt="0.00%" if c==2 else ("0.0000" if c in (1,3) else FMT_KG))
    chart=BarChart(); chart.type="bar"; chart.title="输入排放 vs 情景额度（非核定）"
    chart.height,chart.width=7.5,15
    for c in (3,6):
        chart.add_data(Reference(ws,min_col=c,min_row=1,max_row=len(df_gap)+1),titles_from_data=True)
    chart.set_categories(Reference(ws,min_col=2,min_row=2,max_row=len(df_gap)+1))
    ws.add_chart(chart,"M2")
    logging.info("02表：%d条绿框；模拟额度%s kg；净缺口%s kg；情景金额%s元（先汇总后取正）",len(df_gap),kg(a),kg(result["gap"]),money(result["cost"]))
    return len(df_gap)


def build_shadow_sheet(wb: Workbook, df_shadow: pd.DataFrame, stats: dict) -> int:
    """03_影子碳成本：全部数据 + 单行汇总（绿/黄/区间/全表）。

    Returns:
        写入的数据行数（用于硬校验）。
    """
    ws = wb.create_sheet(SHEET_SHADOW)
    headers = ["原始行号", "车牌号", "数据层级标签", "绝对碳排(kg)", "影子碳价(元/t)",
               "影子碳成本(元)", "报表行项目", "成本下限(元)", "成本中枢(元)", "成本上限(元)"]
    widths = [10, 11, 14, 16, 15, 16, 46, 13, 13, 13]
    style_header(ws, headers, widths)

    for i, (_, row) in enumerate(df_shadow.iterrows()):
        r = 2 + i
        is_green = row["数据层级标签"] == TIER_GREEN
        fill = GREEN_FILL if is_green else YELLOW_FILL
        values = [
            int(row["原始行号"]), row["车牌号"], row["数据层级标签"],
            row["绝对碳排(kgCO₂e)"], row["影子碳价(元/t)"], row["影子碳成本(元)"],
            row["报表行项目"], row["成本下限(元)"], row["成本中枢(元)"], row["成本上限(元)"],
        ]
        fmts = ["0", None, None, FMT_KG, FMT_PRICE, FMT_MONEY, None, FMT_MONEY, FMT_MONEY, FMT_MONEY]
        for col_idx, (value, fmt) in enumerate(zip(values, fmts), start=1):
            align = LEFT if col_idx in (2, 7) else CENTER
            write_cell(ws, r, col_idx, value, fmt=fmt, fill=fill, align=align)
        ws.row_dimensions[r].height = 24

    total_row = 2 + len(df_shadow)
    write_cell(ws, total_row, 1, SHADOW_SUMMARY_LABEL, font=BOLD_FONT, fill=SUM_FILL, align=LEFT)
    ws.merge_cells(start_row=total_row, start_column=1, end_row=total_row, end_column=3)
    write_cell(ws, total_row, 4, "—", fill=SUM_FILL)
    write_cell(ws, total_row, 5, "—", fill=SUM_FILL)
    write_cell(ws, total_row, 6, stats["shadow_total"], fmt=FMT_MONEY, font=BOLD_FONT, fill=SUM_FILL)
    write_cell(
        ws, total_row, 7,
        f"其中绿框合计 {money(stats['shadow_green'])} 元（点值，无区间）；"
        f"黄框合计 {money(stats['shadow_yellow'])} 元（区间 {money(stats['shadow_low'])}—{money(stats['shadow_high'])} 元）",
        fill=SUM_FILL, align=LEFT, font=BODY_FONT,
    )
    write_cell(ws, total_row, 8, stats["shadow_low"], fmt=FMT_MONEY, font=BOLD_FONT, fill=SUM_FILL)
    write_cell(ws, total_row, 9, stats["shadow_yellow"], fmt=FMT_MONEY, font=BOLD_FONT, fill=SUM_FILL)
    write_cell(ws, total_row, 10, stats["shadow_high"], fmt=FMT_MONEY, font=BOLD_FONT, fill=SUM_FILL)
    ws.row_dimensions[total_row].height = 44

    # 成本条形图，直观看层级差异
    chart = BarChart()
    chart.type = "bar"
    chart.style = 12
    chart.title = "每条记录的管理用碳成本（元）"
    chart.height, chart.width = 7.0, 14
    chart.add_data(Reference(ws, min_col=6, min_row=1, max_row=1 + len(df_shadow)),
                   titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=2, min_row=2, max_row=1 + len(df_shadow)))
    chart.series[0].graphicalProperties.solidFill = "1F3864"
    ws.add_chart(chart, "L2")

    logging.info("03 表写入 %d 条记录，中枢合计 %s 元（绿 %s / 黄 %s，区间 %s—%s）",
                 len(df_shadow), money(stats["shadow_total"]), money(stats["shadow_green"]),
                 money(stats["shadow_yellow"]), money(stats["shadow_low"]), money(stats["shadow_high"]))
    return len(df_shadow)


def build_text_sheet(wb: Workbook, texts: list) -> None:
    """04_披露文本生成：四层段落，一层一单元格。"""
    ws = wb.create_sheet(SHEET_TEXT)
    style_header(ws, ["层级", "披露文本（程序拼接，数字引用 02/03 表汇总值）"], [22, 120])
    ws.freeze_panes = None

    for i, (layer_name, text) in enumerate(texts, start=2):
        write_cell(ws, i, 1, layer_name, font=BOLD_FONT, align=LEFT, fill=SUM_FILL)
        cell = write_cell(ws, i, 2, text, align=LEFT)
        cell.font = Font(name=FONT_NAME, size=10)
        # 以中英文显示宽度估算换行；Excel 单行高度上限为409.5磅。
        lines = sum(max(1, (sum(2 if ord(ch)>255 else 1 for ch in part)+109)//110)
                    for part in text.split("\n"))
        ws.row_dimensions[i].height = min(409.5, max(60, lines * 15 + 18))


# ============================================================
# 5. 生成披露文本（四层）
# ============================================================
def script_version() -> str:
    """返回复算脚本版本号（每次运行自动生成，便于留档复算）。"""
    return f"{datetime.now():%Y%m%d-%H%M%S}"


def compose_texts(config: dict, stats: dict, tier_notes: list | None = None,
                  factor_diff: dict | None = None, script_ver: str = "") -> list:
    """拼接四层披露文本；数字全部来自 02/03 表汇总值。

    修订要点（编辑评审 A1–A7 / B1–B10）：
        A1 区间分层计算，范围一用自身 ±幅度，不套用范围三；
        A2 称“排放量”，注明单车辆/单任务口径；
        A3 碳价锚点给出处、日期与复核要求；
        A4 报告期标明“10 天口径、单次快照、不年化”；
        A5 明示“无标识不得升格”；
        A6 给出因子差异率；
        A7 声明范围二缺失；
        B1/B2 因子集版本与框架版本分列、生物燃料口径；
        B4 “明确区别于”合规碳价；B5 统一为“假设带宽”；B6/B9 版本与留档。

    Returns:
        [(层名, 段落字符串), ...] 四元列表。
    """
    period = f"{config['report_period_start']} 至 {config['report_period_end']}"
    pct = config["uncertainty_range"] * 100
    n_total = stats["n_green"] + stats["n_yellow"]

    # ---------- 第一层 ----------
    layer1 = (
        f"【口径】单车辆／单任务级排放量；报告期 {period}（共 10 天，单次快照，不年化）；"
        f"本次录入 {n_total} 条记录（范围一 {stats['n_green']} 条、范围三 {stats['n_yellow']} 条）。"
        f"术语统一使用“排放量”，不使用“排放总量”。\n"
        f"范围一（Scope 1）排放量：{kg(stats['emission_green'])} kgCO₂e"
        f"（OBD实测，绿框，直接计量层级；假设带宽 ±{pct:.0f}% 计 "
        f"{kg(stats['green_low'])}—{kg(stats['green_high'])} kgCO₂e；"
        f"里程 {kg(stats['distance_green'])} km，折算强度 {stats['intensity_green']:.4f} kgCO₂e/km；"
        f"来源：01表绿框行合计）。\n"
        f"范围三（Scope 3）排放量：{kg(stats['emission_yellow'])} kgCO₂e"
        f"（GLEC估算，黄框，估计层级；假设带宽 ±{pct:.0f}% 计 "
        f"{kg(stats['yellow_low'])}—{kg(stats['yellow_high'])} kgCO₂e；来源：01表黄框行合计）。\n"
        f"范围二（Scope 2）：{config['scope2_declaration']}。\n"
        f"声明：两层级数据分列呈现，未跨层级加总；上方两条假设带宽各自独立计算，互不套用。"
    )

    # ---------- 第二层 ----------
    notes = tier_notes or []
    downgrade_note = ""
    if notes:
        downgrade_note = "本次降级记录：" + "；".join(notes) + "。"
    else:
        downgrade_note = "本次无降级记录。"

    if factor_diff:
        diff_text = "；".join(
            f"{fuel} 输入反推因子 {v['input_factor']:.3f} kgCO₂e/L，GLEC 换算值 "
            f"{v['official_factor']:.3f} kgCO₂e/L，差异 {v['diff_pct']:+.1f}%"
            for fuel, v in factor_diff.items()
        )
    else:
        diff_text = "本次无黄框行或消耗量为空，未执行因子一致性校验"

    layer2 = (
        f"1. 层级判定与“无标识不得升格”：数据层级标签由“数据质量标识”经 judge_tier() 自动判定，"
        f"程序不读取输入表中任何手工层级列，判定后冻结、下游只读。"
        f"规则：含“绿框”→绿框，含“黄框”→黄框；空白、错拼、双标签一律不认，按“不得升格”降为黄框（估计层级），"
        f"不得进入范围一与配额缺口轨道；同时含绿框与黄框视为冲突，需人工复核。{downgrade_note}\n"
        f"2. OBD 绿框：碳守恒法一手实测，绝对碳排为输入值，本程序不二次核算；绿框=直接计量层级。"
        f"样例数据未随附采样频率、零点/漂移校正方式、油箱液位或尾气直测口径等 MRV 要素，上述要素缺失，"
        f"故 OBD 绿框 仅视为“一手实测”，不构成法定 MRV 计量。\n"
        f"3. GLEC 黄框：核算框架为 {config['gl_framework_version']}；本次使用的是"
        f"内置因子集 {config['gl_factor_set_version']}（因子集版本与框架版本是两件事，须分开表述）。"
        f"因子：柴油 WTW {config['wtw_diesel']} gCO₂e/MJ、汽油 WTW {config['wtw_petrol']} gCO₂e/MJ，"
        f"GWP 版本 IPCC AR6 GWP100；程序仅做一致性校验，不重新计算；黄框=估计层级。\n"
        f"4. 因子一致性校验结果（量化差异率）：{diff_text}。"
        f"差异原因可能包括燃料密度、生物燃料掺混比例或上游 WTT 口径；本演示不追溯调整，仅作透明度声明。\n"
        f"4-1. 生物燃料口径：{config['biofuel_declaration']}。\n"
        f"5. 缺口轨道：参照北京移动设施公式，假设历史强度 {config['baseline_intensity']:.4f} kgCO₂e/km，"
        f"控排系数 {config['scenario_control_factor']:.2%}，情景碳价 "
        f"{config['gap_carbon_price']:.0f} 元/t（{config['gap_price_source']}），"
        f"{scenario.MODEL_NOTE}政策来源：{config['scenario_policy_source']}；"
        f"官方入口：{config['scenario_policy_url']}。\n"
        f"6. 影子轨道：中枢 {config['shadow_carbon_price_central']:.0f} 元/t，"
        f"价格为管理用前瞻价，明确区别于合规碳价（CEA 配额成交价）；"
        f"设定依据：{config['shadow_price_source']}，情景设定日 {config['shadow_price_anchor_date']}。"
        f"低情景 {config['shadow_carbon_price_low']:.0f} 元/t、高情景 {config['shadow_carbon_price_high']:.0f} 元/t "
        f"仅用于敏感性展示；单期快照，不含年涨幅、不做年化。\n"
        f"7. 假设带宽：范围一、范围三均按 ±{pct:.0f}% 给出假设带宽（各自独立计算）。需说明：该幅度是演示设定的假设带宽，不是经统计或计量"
        f"量化的不确定性区间；ISO 14083 未提供不确定性量化方法，IFRS S2 允许范围三使用二手数据与行业均值估算，"
        f"但要求披露测量方法、输入与假设。\n"
        f"8. 电力记录：样例按原分组保留，范围归属及CO₂/CO₂e边界未验证；不得将本表作为合规排放清单。\n"
        f"报告期口径：{period}（10 天）；数据截止日 {config['data_cutoff']}；文件版本 {config['doc_version']}；"
        f"编制 {config['prepared_by']}；复核 {config['reviewed_by']}；复算脚本版本 {script_ver or '—'}。"
    )

    # ---------- 第三层 ----------
    gap_mapped = money(stats["gap_cost"])
    layer3 = (
        f"缺口轨道（仅绿框模拟分组）：先汇总排放与情景额度，再计算正净差额；"
        f"模拟净缺口 {kg(stats['gap_kg'])} kgCO₂e，"
        f"缺口情景金额 {gap_mapped} 元（=缺口kg÷1000×{config['gap_carbon_price']:.0f} 元/t）。"
        f"非购买义务，无实际交易或核定文件，不映射已确认资产或负债。\n"
        f"影子价格轨道（全部数据）：管理用碳成本合计 {money(stats['shadow_total'])} 元（中枢），"
        f"其中绿框 {money(stats['shadow_green'])} 元、黄框 {money(stats['shadow_yellow'])} 元"
        f"（假设带宽 {money(stats['shadow_low'])}—{money(stats['shadow_high'])} 元）。"
        f"映射营业成本—运输费用（管理用参考）。该值为虚拟决策成本，不进入法定报表。\n"
        f"科目对照见第三层表格；两轨道成本分列，未合并为单一碳成本总数。"
    )

    layer4 = (
        f"【合规定位】{BOUNDARY_STATEMENT}\n{scenario.MODEL_NOTE}\n"
        f"【留档】本底稿与原始数据保留 {config['retention_years']} 年；复算脚本 main.py 版本 {script_ver or '—'}，"
        f"重跑同一输入文件应得到同一结果（确定性算法，不含随机数）。"
    )

    return [
        ("第一层 排放数据底稿", layer1),
        ("第二层 核算方法与关键假设", layer2),
        ("第三层 财务影响（分列，不合并）", layer3),
        ("第四层 边界与留档声明", layer4),
    ]


def accounting_mapping_rows(stats: dict) -> list:
    """仅展示情景用途，不把虚拟金额映射成已确认科目。"""
    return [
        ("绿框模拟净缺口金额", "管理情景；无实际会计科目",stats["gap_cost"],"否（非购买义务）"),
        ("影子轨道 · 绿框", "运输费用管理分析参考",stats["shadow_green"],"否（虚拟成本）"),
        ("影子轨道 · 黄框", "外包运输管理分析参考",stats["shadow_yellow"],"否（估算虚拟成本）"),
    ]



# ============================================================
def validate_workbook(wb: Workbook, df_all: pd.DataFrame, df_gap: pd.DataFrame,
                      df_shadow: pd.DataFrame, gap_rows: int, shadow_rows: int) -> None:
    """执行全部硬性校验，任一失败即抛异常退出。

    校验项：
        1. 缺口表行数 == 绿框行数，且不含黄框行；
        2. 影子表行数 == 输入总行数，且同时含绿框与黄框；
        3. 工作簿任何 Sheet 不得出现“总计碳成本”；
        4. 每个输出数据行必须能用“原始行号”回溯到 01 表；
        5. 黄框行的区间三列不得为空。
    """
    n_green = int((df_all["数据层级标签"] == TIER_GREEN).sum())
    n_total = len(df_all)

    if gap_rows != n_green or len(df_gap) != n_green:
        abort(f"缺口表行数校验失败：写入 {gap_rows} 行、数据 {len(df_gap)} 行，绿框应为 {n_green} 行")
    if shadow_rows != n_total or len(df_shadow) != n_total:
        abort(f"影子表行数校验失败：写入 {shadow_rows} 行、数据 {len(df_shadow)} 行，输入应为 {n_total} 行")

    shadow_tiers = set(df_shadow["数据层级标签"].tolist())
    if shadow_tiers != {TIER_GREEN, TIER_YELLOW}:
        abort(f"影子表层级覆盖失败：{sorted(shadow_tiers)}")

    # 逐表扫描禁词
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and FORBIDDEN_WORD in cell.value:
                    abort(f"工作簿出现“{FORBIDDEN_WORD}”字样：{ws.title}!{cell.coordinate}")

    # 回溯校验
    valid_ids = set(df_all["原始行号"].tolist())
    for name, df in (("缺口表", df_gap), ("影子表", df_shadow)):
        orphans = [int(x) for x in df["原始行号"] if int(x) not in valid_ids]
        if orphans:
            abort(f"{name}存在无法回溯到 01 表的原始行号：{orphans}")

    # 黄框区间不得为空
    yellow = df_shadow[df_shadow["数据层级标签"] == TIER_YELLOW]
    for col in ("成本下限(元)", "成本中枢(元)", "成本上限(元)"):
        blanks = yellow[yellow[col].isna()].shape[0] + int(
            (yellow[col].astype(str).str.strip() == "").sum()
        )
        if blanks:
            abort(f"黄框行“{col}”存在空值 {blanks} 处，区间必须非空")

    logging.info("硬校验全部通过：缺口 %d 行 / 影子 %d 行 / 无禁词 / 可回溯 / 区间非空",
                 gap_rows, shadow_rows)


# ============================================================
# 7. 生成 Word
# ============================================================
def _shade(paragraph, hex_color: str) -> None:
    """给段落加底纹（用于分层标题条）。"""
    p_pr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    p_pr.append(shd)


def _set_run(run, size: float, bold: bool = False, color: RGBColor | None = None):
    """统一设置 run 的中英文字体与字号。"""
    run.font.name = FONT_NAME
    run._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_NAME)
    run.font.size = Pt(size)
    run.bold = bold
    if color is not None:
        run.font.color.rgb = color


def _table_borders(table) -> None:
    """给表格加浅灰细边框。"""
    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), "4")
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), "BFBFBF")
        borders.append(element)
    tbl_pr.append(borders)


def _kpi_card(cell, color: str, label: str, value: str, note: str) -> None:
    """画一个 KPI 卡片单元格（深色底 + 白色字）。"""
    cell.width = Cm(4.42)
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color)
    tc_pr.append(shd)

    cell.text = ""
    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(0)
    _set_run(p.add_run(label), 7.5, False, RGBColor(0xE8, 0xE8, 0xE8))

    p2 = cell.add_paragraph()
    p2.paragraph_format.space_before = Pt(1)
    p2.paragraph_format.space_after = Pt(0)
    _set_run(p2.add_run(value), 17, True, RGBColor(0xFF, 0xFF, 0xFF))

    p3 = cell.add_paragraph()
    p3.paragraph_format.space_before = Pt(1)
    p3.paragraph_format.space_after = Pt(4)
    _set_run(p3.add_run(note), 6.8, False, RGBColor(0xF0, 0xF0, 0xF0))


def _kpi_band(doc, stats: dict, config: dict) -> None:
    """顶部四卡读数：范围一 / 范围三 / 缺口 / 影子成本。"""
    table = doc.add_table(rows=1, cols=4)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False

    pct = float(config["uncertainty_range"])
    cards = [
        ("375623", "范围一 · 绿框 · 直接计量", f"{stats['emission_green']:,.2f}",
         f"kgCO₂e ｜ 带宽 {stats['green_low']:,.2f}—{stats['green_high']:,.2f}"),
        ("7F6000", "范围三 · 黄框 · 估计层级", f"{stats['emission_yellow']:,.2f}",
         f"kgCO₂e ｜ 带宽 {stats['yellow_low']:,.2f}—{stats['yellow_high']:,.2f}"),
        ("1F3864", "缺口情景金额 · 仅绿框", f"{stats['gap_cost']:,.2f}",
         f"元 ｜ 缺口 {stats['gap_kg']:,.2f} kg"),
        ("C00000", "影子碳成本 · 全部数据", f"{stats['shadow_total']:,.2f}",
         f"元 ｜ 管理用，不进入法定报表"),
    ]
    for i, (color, label, value, note) in enumerate(cards):
        _kpi_card(table.cell(0, i), color, label, value, note)


def build_docx(path: Path, config: dict, texts: list, stats: dict,
               chart_png: Path | None = None) -> None:
    """生成 output/披露底稿_一页纸.docx。

    版式：标题 → 四个读数卡 → 一句话结论 → 「实际排放 vs 额度」图 → 四层正文 → 可追溯性说明。
    四层正文直接取自 04 表字符串（调用方从已保存的工作簿读回）。

    Args:
        path: 输出 docx 路径。
        config: 参数字典（报告期等）。
        texts: [(层名, 段落), ...] 四项。
        stats: 汇总数字。
        chart_png: 「实际排放 vs 额度」示意图路径，可为 None。
    """
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin, section.bottom_margin = Cm(1.5), Cm(1.3)
    section.left_margin, section.right_margin = Cm(1.7), Cm(1.7)

    normal = doc.styles["Normal"]
    normal.font.name = FONT_NAME
    normal.font.size = Pt(8.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), FONT_NAME)
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.line_spacing = 1.05

    # 标题与版本
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(2)
    _set_run(p.add_run("道路运输碳排放 → 财务影响 披露底稿（演示原型）"), 15, True, RGBColor(0x1F, 0x38, 0x64))

    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    _set_run(
        p.add_run(
            f"报告期 {config['report_period_start']} 至 {config['report_period_end']} ｜ "
            f"版本 {config['doc_version']}（程序生成 {datetime.now():%Y-%m-%d %H:%M}） ｜ "
            f"单车辆/单任务口径 ｜ 单位 kgCO₂e、元 ｜ 模拟披露稿，不得对外"
        ),
        7.5, False, RGBColor(0x59, 0x59, 0x59),
    )

    # 四个读数卡 + 一句话结论 + 对照图
    _kpi_band(doc, stats, config)

    if stats["gap_kg"] <= 0:
        gap_words = (f"汇总排放未超过情景额度，模拟净缺口为0，情景金额为0（非履约结论）")
    else:
        gap_words = (f"模拟净缺口 {stats['gap_kg']:,.2f} kgCO₂e，按假设价 {config['gap_carbon_price']:.0f} 元/t "
                     f"折合 {stats['gap_cost']:,.2f} 元")
    sentence = (
        f"一句话：范围一 {stats['emission_green']:,.2f} kgCO₂e、"
        f"范围三 {stats['emission_yellow']:,.2f} kgCO₂e，分列呈现；绿框记录{gap_words}；"
        f"按 {config['shadow_carbon_price_central']:.0f} 元/t 内部碳价计算，管理用碳成本 "
        f"{stats['shadow_total']:,.2f} 元。"
    )
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.left_indent = Cm(0.15)
    _shade(p, "FFF7E0")
    _set_run(p.add_run("  " + sentence), 8.5, False, RGBColor(0x33, 0x33, 0x33))

    p = doc.add_paragraph()
    _set_run(p.add_run(f"情景假设：I={config['baseline_intensity']:.4f} kgCO₂e/km（非历史统计结果），f={config['scenario_control_factor']:.2%}；只借用北京移动设施公式，非核定配额。"), 8)

    if chart_png is not None and Path(chart_png).exists():
        pic_para = doc.add_paragraph()
        pic_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        pic_para.paragraph_format.space_before = Pt(2)
        pic_para.paragraph_format.space_after = Pt(2)
        pic_para.add_run().add_picture(str(chart_png), width=Cm(17.2))

    # 四层正文
    for layer_name, text in texts:
        bar = doc.add_paragraph()
        bar.paragraph_format.space_before = Pt(5)
        bar.paragraph_format.space_after = Pt(2)
        _shade(bar, "1F3864")
        bar.paragraph_format.left_indent = Cm(0)
        bar.paragraph_format.first_line_indent = Cm(0)
        bar.alignment = WD_ALIGN_PARAGRAPH.LEFT
        _set_run(bar.add_run("\u3000" + layer_name), 9.5, True, RGBColor(0xFF, 0xFF, 0xFF))

        body = doc.add_paragraph()
        body.paragraph_format.left_indent = Cm(0.3)
        body.alignment = WD_ALIGN_PARAGRAPH.LEFT
        for i, seg in enumerate(text.split("\n")):
            if i:
                body.add_run().add_break()
            _set_run(body.add_run(seg), 8.5, False, RGBColor(0x33, 0x33, 0x33))

        if layer_name.startswith("第三层"):
            # 科目-金额-确认与否 对照表（B3）
            mapping = accounting_mapping_rows(stats)
            table = doc.add_table(rows=1 + len(mapping), cols=4)
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            table.autofit = False
            _table_borders(table)
            headers = ["业务项", "映射会计科目", "金额（元）", "是否确认"]
            widths = [Cm(5.2), Cm(6.0), Cm(2.6), Cm(3.8)]
            for col_idx, title in enumerate(headers):
                cell = table.cell(0, col_idx)
                cell.width = widths[col_idx]
                run = cell.paragraphs[0].add_run(title)
                _set_run(run, 8, True, RGBColor(0xFF, 0xFF, 0xFF))
                shd = OxmlElement("w:shd")
                shd.set(qn("w:val"), "clear")
                shd.set(qn("w:color"), "auto")
                shd.set(qn("w:fill"), "1F3864")
                cell._tc.get_or_add_tcPr().append(shd)
            for row_idx, values in enumerate(mapping, start=1):
                for col_idx, value in enumerate(values):
                    cell = table.cell(row_idx, col_idx)
                    cell.width = widths[col_idx]
                    text = money(value) if col_idx == 2 else str(value)
                    run = cell.paragraphs[0].add_run(text)
                    _set_run(run, 8, col_idx == 2)
            caption = doc.add_paragraph()
            caption.paragraph_format.left_indent = Cm(0.3)
            _set_run(
                caption.add_run(
                    f"注：B 轨拆分——绿框 {money(stats['shadow_green'])} 元、黄框 {money(stats['shadow_yellow'])} 元"
                    f"（假设带宽 {money(stats['shadow_low'])}—{money(stats['shadow_high'])} 元）；"
                    f"影子碳价敏感性 50 元/t → "
                    f"{money(stats['shadow_total'] * config['shadow_carbon_price_low'] / config['shadow_carbon_price_central'])} 元、"
                    f"120 元/t → {money(stats['shadow_total'] * config['shadow_carbon_price_high'] / config['shadow_carbon_price_central'])} 元。"
                    f"上表仅为逻辑演示，不产生任何会计分录。"
                ),
                7.5, False, RGBColor(0x59, 0x59, 0x59),
            )

    # 可追溯性说明
    bar = doc.add_paragraph()
    bar.paragraph_format.space_before = Pt(5)
    bar.paragraph_format.space_after = Pt(2)
    _shade(bar, "595959")
    _set_run(bar.add_run("  可追溯性说明（按表名 + 汇总行标签定位，不写死单元格地址）"), 9, True, RGBColor(0xFF, 0xFF, 0xFF))

    trace_items = [
        f"范围一 {kg(stats['emission_green'])} kgCO₂e —— 见 01 表“数据层级标签=绿框”行，及 01 表表尾“范围一（绿框）行合计”。",
        f"范围三 {kg(stats['emission_yellow'])} kgCO₂e 及区间 —— 见 01 表“数据层级标签=黄框”行，及 03 表表尾汇总行的“成本下限/上限”列。",
        f"模拟净缺口 {kg(stats['gap_kg'])} kg、缺口情景金额 {money(stats['gap_cost'])} 元 —— 见02表“绿框模拟净缺口”“缺口情景金额”行。",
        f"绿框 {money(stats['shadow_green'])} 元、黄框 {money(stats['shadow_yellow'])} 元、全表 {money(stats['shadow_total'])} 元 —— 见 03 表表尾“{SHADOW_SUMMARY_LABEL}”行。",
        f"全部参数（碳价、基准线、区间幅度）—— 见 00 表参数行，关键参数为命名单元格 shadow_central / gap_price / baseline 等。",
        "每一行输出金额的“原始行号”列均可回溯至 01 表同一行号的原始记录；汇总行由明细行加总得出。",
    ]
    for item in trace_items:
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Cm(0.4)
        p.paragraph_format.space_after = Pt(1)
        _set_run(p.add_run("· " + item), 7.5, False, RGBColor(0x59, 0x59, 0x59))

    doc.core_properties.title = "道路运输排放—财务影响披露底稿（演示原型）"
    doc.core_properties.author = "main.py"
    doc.save(str(path))
    logging.info("Word 已生成：%s", path)


# ============================================================
# 8. 主流程
# ============================================================
def main() -> None:
    """端到端主流程：读入 → 判定 → 建表 → 校验 → 导出。

    命令行可选传入一个 Excel 路径作为输入；不传则用同目录的 input_data.xlsx。
    例：python main.py 我的测试数据.xlsx
    """
    setup_logging()
    logging.info("=" * 68)
    logging.info("车辆碳排放 → 财务数据 → 披露底稿（演示原型）启动")
    logging.info("=" * 68)

    input_path = INPUT_PATH
    if len(sys.argv) > 1:
        input_path = Path(sys.argv[1]).expanduser()
        if not input_path.is_absolute():
            input_path = BASE_DIR / input_path
        logging.info("使用命令行指定的输入文件：%s", input_path)

    config = load_config(CONFIG_PATH)

    df_all = read_input(input_path)
    df_all = attach_tier(df_all)

    # 两条轨道
    df_gap = build_gap_frame(df_all, config)
    df_shadow = build_shadow_frame(df_all, config)

    # 跨层级/禁词校验（rules.py）
    try:
        validate_no_cross_tier_sum(df_gap, df_shadow)
    except ValueError as exc:
        abort(f"分层校验失败：{exc}")

    stats = summarize(df_gap, df_shadow, config)
    factor_diff = factor_check(df_all, config)
    if factor_diff:
        for fuel, v in factor_diff.items():
            logging.info("因子一致性校验：%s 输入 %.3f vs GLEC %.3f kg/L，差异 %+.1f%%",
                         fuel, v["input_factor"], v["official_factor"], v["diff_pct"])
    ver = script_version()

    # 四层文本
    texts = compose_texts(config, stats, df_all.attrs.get("tier_notes"), factor_diff, ver)

    # 建工作簿
    wb = Workbook()
    wb.remove(wb.active)
    build_param_sheet(wb, config)
    build_raw_sheet(wb, df_all, stats)
    gap_rows = build_gap_sheet(wb, df_gap, config)
    shadow_rows = build_shadow_sheet(wb, df_shadow, stats)
    build_text_sheet(wb, texts)

    # 工作表顺序与命名
    wb._sheets = [wb[name] for name in SHEET_ORDER]

    # 硬校验
    validate_workbook(wb, df_all, df_gap, df_shadow, gap_rows, shadow_rows)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    wb.save(OUT_XLSX)
    logging.info("Excel 已生成：%s", OUT_XLSX)

    # 从已保存的 04 表读回四段文本，再写 Word（确保文档与表内文本一致）
    saved = load_workbook(OUT_XLSX, data_only=True)
    ws_text = saved[SHEET_TEXT]
    texts_from_sheet = [
        (ws_text.cell(row=r, column=1).value, ws_text.cell(row=r, column=2).value)
        for r in range(2, 6)
    ]

    # 「实际排放 vs 额度」对照图（给 Word 用）
    chart_rows = [
        (f"{row['车牌号']} · 行{int(row['原始行号'])}",
         float(row["绝对碳排(kgCO₂e)"]),
         float(row["情景额度(kg)"]),
         True)
        for _, row in df_gap.iterrows()
    ]
    gap_png = charts.draw_gap_chart(
        chart_rows, float(config["baseline_intensity"])*float(config["scenario_control_factor"]), OUT_DIR / "chart_gap.png"
    )

    build_docx(OUT_DOCX, config, texts_from_sheet, stats, gap_png)

    # 控制台汇总
    logging.info("-" * 68)
    logging.info("缺口轨道：%d 行，缺口 %s kg，成本 %s 元",
                 gap_rows, kg(stats["gap_kg"]), money(stats["gap_cost"]))
    logging.info("影子轨道：%d 行，中枢 %s 元（绿 %s / 黄 %s，假设带宽 %s—%s）",
                 shadow_rows, money(stats["shadow_total"]), money(stats["shadow_green"]),
                 money(stats["shadow_yellow"]), money(stats["shadow_low"]), money(stats["shadow_high"]))
    logging.info("复算脚本版本：%s", ver)
    logging.info("输出目录：%s", OUT_DIR)
    logging.info("完成。")


if __name__ == "__main__":
    main()
