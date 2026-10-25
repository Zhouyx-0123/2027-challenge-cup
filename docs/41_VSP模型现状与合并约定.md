# VSP 模型现状与合并约定

> 结论：VSP 已在当前 main 落地，是**技术轨/敏感性分析**，不是主碳账。
> 任何“没做 VSP”的旧分支，不得整体覆盖当前 main。

## 1. 当前结构

```text
app/
  app.py                 st.navigation 单入口
  home_view.py           首页
  services.py            只读 data/processed
  pages/0–8              8 个业务页面
  components/            视觉、图表、地图组件

src/
  preprocess.py          清洗/关联/质量标记
  optimizer.py           因子表、C1–C5、Pareto、物理行程分摊
  carbon_model.py        物理行程去重 + 法定轨 + VSP 技术轨
  evaluator.py           情景评估
  pipeline.py            主账、KPI、闸门

data/processed/
  baseline_carbon.parquet        运单关联层
  our_carbon_model.json          VSP 模型卡
  our_carbon_trips.parquet       逐物理行程 legal/VSP
  vsp_audit.json                 VSP/ HYC 对照
  vsp_physical_trips.parquet     VSP 审计明细
  vsp_vehicle_groups.parquet     车型组汇总
```

VSP 入口：

```bash
python -m src.carbon_model --out data/processed
python tools/build_vsp_audit.py
```

测试：

```bash
pytest -q tests/test_carbon_model.py tests/test_vsp_audit.py
```

## 2. 三条口径不要混

| 口径 | 数值 | 用途 |
|---|---:|---|
| 法定物理主账 | **2,258.44 t** | 主账、台账、披露 |
| 我们的 VSP 技术轨 | **2,324.65 t** | 速度→VSP→ER 敏感性 |
| HYC 代理因子包 | **2,338.93 t** | 外部对照，不是 VSP |
| 运单关联毛值 | **4,502.04 t** | 分摊基数/审计，不是主账 |

关键差异：

- 法定轨：里程 × 六类因子；
- VSP 轨：速度 → VSP → ER(g/s) × 行驶秒数；
- HYC：代理因子映射，**不能叫 VSP**；
- 4,502.04 t：33,214 条运单关联重复计算，不是 12,167 个物理行程。

## 3. VSP 输入的真实限制

当前 VSP 是**代理模型**，不是油耗/发动机实测：

- 12,167 个物理行程；
- 1 条速度缺失；
- 49 条速度 ≤1 km/h，按 50 km/h 处理；
- 原始速度最大约 3,906 km/h（时间戳异常），VSP 计算时裁剪到 130；
- 逐行程 VSP 相对法定轨的偏差：
  - 中位数 **+28.22%**；
  - 5% 分位 **−21.02%**；
  - 95% 分位 **+506.13%**；
- 按车型：
  - class 1：**+79.25%**
  - class 2：**+0.03%**
  - class 3：**−2.46%**

所以：

- 总账 +2.93% 是聚合结果，不代表每个行程都只差 3%；
- 不能拿 VSP 绝对值替代主账；
- 答辩时要说“速度代理/敏感性”，不要说“实测 VSP”。

## 4. 队友没做 VSP，怎么合并

### 不要做

- 不要把旧分支整体覆盖 main；
- 不要删除：
  - `src/carbon_model.py`
  - `tests/test_carbon_model.py`
  - `tests/test_vsp_audit.py`
  - `tools/build_vsp_audit.py`
  - `data/processed/our_carbon_model.json`
  - `data/processed/vsp_audit.json`

### 正确做法

1. 从当前 main 开分支；
2. 只合并他改的页面/数据/组件；
3. 冲突时保留 main 的 VSP 文件；
4. 重新生成模型卡；
5. 跑完整测试；
6. PR 里写明“未改 VSP 口径”。

## 5. 对软著的影响

- VSP 代码是原创代码，可以纳入软著源程序；
- 用户手册必须写“VSP 为速度代理模型，非实测油耗”；
- 不要把 HYC 2,338.93 t 写成“我们的 VSP”；
- 软著保护代码表达，不保护 VSP 公式思想；公式创新另走论文/专利。

## 6. 建议的对外表述

```text
主账采用法定物理轨：2,258.44 t。
VSP 作为技术轨：2,324.65 t，较主账 +2.93%，
用于速度敏感性分析，不作为合规主账。
HYC 2,338.93 t 为外部代理因子对照，不是 VSP。
```

## 7. 现在的行动

- [ ] 确认 main 的 VSP 文件不被队友分支覆盖；
- [ ] 答辩材料统一上述三条口径；
- [ ] 若继续改 VSP，先加异常速度标记，再谈精度；
- [ ] 软著手册按“代理模型”写，不夸大。
