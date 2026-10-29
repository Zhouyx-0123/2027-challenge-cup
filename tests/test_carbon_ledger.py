# -*- coding: utf-8 -*-
"""碳会计 A0–A6 审计轨的适配器与前端守卫测试（docs/54）。

覆盖五条线：
  1. **适配器** —— 从交付工作簿导出结果表，不重算排放量、不改交付方文件；
  2. **判定链** —— 9 项谓词复现主账 AP2 公式，结论与工作簿的合成判定一致；
  3. **口径边界** —— WTW / TTW 两套口径不混算；正式汇总 0 行 ≠ 排放为零；
  4. **只读层** —— 前端只从 data/processed/ 读，页面里不写公式、不写 Excel；
  5. **证据链** —— 凭证文件存在性按文件系统事实判定，空白状态不算已提供。
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]

from src import carbon_ledger_export as cle  # noqa: E402

APP_DIR = ROOT / "app"
PANEL_PATH = APP_DIR / "components" / "carbon_ledger_panel.py"
SERVICES_PATH = APP_DIR / "services.py"
MAIN_VIEWS_PATH = APP_DIR / "main_views.py"
FIELD_MAP_PATH = ROOT / "config" / "carbon_ledger_field_map.json"


# ---------------------------------------------------------------------------
# 1. 适配器
# ---------------------------------------------------------------------------

def test_export_writes_all_result_tables(tmp_path):
    """写到临时目录 —— 不去弄脏已入库的 data/processed/。"""
    report = cle.export(write=True, out_dir=tmp_path)
    expected = [
        "carbon_ledger_trips.parquet", "carbon_ledger_evidence.parquet",
        "carbon_ledger_summary.json", "carbon_ledger_gates.json",
        "carbon_ledger_quota.json", "carbon_ledger_activity_readiness.json",
        "carbon_ledger_rules.json",
    ]
    for name in expected:
        path = tmp_path / name
        if path.exists():
            assert path.stat().st_size > 0, "结果表为空：%s" % name
        elif name.endswith(".parquet") and (tmp_path / Path(name).with_suffix(".json")).exists():
            # 最小环境缺少 Parquet 引擎时，适配器输出显式 JSON 侧车；不将其伪装成 Parquet。
            assert (tmp_path / Path(name).with_suffix(".json")).stat().st_size > 0
        else:
            pytest.fail("结果表缺失（Parquet 或明确 JSON 侧车）：%s" % name)
    assert report["trip_rows"] == 12
    assert report["approved_rows"] == 0


def test_export_does_not_touch_delivery_files(tmp_path):
    """适配器只读交付物，绝不写回 carbon_ledger/。"""
    before = {path: path.stat().st_mtime for path in cle.LEDGER_DIR.rglob("*")
              if path.is_file()}
    cle.export(write=True, out_dir=tmp_path)
    after = {path: path.stat().st_mtime for path in cle.LEDGER_DIR.rglob("*")
             if path.is_file()}
    assert before == after, "适配器改动了交付方文件"


def test_export_does_not_dirty_committed_result_tables(tmp_path):
    """跑测试不该让 data/processed/ 里已入库的结果表变成脏工作树。"""
    tracked = [cle.PROCESSED / name for name in
               ("carbon_ledger_summary.json", "carbon_ledger_gates.json",
                "carbon_ledger_quota.json", "carbon_ledger_rules.json",
                "carbon_ledger_activity_readiness.json")]
    before = {path: path.stat().st_mtime for path in tracked if path.exists()}
    cle.export(write=True, out_dir=tmp_path)
    after = {path: path.stat().st_mtime for path in tracked if path.exists()}
    assert before == after, "适配器写到了 data/processed/，测试会弄脏工作树"


def test_adapter_does_not_recompute_emissions():
    """排放量列取工作簿缓存值，适配器不重算。"""
    import openpyxl

    workbook = openpyxl.load_workbook(cle.MAIN_LEDGER, data_only=True)
    sheet = workbook["Trip_Detail"]
    header = [cell.value for cell in sheet[1]]
    index = header.index("Emissions_tCO2e")
    expected = sum(float(row[index].value) for row in sheet.iter_rows(min_row=2)
                   if row[0].value is not None)
    trips = cle.build_trips()
    actual = float(trips["Emissions_tCO2e"].astype(float).sum())
    assert abs(actual - expected) < 1e-9
    assert abs(actual - 2.439298) < 1e-6


def test_trips_keep_all_42_ledger_columns():
    trips = cle.build_trips()
    assert len(trips) == 12
    for column in ("Ledger_Row_ID", "Trip_ID", "Emissions_tCO2e", "Data_Status",
                   "Evidence_ID", "Run_ID", "Formal_Use_Gate",
                   "Owner_Approval_Status (manual)",
                   "Formal_Summary_Eligibility"):
        assert column in trips.columns, "主账列缺失：%s" % column


# ---------------------------------------------------------------------------
# 2. 判定链
# ---------------------------------------------------------------------------

def test_gate_chain_has_nine_predicates():
    """AP2 公式的 AND 里是 9 个谓词（docs/53 记成 8 个，漏了 Source_Ledger_Row_No）。"""
    gates = cle.build_gates(cle.build_trips())
    assert gates["predicate_count"] == 9
    keys = [item["key"] for item in gates["conditions"]]
    for key in ("data_status_actual", "simulated_not_yes", "factor_verified",
                "formal_precheck", "unique_physical_trip", "owner_approval",
                "evidence_id", "run_id", "source_ledger_row_no"):
        assert key in keys, "判定项缺失：%s" % key


def test_gate_chain_matches_workbook_verdict():
    """适配器拆解出的结论必须与主账的合成判定一致（12 行全部 EXCLUDED）。"""
    gates = cle.build_gates(cle.build_trips())
    assert gates["total_rows"] == 12
    assert gates["approved_rows"] == 0
    distribution = {item["value"]: item["count"]
                    for item in gates["eligibility_distribution"]}
    assert distribution == {"EXCLUDED_NOT_FORMALLY_APPROVED": 12}


def test_gate_chain_identifies_the_first_blocking_condition():
    """12 行都在第一道判定（Data_Status = actual）即被阻断。"""
    gates = cle.build_gates(cle.build_trips())
    assert gates["first_block"][0]["key"] == "data_status_actual"
    assert gates["first_block"][0]["count"] == 12


def test_gate_conditions_report_block_reasons():
    gates = cle.build_gates(cle.build_trips())
    by_key = {item["key"]: item for item in gates["conditions"]}
    assert by_key["data_status_actual"]["blocked"] == 12
    assert by_key["data_status_actual"]["block_reasons"][0]["value"] == "simulated"
    assert by_key["owner_approval"]["block_reasons"][0]["value"] == "not approved"
    assert by_key["unique_physical_trip"]["passed"] == 12
    assert by_key["evidence_id"]["passed"] == 12
    assert by_key["run_id"]["passed"] == 12


# ---------------------------------------------------------------------------
# 3. 口径边界
# ---------------------------------------------------------------------------

def test_formal_zero_rows_is_not_zero_emissions():
    """「0 行」必须表述为「无合格正式行」，不能读成排放为零。"""
    summary = cle.build_summary(cle.build_trips(), cle.build_gates(cle.build_trips()))
    assert summary["formal"]["approved_row_count"] == 0
    assert summary["formal"]["gate_status"] == "NO_APPROVED_ROWS"
    assert "不等于实际排放为零" in summary["formal"]["meaning"]
    assert summary["total_emissions_t"] > 0


def test_two_bases_are_not_mixed():
    """WTW（碳账）与 TTW（工程）两套口径不混算：结果表里不得同时出现两边总量。"""
    summary = cle.build_summary(cle.build_trips(), cle.build_gates(cle.build_trips()))
    factor = summary["factor"]
    assert factor["basis"] == "WTW"
    assert factor["engineering_basis"] == "TTW"
    assert factor["mix_allowed"] is False
    # 分解值来自唯一真源 config/calc_factors.json：尾管 2.63 + 上游 0.53 = 3.16
    decomposition = factor["decomposition"]
    assert decomposition["diesel_ttw_kg_per_l"] == 2.63
    assert decomposition["diesel_wtt_kg_per_l"] == 0.53
    assert abs(decomposition["diesel_wtw_kg_per_l"] - 3.16) < 1e-9
    # 结果表里不得出现工程侧 E₀ 的总量字段
    assert "e0_t" not in json.dumps(summary, ensure_ascii=False)
    assert "engineering_total_t" not in json.dumps(summary, ensure_ascii=False)


def test_ledger_factor_matches_calc_factors():
    """碳账的 EF 3.16 应等于本项目因子表的 TTW + WTT（口径自洽，非数据错误）。"""
    summary = cle.build_summary(cle.build_trips(), cle.build_gates(cle.build_trips()))
    assert summary["factor"]["ledger_ef_matches_calc_factors"] is True


def test_summary_marks_simulated():
    summary = cle.build_summary(cle.build_trips(), cle.build_gates(cle.build_trips()))
    assert summary["is_simulated"] is True
    assert summary["simulated_rows"] == 12
    assert summary["actual_rows"] == 0
    assert summary["ledger_status"] == "未获正式批准"


# ---------------------------------------------------------------------------
# 4. 证据链
# ---------------------------------------------------------------------------

def test_evidence_file_existence_distinguishes_placeholders_from_originals():
    """模拟占位路径存在，不得误报为真实活动凭证。

    2026-10-29 更新：12 个占位凭证已随交付补入 ``carbon_ledger/a5_evidence/simulated_evidence/``；
    第 13 条 ``SRC-EVD-SRC004`` 指向的 ``碳慧智算汇报.pptx`` 已随方向调整**作废删除**，
    因此它必须被判为 ``missing_file`` —— 断言按**分类**写，不写死计数，
    免得下次补件或再删件时又红。
    """
    evidence = cle.build_evidence()
    assert len(evidence) == 13
    placeholder = evidence[evidence["evidence_is_simulated_placeholder"]]
    assert len(placeholder) == 12, "12 条行级凭证应为占位文件"
    assert set(placeholder["evidence_artifact_kind"]) == {
        "simulated_placeholder_not_original_evidence"}
    # 已作废的 PPT 不再是可用来源文件
    retired = evidence[evidence["Evidence_ID"] == "SRC-EVD-SRC004"]
    assert not retired.empty
    assert int(retired["evidence_file_exists"].sum()) == 0
    assert set(retired["evidence_artifact_kind"]) == {"missing_file"}
    # 占位文件**不得**被算成原始活动凭证
    assert int(evidence["original_activity_evidence_available"].sum()) == 0
    assert int(evidence["is_row_level"].sum()) == 12


def test_blank_status_is_not_counted_as_provided():
    evidence = cle.build_evidence()
    buckets = evidence["status_bucket"].value_counts().to_dict()
    assert buckets.get("blank", 0) == 9
    assert buckets.get("not_provided", 0) == 3
    assert buckets.get("provided", 0) == 1
    # 空白字段既不是"已提供"，也不该被当成未提供 —— 单列一档
    assert sum(buckets.values()) == 13


# ---------------------------------------------------------------------------
# 5. 就绪度与规则
# ---------------------------------------------------------------------------

def test_readiness_covers_all_51_fields():
    readiness = cle.build_readiness()
    assert readiness["field_count"] == 51
    assert readiness["unmapped_fields"] == []
    assert readiness["unused_map_entries"] == []
    assert readiness["template_field_count"] == 51


def test_readiness_dictionary_header_misalignment_is_handled():
    """字段字典表头与数据错位一列，适配器按语义真序读，结果不能串列。"""
    readiness = cle.build_readiness()
    by_name = {item["field"]: item for item in readiness["fields"]}
    source = by_name["source_row_id"]
    assert source["data_type"] == "文本"
    assert source["required"] == "是"
    assert source["source_text"] == "源系统/导入文件原始行"
    assert source["finance_field"] == "finance_source_row_id"
    assert source["confirm_status"] == "待财务账负责人共同确认"


def test_field_map_has_no_duplicate_factor_values():
    """映射表只描述就绪度，不得夹带 CO₂/L 数值（因子唯一真源是 calc_factors.json）。"""
    payload = json.loads(FIELD_MAP_PATH.read_text(encoding="utf-8"))
    text = json.dumps(payload, ensure_ascii=False)
    for literal in ("2.63", "3.16", "0.5568", "0.53"):
        assert literal not in text, "映射表里混入了因子数值：%s" % literal


def test_rules_are_exported():
    rules = cle.build_rules()
    assert rules["validation_rule_count"] == 14
    assert rules["test_case_count"] == 15
    assert rules["pressure_case_count"] == 19
    assert rules["rule_card_markdown"]


# ---------------------------------------------------------------------------
# 6. 配额台账
# ---------------------------------------------------------------------------

def test_quota_ledger_values():
    quota = cle.build_quota()
    assert quota["quota_record_id"] == "QUOTA-SIM-001"
    assert quota["opening_quota_t"] == 1.0
    assert quota["purchased_quota_t"] == 0.2
    assert quota["available_quota_t"] == 1.2
    assert abs(quota["compliance_used_t"] - 2.439298) < 1e-6
    assert quota["closing_balance_t"] == 0
    assert abs(quota["shortfall_t"] - 1.239298) < 1e-6
    assert quota["approval_status"] == "not approved"
    assert quota["data_status"] == "simulated"


def test_quota_raw_notes_are_preserved_not_rewritten():
    """适配器不改写交付方原文（已知的陈旧数值照实搬运，由文档登记）。"""
    quota = cle.build_quota()
    assert quota["notes"]
    assert "0.5433t" in quota["notes"]


# ---------------------------------------------------------------------------
# 7. 前端守卫
# ---------------------------------------------------------------------------

def _code_only(path: Path) -> str:
    """去掉注释与文档字符串后的源码（避免断言撞上说明文字）。"""
    import io
    import tokenize

    source = path.read_text(encoding="utf-8")
    tokens = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        tokens.append(token)
    text = tokenize.untokenize(tokens)
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body[0].value.value = ""
    return ast.unparse(tree)


def test_panel_does_not_import_the_adapter():
    """前端只读结果表，不 import 适配器、不重算判定。"""
    tree = ast.parse(PANEL_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("src"), \
                    "前端组件不许 import src：%s" % alias.name
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("src"), \
                "前端组件不许 import src：%s" % node.module
    assert "carbon_ledger_export" not in [
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)]
    assert "carbon_ledger_export" not in [
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)]


def test_service_exposes_carbon_integration_provenance():
    import sys
    import types
    try:
        import streamlit  # noqa: F401
    except ModuleNotFoundError:
        # Minimal test-only shim: exercise service data contracts without pretending to launch Streamlit.
        stub = types.ModuleType("streamlit")
        def cache_data(*args, **kwargs):
            def decorate(fn):
                return fn
            return decorate
        stub.cache_data = cache_data
        sys.modules["streamlit"] = stub
    # `app/` 不是 Python 包（前端是 app/app.py 单入口，模块靠 sys.path 注入导入）。
    # 用 `import app.services` 会在别的测试把 app.py 载入 sys.modules['app'] 之后炸掉
    # （"'app' is not a package"），因此这里跟项目里其他前端测试用同一种导入方式。
    app_dir = str(ROOT / "app")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
    import services
    status = services.carbon_ledger_integration_status()
    assert status["ready"] is True
    assert status["trip_rows"] == 12
    assert status["simulated_rows"] == 12
    assert status["actual_rows"] == 0
    assert status["approved_rows"] == 0
    assert status["formal_gate_status"] == "NO_APPROVED_ROWS"
    assert status["source_workbook"].endswith("carbon_ledger_main.xlsx")


def test_services_reads_only_from_processed():
    """只读层只从 data/processed/ 读，不碰 Excel 交付物。"""
    tree = ast.parse(SERVICES_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "carbon_ledger_export" not in alias.name
        if isinstance(node, ast.ImportFrom):
            assert "carbon_ledger_export" not in (node.module or "")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "carbon_ledger/" not in node.value, \
                "只读层不许直接读交付目录：%s" % node.value
            assert "carbon_ledger_main" not in node.value
    source = SERVICES_PATH.read_text(encoding="utf-8")
    for name in ("carbon_ledger_summary", "carbon_ledger_gates",
                 "carbon_ledger_quota", "carbon_ledger_readiness",
                 "carbon_ledger_rules", "carbon_ledger_trips",
                 "carbon_ledger_evidence"):
        assert name in source, "只读层缺函数：%s" % name


def test_services_has_no_arithmetic_on_ledger_totals():
    """只读层不做加减乘除算账（只做筛选/取数）。"""
    tree = ast.parse(SERVICES_PATH.read_text(encoding="utf-8"))
    targets = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith(
                ("carbon_ledger", "_read_carbon_ledger")):
            targets.add(node.name)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in targets:
            for inner in ast.walk(node):
                if isinstance(inner, ast.BinOp) and isinstance(
                        inner.op, (ast.Mult, ast.Div)):
                    offenders.append(node.name)
    assert not offenders, "只读层出现了乘除运算：%s" % offenders


def test_panel_offers_no_write_or_approval_control():
    """页面不提供审批/写入控件 —— 审批载体是 Excel。"""
    source = PANEL_PATH.read_text(encoding="utf-8")
    for banned in ("st.button", "st.form", "st.form_submit_button",
                   "st.data_editor", "st.download_button"):
        assert banned not in source, "审计轨面板出现写控件：%s" % banned


def test_ledger_page_has_two_tabs_not_six_pages():
    """碳账进的是「碳台账」页的第二个 tab，不新增第六页。"""
    source = MAIN_VIEWS_PATH.read_text(encoding="utf-8")
    assert "碳会计口径 A0–A6（审计轨）" in source
    assert "carbon_ledger_panel.render_carbon_ledger_panel()" in source
    nav = (APP_DIR / "components" / "main_nav.py").read_text(encoding="utf-8")
    assert nav.count('{"id":') == 5, "主导航应保持 5 页"


def test_panel_states_the_boundary_and_simulation():
    """页面必须同时给出「口径不混算」与「模拟数据」两条声明。"""
    source = PANEL_PATH.read_text(encoding="utf-8")
    assert "不合并" in source and "不相减" in source
    assert "simulated" in source
    assert "proxy_pending_verification" in source
    assert "不可用于对外披露" in source


def test_panel_never_claims_zero_emissions():
    """「0 行正式汇总」不能被写成「排放为零」（注释与文档字符串不算数）。"""
    code = _code_only(PANEL_PATH)
    assert "排放为零" not in code
    # 正确的表述来自数据（适配器写进 formal.meaning），面板原样引用
    summary = cle.build_summary(cle.build_trips(), cle.build_gates(cle.build_trips()))
    assert "不等于实际排放为零" in summary["formal"]["meaning"]
    assert re.search(r"formal\.get\(\s*['\"]meaning['\"]\s*\)", code)
