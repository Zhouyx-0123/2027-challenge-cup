# A6 测试记录（第二轮压力测试修订版）

## 测试范围与边界

- 版本目录：`/mnt/data/carbon_delivery_pressurefix/`
- 主账：`carbon_ledger_main.xlsx`；输入数据：`simulated_test_data.csv`。
- 主账含 12 条合成 `simulated` 记录，覆盖 2026 年 10—12 月。
- 原有场景测试：`Test_Cases` 15 项。
- 第二轮压力测试：`Pressure_Test_Cases` 19 项；全部场景行均 `Scenario_Only=yes` 且 `Fixture_Data_Status=simulated`，仅用于规则验证，不进入主账。`Record_Data_Status_Under_Test` / `Record_Simulated_Flag_Under_Test` 代表假设的被测状态，而非真实输入状态。
- 所有主账因子仍为 `proxy_pending_verification`。没有使用真实运单、实际发票、装置读数或财务账底稿。
- 四辆车历史偏差样例按负责人指示暂不处理：不复制、不计算平均值、不纳入主账或测试。

## 第一轮：多组模拟端到端验证

| 测试项 | 预期 | 实际 | 结果 |
|---|---|---|---|
| 主账行完整性 | 12 行必要字段齐全 | 12 行齐全 | PASS |
| 计算单位 | L 对应 kgCO₂e/L；kWh 对应 kgCO₂e/kWh | 主账模拟行匹配 | PASS |
| 独立计算 | 活动量 × 因子；kg / 1000 = t | 2.439298 tCO₂e | PASS（模拟） |
| 月度汇总 | 明细与月度总量对账一致 | Oct 0.844132；Nov 1.141780；Dec 0.453386 t | PASS（测试汇总） |
| 年度汇总 | 年汇总与明细总量一致 | 2026 年 2.439298 t | PASS（测试汇总） |
| 运单分摊 | 按趟分摊比例和为 1 | TRIP-SIM-010 为 40% / 60%，金额回到趟次 | PASS |
| 证据链 | ID、发票占位号、run_id、台账行号能映射 | 12 条模拟索引对应占位文件 | PASS（占位证据） |
| 配额台账 | 余额/缺口公式对账 | 使用模拟排放量验证 | PASS（模拟） |

## 原有 15 项场景测试

- 11 项正常计算、零值边界或分摊场景符合预期。
- 4 项缺失因子、负活动量、单位错配或重复 ID 场景按预期阻断。
- 这些只是模拟输入和控制逻辑的测试，不代表正式因子通过核验。

## 第二轮：19 项压力测试（新增）

| 测试 ID | 场景 | 预期控制结果 | 实际控制结果 | 结果 |
|---|---|---|---|---|
| PT-001 | 支持单位 L | `PASS_SIMULATED_ONLY` | 相同 | PASS |
| PT-002 | 零活动量 | `PASS_SIMULATED_ONLY`，不能当空值 | 相同 | PASS |
| PT-003 | 未知单位 m³ | `BLOCK_UNSUPPORTED_UNIT` | 相同 | PASS |
| PT-004 | 负燃油量 | `BLOCK_NEGATIVE_ACTIVITY` | 相同 | PASS |
| PT-005 | 缺失排放因子 | `BLOCK_MISSING_REQUIRED` | 相同 | PASS |
| PT-006 | L 活动量配 kWh 因子 | `BLOCK_UNIT_MISMATCH` | 相同 | PASS |
| PT-007 | 已存在的 Ledger_Row_ID 重复导入 | `BLOCK_DUPLICATE_LEDGER_ID` | 相同 | PASS |
| PT-008 | 新台账 ID 但 Trip_ID 已存在 | `BLOCK_POSSIBLE_PHYSICAL_DUPLICATE` | 相同 | PASS |
| PT-009 | 新 ID 但物理趟次候选指纹与主账相同 | `BLOCK_POSSIBLE_PHYSICAL_DUPLICATE` | 相同 | PASS |
| PT-010 | 来源台账行号重复 | `BLOCK_POSSIBLE_PHYSICAL_DUPLICATE` | 相同 | PASS |
| PT-011 / PT-012 | 分摊 40% + 60% | `PASS_ALLOCATION` | 两行通过 | PASS |
| PT-013 / PT-014 | 分摊 40% + 50% | `BLOCK_ALLOCATION_SUM` | 两行阻断 | PASS |
| PT-015 | 模拟记录把审批字段写成 approved | 仍排除正式汇总 | `EXCLUDED_NOT_FORMALLY_APPROVED` | PASS |
| PT-016 | 假设为 actual、因子已核验，但无负责人批准 | 不得进入正式汇总 | `EXCLUDED_NOT_FORMALLY_APPROVED` | PASS |
| PT-017 | 假设所有门禁条件满足且审批字段为 approved | 规则逻辑可产出 `FORMAL_APPROVED` | 与预期一致 | PASS（假设逻辑用例，不是真实审批） |
| PT-018 | actual 状态但因子仍是代理待核验 | 复核/阻断正式资格 | `REVIEW_DATA_STATUS` / 排除正式汇总 | PASS |
| PT-019 | 缺失 Vehicle_ID | `BLOCK_MISSING_REQUIRED` | 相同 | PASS |

### 物理趟次去重说明

当前候选指纹由车辆、日期、活动类型、里程、货物质量、活动量和单位组成，并辅以 `Trip_ID` 和来源台账行号校验。命中时会**冻结并要求复核**，不自动删除或自动认定重复。由于目前缺少真实起讫点、运行时间和设备事件 ID，指纹可能对数据完全相同的合法班次产生误报；接入正式数据后应补充更强身份字段。

## 第三轮：文件、公式、正式汇总门禁和可重复性

- `self_check.py` 最终结果：**27 passed / 0 failed**。
- 自检覆盖输入完整性、12 条模拟主账与 CSV 对账、月/年测试汇总、分摊、证据占位链、四车历史偏差排除、配额模拟账、8 个 Excel 文件包完整性、必需工作表、关键公式、公式错误单元格扫描、因子来源登记和 19 项第二轮压力测试。
- 连续两次完整运行，`self_check_results.json` SHA-256 一致：`5295791ff90654fe5559669397b3d65771eedf4efa41f806d54dd85eb3bb0846`。
- `Formal_Monthly_Summary` 与 `Formal_Annual_Summary` 只统计 `FORMAL_APPROVED` 记录。当前 12 条主账都是 simulated / 代理因子 / 未批准记录，因此正式汇总行为为 0、状态 `NO_APPROVED_ROWS`；这表示**没有合格的正式入账行**，不是实际排放为零。
- `Monthly_Summary` 和 `Annual_Summary` 则明确标记为 `SIMULATED_TEST_SUMMARY_NOT_FORMAL`，仅用于验证流程。
- 8 个核心工作簿通过 ZIP/XML 完整性、工作表结构、关键公式存在性和公式错误单元格检查。未在 Microsoft Excel 桌面 GUI 中人工打开测试。

## 最终判断

本轮证明：支持单位白名单、换新 ID 的物理趟次候选重复、来源行重复、分摊比例、关键身份字段缺失，以及正式汇总审批门禁均已纳入模拟规则测试并符合期望逻辑。正式排放因子、标准条款、真实活动数据、实际证据、财务账字段和负责人审批仍待补，不得声称正式碳账已获批准。
