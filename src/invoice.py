# -*- coding: utf-8 -*-
"""发票内核：识别（图片 → 字段）与口径（油量 → 碳排 / 里程）合一，纯函数、可单测。

定位（docs/20 §11 演算页纪律的同构件）：
    逻辑全部收口到本文件（src/ 侧、可单测），app/ 页面只收集输入、调用、展示。
    **业务逻辑仍在 src/，app/ 不写一行公式。**

三条纪律（与 docs/20 §11.2 并列）：
    1. 只读 config/calc_factors.json（柴油因子）与调用方传入参数；
       不读宽表、不写任何结果表、不 import preprocess 的取数逻辑；
    2. 输出必带 ``assumptions`` 列表，页面据此给假设值打黄框；
    3. 演算结果**不得**进入 kpi.json / gate_report.json —— 对拍口径与情景推演分列。

关于网络 I/O（对 docs/23 §5.1 的一处**刻意破例**）：
    docs/23 §5.1 说「网络 I/O 是工具行为，放 tools/，不进 src/」。那条针对的是
    **可预取**的数据（高德路线 → route_od.parquet，供引擎与页面只读消费）。
    发票识别是**交互式**的：图片是现场给的，预取模型套不上。
    所以本文件把调用隔离在一个纯函数里：
        extract_invoice_fields(image_bytes, api_key) -> dict
    - key 由调用方传入：本模块不读密钥文件、不落盘、不进前端代码；
    - 网络部分可 mock，单元测试不联网；
    - 识别结果只回给页面展示，不写任何结果表、不进闸门。
    批量预取的 CLI 走 tools/invoice_ocr.py，复用本文件的同一套函数（不重复实现）。

口径说明：
    油量是**实测值**（发票上印的加注数量），因子表是**估算值**。
    两者正确的关系是**交叉校验**而非替换：差异本身就是诊断信号
    （车型错配 / 里程错报 / 油耗异常）。本模块因此同时给两条轨：
        油量直算轨   fuel_liters × 柴油因子            → kg CO₂
        里程因子表轨 dist_km × 因子表(车型, 载重)      → kg CO₂
    油量反推里程   fuel_liters ÷ 油耗基准(L/100km)     → km

人民币/物理口径：油量＝L；里程＝km；载重＝t；碳排＝kg CO₂ 与 tCO₂e。
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
from pathlib import Path

import requests
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import factor_model

ROOT = Path(__file__).resolve().parents[1]
FACTORS_PATH = ROOT / "config" / "calc_factors.json"
SPEND_FACTORS_PATH = ROOT / "config" / "invoice_spend_factors.json"

# ---------------------------------------------------------------------------
# DeepSeek 端点与请求格式（OpenAI 兼容）
# ---------------------------------------------------------------------------

DEEPSEEK_CHAT_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_VISION_MODEL = "deepseek-flash"
DEEPSEEK_TEXT_MODEL = "deepseek-flash"
API_TIMEOUT = 90

MAX_IMAGE_BYTES = 32 * 1024 * 1024      # DeepSeek Vision：单图 ≤ 32 MiB
MAX_REQUEST_BYTES = 48 * 1024 * 1024    # DeepSeek Vision：请求体 ≤ 48 MiB

#: 抽取提示词：只认印刷数字、只出 JSON、读不出就说读不出。
#: 柴油字段保持向后兼容；通用字段用于电力、运输服务等财务发票的分层碳核算。
EXTRACT_PROMPT = (
    "你是财务发票信息抽取器。从这张中国发票图片中抽取以下字段：\n"
    "  invoice_code   发票代码（没有则空字符串）\n"
    "  invoice_no     发票号码（若票面有「发票号码」则填，否则空字符串）\n"
    "  seller_name    销售方名称（没有则空字符串）\n"
    "  invoice_amount 价税合计金额（元，纯数字，不要「¥」和千分位逗号）\n"
    "  amount_excluding_tax 不含税金额（元，没有则 null）\n"
    "  tax_amount     税额（元，没有则 null）\n"
    "  item_name      货物或应税劳务、服务名称（照票面，不要推测）\n"
    "  quantity       票面数量（纯数字，没有则 null；多行商品时返回空并说明需分行）\n"
    "  unit           票面数量单位（如 L、升、kWh、千瓦时，没有则空字符串）\n"
    "  unit_price     票面单价（元/单位，如 7.28；没有则 null）\n"
    "  fuel_liters    柴油加注数量 / 加油升数（单位：升 L；非柴油发票填 null）\n"
    "  plate          车牌号（若票面有则填，否则空字符串）\n"
    "  invoice_date   开票日期（YYYY-MM-DD）\n"
    "规则：\n"
    "1. 只认图片中真实印刷的文字和数字，不要推算、不要用常识补全、不要自行分类；\n"
    "2. 只输出一个 JSON 对象，不要任何解释文字、不要 Markdown 代码块；\n"
    "3. 格式固定为 {\"invoice_code\":\"\",\"invoice_no\":\"\","
    "\"seller_name\":\"\",\"invoice_amount\":null,\"amount_excluding_tax\":null,"
    "\"tax_amount\":null,\"item_name\":\"\",\"quantity\":null,\"unit\":\"\","
    "\"unit_price\":null,\"fuel_liters\":null,\"plate\":\"\","
    "\"invoice_date\":\"\"}；\n"
    "4. 某个字段确实读不出时：数字字段填 null，文本字段填空字符串；\n"
    "5. 多行明细不得合并数量或自行选择一行，quantity 填 null；\n"
    "6. 若整张图都不是发票，输出 {\"error\":\"not_an_invoice\"}。"
)

#: 车型大类 → 油耗基准（L/100km）。**假设值**，页面必须打黄框。
TRUCK_CONSUMPTION: Dict[int, float] = {1: 12.0, 2: 18.0, 3: 28.0}
VCLASS_NAME: Dict[int, str] = {1: "轻型", 2: "中型", 3: "重型"}
DEFAULT_CONSUMPTION_L_PER_100KM = 28.0

#: Mock 模式：INVOICE_OCR_MOCK=1 时不联网，返回固定的演示识别结果。
#: 用途：无 key / 断网时的本地演练与 CI 验证（对标本项目「任何一步失败都不阻断启动」纪律）。
#: **纪律**：mock 结果必须带 mock=True 标记，页面必须显式标注「演示用，非真实识别」，
#: 绝不能让假数据被误认成真实发票数据（docs/42 §3.1）。
MOCK_MODE = (os.environ.get("INVOICE_OCR_MOCK", "") or "").strip().lower() in ("1", "true", "yes")
MOCK_RESULT: Dict[str, Any] = {
    "invoice_code": "",
    "invoice_no": "DEMO-INVOICE-0001",
    "seller_name": "DEMO-SELLER",
    "invoice_amount": 476.84,
    "amount_excluding_tax": 421.98,
    "tax_amount": 54.86,
    "item_name": "0#柴油",
    "quantity": 65.5,
    "unit": "L",
    "unit_price": 7.28,
    "fuel_liters": 65.5,
    "plate": "DEMO-PLATE-01",
    "invoice_date": "2026-10-25",
}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def load_factors() -> Dict[str, Any]:
    """读口径配置（每次调用重读：改 JSON 即改口径，无需重启）。"""
    try:
        return json.loads(FACTORS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def diesel_factors(factors: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    """柴油碳因子（kg CO₂/L）：只从 config/calc_factors.json 读，不维护第二套。"""
    data = factors if factors is not None else load_factors()
    co2 = (data or {}).get("co2_factors") or {}
    out: Dict[str, float] = {}
    for key, name in (("diesel_ttw_kg_per_l", "ttw_kg_per_l"),
                      ("diesel_wtt_kg_per_l", "wtt_kg_per_l")):
        value = (co2.get(key) or {}).get("value")
        try:
            if value is not None:
                out[name] = float(value)
        except (TypeError, ValueError):
            pass
    return out


def factor_for(vclass: int, load_t: float) -> float:
    """因子表口径的排放因子（g CO₂/km）。

    与 src/carbon_model._factor 同规则：优先查 2023 基准表
    factor_model.FACTOR_BY_CLASS_LOAD[(vclass, load)]，未命中退回
    factor_model.factor_lookup 的 6 锚点内插（并夹紧到量程两端）。
    tests/test_invoice.py 有一条用例把两者逐格比对，防止口径漂移。
    """
    try:
        vc = int(vclass)
    except (TypeError, ValueError):
        vc = 2
    try:
        load = float(load_t)
    except (TypeError, ValueError):
        load = 12.0
    key = (vc, round(load, 2))
    if key in factor_model.FACTOR_BY_CLASS_LOAD:
        return float(factor_model.FACTOR_BY_CLASS_LOAD[key])
    return float(factor_model.factor_lookup(load))


# ---------------------------------------------------------------------------
# 图片 → data URL
# ---------------------------------------------------------------------------

_EXT_TO_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".webp": "image/webp", ".gif": "image/gif"}
ALLOWED_EXT = set(_EXT_TO_MIME.keys())


def image_to_data_url(image_bytes: bytes, filename: str = "invoice.jpg") -> Tuple[Optional[str], str]:
    """把图片字节转成 data URL。返回 (data_url, 错误信息)。"""
    if not image_bytes:
        return None, "图片内容为空"
    if len(image_bytes) > MAX_IMAGE_BYTES:
        return None, "图片 %.1f MB，超过 DeepSeek 单图 32 MB 上限" % (len(image_bytes) / 1048576)
    suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ".jpg"
    mime = _EXT_TO_MIME.get(suffix, "image/jpeg")
    encoded = base64.b64encode(image_bytes).decode("ascii")
    if len(encoded) + 8192 > MAX_REQUEST_BYTES:
        return None, "base64 编码后超过 DeepSeek 48 MB 请求体上限"
    return "data:%s;base64,%s" % (mime, encoded), ""


# ---------------------------------------------------------------------------
# DeepSeek 多模态调用
# ---------------------------------------------------------------------------

def call_deepseek_vision(data_url: str, api_key: str,
                         prompt: str = EXTRACT_PROMPT) -> str:
    """POST DeepSeek Vision，返回 choices[0].message.content。"""
    if not api_key:
        if MOCK_MODE:
            return json.dumps(MOCK_RESULT, ensure_ascii=False)
        raise ValueError("未提供 DeepSeek API key")
    payload = {
        "model": DEEPSEEK_VISION_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": data_url, "detail": "high"}},
            ],
        }],
        "max_tokens": 800,
        "stream": False,
    }
    resp = requests.post(
        DEEPSEEK_CHAT_URL,
        headers={"Authorization": "Bearer %s" % api_key,
                 "Content-Type": "application/json"},
        json=payload,
        timeout=API_TIMEOUT,
    )
    if resp.status_code != 200:
        raise RuntimeError("DeepSeek HTTP %d：%s" % (resp.status_code, resp.text[:300]))
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        error = data.get("error") or {}
        raise RuntimeError("DeepSeek 返回无 choices：%s"
                           % (error.get("message") or str(data)[:300]))
    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    if not content:
        raise RuntimeError("DeepSeek 返回内容为空")
    return content


# ---------------------------------------------------------------------------
# JSON 抽取（鲁棒）
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


def extract_json_object(text: str) -> Dict[str, Any]:
    """从模型输出里稳健地抠出 JSON 对象。

    顺序：Markdown 代码块 → 首个配平的 {...} → 整段文本。
    配平扫描会跳过字符串字面量里的花括号（否则 "备注：含 } 杂字符" 会提前截断）。
    """
    candidates: List[str] = []

    fenced = _FENCE_RE.search(text)
    if fenced:
        candidates.append(fenced.group(1))

    start = text.find("{")
    if start != -1:
        depth = 0
        in_str = False
        escaped = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:idx + 1])
                    break

    candidates.append(text)

    for cand in candidates:
        try:
            obj = json.loads(cand.strip())
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError("模型输出里找不到合法 JSON：%s" % text[:300])


_CURRENCY_RE = re.compile(r"[¥￥$,，\s]")
_UNIT_RE = re.compile(r"(升|公升|升/L|L|liters?|LTR|kg|千克|公斤|吨|t|元|人民币)",
                       re.IGNORECASE)


def _to_float(value: Any) -> Optional[float]:
    """把票面数值转成 float：剥掉货币符号、千分位与常见单位。

    票面常见写法：「¥1,234.50」「65.5 升」「65.50L」。剥不掉就返回 None，
    绝不猜、绝不用 0 顶替。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return f if f == f else None
    text = _CURRENCY_RE.sub("", str(value).strip())
    text = _UNIT_RE.sub("", text).strip()
    if not text:
        return None
    try:
        f = float(text)
    except ValueError:
        return None
    return f if f == f else None


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_invoice(obj: Dict[str, Any]) -> Dict[str, Any]:
    """把抽取结果归一成台账行。

    范围校验：油量 0–2000 L（OCR 常把 65.5 读成 655 或 6.5）；金额 ≥ 0。
    越界不是"修一修"，是判为识别失败——宁可留空，不填一个看着像真的数。
    """
    if obj.get("error"):
        return {"status": "failed", "note": str(obj.get("error"))}

    liters = _to_float(obj.get("fuel_liters"))
    amount = _to_float(obj.get("invoice_amount"))
    quantity = _to_float(obj.get("quantity"))
    net_amount = _to_float(obj.get("amount_excluding_tax"))
    tax_amount = _to_float(obj.get("tax_amount"))
    unit_price = _to_float(obj.get("unit_price"))
    notes: List[str] = []

    if liters is not None and not (0 < liters <= 2000):
        notes.append("油量 %.2f L 超出合理范围（0–2000 L），判为识别失败" % liters)
        liters = None
    if unit_price is not None and not (0 < unit_price <= 1000):
        notes.append("单价超出合理范围，保留为空并转人工复核")
        unit_price = None
    if quantity is not None and not (0 < quantity <= 1000000000):
        notes.append("数量超出合理范围，保留为空并转人工复核")
        quantity = None
    for label, value_name in (("价税合计", "amount"), ("不含税金额", "net"),
                              ("税额", "tax")):
        value = {"amount": amount, "net": net_amount, "tax": tax_amount}[value_name]
        if value is not None and value < 0:
            notes.append("%s为负，保留为空并转人工复核" % label)
            if value_name == "amount":
                amount = None
            elif value_name == "net":
                net_amount = None
            else:
                tax_amount = None

    has_activity = liters is not None or quantity is not None
    status = "ok"
    if not has_activity and amount is None:
        status = "failed"
        notes.append("数量与金额均未识别出")
    elif not has_activity or amount is None:
        status = "partial"

    return {
        "status": status,
        "invoice_code": _to_text(obj.get("invoice_code")),
        "invoice_no": _to_text(obj.get("invoice_no")),
        "seller_name": _to_text(obj.get("seller_name")),
        "invoice_amount": amount,
        "amount_excluding_tax": net_amount,
        "tax_amount": tax_amount,
        "item_name": _to_text(obj.get("item_name")),
        "quantity": quantity,
        "unit": _to_text(obj.get("unit")),
        "unit_price": unit_price,
        "fuel_liters": liters,
        "plate": _to_text(obj.get("plate")),
        "invoice_date": _to_text(obj.get("invoice_date")),
        "note": "；".join(notes),
    }


def extract_invoice_fields(image_bytes: bytes, api_key: str,
                           filename: str = "invoice.jpg") -> Dict[str, Any]:
    """识别入口：图片字节 → 归一后的字段字典。

    这是本模块唯一触网的地方，函数化后：单元测试可 mock，页面可转发。
    返回结构与 tools/invoice_ocr.py 的台账行同构。
    """
    data_url, err = image_to_data_url(image_bytes, filename)
    if err:
        return {"status": "failed", "invoice_no": "", "invoice_amount": None,
                "fuel_liters": None, "plate": "", "invoice_date": "",
                "mock": False, "note": err}
    try:
        raw = call_deepseek_vision(data_url, api_key)
    except Exception as exc:
        return {"status": "failed", "invoice_no": "", "invoice_amount": None,
                "fuel_liters": None, "plate": "", "invoice_date": "",
                "mock": False, "note": "识别失败：%s: %s" % (type(exc).__name__, exc)}
    try:
        obj = extract_json_object(raw)
    except ValueError as exc:
        return {"status": "failed", "invoice_no": "", "invoice_amount": None,
                "fuel_liters": None, "plate": "", "invoice_date": "",
                "mock": False, "note": str(exc)}
    parsed = parse_invoice(obj)
    parsed["raw"] = raw
    # mock 标记：让页面能显式标注「演示用，非真实识别」，杜绝假数据被误认
    parsed["mock"] = bool((not api_key) and MOCK_MODE)
    return parsed


# ---------------------------------------------------------------------------
# 口径：油量 / 里程 → 碳排
# ---------------------------------------------------------------------------

DEFAULT_FUEL_LITERS = 50.0


def fuel_with_estimation(fuel_liters: Any) -> Tuple[float, bool]:
    """OCR 空油量容错：返回 (有效油量, is_estimated)，绝不向调用方抛类型异常。"""
    value = _to_float(fuel_liters)
    if value is None or value <= 0:
        return DEFAULT_FUEL_LITERS, True
    return float(value), False


def implied_distance(fuel_liters: Optional[float],
                     l_per_100km: float) -> Optional[float]:
    """油量反推里程：km = L ÷ (L/100km ÷ 100)。油耗基准是假设值，页面要打黄框。"""
    if fuel_liters is None or not l_per_100km or l_per_100km <= 0:
        return None
    return float(fuel_liters) / (float(l_per_100km) / 100.0)


def calc_fuel_carbon(fuel_liters: Optional[float],
                     factors: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """油量直算轨：fuel_liters × 柴油因子（TTW / WTW）。

    因子来自 config/calc_factors.json（唯一口径来源），本函数不硬编码。
    """
    dfac = diesel_factors(factors)
    ttw_per_l = dfac.get("ttw_kg_per_l")
    wtt_per_l = dfac.get("wtt_kg_per_l")
    if fuel_liters is None or ttw_per_l is None or wtt_per_l is None:
        return {"ok": False, "reason": "缺油量或缺柴油因子（config/calc_factors.json）"}
    ttw = float(fuel_liters) * ttw_per_l
    wtt = float(fuel_liters) * wtt_per_l
    return {
        "ok": True,
        "fuel_liters": round(float(fuel_liters), 3),
        "ttw_kg": round(ttw, 3),
        "wtt_kg": round(wtt, 3),
        "co2_kg": round(ttw + wtt, 3),
        "co2_t": round((ttw + wtt) / 1000.0, 6),
        "ttw_kg_per_l": ttw_per_l,
        "wtt_kg_per_l": wtt_per_l,
    }


def calc_distance_carbon(distance_km: Optional[float], load_t: float,
                         vclass: int = 3,
                         factors: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """里程因子表轨：里程 × 因子表(车型, 载重)。

    这是**估算**口径（与第 1 页 E₀ 同源），用于与油量直算轨交叉校验。
    """
    if distance_km is None or distance_km <= 0:
        return {"ok": False, "reason": "缺里程"}
    gkm = factor_for(vclass, load_t)
    kg = float(distance_km) * gkm / 1000.0
    return {
        "ok": True,
        "distance_km": round(float(distance_km), 3),
        "factor_gkm": round(gkm, 3),
        "co2_kg": round(kg, 3),
        "co2_t": round(kg / 1000.0, 6),
        "vclass": int(vclass),
        "load_t": float(load_t),
    }


def calc_carbon(load: float, distance: float, truck_type: Any = "重型") -> float:
    """统一业务入口：载重(t)、里程(km)、车型 -> 碳排量(kgCO2)。

    truck_type 支持“轻型/中型/重型”或 vclass 1/2/3；缺省保持原重型口径。
    """
    if isinstance(truck_type, str):
        vclass = {"轻型": 1, "中型": 2, "重型": 3}.get(truck_type.strip())
        if vclass is None:
            raise ValueError("未知车型：%s" % truck_type)
    else:
        vclass = int(truck_type)
    if vclass not in (1, 2, 3):
        raise ValueError("车型 vclass 必须为 1、2 或 3")
    result = calc_distance_carbon(distance_km=distance, load_t=load, vclass=vclass)
    if not result.get("ok"):
        raise ValueError(str(result.get("reason") or "碳核算失败"))
    return float(result["co2_kg"])


def invoice_summary(fuel_liters: Optional[float], distance_km: Optional[float],
                    load_t: float = 18.0, vclass: int = 3,
                    l_per_100km: Optional[float] = None,
                    invoice_amount: Optional[float] = None,
                    factors: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """发票核算总入口：给两条轨 + 交叉校验 + 假设值清单。

    返回里必带 ``assumptions`` 列表 —— 页面据此给假设值打黄框（纪律 2）。
    结果**不进** kpi.json / gate_report.json（纪律 3）。
    """
    facts = factors if factors is not None else load_factors()
    consumption = float(l_per_100km) if l_per_100km else \
        TRUCK_CONSUMPTION.get(int(vclass), DEFAULT_CONSUMPTION_L_PER_100KM)

    fuel_track = calc_fuel_carbon(fuel_liters, facts)
    derived_km = implied_distance(fuel_liters, consumption)
    # 里程优先用调用方给的（可手填）；否则用油量反推的
    effective_km = float(distance_km) if distance_km else derived_km
    distance_track = calc_distance_carbon(effective_km, load_t, vclass, facts)

    cross: Dict[str, Any] = {"ok": False}
    if fuel_track.get("ok") and distance_track.get("ok"):
        a = float(fuel_track["co2_kg"])
        b = float(distance_track["co2_kg"])
        diff_pct = (a - b) / b * 100.0 if b else None
        cross = {
            "ok": True,
            "fuel_kg": round(a, 3),
            "distance_kg": round(b, 3),
            "diff_kg": round(a - b, 3),
            "diff_pct": round(diff_pct, 2) if diff_pct is not None else None,
            "verdict": _cross_verdict(diff_pct),
        }

    assumptions: List[str] = []
    assumptions.append("油耗基准 %.1f L/100km 为假设值（车型%d 典型值），"
                       "用于把油量反推成里程，非实测" % (consumption, int(vclass)))
    if effective_km and derived_km and abs(effective_km - derived_km) > 0.5:
        assumptions.append("里程 %.1f km 为手工输入，与油量反推值 %.1f km 不一致，"
                           "交叉校验差异可能来自油耗基准失真"
                           % (effective_km, derived_km))
    if invoice_amount is not None:
        assumptions.append("发票金额 %.2f 元为票面识别值，仅用于运价核对，不参与碳排计算"
                           % float(invoice_amount))
    assumptions.append("油量直算轨＝油量×柴油因子（TTW+WTT）；里程因子表轨＝里程×因子表；"
                       "两轨差异是诊断信号，不是误差")
    assumptions.append("本结果为情景推演，非对拍口径：不进闸门、不进 kpi、不改任何结果表")

    return {
        "generated_at": _now(),
        "fuel_liters": fuel_liters,
        "distance_km": effective_km,
        "derived_distance_km": derived_km,
        "load_t": float(load_t),
        "vclass": int(vclass),
        "vclass_name": VCLASS_NAME.get(int(vclass), "未知"),
        "consumption_l_per_100km": consumption,
        "fuel_track": fuel_track,
        "distance_track": distance_track,
        "cross_check": cross,
        "assumptions": assumptions,
    }


def _cross_verdict(diff_pct: Optional[float]) -> str:
    """差异判读。阈值是工程经验值，页面需标注为参考线，不是标准。"""
    if diff_pct is None:
        return "无法判读"
    if abs(diff_pct) <= 10:
        return "两轨一致（±10% 内），互证通过"
    if abs(diff_pct) <= 25:
        return "两轨有偏差（±10~25%），建议核对油耗基准与里程"
    if diff_pct > 0:
        return "油量轨显著高于里程轨：查车型错配 / 里程错报 / 油耗异常"
    return "里程轨显著高于油量轨：查里程虚报 / 油量识别偏低"


# ---------------------------------------------------------------------------
# 通用财务发票碳核算：活动数据优先，支出法兜底，批量去重后汇总
# ---------------------------------------------------------------------------

#: 关键词只负责提出分类，不负责编造数量或因子；多类别同时命中时转人工复核。
CATEGORY_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "diesel": ("柴油", "diesel"),
    "electricity": ("电费", "购电", "电力", "充电", "electricity", "kwh", "千瓦时"),
    "cng": ("压缩天然气", "cng"),
    "hvo": ("hvo", "生物柴油"),
    "road_freight": ("公路运输", "货物运输", "运输费", "运费", "货运"),
}
CATEGORY_ALIASES = {
    "柴油": "diesel", "电力": "electricity", "电费": "electricity",
    "天然气": "cng", "运输服务": "road_freight", "公路货运": "road_freight",
}
UNIT_ALIASES = {
    "l": "L", "升": "L", "公升": "L", "liter": "L", "litre": "L",
    "liters": "L", "litres": "L", "ltr": "L",
    "kwh": "kWh", "千瓦时": "kWh", "度": "kWh", "度电": "kWh",
    "m3": "m3", "m³": "m3", "立方米": "m3", "立方": "m3",
}


def load_spend_factors() -> Dict[str, Any]:
    """读取支出法因子库；默认配置为空，绝不内置无来源的金额系数。"""
    try:
        data = json.loads(SPEND_FACTORS_PATH.read_text(encoding="utf-8"))
        values = data.get("factors", {})
        return values if isinstance(values, dict) else {}
    except Exception:
        return {}


def normalize_activity_unit(unit: Any) -> str:
    """只归一明确表示同一物理量的单位，不在 L、kWh、m3 之间换算。"""
    text = _to_text(unit).replace(" ", "").lower()
    return UNIT_ALIASES.get(text, _to_text(unit))


def classify_invoice_item(record: Dict[str, Any]) -> str:
    """显式类别优先，其次按票面品名分类；冲突或未知返回 unknown。

    旧版柴油 OCR 只有 fuel_liters、没有品名，保留 diesel 兼容分支。
    """
    explicit = _to_text(record.get("emission_category"))
    if explicit:
        key = explicit.strip().lower()
        return CATEGORY_ALIASES.get(explicit, CATEGORY_ALIASES.get(key, key))
    text = " ".join(_to_text(record.get(k))
                    for k in ("item_name", "fuel_type")).strip().lower()
    matches = [category for category, words in CATEGORY_KEYWORDS.items()
               if any(word.lower() in text for word in words)]
    if len(matches) == 1:
        return matches[0]
    if not matches and _to_float(record.get("fuel_liters")) is not None:
        return "diesel"
    return "unknown"


def _positive_number(value: Any) -> Optional[float]:
    number = _to_float(value)
    if number is None or not math.isfinite(number) or number <= 0:
        return None
    return float(number)


def _carbon_base(record: Dict[str, Any], category: str) -> Dict[str, Any]:
    return {
        "status": "needs_review",
        "invoice_no": _to_text(record.get("invoice_no")),
        "source_image": _to_text(record.get("source_image")),
        "emission_category": category or "unknown",
        "method": None,
        "activity_quantity": None,
        "activity_unit": None,
        "emissions_kg_co2e": None,
        "scope": None,
        "scope_breakdown_kg": {},
        "factors": [],
        "gas_coverage": "CO2 only",
        "included_in_total": False,
        "assumptions": [],
        "warnings": [],
    }


def _review_result(record: Dict[str, Any], category: str, code: str,
                   reason: str) -> Dict[str, Any]:
    result = _carbon_base(record, category)
    result.update({"reason_code": code, "reason": reason})
    result["assumptions"].append("未满足可核算条件，排放量留空而非按 0 处理")
    return result


def _factor_meta(factors: Dict[str, Any], key: str, unit: str) -> Optional[Dict[str, Any]]:
    raw = ((factors or {}).get("co2_factors") or {}).get(key) or {}
    value = _positive_number(raw.get("value"))
    if value is None:
        return None
    return {
        "key": key,
        "value": value,
        "unit": unit,
        "source": _to_text(raw.get("source")) or "未登记来源",
        "basis": _to_text(raw.get("basis")),
    }


def _finish_calculated(result: Dict[str, Any]) -> Dict[str, Any]:
    result["status"] = "calculated"
    result["included_in_total"] = True
    result["assumptions"].append(
        "排放因子为估算口径；发票结果为独立情景核算，不自动并入 E0、kpi 或闸门")
    result["assumptions"].append(
        "当前配置因子仅覆盖 CO2；结果字段以 kgCO2e 承载，不外推其他温室气体")
    return result


def _spend_result(record: Dict[str, Any], category: str,
                  spend_factors: Dict[str, Any]) -> Dict[str, Any]:
    """支出法：金额 × kgCO2e/CNY；因子必须登记来源、价格年与含税口径。"""
    factor = (spend_factors or {}).get(category)
    if not isinstance(factor, dict):
        return _review_result(
            record, category, "missing_spend_factor",
            "缺少活动数据，且类别 %s 未配置支出法因子；不使用通用金额系数代替" % category)
    value = _positive_number(factor.get("value"))
    unit = _to_text(factor.get("unit")).replace("_", "").replace("/", "").lower()
    source = _to_text(factor.get("source"))
    price_year = factor.get("price_year", factor.get("year"))
    price_basis = _to_text(factor.get("price_basis"))
    if value is None or unit not in ("kgco2ecny", "kgco2ecny元"):
        return _review_result(record, category, "invalid_spend_factor",
                              "支出法因子必须为正的 kgCO2e/CNY")
    if not source or not price_year or price_basis not in ("including_tax", "excluding_tax"):
        return _review_result(record, category, "invalid_spend_factor_metadata",
                              "支出法因子必须登记 source、price_year 与 price_basis")
    currency = _to_text(record.get("currency")) or "CNY"
    if currency.upper() not in ("CNY", "RMB", "人民币"):
        return _review_result(record, category, "unsupported_currency",
                              "支出法仅接受人民币金额，未做汇率换算")
    amount_key = "invoice_amount" if price_basis == "including_tax" else "amount_excluding_tax"
    amount = _positive_number(record.get(amount_key))
    if amount is None:
        return _review_result(record, category, "missing_amount_basis",
                              "因子口径 %s 需要有效字段 %s" % (price_basis, amount_key))

    scope = _to_text(factor.get("scope")) or "scope_3"
    emissions = amount * value
    result = _carbon_base(record, category)
    result.update({
        "method": "spend_based",
        "activity_quantity": round(amount, 6),
        "activity_unit": "CNY",
        "emissions_kg_co2e": round(emissions, 6),
        "scope": scope,
        "scope_breakdown_kg": {scope: round(emissions, 6)},
        "factors": [{"key": category, "value": value, "unit": "kgCO2e/CNY",
                     "source": source, "price_year": price_year,
                     "price_basis": price_basis}],
        "gas_coverage": _to_text(factor.get("gas_coverage")) or "按所配因子",
        "data_quality": "spend_estimated",
    })
    result["assumptions"].append("支出法结果为筛查级估算，不与同一活动数据的实物量结果相加")
    invoice_year = _to_text(record.get("invoice_date"))[:4]
    if invoice_year.isdigit() and str(price_year) != invoice_year:
        result["warnings"].append(
            "因子价格年 %s 与发票年 %s 不同，未自动进行通胀或币值调整" % (price_year, invoice_year))
    return _finish_calculated(result)


def calculate_invoice_carbon(record: Dict[str, Any],
                             factors: Optional[Dict[str, Any]] = None,
                             spend_factors: Optional[Dict[str, Any]] = None
                             ) -> Dict[str, Any]:
    """单张财务发票核算入口。

    优先级：有效实物量 × 活动因子 > 金额 × 已审核支出因子 > 留空待复核。
    每张票只返回一个主结果，杜绝油量法、金额法重复计碳。负数和红字发票不在
    当前 OCR 契约内，需先经财务红冲流程处理，不在这里静默取绝对值。
    """
    facts = factors if factors is not None else load_factors()
    spends = spend_factors if spend_factors is not None else load_spend_factors()
    category = classify_invoice_item(record)

    if category == "diesel":
        liters = _positive_number(record.get("fuel_liters"))
        quantity = _positive_number(record.get("quantity"))
        unit = normalize_activity_unit(record.get("unit"))
        if liters is not None and quantity is not None and unit == "L" \
                and not math.isclose(liters, quantity, rel_tol=0.001, abs_tol=0.001):
            return _review_result(record, category, "conflicting_activity_data",
                                  "fuel_liters 与票面 quantity 不一致，需人工核对")
        activity = liters if liters is not None else (quantity if unit == "L" else None)
        if activity is not None:
            ttw = _factor_meta(facts, "diesel_ttw_kg_per_l", "kgCO2/L")
            wtt = _factor_meta(facts, "diesel_wtt_kg_per_l", "kgCO2/L")
            if not ttw or not wtt:
                return _review_result(record, category, "missing_activity_factor",
                                      "缺少柴油 TTW/WTT 因子")
            total = activity * (ttw["value"] + wtt["value"])
            result = _carbon_base(record, category)
            result.update({
                "method": "activity_data", "activity_quantity": activity,
                "activity_unit": "L", "emissions_kg_co2e": round(total, 6),
                "scope": "scope_1+scope_3",
                "scope_breakdown_kg": {
                    "scope_1": round(activity * ttw["value"], 6),
                    "scope_3": round(activity * wtt["value"], 6),
                },
                "factors": [ttw, wtt], "data_quality": "activity_actual_factor_estimated",
            })
            result["assumptions"].append("默认柴油由企业自有或控制车辆消耗；TTW 归范围一，WTT 归范围三")
            return _finish_calculated(result)

    elif category == "electricity":
        activity = _positive_number(record.get("quantity"))
        unit = normalize_activity_unit(record.get("unit"))
        if activity is not None and unit == "kWh":
            factor = _factor_meta(facts, "grid_wtw_kg_per_kwh", "kgCO2/kWh")
            if not factor:
                return _review_result(record, category, "missing_activity_factor",
                                      "缺少外购电力因子")
            emissions = activity * factor["value"]
            result = _carbon_base(record, category)
            result.update({
                "method": "activity_data", "activity_quantity": activity,
                "activity_unit": "kWh", "emissions_kg_co2e": round(emissions, 6),
                "scope": "scope_2", "scope_breakdown_kg": {"scope_2": round(emissions, 6)},
                "factors": [factor], "data_quality": "activity_actual_factor_estimated",
            })
            result["assumptions"].append("按外购电力位置法核算；未扣除绿电或市场化交易的环境属性")
            return _finish_calculated(result)

    elif category == "cng":
        activity = _positive_number(record.get("quantity"))
        unit = normalize_activity_unit(record.get("unit"))
        if activity is not None and unit == "m3":
            ttw = _factor_meta(facts, "cng_ttw_kg_per_m3", "kgCO2/m3")
            wtt = _factor_meta(facts, "cng_wtt_kg_per_m3", "kgCO2/m3")
            if not ttw or not wtt:
                return _review_result(record, category, "missing_activity_factor",
                                      "缺少 CNG TTW/WTT 因子")
            total = activity * (ttw["value"] + wtt["value"])
            result = _carbon_base(record, category)
            result.update({
                "method": "activity_data", "activity_quantity": activity,
                "activity_unit": "m3", "emissions_kg_co2e": round(total, 6),
                "scope": "scope_1+scope_3",
                "scope_breakdown_kg": {
                    "scope_1": round(activity * ttw["value"], 6),
                    "scope_3": round(activity * wtt["value"], 6),
                },
                "factors": [ttw, wtt], "data_quality": "activity_actual_factor_estimated",
            })
            result["assumptions"].append("默认天然气由企业自有或控制设备消耗；TTW 归范围一，WTT 归范围三")
            return _finish_calculated(result)

    if category == "unknown":
        return _review_result(record, category, "unknown_category",
                              "无法从票面品名确定排放类别，需人工指定 emission_category")
    return _spend_result(record, category, spends)


def _invoice_dedupe_key(record: Dict[str, Any]) -> str:
    """保守去重键：代码+号码+销售方；缺号码不猜重复。"""
    number = _to_text(record.get("invoice_no"))
    if not number:
        return ""
    return "|".join((_to_text(record.get("invoice_code")), number,
                     _to_text(record.get("seller_name"))))


def _invoice_signature(record: Dict[str, Any]) -> str:
    """重复组冲突检测：只比较影响核算和票据身份的字段。"""
    numeric = {"invoice_amount", "amount_excluding_tax", "tax_amount",
               "quantity", "fuel_liters"}
    fields = ("invoice_date", "invoice_amount", "amount_excluding_tax", "tax_amount",
              "item_name", "quantity", "unit", "fuel_liters", "emission_category")
    normalized = {key: (_to_float(record.get(key)) if key in numeric
                        else _to_text(record.get(key))) for key in fields}
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, default=str)


def calculate_invoice_batch(records: Sequence[Dict[str, Any]],
                            factors: Optional[Dict[str, Any]] = None,
                            spend_factors: Optional[Dict[str, Any]] = None
                            ) -> Dict[str, Any]:
    """批量核算：完全重复只计一次，冲突重复整组留空，汇总只含已核算行。"""
    keys = [_invoice_dedupe_key(row) for row in records]
    groups: Dict[str, List[int]] = {}
    for index, key in enumerate(keys):
        if key:
            groups.setdefault(key, []).append(index)
    conflict_keys = {key for key, indices in groups.items()
                     if len({_invoice_signature(records[i]) for i in indices}) > 1}

    results: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for index, (record, key) in enumerate(zip(records, keys)):
        category = classify_invoice_item(record)
        if key and key in conflict_keys:
            result = _review_result(record, category, "duplicate_conflict",
                                    "同一发票键存在不同金额、日期或活动数据，整组转人工复核")
        elif key and key in seen:
            result = _carbon_base(record, category)
            result.update({"status": "excluded_duplicate", "reason_code": "duplicate",
                           "reason": "与首张发票身份及关键字段一致，本行不重复计碳"})
        else:
            result = calculate_invoice_carbon(record, factors, spend_factors)
        if key:
            seen.add(key)
        result["record_index"] = index
        result["dedupe_key_present"] = bool(key)
        results.append(result)

    calculated = [r for r in results if r["status"] == "calculated"]
    review = [r for r in results if r["status"] == "needs_review"]
    duplicates = [r for r in results if r["status"] == "excluded_duplicate"]
    total = sum(float(r["emissions_kg_co2e"]) for r in calculated)
    by_method: Dict[str, float] = {}
    by_category: Dict[str, float] = {}
    by_scope: Dict[str, float] = {}
    for row in calculated:
        emissions = float(row["emissions_kg_co2e"])
        by_method[row["method"]] = by_method.get(row["method"], 0.0) + emissions
        by_category[row["emission_category"]] = \
            by_category.get(row["emission_category"], 0.0) + emissions
        for scope, value in row["scope_breakdown_kg"].items():
            by_scope[scope] = by_scope.get(scope, 0.0) + float(value)
    eligible = len(records) - len(duplicates)
    assumptions = list(dict.fromkeys(
        text for row in results for text in row.get("assumptions", [])))
    warnings = ["有 %d 张发票待复核，当前合计为不完整小计" % len(review)] if review else []
    if conflict_keys:
        warnings.append("检测到 %d 组关键字段冲突的重复发票，均未计入合计" % len(conflict_keys))
    summary = {
        "input_count": len(records),
        "calculated_count": len(calculated),
        "needs_review_count": len(review),
        "excluded_duplicate_count": len(duplicates),
        "total_complete": not review,
        "total_emissions_kg_co2e": round(total, 6) if calculated else None,
        "total_emissions_t_co2e": round(total / 1000.0, 9) if calculated else None,
        "coverage_pct": round(len(calculated) / eligible * 100.0, 2) if eligible else None,
        "by_method_kg": {k: round(v, 6) for k, v in sorted(by_method.items())},
        "by_category_kg": {k: round(v, 6) for k, v in sorted(by_category.items())},
        "by_scope_kg": {k: round(v, 6) for k, v in sorted(by_scope.items())},
        "duplicate_conflict_groups": len(conflict_keys),
    }
    status = "complete" if not review and calculated else \
             ("partial" if calculated else "needs_review")
    return {
        "status": status,
        "generated_at": _now(),
        "results": results,
        "summary": summary,
        "assumptions": assumptions,
        "warnings": warnings,
    }


def self_test() -> Dict[str, Any]:
    """内核自测：给 CI 与 tools/ 冒烟用。"""
    out: Dict[str, Any] = {}
    fuel = calc_fuel_carbon(65.5)
    out["fuel_65_5_kg"] = fuel.get("co2_kg")
    out["fuel_65_5_t"] = fuel.get("co2_t")
    out["implied_km_28"] = round(implied_distance(65.5, 28.0) or 0.0, 3)
    out["distance_track_233_9"] = calc_distance_carbon(
        round(implied_distance(65.5, 28.0) or 0.0, 3), 18.0, 3).get("co2_kg")
    out["factor_class3_30t"] = factor_for(3, 30.0)
    out["factor_class2_18t"] = factor_for(2, 18.0)
    out["financial_diesel_kg"] = calculate_invoice_carbon(
        {"item_name": "0#柴油", "fuel_liters": 65.5}).get("emissions_kg_co2e")
    out["financial_electricity_kg"] = calculate_invoice_carbon(
        {"item_name": "电费", "quantity": 1000, "unit": "kWh"}).get("emissions_kg_co2e")
    return out


if __name__ == "__main__":
    print(json.dumps(self_test(), ensure_ascii=False, indent=2))
