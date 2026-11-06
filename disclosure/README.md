# 碳序 · 碳披露工作台

基于用户提供的「碳披露演示包」制作的独立教学演示轨，不写入工程 E₀ 主账或 A0–A6 碳账。

## 运行

在仓库根目录运行：

```powershell
python -m pip install -r requirements.txt
python -m streamlit run disclosure_app.py
```

亦可使用仓库原入口 `python -m streamlit run app/app.py`，选择顶部「碳披露」。独立入口只需 `streamlit pandas openpyxl PyYAML python-docx Pillow`。

## 功能

- 分析总览：绿框排放、净缺口、缺口情景金额、影子碳成本中枢、逐条排放图和绿黄分层。
- 原始数据：Excel / UTF-8 CSV 上传、Excel 模板下载、会话内编辑、车牌检索、绿黄筛选、输入表下载。
- 情景分析：调整假设历史强度、控排系数、两种价格和黄框带宽；四档情景及独立双轨结果联动。
- 披露底稿：调用原包引擎生成五表 Excel、Word 底稿；Word 读取已保存的 Excel 披露文本。

上传、编辑、参数和下载缓存按 Streamlit 会话隔离；导出任务用独立临时目录和子进程，不覆盖仓库数据。更改输入或参数后导出自动失效。每次重启恢复默认样例。自定义数据须另核实报告期；页面明确标出未核验。

`engine/` 保留原始 main.py、rules.py、scenario.py、charts.py、config.yaml 与政策说明；`fixtures/demo.json` 为原演示输入的可复现教学样例。它不代表真实企业实测数据。未上传桌面包中的个人测试表、生成物和操作截图。

## 计算与验证

绿框：额度 = 里程 × 假设强度 × 控排系数；净缺口 = max(0, 汇总排放 − 汇总额度)。不累加逐条正缺口。未知质量标识保守降黄，冲突标识拒绝；零里程允许；缺失、负数、无穷数拒绝。

影子轨：按原始两层计算并分列；黄框提供假设带宽。两条轨道不可相加。教学情景不构成实际配额核定、购买义务、会计费用或负债，详见 `engine/POLICY_NOTE.md`。

```powershell
python -m unittest discover -s disclosure/tests -v
python -m unittest discover -s disclosure/engine -p test_scenario.py -v
```

默认样例：绿 6 / 黄 4；绿排放 622.22 kg，里程 1320 km，额度 718.74 kg，净缺口与金额为 0。0.45 压力情景缺口 34.16 kg，金额 3.416 元。影子成本中枢 60.3384 元（绿 49.7776 / 黄 10.5608），黄框带宽 7.39256—13.72904 元。
