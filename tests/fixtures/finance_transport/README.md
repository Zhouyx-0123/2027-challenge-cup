# 运输成本模拟测试数据

本目录的 Excel/CSV/JSON 数据均为**仓库内合成测试数据**，由 `tools/make_finance_sim_fixture.py` 按固定规则确定性生成，不是用户提供的原始凭证，也不是真实发票。原始 45 票真值工作簿未随该分支或全部 git 历史提交；以下工作簿与行程是按 `tests/test_invoice_workbook_transport_integration.py` 测试契约重建的合成替代。

- `invoice_simulated_truth.xlsx`：合成 45 票票级真值（45 行票级、48 行明细），含 39 张柴油票（`unit=L`、项目名称含“柴油”）、6 张维修/服务票（其中 2 张多明细）、8 个车牌。备注列带“模拟样张”标记，`--source-mode real` 会被导入器拒绝。
- `invoice_template.xlsx`：与真值工作簿同表头/样式的空白导入模板（仅表头，无数据行）。
- `simulated_trips.csv`：39 趟合成行程，与 39 张柴油票一一对应；`vehicle_type=牵引车`，`trip_km=100+i`，合计 4,641 km。
- `simulated_team_factors.json`：`vehicle_type=牵引车`、`energy_type=fuel` 的合成理论因子（30 L/100km），来源注明“仅供自动化测试”。
- `FP-REPAIR-003.png`：与多明细维修票 25946248797655020504 对应的仓库既有测试图片。
- `FP-REPAIR-001.png`：与维修票 25245078884927233714 对应的仓库既有测试图片。

工作簿“文件”列还引用 39 个 `FP-DIESEL-*.png` 和 4 个 `FP-REPAIR-*.png`，共 43 个文件名在本目录中不存在；导入器会报告缺图，测试不伪造这些原图。两张既有 PNG 仅用于测试快照与导入链路，同样不代表真实业务凭证。

重新生成（确定性、幂等）：

```bash
python tools/make_finance_sim_fixture.py
```

生成脚本会断言 45 票 / 48 明细 / 39 柴油票 / 39 行程 / 8 车牌。目录内 `.gitignore` 用否定规则将三个生成文件纳入版本控制（根 `.gitignore` 全局忽略 `*.xlsx` 与 `*.csv`）；图片与参考因子文件不由脚本重建。
