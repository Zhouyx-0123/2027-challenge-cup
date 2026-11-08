# 运输成本证据模块 v2

本模块已合入 `main`（`e7e4985`，2026-11-08）；保留主项目原有路线、仪表盘、财务主表等功能。

## 本轮原八项与本次三项补齐

1. 发票补充改为中文表单，可补票号、日期、车牌、金额、数量、单位等。显示缺失项，提交检查同票号冲突并记录修改前值。
2. 实现补能时间与行程交叉核验、车辆近时点位置与补能位置距离核验。缺实际补能时间或完整覆盖范围时为无法检查；异常输出核查依据。判定阈值由配置提供。
3. 页面新增参考配置导入、保存、导出和成本口径选择，CLI 与页面共用参考配置。配置支持容量、理论因子、油价、时间和位置观测。
4. 每条路线输出样本趟数、总里程、覆盖里程、关联覆盖率、周期覆盖率、油耗中位数、元/km中位数、成本有效样本数及周期/票据追溯字段。无分配的路线也列出覆盖率。
5. 每条票每条规则输出 checked、unavailable、anomaly，缺口根据实际执行条件生成。已有因子且有有效分配时执行理论比较，不再统一附加缺因子标记。
6. 同票号主记录保持一条。冲突版本写入 versions，原图和原始内容保留，整票保持待核。读取上一版重复主记录自动归并，后续保存采用新结构。
7. 多明细票主表数量、单位、单价留空并注明详见明细，整票金额保留，明细页保留全部明细。主表维持15列。
8. 页面与CLI保存独立分析批次。页面可查询历史批次，票据内容或参考配置变更后旧批次显示过期，重新分析生成新批次。历史结果保留原输入摘要、配置、创建时间。

9. 团队理论因子表直接接入：支持 `team_theory_factors` / `theory_factors` 记录列表，按车牌（可选）、车型、能源类型、有效期及 conditions 匹配；无匹配和多匹配明确输出 unmatched/ambiguous 及候选因子ID，不会静默挑选。
10. 统计期间和车辆范围：页面与CLI均可传入统一统计起止日期和车辆集合；行程、覆盖率、路线指标统一使用筛选后的行程，发票输入按车辆和期间筛选，并保留期间开始前最近一张燃油/充电类票作为周期起点，维修等非能源票不会覆盖周期锚点。范围写入批次。
11. 历史批次快照与复算：批次保存票据记录、票据原图Base64快照、行程原始行和列结构、参考配置及统计范围；历史批次可在页面一键按原快照复算，不依赖当前输入文件是否被覆盖。

## 操作入口

发票页保留财务主表登记/下载；运输成本证据登记收在同页「运输成本证据（票面原始记录，15 列证据表）」expander 内，先主表、后证据表，不再有两个并列入口。证据表负责票面原始记录、补充、检索、原图查看、证据表下载、参考配置导入、行程CSV/Parquet上传、分析和历史批次复算；财务主表负责人工确认、抵扣判断与跨趟分摊。多明细票保留逐行明细，暂不允许误写入单行财务主表。

运输证据批次生成后，页面会显示「油耗中间层」预览，并可点击「生成本批次油耗中间层」；财务页新增「油耗中间层」标签页读取同一文件。该层是财务归集的中间产物：一行一条趟级分摊，按车牌和月份加权汇总，不自动写财务主表分摊，也不写碳账。

CLI示例：

```bash
python -m tools.transport_evidence_report --invoices outputs/transport_simulated_invoices.json --trips tests/fixtures/finance_transport/simulated_trips.csv --source-mode simulated --output outputs/transport_result.json --references tests/fixtures/finance_transport/simulated_team_factors.json --complete-history --image-dir tests/fixtures/finance_transport
```

CLI可增加 `--period-start YYYY-MM-DD --period-end YYYY-MM-DD --vehicles 京A1,京A2` 控制统计范围。complete-history由调用方确认行程完整性，周期采用docs/45前票油量、相邻日期半开区间和物理里程比例；统计期间开始前的锚点票，分母取完整发票周期的行程，不把整张票硬摊到期间内，若缺期间前行程则标 `missing_pre_period_trips`。时间核验的完整范围另由time_checks指定：补能时刻落在某行程内才记 `time_contradiction` 异常；落在行程间隙为 `checked` 无冲突；无相交行程则 `unavailable` + `no_trip_to_compare`，不伪造 checked。位置检查使用同车、时间差在窗口内的最近观测点，阈值须覆盖定位误差与时间差影响。

参考配置示例位于 examples/finance/transport_references.json，全部为模拟接口样例。替换票据record_id、车牌、阈值、来源和时间后可由页面导入。理论因子优先从团队因子表匹配车型、能源类型、车牌（可选）、有效期和适用条件，并保留因子ID、来源、版本及候选选择依据；旧版车牌映射仍兼容。成本口径为invoice_gross时按票面含税总额分配，否则保持待确认。

实际业务数据验收由PM执行。包内自检针对代码行为与功能入口。

## 参考配置字段

- capacities 为车牌到正数油箱容量的映射，单位 L。
- `team_theory_factors` 为团队理论因子记录列表，每行可含 factor_id、plate（可选）、vehicle_type、energy_type、valid_from、valid_to、conditions、value、version、source；value 单位 L/100km。`theory_factors` 同义。旧 `theory_l_per_100km` 车牌映射继续兼容。只有唯一匹配才执行比较。
- price_checks 按 record_id 设置 expected、relative_threshold、basis_confirmed、source；expected 须与票面税费、油品、日期地区口径一致。
- time_checks 按 record_id 提供 replenished_at、coverage_start、coverage_end、history_complete、source。
- location_checks 按 record_id 提供 lat、lon、replenished_at、source、max_distance_km、max_time_delta_minutes 和 observations 列表。观测项含 plate、lat、lon、observed_at、source。坐标为十进制度数。

时间与区域异常仅输出核查线索。完整性声明、阈值及来源由调用方配置并随批次保存。缺少必要输入时输出 unavailable 和具体原因。


### 团队因子表示例

```json
{
  "team_theory_factors": [
    {
      "factor_id": "FUEL-TRACTOR-001",
      "vehicle_type": "牵引车",
      "energy_type": "fuel",
      "valid_from": "2026-01-01",
      "valid_to": "2026-12-31",
      "value": 28.5,
      "version": "2026-v1",
      "source": "团队理论油耗因子表"
    }
  ]
}
```

页面也可直接上传团队因子 CSV/XLSX 表格。表格列支持英文 `factor_id / vehicle_type / energy_type / value / version / source / valid_from / valid_to / plate / conditions`，以及中文 `因子ID / 车型 / 能源类型 / 百公里理论油耗 / 版本 / 来源 / 生效日期 / 失效日期 / 车牌 / 适用条件`。`适用条件` 如填写，使用 JSON 对象字符串，例如 `{"vehicle_class":"heavy"}`。车型与能源类型来自行程数据；若同一适用条件命中多条因子，系统标记 ambiguous 并列出候选ID，不会自行选择。


## 发票识别工作簿接口

`tools/invoice_workbook.py` 支持导入项目约定的 `票级` / `明细行` 工作簿，也兼容 `发票识别_模拟.xlsx` 的 `票面字段真值` / `明细行真值` 工作表。它将票级字段与多条明细按发票号码归并为运输证据模块可读的 JSON 记录；多明细票的票级数量、单位、单价保持空值，明细逐行保留。

```bash
python -m tools.invoice_workbook --input tests/fixtures/finance_transport/invoice_simulated_truth.xlsx --output outputs/transport_simulated_invoices.json --source-mode simulated --image-dir tests/fixtures/finance_transport
```

注意：该工具导入的是已抽取的结构化字段，不执行图片 OCR，也不会伪造缺失的原始图片。`src/invoice.py` 的图片识别提示词及解析器已扩展为保留 `items` 多行明细、购买方/销售方税号、价税合计大写、备注与开票人；备注中的车牌号可回填到规范车牌字段。

`tests/fixtures/finance_transport/` 内的工作簿、行程与参考因子均为仓库内合成测试数据（由 `tools/make_finance_sim_fixture.py` 生成），仅供自动化测试，不是用户原始凭证或真实发票。原始 45 票真值工作簿未随该分支提交；目录内仅保留两张既有测试图片，其余 43 个文件名故意缺失。


## 本轮模拟数据复现命令

以下行程与理论因子均为自动化测试生成的合成数据，仅用于验证代码链路，不代表真实车辆轨迹或团队因子。可在项目根目录依次执行：

```bash
python -m tools.invoice_workbook --input tests/fixtures/finance_transport/invoice_simulated_truth.xlsx --output outputs/transport_simulated_invoices.json --source-mode simulated --image-dir tests/fixtures/finance_transport

python -m tools.transport_evidence_report --invoices outputs/transport_simulated_invoices.json --trips tests/fixtures/finance_transport/simulated_trips.csv --source-mode simulated --output outputs/transport_simulated_result.json --references tests/fixtures/finance_transport/simulated_team_factors.json --complete-history --period-start 2026-10-31 --period-end 2026-11-04 --image-dir tests/fixtures/finance_transport
```

`tools.transport_evidence_report` 成功时退出码为 0 且无控制台输出，结果写入 `outputs/transport_simulated_result.json`；`tools.invoice_workbook` 会在标准输出打印 `records`、`multi_line`、`missing_source_images` 等摘要。

### 本轮实测输出（2026-11-08）

以下命令在项目根目录依次执行，`source_mode=simulated`、`complete_history=true`；批次 ID 由工具按运行生成（本次复跑为 `e59589e464de4b1c875688d6d5fc6d0a`，每次运行可能不同，不影响计数与覆盖率）：

口径先说清楚：**只有能源票（燃油/充电）进入油耗与碳核算**。维修、保养、轮胎、四轮定位等非能源票保留在凭证库和运输证据表中，但不计入 `invoice_count`、不生成票级检查/缺口、不参与覆盖率分母。用户已拍板“保养不纳入，只算油和碳”。

- 工作簿导入：45 条票据记录（39 张柴油票、6 张维修/服务票、2 张多明细票、8 个车牌），43 个原图文件名缺失。
- 统计期间：2026-10-31 至 2026-11-04。期间内共 44 张票据证据，其中 39 张柴油票进入核算；5 张维修/保养票被排除（`excluded_invoice_count=5`，金额合计 5,932.5 元）。2025-06-01 第 6 张维修票在期间外。
- 匹配：39 条票级匹配记录，全部 `matched`；39 张运单全部 `matched`；维修票不再产生 `unmatched_without_trip`。
- 分配：31 条分配、31 个 closed cycles、8 个车牌、1 条路线（A|B），规则 `docs45-section5`，理论因子 31/31 全部 `matched`。
- 覆盖率：总里程 4,641 km；关联里程 4,641 km（100.00%）；分配里程 3,565 km（76.82%）。
- 分配量：1,069.5 L；分配成本 8,622.25 元（`invoice_gross`）。
- 检查：234 条（101 `checked` / 133 `unavailable`）；缺口 149 条；异常 0 条。


### 油耗中间层（本轮新增）

生成命令：

```bash
python -m src.fuel_layer --run outputs/finance/runs/simulated/<batch_id>.json
```

默认写到 `outputs/finance/fuel_layer_simulated.xlsx` 和同名 JSON；也可在页面「运输成本证据」的分析批次结果下点击「生成本批次油耗中间层」。工作簿包含 7 个 sheet：`00_说明`、`01_趟级油耗`、`02_按车牌汇总`、`03_按月份汇总`、`04_未覆盖行程`、`05_已排除非能源票`、`06_数据缺口`。

- `01_趟级油耗` 32 列，一行一条 allocation；保留 `invoice_record_id / invoice_no / next_invoice_no / cycle_id / share / allocated_liters / allocated_cost / theory_liters / deviation / source_image / input_sha256`，可从金额回到票号和原图。
- `02/03` 只做加权汇总：`L/100km = 100 × Σ油量 / Σ里程`，`元/km = Σ费用 / Σ里程`，不平均单行油耗。
- 本层不写 `carbon_kg`，不改 `baseline_carbon`、`kpi.json` 或 28 项闸门；碳值仍只由主账产生。
- 本层不自动写财务主表「分摊明细」：主表 `allocate` 要求人工确认成本且分摊总额必须等于确认成本，自动写会绕过人工决定。

本轮合成数据实测：39 趟行程，31 条 allocation，8 条未覆盖，5 张非能源票排除，149 条数据缺口；分配里程 3,565 km / 总里程 4,641 km（76.82%），分配油量 1,069.5 L，分配成本 8,622.25 元，加权油耗 30.00 L/100km。2026-10 为 8 趟 / 828 km / 248.4 L / 2,001.16 元；2026-11 为 23 趟 / 2,737 km / 821.1 L / 6,621.09 元。

45 票验收摘要（计数与关键数字，不含原图 Base64）见 `docs/finance/transport_simulated_acceptance_45.json`。

工作簿导入器会报告原图缺失数量。本轮模拟包只有 2 张原图，其余 43 个文件名仅在真值表中存在；缺图不会被伪造。历史批次快照仍会保存已提供的两张原图及完整结构化票据/行程输入。
