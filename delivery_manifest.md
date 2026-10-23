# VSP 碳排放模型交付清单（原 T1–T6 + E0–E7 兼容扩展）

## 实际路径
- 原交付只读目录：`/mnt/data/vsp_delivery/`（本轮运行环境拒绝写入）
- 扩展工作区与最终输出目录：`/mnt/data/vsp_extension_delivery/`
- 输入副本：`/mnt/data/vsp_input/`
- 最终压缩包：`/mnt/data/vsp_delivery.zip`
- 原始文件未修改；扩展输出在独立工作区生成。

## 基准回归
- 基准物理行程：12,167 趟；2,174,173.86 km；2,338.931589 tCO2。
- 回归测试输出与原 manifest 一致；基准代表因子未被新表替换。
- 新参数只用于独立扩展情景；不称为精度提升。

## 扩展匹配统计
- 现有车型映射记录 75 条；matched 0、proxy 72、unmatched 3。严格匹配率 0%；proxy 仅是弱代理，需人工/业务字段确认。
- 新货车参数库 150 条，乘用车参数库 150 条；乘用车数据不进入货车基准。
- 2026燃料价格/成本只用于2026情景，未回填2023基准。
- 无实测油耗/CO2，无法做独立精度验证。

## 文件清单与状态
- 原 T1–T6 文件：从已验证原包恢复至扩展输出目录；原始基准脚本和工作簿保留。
- `new_data_audit.xlsx`, `new_data_audit.md`: 已生成；字段、年份、单位、缺失与边界审计。
- `truck_parameter_library.xlsx`, `passenger_parameter_library.xlsx`: 已生成；各150款车型；保留原字段和换算列。
- `parameter_matching.xlsx`: 已生成；matched/proxy/unmatched 三态及未匹配清单。
- `regression_test.md`: 已生成；基准回归通过。
- `extended_scenario.xlsx`, `cost_analysis.xlsx`, `factor_comparison.xlsx`: 已生成；情景/成本/因子对照独立标注。
- `improved_model.py`: 基准入口保持默认行为；另提供 `improved_model_extension.py` 和 `extension_pipeline.py`。
- `improved_model.xlsx`: 原8个工作表保留，增加5个扩展工作表。
- `run_all.py`: 已更新为基准与扩展工件检查入口；完整连续端到端运行受当前工具子进程时间限制，未标记为全通过。
- `extension_report.md`, `test_run_extension.md`, `logs/`: 已生成；记录局限和测试限制。
- `git_ready/BRANCH_NOTES.md`, `COMMIT_MESSAGE.txt`, `push_commands.md`: 已生成；未推送 GitHub。

## 未完成/待补资料
- 无法三轮完整端到端连续复跑：当前执行环境触发中断；建议在本地正常 Python 环境运行 `python run_all.py`。
- 现有车型缺少统一公告型号键；严格匹配为0%，72条仅为弱代理，3条未匹配。
- 新表 CO2 核算边界及部分源链接需逐条核实；公告油耗/CO2 缺失值保持缺失。
- 未创建远程 Git 分支或提交；需用户在有权限的本地仓库执行 `git push` 命令。
