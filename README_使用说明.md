# 碳慧智算 A0–A6 碳账交付包（第二轮压力测试修订版）

## 结论与状态

本目录是**经过模拟输入、公式控制逻辑和独立脚本检查的工作交付版**，不是正式批准的碳盘查、财务入账或 ESG 披露结果。

本次按第二轮自查修复：
- 未知活动单位（如 `m³`）明确触发 `BLOCK_UNSUPPORTED_UNIT`，不再只对已知单位做局部匹配。
- 增加物理趟次候选指纹，组合车辆、日期、活动类型、里程、货物质量、活动量和单位；Trip_ID、来源台账行号或候选指纹重复时，冻结该记录并要求复核。
- 增加关键物理身份字段和证据字段缺失拦截。
- 月度/年度测试汇总明确标注 `SIMULATED_TEST_SUMMARY_NOT_FORMAL`。
- 新增独立 `Formal_Monthly_Summary` / `Formal_Annual_Summary`，仅统计所有规则通过且负责人审批状态为 `approved` 的 `FORMAL_APPROVED` 行。目前无任何真实行满足条件，因此正式汇总为 0 且显示 `NO_APPROVED_ROWS`。
- 增加 `Pressure_Test_Cases` 第二轮 19 项压力测试；每条场景都标记 `Fixture_Data_Status=simulated` 和 `Scenario_Only=yes`。被测的假设状态在 `Record_Data_Status_Under_Test` / `Record_Simulated_Flag_Under_Test` 列表示，PT-017 不是真实数据或审批。
- 原始版本保留；新版本独立存放于 `/mnt/data/carbon_delivery_pressurefix/`。

## 当前模拟结果与实际测试

- `Trip_Detail` 主账：12 条合成 `simulated` 记录，覆盖 2026 年 10—12 月。
- 模拟测试总排放：`2.439298 tCO₂e`。这只是核验流程和汇总的测试结果，不代表项目真实排放量。
- 原有 `Test_Cases`：15 项（11 项符合预期计算/控制行为；4 项异常/重复输入按预期阻断）。
- 新增 `Pressure_Test_Cases`：19 项。包括未知单位、零值、负值、缺失因子、单位错配、重复台账 ID、相同 Trip_ID、换新 ID 的相同物理趟次指纹、重复来源行、多运单分摊、正式使用门禁与关键身份字段缺失；公式输出与预期状态逐项匹配。
- `python self_check.py`：**27 项通过，0 项失败**。已连续运行两次，`self_check_results.json` 的 SHA-256 相同。
- 8 个核心 Excel 工作簿已通过 ZIP/XML 完整性、工作表结构、公式存在性及公式错误单元格扫描；**未在 Microsoft Excel 桌面 GUI 人工打开测试**。
- 负责人要求暂不处理的四辆车历史偏差样例未复制、未计算平均值、未纳入主账、汇总或测试矩阵。

## 文件与工作表

| 文件 | 用途 |
|---|---|
| `input_inventory.md` | 输入及缺失资料盘点 |
| `source_register.xlsx` | 来源、版本、适用条件和待核验事项登记 |
| `carbon_accounting_rule_card_v2.xlsx` / `.md` | 碳排放规则草案；尚未冻结 |
| `activity_data_field_dictionary.xlsx` | 碳账与财务账共用字段字典；待财务账负责人共同确认 |
| `activity_data_template.xlsx` | 共用活动数据底稿模板与模拟示例 |
| `field_dictionary_review_note.md` | 需要财务账负责人共同确认的字段及原因 |
| `emission_source_list.xlsx` / `scope_division_note.md` | 排放源清单、范围候选及边界局限 |
| `carbon_ledger_main.xlsx` | 逐趟主账、路段与运单映射、模拟汇总、控制规则、压力测试与正式汇总门禁 |
| `quota_ledger.xlsx` | 配额台账公式示例；数据模拟且未批准 |
| `carbon_ledger_notes.md` / `A4_status.md` | 核算方法、限制和 A4 状态 |
| `evidence_index.xlsx` / `simulated_evidence/` | 12 条模拟记录的证据索引与占位说明，不是真实凭证 |
| `simulated_test_data.csv` | 12 条模拟主账的独立计算输入 |
| `self_check.py` / `self_check_results.json` | Python 标准库自检脚本与最近运行结果 |
| `test_record.md` | 原有三轮和新增第二轮压力测试记录 |
| `self_audit_changes.md` | 两轮自查与修复记录 |
| `stage_status_log.md` | A0–A6 阶段状态块 |
| `delivery_manifest.md` | 文件级交付清单、验证状态和局限 |
| `GIT_COMMIT_GUIDE.md` / `git_ready/` | 本地 Git 提交指引；未推送 GitHub |

## `carbon_ledger_main.xlsx` 内部说明

主要工作表：
- `Trip_Detail`：唯一加总来源；新增 `Physical_Trip_Key`、`Physical_Trip_Control`、`Owner_Approval_Status (manual)` 和 `Formal_Summary_Eligibility`。
- `Monthly_Summary` / `Annual_Summary`：只用于模拟流程核对，明确标注 `SIMULATED_TEST_SUMMARY_NOT_FORMAL`。
- `Formal_Monthly_Summary` / `Formal_Annual_Summary`：只计入 `FORMAL_APPROVED` 行。当前没有任何已批准记录，显示 0 和 `NO_APPROVED_ROWS`。
- `Segment_Detail` / `Waybill_Detail`：只作映射/分摊，不作为额外的碳排放加总来源。
- `Test_Cases`：原有 15 项基础场景测试。
- `Pressure_Test_Cases`：新增 19 项独立压力场景，全部 `Fixture_Data_Status=simulated` 且 `Scenario_Only=yes`，不进入主账。
- `Validation_Rules`：包括 CTRL-11 单位白名单、CTRL-12 物理趟次疑似重复、CTRL-13 正式汇总门禁和 CTRL-14 关键物理身份/证据字段。
- `Pending_4_Vehicle_Data`：只保留按负责人指示暂不处理的状态，不存放偏差值或平均值。

### 物理趟次指纹的限制

指纹是一种**疑似重复候选规则，不是自动判定重复的事实**。由于现有数据缺少完整的起讫点、实际起止时间和标准化设备事件 ID，当前指纹只组合可用字段；完全相同的合法重复班次也可能被标记。为防止重复入账，系统先冻结候选记录并要求人工复核，不自动删除原始记录。接入真实数据后，应加入起讫点、设备事件/运行日志、标准化时间和原始记录 ID 进一步确认。

## 运行自检

需要 Python 3 标准库；无需第三方依赖：

```bash
cd /mnt/data/carbon_delivery_pressurefix
python self_check.py
```

脚本逐项输出 PASS/FAIL，并覆盖原有数据对账、证据链、配额模拟账、Excel 结构/公式扫描，以及 19 项压力测试控制输出与预期结果的比较。

## 尚未满足的正式使用条件

1. 正式国家标准全文及具体条款、公式映射仍待核验；目前来源登记主要指向官方信息页。
2. 柴油/电力排放因子仍是代理待核验值，不得作为正式因子。
3. 真实运单、燃料/电力数据、发票、设备实测、运行日志和财务账底稿尚未提供。
4. 报告主体、车辆运营控制、范围划分、字段映射、去重/分摊规则、配额口径及正式审批状态需相应负责人确认。
5. 当前未在 Microsoft Excel 桌面 GUI 中人工打开测试，且未推送 GitHub。

当前交付适合进行团队评审、补齐正式数据和检查规则。不得将模拟总量或场景模拟中出现的假设审批状态表述为真实结果或真实审批。
