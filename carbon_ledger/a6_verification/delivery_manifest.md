# 交付清单（第二轮压力测试修订版）

- 输出目录：`/mnt/data/carbon_delivery_pressurefix/`
- 主交付包：`/mnt/data/carbon_ledger_delivery_pressurefix.zip`
- 核心工作簿：`/mnt/data/carbon_delivery_pressurefix/carbon_ledger_main.xlsx`
- 独立压力测试查看文件：`/mnt/data/碳账新版本第二轮压力测试.xlsx`
- 当前状态：A0–A6 工作文件、第二轮 19 项压力测试和补强后的汇总门禁均已生成并通过脚本测试；不是正式批准碳账；未推送 GitHub。

## 主要产物

| 相对路径 | 已生成 | 测试/状态 | 限制 | 阶段 |
|---|---|---|---|---|
| `input_inventory.md` | 是 | 存在 | 标准全文、真实数据缺失已列出 | A0 |
| `source_register.xlsx` | 是 | 工作簿结构/来源映射通过 | 标准正文条款及因子仍待核验 | A0 |
| `carbon_accounting_rule_card_v2.xlsx` / `.md` | 是 | 规则结构检查通过 | 草案，未冻结 | A1 |
| `activity_data_field_dictionary.xlsx` | 是 | 文件检查通过 | 待财务账负责人共同确认 | A2 |
| `activity_data_template.xlsx` | 是 | 公式结构检查通过 | 示例为 simulated | A2 |
| `field_dictionary_review_note.md` | 是 | 文件存在 | 字段/财务映射待共同确认 | A2 |
| `emission_source_list.xlsx` / `scope_division_note.md` | 是 | 文件/表结构检查通过 | 边界待确认 | A3 |
| `carbon_ledger_main.xlsx` | 是 | 27 项自检通过；19 项压力场景通过 | 12 条 simulated；正式汇总当前为零且 `NO_APPROVED_ROWS` | A4/A6 |
| `quota_ledger.xlsx` | 是 | 公式与主账模拟总量对账通过 | 配额数据模拟且未批准 | A4 |
| `A4_status.md` / `carbon_ledger_notes.md` | 是 | 更新中 | 真实凭证/因子待补 | A4/A5 |
| `evidence_index.xlsx` / `simulated_evidence/*.txt` | 是 | 12/12 模拟索引可追溯 | 是占位证据，不是真实凭证 | A5 |
| `simulated_test_data.csv` | 是 | 12 行与主账重算一致 | 合成数据 | A4/A6 |
| `self_check.py` / `self_check_results.json` | 是 | 27 passed / 0 failed | Python 标准库测试，非 Excel 桌面 GUI | A6 |
| `test_record.md` | 是 | 含三轮基础测试及第二轮 19 项压力测试 | 明确列出限制 | A6 |
| `self_audit_changes.md` | 是 | 包含本轮修复记录 | 物理指纹为候选规则 | A6 |
| `README_使用说明.md` | 是 | 更新说明 | 不代表正式批准 | A6 |
| `stage_status_log.md` | 是 | 更新 A0–A6 状态块 | 等待真实输入与负责人审批 | A0–A6 |
| `GIT_COMMIT_GUIDE.md` / `git_ready/` | 是 | 本地指南 | 无 GitHub push | A6 |

## 第二轮主要改进

- 未知单位白名单拒绝；PT-003 通过。
- 换新 ID 的物理趟次候选重复；PT-009 阻断并标记待复核。
- Trip_ID 和来源行号重复；PT-008 / PT-010 阻断。
- 分摊比例 40%+60% 通过；40%+50% 阻断。
- 模拟数据或未核验因子不能进入正式汇总；正式摘要仅计 `FORMAL_APPROVED` 行。
- 缺失物理身份字段 PT-019 阻断。
- 19 项压力测试全部 PASS；总自检 27 passed / 0 failed。

## 保留事项

1. 正式国家标准全文、具体条款与公式映射待核验。
2. 正式排放因子与版本/地区/时期适用条件待核验。
3. 真实运单、能源活动数据、发票、设备实测、run logs、财务底稿待补。
4. 正式规则、Scope、配额和入账结论需负责人/财务账负责人确认。
5. 物理趟次指纹目前为保守的疑似重复候选键；真实数据接入后需增加起讫点/时间/事件 ID。
6. 未用 Microsoft Excel 桌面 GUI 人工打开，未推送 GitHub。

不得将模拟排放总量或压力测试中用于验证审批逻辑的假设状态用作正式排放/入账结论。四车旧偏差样例继续暂不处理。
