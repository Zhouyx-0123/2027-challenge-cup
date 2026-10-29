# A4 状态：碳账主表与配额台账（第二轮压力测试修订版）

## 已完成
- `carbon_ledger_main.xlsx`：12 条 simulated 趟次行、唯一加总来源、月/年测试汇总、路段/运单关联。
- 新增单位白名单，未知单位（例如 m³）阻断；单位和因子单位严格匹配。
- 新增 `Physical_Trip_Key` / `Physical_Trip_Control`，检查 Trip_ID、来源台账行号、车辆/日期/活动类型/里程/货重/活动量/单位候选指纹。疑似重复冻结待复核，不自动删记录。
- 新增关键身份/证据字段缺失拦截。
- 新增 `Owner_Approval_Status` 与 `Formal_Summary_Eligibility` 门禁。
- 原有 `Monthly_Summary` / `Annual_Summary` 仅供模拟流程测试，并明确标注 `SIMULATED_TEST_SUMMARY_NOT_FORMAL`。
- 正式汇总独立放在 `Formal_Monthly_Summary` / `Formal_Annual_Summary`，只纳入 `FORMAL_APPROVED`；当前为 0 行，状态 `NO_APPROVED_ROWS`。
- `quota_ledger.xlsx` 为模拟公式测试；数值未批准，不构成真实履约/配额结论。

## 当前模拟结果
- 主账 12 行总排放：2.439298 tCO₂e；仅用于流程测试。
- 月度：2026-10 = 0.844132；2026-11 = 1.141780；2026-12 = 0.453386 tCO₂e。
- 15 项原场景测试与 19 项第二轮压力场景均符合预期状态。

## 未完成
- 正式排放因子和标准条款仍待核验。
- 真实运输数据、燃油/电力记录、发票、装置实测、run_id 运行日志、财务账底稿和配额凭证未提供。
- 物理趟次指纹是候选规则，需用真实起讫点、运行时间、设备事件 ID 和来源记录进一步完善。
- 正式规则与入账结果待负责人/财务账负责人确认。
