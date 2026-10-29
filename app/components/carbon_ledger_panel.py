# -*- coding: utf-8 -*-
"""碳会计 A0–A6 审计轨面板：挂在「碳核算与台账」页的第二个 tab。

定位：**只读展示**。所有数字与判定都来自 ``data/processed/`` 的碳账结果表
（由 ``python -m src.carbon_ledger_export`` 从 ``carbon_ledger/`` 的 Excel 交付物
预计算导出），本模块不 import 适配器、不在页面里做任何判定或核算 ——
守住项目「前端只读不算」的铁律。

六块内容（对应 docs/54 §5）：

    ① 账本状态条      —— 这份账现在能不能用，一眼看完
    ② 资格判定链      —— 为什么正式汇总是 0 行，逐条说清（**本面板的核心**）
    ③ 配额台账        —— 可用量、已用、缺口
    ④ 证据链索引      —— 每行的凭证在哪，凭证文件是否真的存在
    ⑤ 活动量就绪度    —— 51 个字段今天能不能填、谁来填
    ⑥ 规则与自证      —— 控制规则、基础用例、压力场景、规则卡

纪律：
    · 模拟数据必须显式标注（``simulated`` / 代理因子），绝不冒充实测；
    · **不提供任何写入或审批控件** —— 审批载体是 Excel，不是页面；
    · WTW（碳账）与 TTW（工程 E₀）两套口径**不合并、不相减、不并排比大小**；
    · 「0 行正式汇总」必须写成"无合格正式行"，**不能**让人读成"排放为零"。
"""
from __future__ import annotations

import html
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

import services
from components import theme

CSS = """
<style>
.cl-status{display:flex;gap:12px;align-items:center;flex-wrap:wrap;
  padding:13px 18px;border-radius:14px;background:rgba(255,255,255,.055);
  box-shadow:0 0 0 1px rgba(255,255,255,.09);margin:2px 0 14px;
  font:12px/1.6 'JetBrains Mono',monospace;color:#9AA2AD}
.cl-tag{padding:3px 10px;border-radius:100px;font-weight:700;letter-spacing:.04em}
.cl-red{background:rgba(255,107,107,.14);color:#FF8A8A;
  box-shadow:0 0 0 1px rgba(255,107,107,.28)}
.cl-amber{background:rgba(245,176,76,.14);color:#FFC46B;
  box-shadow:0 0 0 1px rgba(245,176,76,.28)}
.cl-blue{background:rgba(70,183,255,.12);color:#7CCBFF;
  box-shadow:0 0 0 1px rgba(70,183,255,.26)}
.cl-mint{background:rgba(95,227,210,.14);color:#8BF3E6;
  box-shadow:0 0 0 1px rgba(95,227,210,.28)}
.cl-frozen{background:rgba(124,147,255,.14);color:#A9B8FF;
  box-shadow:0 0 0 1px rgba(124,147,255,.28)}
.cl-note{padding:11px 15px;border-radius:12px;background:rgba(70,183,255,.08);
  box-shadow:0 0 0 1px rgba(70,183,255,.18);font-size:12.5px;line-height:1.85;
  color:#9AA2AD;margin:10px 0}
.cl-note b{color:#A9DDFF}
.cl-warn{padding:11px 15px;border-radius:12px;background:rgba(245,176,76,.08);
  box-shadow:0 0 0 1px rgba(245,176,76,.2);font-size:12.5px;line-height:1.85;
  color:#9AA2AD;margin:10px 0}
.cl-warn b{color:#FFC46B}
.cl-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));
  gap:12px;margin:6px 0 18px}
.cl-cell{padding:15px 16px;border-radius:14px;background:rgba(255,255,255,.05);
  box-shadow:0 0 0 1px rgba(255,255,255,.08)}
.cl-cell .k{font:10px/1.4 'JetBrains Mono',monospace;letter-spacing:.12em;
  color:#5B636E;text-transform:uppercase}
.cl-cell .v{margin-top:7px;font:700 25px/1.15 'Hanken Grotesk',sans-serif;
  color:#F3F5F8;font-variant-numeric:tabular-nums}
.cl-cell .u{margin-top:3px;font:11px/1.4 'Inter',sans-serif;color:#5B636E}
.cl-cell.cl-blue .v{color:#7CCBFF}
.cl-cell.cl-mint .v{color:#8BF3E6}
.cl-cell.cl-amber .v{color:#FFC46B}
.cl-cell.cl-red .v{color:#FF8A8A}
.cl-gates{display:flex;flex-direction:column;gap:9px;margin:8px 0 6px}
.cl-gate{display:grid;grid-template-columns:26px minmax(200px,1.7fr) 116px minmax(170px,1.3fr);
  gap:12px;align-items:center;padding:11px 14px;border-radius:12px;
  background:rgba(255,255,255,.04);box-shadow:0 0 0 1px rgba(255,255,255,.07)}
.cl-gate .ic{font:700 15px/1 'JetBrains Mono',monospace;text-align:center}
.cl-gate.pass .ic{color:#8BF3E6}
.cl-gate.fail .ic{color:#FF8A8A}
.cl-gate.partial .ic{color:#FFC46B}
.cl-gate .lb{font-size:12.5px;line-height:1.45;color:#C7CDD6}
.cl-gate .lb small{display:block;margin-top:3px;color:#5B636E;font-size:11px}
.cl-gate .ct{font:11px/1.5 'JetBrains Mono',monospace;color:#9AA2AD}
.cl-gate .rs{font:11px/1.5 'JetBrains Mono',monospace;color:#FFC46B}
.cl-foot{margin-top:6px;font:11px/1.7 'JetBrains Mono',monospace;color:#5B636E}
@media (max-width:900px){
  .cl-gate{grid-template-columns:26px 1fr;row-gap:6px}
  .cl-gate .ct,.cl-gate .rs{grid-column:2}
}
</style>
"""


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _e(value: Any) -> str:
    return html.escape(str(value))


def _f(value: Any, digits: int = 2, dash: str = "—") -> str:
    """安全数字格式化；缺失显示破折号，**不编数字**。"""
    try:
        if value is None:
            return dash
        number = float(value)
    except (TypeError, ValueError):
        return dash
    if number != number:  # NaN
        return dash
    return ("%%.%df" % digits) % number


def _i(value: Any, dash: str = "—") -> str:
    try:
        if value is None:
            return dash
        return "%d" % int(value)
    except (TypeError, ValueError):
        return dash


def _cell(css: str, key: str, value: str, unit: str) -> str:
    return ("<div class='cl-cell %s'><div class='k'>%s</div>"
            "<div class='v'>%s</div><div class='u'>%s</div></div>"
            % (css, _e(key), _e(value), _e(unit)))


# ---------------------------------------------------------------------------
# ① 账本状态条
# ---------------------------------------------------------------------------

def _status_strip(summary: Dict[str, Any], gates: Dict[str, Any]) -> None:
    formal = summary.get("formal") or {}
    factor = summary.get("factor") or {}
    ledger_status = str(summary.get("ledger_status") or "—")
    formal_status = str(formal.get("gate_status") or "—")
    simulated_rows = int(summary.get("simulated_rows") or 0)
    factor_status = ""
    for item in factor.get("factor_status") or []:
        factor_status = str(item.get("value") or "")
        break
    ledger_class = "cl-red" if ledger_status != "已获正式批准" else "cl-mint"
    if "未获正式批准" in ledger_status:
        ledger_class = "cl-amber"
    formal_class = "cl-red" if str(formal.get("approved_row_count") or 0) == 0 else "cl-mint"
    basis = str(factor.get("basis_label") or "—")
    st.markdown(
        "<div class='cl-status'>"
        "<span class='cl-tag %s'>%s</span>"
        "<span class='cl-tag %s'>正式汇总 %s</span>"
        "<span class='cl-tag cl-blue'>%s</span>"
        "<span class='cl-tag %s'>%s</span>"
        "<span class='cl-tag cl-amber'>%s 行 simulated</span>"
        "<span class='cl-tag cl-frozen'>只读 · 不参与 28 条闸门 · 不回写 E₀</span>"
        "</div>" % (
            ledger_class, _e(ledger_status),
            formal_class, _e(formal_status),
            _e(basis),
            "cl-amber" if factor_status and "pending" in factor_status else "cl-mint",
            _e(factor_status or "因子状态未知"),
            _e(simulated_rows),
        ),
        unsafe_allow_html=True)


def _boundary_note(summary: Dict[str, Any]) -> None:
    factor = summary.get("factor") or {}
    decomposition = factor.get("decomposition") or {}
    formal = summary.get("formal") or {}
    st.markdown(
        "<div class='cl-note'>"
        "<b>口径边界（先看这段）：</b>%s<br>"
        "<b>本轨因子：</b>柴油 %s kgCO₂e/L = 尾管 %s + 上游 %s（来源 %s）；"
        "工程侧 E₀ 是 <b>TTW 尾管燃烧</b>口径 —— 两边的总量<b>不并排、不相减、不做趋势对比</b>。<br>"
        "<b>「0 行正式汇总」的意思：</b>%s。"
        "</div>" % (
            _e(summary.get("boundary") or "两套口径不可混算"),
            _f(decomposition.get("diesel_wtw_kg_per_l"), 2),
            _f(decomposition.get("diesel_ttw_kg_per_l"), 2),
            _f(decomposition.get("diesel_wtt_kg_per_l"), 2),
            _e(decomposition.get("source") or "config/calc_factors.json"),
            _e(formal.get("meaning") or ""),
        ),
        unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# ② 资格判定链（本面板的核心）
# ---------------------------------------------------------------------------

def _render_gates(gates: Dict[str, Any]) -> None:
    conditions = gates.get("conditions") or []
    if not conditions:
        st.info("没有判定链数据（结果表缺失，请先执行 python -m src.carbon_ledger_export）。")
        return
    total = int(gates.get("total_rows") or 0)
    approved = int(gates.get("approved_rows") or 0)
    rows_html: List[str] = []
    for condition in conditions:
        status = str(condition.get("status") or "fail")
        icon = {"pass": "✓", "fail": "✗", "partial": "◐"}.get(status, "·")
        reasons = condition.get("block_reasons") or []
        reason_text = "；".join(
            "%s × %d" % (item.get("value"), item.get("count")) for item in reasons)
        if not reason_text:
            reason_text = "全部通过"
        rows_html.append(
            "<div class='cl-gate %s'>"
            "<div class='ic'>%s</div>"
            "<div class='lb'>%s<small>%s</small></div>"
            "<div class='ct'>通过 %s / 阻断 %s（共 %s 行）</div>"
            "<div class='rs'>%s</div>"
            "</div>" % (
                status, icon, _e(condition.get("label")),
                _e(condition.get("why")),
                _i(condition.get("passed")), _i(condition.get("blocked")), _i(total),
                _e(reason_text),
            ))
    st.markdown("<div class='cl-gates'>%s</div>" % "".join(rows_html),
                unsafe_allow_html=True)
    st.caption(
        "判定链逐项复现主账 `Trip_Detail!AP2` 公式的谓词（来源：%s）。"
        "公式是 AND 关系，**任一项不满足即排除**；本面板只展示判定结果，"
        "不提供任何写入或审批控件 —— 审批载体是 Excel。"
        % gates.get("formula_source", "carbon_ledger_main.xlsx!Trip_Detail!AP2"))
    first_block = gates.get("first_block") or []
    if first_block:
        top = first_block[0]
        st.info("12/12 行在**第一道**判定（%s）即被阻断 —— 这是正式汇总为 0 行的直接原因。"
                % next((str(item.get("label")) for item in conditions
                        if item.get("key") == top.get("key")), "第一项"))


# ---------------------------------------------------------------------------
# ③ 配额台账
# ---------------------------------------------------------------------------

def _render_quota(quota: Dict[str, Any]) -> None:
    if not quota:
        st.info("没有配额台账数据。")
        return
    cells = [
        ("cl-blue", "OPENING", _f(quota.get("opening_quota_t"), 4), "tCO₂e 期初配额"),
        ("cl-blue", "PURCHASED", _f(quota.get("purchased_quota_t"), 4), "tCO₂e 本期购入"),
        ("cl-mint", "AVAILABLE", _f(quota.get("available_quota_t"), 4), "tCO₂e 可用配额"),
        ("cl-amber", "USED", _f(quota.get("compliance_used_t"), 4), "tCO₂e 已用（模拟汇总口径）"),
        ("", "BALANCE", _f(quota.get("closing_balance_t"), 4), "tCO₂e 结余"),
        ("cl-red", "SHORTFALL", _f(quota.get("shortfall_t"), 4), "tCO₂e 配额缺口"),
    ]
    st.markdown("<div class='cl-grid'>%s</div>"
                % "".join(_cell(*item) for item in cells),
                unsafe_allow_html=True)
    shortfall = quota.get("shortfall_t")
    if shortfall is not None and float(shortfall) > 0:
        st.error("配额缺口 %.6f tCO₂e：按当前台账口径，可用配额不足，需追加购买。"
                 % float(shortfall))
    st.caption("数据状态 `%s`；审批状态 `%s`。本页只读，**不提供**「批准」或「修改配额」控件。"
               % (quota.get("data_status") or "—", quota.get("approval_status") or "—"))
    with st.expander("配额台账原始字段（交付方原文，未经改写）", expanded=False):
        st.caption("以下为工作簿原始字段。**字段原文可能与上方汇总数值不一致**"
                   "（已知问题登记见 docs/54 §6），本页不改写、不隐藏。")
        st.json(quota.get("raw_fields") or {})
        if quota.get("source_document"):
            st.caption("Source_Document：%s" % quota.get("source_document"))
        if quota.get("notes"):
            st.caption("Notes：%s" % quota.get("notes"))
        instructions = quota.get("instructions") or []
        if instructions:
            st.dataframe(pd.DataFrame(instructions), width="stretch", hide_index=True)


# ---------------------------------------------------------------------------
# ④ 证据链索引
# ---------------------------------------------------------------------------

def _render_evidence(evidence: pd.DataFrame) -> None:
    if evidence.empty:
        st.info("没有证据索引数据。")
        return
    total = int(len(evidence))
    row_level = int(evidence["is_row_level"].sum()) if "is_row_level" in evidence else 0
    present = int(evidence["evidence_file_exists"].sum()) if "evidence_file_exists" in evidence else 0
    not_provided = int((evidence.get("status_bucket") == "not_provided").sum()) \
        if "status_bucket" in evidence else 0
    placeholders = int(evidence["evidence_is_simulated_placeholder"].sum()) \
        if "evidence_is_simulated_placeholder" in evidence else 0
    source_docs = int(evidence["source_reference_file_available"].sum()) \
        if "source_reference_file_available" in evidence else 0
    original_activity = int(evidence["original_activity_evidence_available"].sum()) \
        if "original_activity_evidence_available" in evidence else 0
    cells = [
        ("cl-blue", "EVIDENCE", _i(total), "条证据索引"),
        ("cl-mint", "ROW-LEVEL", _i(row_level), "条挂到主账行"),
        ("", "ARTIFACT EXISTS", "%d / %d" % (present, total), "路径对应文件存在"),
        ("cl-amber", "SIMULATED PLACEHOLDERS", _i(placeholders), "个模拟占位文件，不是原始凭证"),
        ("cl-blue", "SOURCE DOCS", _i(source_docs), "个非占位来源文件"),
        ("cl-red", "ORIGINAL ACTIVITY EVIDENCE", "%d / %d" % (original_activity, row_level), "条行级原始活动凭证已提供"),
    ]
    st.markdown("<div class='cl-grid'>%s</div>"
                % "".join(_cell(*item) for item in cells),
                unsafe_allow_html=True)
    columns = [name for name in [
        "Evidence_ID", "Ledger_Row_ID", "Trip_ID", "Voucher_Type",
        "Evidence_File_Path", "evidence_file_resolved", "evidence_file_exists",
        "evidence_is_simulated_placeholder", "source_reference_file_available",
        "original_activity_evidence_available", "evidence_artifact_kind",
        "is_row_level", "Original_Document_Status", "Verification_Status",
    ] if name in evidence.columns]
    view = evidence[columns].copy()
    view = view.rename(columns={
        "Evidence_ID": "证据编号", "Ledger_Row_ID": "主账行",
        "Trip_ID": "物理趟次", "Voucher_Type": "凭证类型",
        "Evidence_File_Path": "凭证路径（交付方声明）",
        "evidence_file_resolved": "凭证路径（仓库内解析）",
        "evidence_file_exists": "索引路径文件存在",
        "evidence_is_simulated_placeholder": "模拟占位文件",
        "source_reference_file_available": "非占位来源文件存在",
        "original_activity_evidence_available": "行级原始活动凭证已提供",
        "evidence_artifact_kind": "文件类型判定",
        "is_row_level": "凭证类型归属",
        "Original_Document_Status": "原始凭证状态（交付方声明）",
        "Verification_Status": "核验状态（交付方声明）",
    })
    for column in ("索引路径文件存在", "模拟占位文件", "非占位来源文件存在", "行级原始活动凭证已提供"):
        if column in view.columns:
            view[column] = view[column].map({True: "是", False: "否"})
    if "凭证类型归属" in view.columns:
        view["凭证类型归属"] = view["凭证类型归属"].map(
            {True: "行级凭证", False: "因子来源证据"})
    st.dataframe(view, width="stretch", hide_index=True)
    st.caption("“索引路径文件存在”只说明文件系统中有对应文件；simulated_evidence 占位文件不是真实发票或设备记录。"
               "只有“行级原始活动凭证已提供”为是且通过人工核验，才能作为真实活动证据；状态字段空白**不算**已提供。")


# ---------------------------------------------------------------------------
# ⑤ 活动量就绪度
# ---------------------------------------------------------------------------

def _render_readiness(readiness: Dict[str, Any]) -> None:
    fields = readiness.get("fields") or []
    if not fields:
        st.info("没有就绪度数据。")
        return
    counts = readiness.get("counts") or []
    css_by_id = {
        "engineering": "cl-mint", "obd": "cl-blue",
        "invoice": "cl-blue", "finance": "cl-amber", "external": "cl-red",
    }
    cells = []
    for item in counts:
        cells.append((css_by_id.get(str(item.get("category")), ""),
                      str(item.get("label") or ""),
                      _i(item.get("count")), "个字段"))
    st.markdown("<div class='cl-grid'>%s</div>"
                % "".join(_cell(*item) for item in cells),
                unsafe_allow_html=True)
    frame = pd.DataFrame(fields)
    view = frame[["field", "label_zh", "data_type", "required", "source_text",
                  "category_label", "note"]].rename(columns={
        "field": "字段名", "label_zh": "中文含义", "data_type": "数据类型",
        "required": "必填", "source_text": "交付方声明的来源",
        "category_label": "今天能否填", "note": "本仓库映射说明"})
    st.dataframe(view, width="stretch", hide_index=True,
                 column_config={
                     "今天能否填": st.column_config.TextColumn(width="medium"),
                     "本仓库映射说明": st.column_config.TextColumn(width="large"),
                 })
    st.caption("%s" % (readiness.get("note") or ""))
    st.caption("就绪度口径来源：`config/carbon_ledger_field_map.json`；"
               "字段清单来源：`carbon_ledger/a2_activity_data/activity_data_field_dictionary.xlsx`。")


# ---------------------------------------------------------------------------
# ⑥ 规则与自证
# ---------------------------------------------------------------------------

def _render_rules(rules: Dict[str, Any]) -> None:
    if not rules:
        st.info("没有规则数据。")
        return
    cells = [
        ("cl-blue", "CONTROL RULES", _i(rules.get("validation_rule_count")), "条控制规则"),
        ("cl-blue", "TEST CASES", _i(rules.get("test_case_count")), "项基础用例"),
        ("cl-mint", "PRESSURE", "%s / %s" % (_i(rules.get("pressure_passed")),
                                            _i(rules.get("pressure_case_count"))),
         "项压力场景通过"),
        ("cl-mint" if (rules.get("self_check") or {}).get("script_available") else "cl-red",
         "SELF-CHECK",
         ("%s / %s" % ((rules.get("self_check") or {}).get("passed", 0),
                        (rules.get("self_check") or {}).get("failed", 0))
          if (rules.get("self_check") or {}).get("result_available") else
          ("脚本已提供" if (rules.get("self_check") or {}).get("script_available") else "未提供")),
         "自检通过/失败；%s 个模拟占位文件" % _i(rules.get("simulated_placeholder_file_count"))),
    ]
    st.markdown("<div class='cl-grid'>%s</div>"
                % "".join(_cell(*item) for item in cells),
                unsafe_allow_html=True)
    st.caption("%s" % (rules.get("note") or ""))

    validation = rules.get("validation_rules") or []
    if validation:
        with st.expander("控制规则（Validation_Rules）", expanded=False):
            st.dataframe(pd.DataFrame(validation), width="stretch", hide_index=True)
    pressure = rules.get("pressure_test_cases") or []
    if pressure:
        with st.expander("压力场景（Pressure_Test_Cases）", expanded=False):
            st.dataframe(pd.DataFrame(pressure), width="stretch", hide_index=True)
    feasibility = rules.get("feasibility_summary") or []
    if feasibility:
        with st.expander("可行性自查（Feasibility_Summary）", expanded=False):
            st.dataframe(pd.DataFrame(feasibility), width="stretch", hide_index=True)
    pending = rules.get("pending_items") or []
    if pending:
        with st.expander("待补数据项（Pending_4_Vehicle_Data）", expanded=False):
            st.dataframe(pd.DataFrame(pending), width="stretch", hide_index=True)
            st.caption("四辆车历史偏差样例：**按负责人指示暂不处理**，不复制、不计算、不入账。")
    card = rules.get("rule_card_markdown") or ""
    if card:
        with st.expander("核算规则口径卡（原文）", expanded=False):
            st.caption("来源：%s" % rules.get("rule_card_file", ""))
            st.markdown(card)


# ---------------------------------------------------------------------------
# 主渲染
# ---------------------------------------------------------------------------

def _empty_state() -> None:
    st.markdown(
        "<div class='cl-note'>"
        "<b>碳会计 A0–A6 审计轨尚未导出。</b><br>"
        "本轨读的是 <code>carbon_ledger/</code> 下交付方工作簿的**已算好**结果，"
        "由适配器导出成前端只读结果表。<br>"
        "生成方式：<code>python -m src.carbon_ledger_export</code>。"
        "</div>", unsafe_allow_html=True)


def render_carbon_ledger_panel() -> None:
    """碳会计审计轨面板（挂在「碳核算与台账」页的第二个 tab）。"""
    st.markdown(CSS, unsafe_allow_html=True)
    summary = services.carbon_ledger_summary()
    gates = services.carbon_ledger_gates()
    trips = services.carbon_ledger_trips()
    if not summary or trips.empty:
        _empty_state()
        return

    _status_strip(summary, gates)
    integration_status = services.carbon_ledger_integration_status()
    if integration_status.get("ready"):
        st.caption("接入来源：%s · 导出时间：%s · 主账 SHA-256 前缀：%s · 正式批准行：%s" % (
            integration_status.get("source_workbook") or "—",
            integration_status.get("generated_at") or "—",
            integration_status.get("source_sha256_16") or "—",
            _i(integration_status.get("approved_rows")),
        ))
    else:
        st.warning("碳账适配器元数据不可用：%s" % integration_status.get("reason", "未知原因"))
    if summary.get("is_simulated"):
        st.warning("模拟数据：当前主账 %d 行全部为 `simulated` 合成记录，"
                   "排放因子为代理值（proxy_pending_verification），"
                   "**不代表真实排放，不可用于对外披露**。"
                   % int(summary.get("simulated_rows") or 0))
    _boundary_note(summary)

    # ① 正式汇总资格判定链 —— 本轨最该被看见的东西
    theme.section("正式汇总资格判定链",
                  "主账 AP2 公式的 9 项谓词逐条复现 · 只读判定，审批仍在 Excel")
    _render_gates(gates)

    # ② 配额台账
    theme.section("配额台账", "模拟口径下的可用量、已用量与缺口")
    _render_quota(services.carbon_ledger_quota())

    # ③ 证据链索引
    theme.section("证据链索引", "每行挂哪条凭证 · 凭证文件是否真的存在")
    _render_evidence(services.carbon_ledger_evidence())

    # ④ 活动量就绪度
    theme.section("活动数据 51 字段就绪度", "今天能不能填 · 谁来填 · 下一步补什么")
    _render_readiness(services.carbon_ledger_readiness())

    # ⑤ 规则与自证
    theme.section("规则与自证", "控制规则 · 基础用例 · 压力场景 · 核算规则卡")
    _render_rules(services.carbon_ledger_rules())

    # ⑥ 主账明细（展开）
    with st.expander("碳账主账明细（Trip_Detail 全 42 列 + 门禁谓词拆解）", expanded=False):
        columns = [name for name in [
            "Ledger_Row_ID", "Trip_ID", "Vehicle_ID", "Trip_Date", "Activity_Type",
            "Activity_Value", "Activity_Unit", "Distance_km", "Cargo_t",
            "EF_Value", "EF_Unit", "Factor_Status", "Emissions_kgCO2e",
            "Emissions_tCO2e", "Data_Status", "Simulated", "Evidence_ID",
            "Invoice_No", "Run_ID", "Include_In_Summary", "Validation_Status",
            "Validation_Message", "Formal_Use_Gate", "Physical_Trip_Control",
            "Owner_Approval_Status (manual)", "Formal_Summary_Eligibility",
        ] if name in trips.columns]
        st.dataframe(trips[columns], width="stretch", hide_index=True)
        st.caption("排放量列为工作簿已算好的缓存值，适配器不重算；"
                   "门禁谓词列（`gate_*`）是主账 AP2 公式的逐项拆解。")

    st.markdown(
        "<div class='cl-foot'>"
        "碳会计 A0–A6 审计轨 · 只读视图 · 不参与 28 条主闸门对拍 · 不回写 E₀ 主口径 · "
        "WTW 与 TTW 两套口径不合并、不相减、不并排比大小<br>"
        "结果表由 <code>python -m src.carbon_ledger_export</code> 生成 · "
        "数据源 <code>carbon_ledger/</code> · 口径见 docs/54"
        "</div>", unsafe_allow_html=True)
