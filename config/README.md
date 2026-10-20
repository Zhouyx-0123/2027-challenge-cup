# config/ · 场景配置化预留位（docs/25 §5 / §7 第 1 条）

**项目**：物流企业碳感知智能决策系统 ｜ **日期**：2026-10-20 ｜ **主笔**：WorkBuddy（受托于 PM 周嬴鑫）

**这个目录现在只有两类东西**：

1. **高德 key 落位文件**（`amap_*.txt`）——已进 `.gitignore`，永不入库；
2. **未来的场景配置 schema 位**（本文件描述）——**本期只留位置，不写实现**。

> 为什么留：国赛决胜点是**可推广性**（方法与数据解耦），而可推广性不是靠加功能长出来的，
> 是靠"同一套代码换个场景还能跑"长出来的。`config/` 现在就是空的，正是预留位。
> 详见 `docs/25_国赛格局与功能路线图.md` §5 与路线图 S1/S2。

---

## 0. 一句话纪律

> **`src/` 里的硬编码 = 技术债；`config/` 里的字段 = 契约。**
> 本期**不搬**任何现有常量（搬 = 返工风险），但**每个将来要外置的量，现在先在本文档里把名字、
> 类型、谁维护、怎么进闸门写清楚**——国赛期直接照着这张表长出 S1。

---

## 1. 高德 key 落位（已生效，本期唯一实际使用的配置）

| 文件 | 用途 | 调用方 | 白名单 | 入库 |
|---|---|---|---|---|
| `amap_key.txt` | **Web 服务** key：地理编码 + 驾车路径规划（REST） | `tools/route_fetch.py`（后端 Python） | IP 白名单（或不限 IP + 日限额） | ❌ 永不入库 |
| `amap_js_key.txt` | **Web 端（JS API）** key：在线底图 + polyline 渲染 | `app/components/amapview.py`（服务端拼 HTML 时注入） | **域名白名单**（含 `localhost:8501` 与答辩现场地址） | ❌ 永不入库 |
| `amap_js_code.txt` | **安全密钥**（securityJsCode） | 同上，注入 `window._AMapSecurityConfig` | — | ❌ 永不入库 |

**填写规则**：

- 文件内容就是 key 本身，**单行**，可带首尾空白（读取时 `.strip()`）；
- 未配置 / 内容仍是占位符时，代码返回"不可用"——**这是演示模式，不是错误**：
  - 后端 → 全部走 `offline_route()`（haversine × 1.28 绕行系数，与 `docs/20` §2 同口径）；
  - 前端 → `amapview.online_available()` 返回 `False`，页面自动回退 Plotly 离线底图；
- **禁止**把 key 写进任何 `.py` / `.md` / `.json`；仓库里只允许出现占位符文案
  `"请至高德开放平台申请自有 key 并替换此占位符"`。

**两类 key 千万别混用**：Web 服务 key 放前端 = 泄露；JS key 调 REST = 报 INVALID_USER_SIG。

---

## 2. 未来的场景配置位（S1，本期只留名不留码）

国赛期 S1「场景配置化」要把下列四类量从 `src/` 搬到 `config/*.json`。**本期不改代码**，
但名字和口径现在就定死，避免国赛期改一次要动四个文件。

### 2.1 `config/scenario.json` —— 场景身份

| 字段 | 类型 | 现在在哪 | 国赛期语义 |
|---|---|---|---|
| `scenario_id` | str | 隐含（"京采物流"） | 场景标识，进结果表 `scenario_id` 列 |
| `scenario_name` | str | 隐含 | 场景展示名 |
| `data_source` | str | `src/preprocess.py` 硬编码读附件 1/2 | 数据来源标识（`real` / `demo` / 第三方） |
| `period` | str | 宽表 `month` 推导 | 数据期 |
| `city_scope` | list[str] | `preprocess` 内部城市表 | 覆盖城市/网点范围 |

### 2.2 `config/factors/<factor_id>.json` —— 排放因子版本

| 字段 | 类型 | 现在在哪 | 国赛期语义 |
|---|---|---|---|
| `factor_id` | str | `optimizer.FACTOR_BY_CLASS_LOAD` 的版本注记 | 因子版本号（现为 `JCB2023-BASE-v1`） |
| `source` | str | 同上 | 因子来源（标准/文献/企业实测） |
| `effective_date` | str | 无 | 生效日期 |
| `table` | dict | `FACTOR_BY_CLASS_LOAD` 常量 | 62 类 → 17 锚点映射 |
| `er_gs_anchor` | dict | `optimizer.er_at` 内插表 | 载重→ER 内插锚点 |

> **口径铁律不变**：因子是"口径"不是"数据"——因子表永远在引擎层（`src/`）由人审，
> 配置层只负责**选择哪一版**。换因子 = 换版本号 = 走 S4「口径版本与影响分析」，
> 不是在配置文件里手改数值。

### 2.3 `config/cities.json` —— 地理坐标表

| 字段 | 类型 | 现在在哪 | 国赛期语义 |
|---|---|---|---|
| `name` | str | `preprocess` CITY 常量表 | 城市/网点名 |
| `lng` / `lat` | float | 同上 | **GCJ-02** 坐标（城市级近似值须标 `precision: "city_approx"`） |
| `precision` | enum | 无 | `exact`（地理编码补全）/ `city_approx`（城市级近似） |
| `city` | str | 无 | 所属城市（地理编码时的 `city` 参数） |

> 网点级坐标由 `tools/route_fetch.py` 的 `geocode()` 补全并落进 `route_od.parquet`，
> 不回写 `config/`——**配置表只放人工维护的底表，机器补的数据进结果表**。

### 2.4 `config/constraints.json` —— 运营约束与决策参数

| 字段 | 类型 | 现在在哪 | 国赛期语义 |
|---|---|---|---|
| `c1_range` | dict | `docs/20` §7 文本 | 续航衰减系数、可用系数 0.8 |
| `c2_window_h` | number | 同上 | 14 h 驾驶窗口、每 4 h 休息 20 min |
| `c3_capacity_x` | number | 同上 | 运力冗余 1.05 |
| `c4_buffer_km` | number | 同上 | 补能缓冲区 30 km |
| `c5_winter` | dict | 同上 | 气温阈值 −10℃、坡度阈值 4% |
| `lambda_default` | number | `layout.sidebar_params` 默认 | 内部碳价默认值（λ 元/t） |
| `lambda_range` | [num, num] | 同上 | λ 可调范围 |
| `target_reduction_pct` | number | 闸门目标 70 | 达标线 |

> `docs/20` §7 的规则文本**是这四个字段的散文版**。S1 时两者必须一一对应，
> 且 `constraints.json` 的值要进闸门（改了阈值 → 结论变 → 闸门报红），不能只当展示参数。

---

## 3. 谁维护、怎么进闸门

| 配置 | 维护人 | 变更方式 | 闸门关联 |
|---|---|---|---|
| `amap_*.txt` | 杨盛文（FE） | 本机直接改文件，**永不入库** | 无（演示资源） |
| `scenario.json` | 周嬴鑫（PM） | 提 PR，改动需在 `docs/25` 记录 | G1 运行日志记录 `scenario_id` |
| `factors/*.json` | 周嬴鑫 + 曾广（FM） | 新增版本号而非改旧版；旧版只标 `deprecated` | G2 对拍：`factor_id` 与 E₀ 绑定，改版必重算 |
| `cities.json` | 蒿韵丞（DA） | 补网点坐标时更新 | G2 结构校验：坐标必须在国界内且为 GCJ-02 |
| `constraints.json` | 杨盛文（FE） | 改阈值必须同时改 `docs/20` §7 | G2：阈值变更 → 结论重算 → 对拍 |

**通用规则**：

1. 配置**只读**于业务代码——`src/` 读 `config/`，绝不许 `src/` 写 `config/`；
2. 任何配置变更 → **重跑 `python -m src.pipeline`** → 闸门不绿 = 不许合；
3. 配置文件的**字段增删**算口径变更，要在 `docs/08 口径与假设清单` 留痕。

---

## 4. 现在的状态（诚实交代）

| 项 | 状态 |
|---|---|
| 高德 key 落位 | ✅ 三个文件已建、已进 `.gitignore`；**内容待填**（填了就生效，不填走降级） |
| `scenario.json` 等 4 个 schema | ⬜ **只有本文档里的表头，没有 JSON 文件**——本期故意不建，避免"看起来已经做了" |
| `src/` 硬编码迁移 | ⬜ 本期**零改动**。26 天冲刺期不搬常量，S1 是国赛备战期的事 |

---

> **先立契约，再谈效率。** 本文件就是那份契约：国赛期 S1 开工时，不必再讨论"字段叫什么、谁维护、
> 怎么进闸门"，照着这张表建 JSON 即可。
