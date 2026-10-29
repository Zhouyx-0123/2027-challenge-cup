# Git 提交指南（第二轮压力测试修订版；仅本地准备，未推送）

## 建议分支
按团队规范创建独立分支，例如 `feat/carbon-ledger-a0-a6-pressure-fix`。先确认最新 `main`，再创建分支；不要直接提交 `main`。

## 建议提交说明
`test(carbon-ledger): block unknown units and suspected physical-trip duplicates`

## 建议纳入版本控制
- A0/A1：`input_inventory.md`, `source_register.xlsx`, `carbon_accounting_rule_card_v2.xlsx`, `carbon_accounting_rule_card_v2.md`
- A2：`activity_data_field_dictionary.xlsx`, `activity_data_template.xlsx`, `field_dictionary_review_note.md`
- A3：`emission_source_list.xlsx`, `scope_division_note.md`
- A4/A5：`carbon_ledger_main.xlsx`, `quota_ledger.xlsx`, `A4_status.md`, `carbon_ledger_notes.md`, `evidence_index.xlsx`, `simulated_test_data.csv`, `simulated_evidence/*.txt`
- A6：`self_check.py`, `self_check_results.json`, `test_record.md`, `self_audit_changes.md`, `README_使用说明.md`, `stage_status_log.md`, `delivery_manifest.md`, `GIT_COMMIT_GUIDE.md`, `git_ready/`

## 不要上传
- Secrets、API key、token、密码、`.env`、私钥。
- 未脱敏的真实发票、司机个人信息、车牌、客户/承运合同和设备数据，除非已获得授权并完成必要脱敏。
- 未获团队许可的原始输入附件。
- 未核验排放因子被描述为官方因子。
- 四辆历史偏差样例；按负责人指示仍未处理。

## 重要测试边界
`Pressure_Test_Cases` 的 PT-017 只是假设控制逻辑成立时的公式测试，不是真实数据或真实负责人审批记录。主账 12 行仍全部 simulated 且不得正式入账。当前 27 项自检通过，但正式标准/因子/数据和人工审批仍待补齐。
