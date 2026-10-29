# A2 字段字典复核说明

- 状态：候选字段表，尚未与财务账负责人共同确认。
- 规则：碳账与财务账必须共用同一份活动数据底稿；本文件提供字段候选与关联关系，不创建第二套财务账。

## 需要财务账负责人共同确认的字段

1. `reporting_entity`、`vehicle_control_type`：用于确定报告主体和运营控制边界；影响Scope归属。
2. `trip_id`、`segment_id`、`waybill_id`、`duplicate_group_id`：用于防止一条物理行程因多张运单重复计量；需与现有run_id/数据模型一致。
3. `activity_value`、`activity_unit`、`invoice_qty`、`invoice_unit`：确定燃料/电力等活动量与发票数量的可比性。
4. `factor_value`、`factor_unit`、`factor_source_row`、`factor_status`：因子映射与正式核算口径必须对上，未核验因子只许模拟。
5. `emission_kgco2e`、`emission_tco2e`：确认统一单位、四舍五入和汇总精度。
6. `invoice_no`、`invoice_date`、`invoice_amount_cny`、`invoice_unit_price_cny`、`expense_type`、`finance_account_code`、`cost_center`：需要对接财务凭证与科目表；当前科目编码未提供，故未编造。
7. `finance_ledger_row_id`、`allocation_key`、`carbon_price_cny_per_t`、`compliance_cost_cny`：决定两账对账、分摊和碳成本计算，需要财务账规则与授权假设。
8. `run_id`、`source_system`、`source_ledger_row_no`、`evidence_id`：需和原始系统/凭证索引的真实字段一致。
9. `data_status`、`simulated`、`include_in_summary`、`exception_flag`：应有受控值和权限规则，避免模拟值被误入正式汇总。

## 字段规则

- 所有本字段字典内字段的定义/财务字段映射均标记为“待财务账负责人共同确认”。
- 模拟示例都标记 `data_status=simulated` 与 `simulated=yes`。
- 四辆车历史偏差样例未导入模板，不计算、不纳入汇总。
- 现行国家标准正文未取得，字段定义中凡涉及法定条款与特定因子，仍待核验。

## A2 状态块

当前阶段：A2
已完成：字段字典和可复用底稿模板已生成，包含示例行、数据验证下拉及使用说明。
未完成：A/B共同确认字段；填入真实运单、燃料/电力、发票和财务字段。
生成文件：`activity_data_field_dictionary.xlsx`、`activity_data_template.xlsx`、`field_dictionary_review_note.md`。
实际运行命令：Python/artifact_tool生成工作簿；A6将运行文件结构和字段/公式测试。
测试结果：工作簿已导出；Excel兼容性与公式列需在A6验证。
关键依据/待核验：SRC-001/SRC-002正文条款待核验；SRC-003动态模型应用参数待核验。
待负责人或财务确认：字段定义需财务账负责人共同确认；范围边界、因子来源与版本待负责人确认。
待补资料：财务账底稿、真实财务科目表、发票/运单原始行、正式因子表。
下一阶段：A3
