# outputs/ · 运行产物目录

本目录放**运行产物**——不是源代码、不是入库数据：

| 位置 | 内容 | 入库 |
|---|---|---|
| `figures/` | 图表导出（PNG / SVG） | ❌ `.gitignore` 已永久忽略 |
| `tables/` | 表格导出（CSV / XLSX） | ❌ `.gitignore` 已永久忽略 |
| 根目录其他文件 | 演示导出、`gate_report.json` 副本、`run_log.json` 副本等一次性产物 | 默认入库（确认要留痕再放） |

**纪律**

1. 结果表的唯一来源是 `data/processed/`（白名单见 `.gitignore`），`outputs/` 只是**导出层**——
   往 `outputs/` 放东西不等于存了结果，别把它当第二数据目录；
2. 不要把高德 key、原始数据（附件1/附件2）放进本目录；
3. CI 不读本目录；本地产物默认不 commit（`figures/` 与 `tables/` 已永久忽略）；
4. 目录里的 `.gitkeep` 只是占位，让空目录能进版本库。
