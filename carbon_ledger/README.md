# 碳账交付工作区（carbon_ledger/）

> 本目录是**碳会计线**的交付物，与 `src/` 的工程主链路是**两条并行的线**。
> 归位说明与接入评估见 `docs/53_碳账交付接入与口径对齐.md`。

---

## 这是什么

碳慧智算项目的**碳账（碳会计）部分**交付物，按 A0–A6 阶段组织，
形态是 **Excel 工作簿 + 阶段说明文档**。它回答的是：
「每一笔碳排是怎么算出来的、凭什么能入账、谁来批、证据在哪」。

工程侧（`src/`）回答的是：「碳排总量是多少、优化方案怎么选、路线怎么走」。

两条线在 **`Trip_Detail` 的活动量** 上汇合 —— 见接入评估文档 §4。

---

## 目录地图

| 目录 | 阶段 | 内容 |
|---|---|---|
| `a0_input/` | A0 输入盘点 | `input_inventory.md` 输入清单与缺失项；`source_register.xlsx` 来源登记表（规则按 SRC 行号追溯到这里） |
| `a1_rules/` | A1 规则卡 | `carbon_accounting_rule_card_v2.md` / `.xlsx` 核算规则卡（公式、边界、单位、缺失与重复处理） |
| `a2_activity_data/` | A2 活动数据 | `activity_data_field_dictionary.xlsx` 字段字典；`activity_data_template.xlsx` 活动数据底稿模板；`field_dictionary_review_note.md` 财务映射待确认说明 |
| `a3_scope/` | A3 范围划分 | `emission_source_list.xlsx` 排放源清单；`scope_division_note.md` Scope 1/2 候选与排除项 |
| `a4_ledger/` | A4 主账 | `carbon_ledger_main.xlsx` 碳账主表；`quota_ledger.xlsx` 配额台账；`carbon_ledger_notes.md` 附注；`A4_status.md` 状态；`simulated_test_data.csv` 模拟测试数据 |
| `a5_evidence/` | A5 证据 | `evidence_index.xlsx` 证据索引（Evidence_ID ↔ run_id ↔ 主账行号 ↔ 凭证） |
| `a6_verification/` | A6 自检 | `test_record.md` 测试记录；`self_audit_changes.md` 改动自审；`stage_status_log.md` 阶段状态块；`delivery_manifest.md` 交付清单；`GIT_COMMIT_GUIDE.md` 提交指南；`README_使用说明.md` 使用说明 |
| `validation/` | 多组验证 | `碳账可行性多组数据验证改进版.xlsx` 多组数据可行性验证工作簿 |

---

## 当前状态（**未获正式批准**）

- 主账 12 条记录全部是 `simulated` 合成数据，模拟排放合计 **2.439298 tCO₂e**；
- 正式月/年汇总为 **0 行**，状态 `NO_APPROVED_ROWS`（是「没有合格正式行」，**不是实际排放为零**）；
- 排放因子标记 `proxy_pending_verification`（待核验代理值）；
- 交付方自述：**27 项自检通过 / 0 失败**，19 项压力场景全 PASS。

> ⚠️ **不得**把这里的模拟总量、压力测试假设状态当作正式排放或入账结论。

---

## 三条硬纪律（来自交付方，本仓库照单接收）

1. **模拟数据不得伪装成实测**。`Data_Status=simulated` 的行永远不进正式汇总。
2. **公式不能替负责人批准**。`Owner_Approval_Status` 必须由人填 `approved`。
3. **四辆历史偏差样例不计算、不转录、不入账**，单列「按负责人指示暂不处理」
   （`Pending_4_Vehicle_Data` 工作表里只有状态行，没有真实数据）。

---

## 与工程主链路的边界

| | 碳账（这里） | 工程主链（`src/`） |
|---|---|---|
| 载体 | Excel 工作簿 | Python + parquet 结果表 |
| 口径 | **WTW 全生命周期**（柴油 3.16 kgCO₂e/L） | **TTW 尾管燃烧**（柴油 2.63 kgCO₂/L） |
| 单元 | 物理趟次（trip） | 运单关联路段（segment） |
| 门禁 | 负责人审批 + 正式汇总资格 + 证据索引 | 28 条对拍闸门 + run 发布/回滚 |
| 状态 | 未冻结、未批准 | 已发布（run `20261027-152051-71d711d6`，28 PASS） |

**两套口径不能混算** —— 详见接入评估文档 §3（这是本目录落地时最需要先解决的一件事）。

---

## 怎么用

### 看结构
用 Excel 桌面打开 `a4_ledger/carbon_ledger_main.xlsx`，13 个工作表：

```
Trip_Detail              42 列 × 101 行   ← 唯一排放明细来源
Monthly_Summary / Annual_Summary         模拟测试汇总（标 SIMULATED_TEST_SUMMARY_NOT_FORMAL）
Formal_Monthly_Summary / Formal_Annual_Summary   正式汇总（只算 FORMAL_APPROVED）
Segment_Detail / Waybill_Detail          映射与分摊视图（禁止再与主账相加）
Validation_Rules                        15 条控制规则
Test_Cases / Pressure_Test_Cases        15 项基础 + 19 项压力场景
Feasibility_Summary                     可行性自查汇总
Pending_4_Vehicle_Data                  四车样例「暂不处理」状态
Ledger_Notes                            附注
```

### 录自己的数据
复制 `a2_activity_data/activity_data_template.xlsx` 的 `Activity_Data` 表，
按 `activity_data_field_dictionary.xlsx` 的 51 个字段填写（每字段带中文含义、
单位、是否必填、缺失规则、校验规则、财务账映射）。

### 接工程侧数据
字段映射、单位口径、真实活动量从哪来 → `docs/53_碳账交付接入与口径对齐.md` §4。

### 前端只读出口

本目录的交付物**已接入前端**：「碳核算与台账」页的第二个 tab
「📋 碳会计口径 A0–A6（审计轨）」，**只读**。

前端**不直接读本目录的 Excel**，而是由适配器导出预计算结果表供页面读取：

~~~bash
python -m src.carbon_ledger_export      # → data/processed/carbon_ledger_*
~~~

适配器只搬运工作簿里已算好的值，不重算排放量、不重算汇总、不改本目录任何文件；
页面不提供任何写入或审批控件（审批载体仍然是本目录里的 Excel）。

口径纪律、落地设计与**已知问题登记**（含配额台账 Notes 的陈旧数值、README 行数口径、
凭证文件缺失等 6 条）见 `docs/54_碳数据输出格式与前端落地方案.md` §5 / §6。

### 交付方修订指南（docs/55）

交付物里有 **6 处自相矛盾**（文档与表格打架、口径错位）。其中 **5 处不需要任何真实数据**，
已整理成修订单交给交付方：

| 编号 | 文件 | 改什么 |
|---|---|---|
| 1 | `a4_ledger/quota_ledger.xlsx` | `Quota_Ledger` 行 2 `Notes`：`0.5433t` → `2.439298 t` |
| 2 | `README.md` | `Trip_Detail … 101 行` → `12 条有效记录` |
| 3 | `a5_evidence/evidence_index.xlsx` | 9 行空白状态填 `NOT PROVIDED`；去掉「source file readable」 |
| 4 | `a2_activity_data/activity_data_field_dictionary.xlsx` | 只改第 1 行表头（数据行不动） |
| 5 | `README.md` | `Validation_Rules … 15 条` → `14 条` |
| 6 | `validation/…改进版.xlsx` | 改名带版本警示 + 工作簿内加说明（或负责人确认后删除） |

逐条改法、验收口径与自助检查脚本见 `docs/55_碳账交付修订指南.md`：

~~~bash
python -m tools.verify_carbon_ledger_delivery     # 改完自查，全绿退出码 0
~~~

第 6 条之外的**凭证文件本体、正式排放因子、车辆参数与报告边界**需要真实资料，
不在本轮修订范围（见 `docs/55` §3）。

---

## 注意

- 本目录里的文档内部用**裸文件名**互相引用（如 `source_register.xlsx`）。
  归位到子目录后路径变了，跨目录引用请以本页的目录地图为准。
- `a6_verification/` 里**没有** `self_check.py` —— 交付方清单里列了它，
  但分支里没带上，所以「27 项自检」目前**无法在本仓库复现**。待补清单见
  `docs/53` §5。
- 本目录只放**可复现的交付物**。真实台账数据、正式凭证、含车牌/发票号的明细
  不入库（沿用仓库根 `.gitignore` 的规则）。
