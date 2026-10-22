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

## 1.1 怎么申请：三步拿齐三样东西（2026-10-20 官方核对）

**入口**：https://console.amap.com/ →【应用管理】→【创建新应用】（应用名随意，如 `碳感知决策系统`）
→ 进入应用 →【添加 Key】。**同一个应用下添加两次 key，分别选不同服务平台。**

### 第 1 步：添加「Web 服务」key → 填 `amap_key.txt`

| 项 | 填什么 |
|---|---|
| 服务平台 | **Web 服务** |
| 复制 | 那一列 **Key**（32 位，如 `4a2…c89dc`） |
| IP 白名单 | **留空**（非必填）。留空 = 不限 IP，最省事；本项目 key 只在服务端用、不外泄 |
| 数字签名 | **不开**。开了要额外算 `sig` 签名，我们不需要 |

> **注意**：Web 服务 key 在控制台也会显示一列"安全密钥"，**那个不要填进任何文件**——
> 它是数字签名的私钥（用于 `sig=MD5(参数+私钥)`），只在开了"数字签名"时才用；
> 我们走明文 `key=` 调用，用不上它。**Web 服务 key 不需要安全密钥。**

### 第 2 步：添加「Web 端（JS API）」key → 填 `amap_js_key.txt` + `amap_js_code.txt`

| 项 | 填什么 |
|---|---|
| 服务平台 | **Web 端（JS API）** |
| 复制① | **Key** → `amap_js_key.txt` |
| 复制② | **安全密钥**（同一行的第二列，32 位，如 `8fb2…9f31`）→ `amap_js_code.txt` |
| 域名白名单 | **必填**，加：`localhost`、`127.0.0.1`、`localhost:8501`、`127.0.0.1:8501`；答辩现场再加演示机的 `IP:端口` |
| 勾选 | **勾上"安全密钥"**（不勾就没有 securityJsCode） |

> **安全密钥是 JS API 的强制项**：官方明确"2021-12-02 之后创建的 key 必须配合安全密钥使用"。
> 代码里它必须写在**加载 SDK 的 script 之前**：
> `<script>window._AMapSecurityConfig={securityJsCode:"…"};</script>` → 顺序反了不生效，页面直接白屏。

### 第 3 步：验证

```bash
python -m tools.amap_smoke_test.py     # Streamlit 打开这个页面，5 分钟验完整链
```

看到「上海 → 北京」的真实驾车路线 = key 类型对、安全密钥顺序对、域名白名单对、插件对、渲染对，**五件事一次验完**。

### 常见错误对照

| 现象 | 原因 | 处理 |
|---|---|---|
| `INVALID_USER_KEY` | key 复制错 / 类型选错 / 白名单没加 | 核对服务平台；白名单加 `localhost:8501` |
| `USERKEY_PLAT_NOMATCH` | **服务平台选错**（拿 Web 服务 key 去加载 JS SDK） | 重新添加「Web 端（JS API）」类型的 key |
| 地图全白 / 全灰 | `securityJsCode` 写在 SDK 之后（顺序错） | `_AMapSecurityConfig` 必须在 script 之前 |
| 地图能出但路线不出 | Driving 插件没加载 | 确认 URL 里带 `&plugin=AMap.Driving` |
| `INVALID_USER_IP` | 设了 IP 白名单但本机出口 IP 不在列表 | 清空白名单，或填本机出口 IP |
| 8 秒未加载 | 现场断网 | 走 Plotly 离线回退（业务无损） |

### 配额够不够（官方定价页）

个人认证开发者**免费月配额**：基础 LBS 服务（地理编码 + 驾车路径规划 + 静态地图等）**15 万次/月**；
基础地图定位（JS 地图初始化）**150 万次/月**。本项目 310 条 OD 一次性预取 + 之后读缓存，
**月消耗量 < 0.3%**，配额压力为零。

### Key 数量限制

认证个人开发者可创建 **5 个 Web 服务 key + 20 个其他类型 key**——我们只用 2 个，绰绰有余。

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
| 天地图 token 落位 | ⬜ `tianditu_token.txt` **文件待建**（见 §1.2）；不填时第 6 页模式 ② 只渲染路线矢量、无底图 |
| `scenario.json` 等 4 个 schema | ⬜ **只有本文档里的表头，没有 JSON 文件**——本期故意不建，避免"看起来已经做了" |
| `src/` 硬编码迁移 | ⬜ 本期**零改动**。26 天冲刺期不搬常量，S1 是国赛备战期的事 |

---

## 1.2 天地图 token 落位（OSM 基线底图）

**用途**：第 6 页「碳核算基线·OSM」模式的底图。OpenLayers 通过 WMTS 加载天地图。

| 文件 | 用途 | 调用方 | 入库 |
|---|---|---|---|
| `tianditu_token.txt` | 天地图 WMTS token | `app/components/olmapview.py`（服务端拼 WMTS URL 时注入） | ❌ 永不入库 |

**为什么必须是天地图，不能用高德底图**

| 底图 | 数据基准 | 与 OSM 路线（WGS-84）叠加 |
|---|---|---|
| **天地图 WMTS** | CGCS2000 | ✅ 同基准，差异 < 1 m，无可见偏移 |
| 高德栅格/矢量 | GCJ-02 | ❌ 偏 500–800 m，路线明显错位 |

这不是"偏好问题"，是**硬约束**：OSM 基线的几何是 WGS-84，
换 GCJ-02 底图必然错位。

**怎么申请**

1. 打开 https://console.tianditu.gov.cn/ → 注册/登录 →【控制台】→【应用管理】→【创建新应用】；
2. 应用类型选「Web服务」，填应用名（如 `碳感知决策系统`）；
3. 创建后在应用详情里得到 **Token**（一串十六进制字符）；
4. 把 Token 复制到 `config/tianditu_token.txt`（单行，首尾空白会被 `.strip()`）；
5. 刷新第 6 页 → 模式 ② 出现天地图底图。

**不填会怎样**：**不是错误，是降级**。

- 第 6 页模式 ② 仍然渲染路线矢量（道路等级着色 + 逐 step 列表 + 图例全都在）；
- 只是没有底图参照，图内会显示一条明确提示；
- 其余八页与高德实时模式**完全不受影响**（高德底图用自己的 key）。

> token 只在服务端拼 WMTS URL 时注入，**不出现在任何前端源码、文档或版本库里**。

---

> **先立契约，再谈效率。** 本文件就是那份契约：国赛期 S1 开工时，不必再讨论"字段叫什么、谁维护、
> 怎么进闸门"，照着这张表建 JSON 即可。
