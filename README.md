# 碳感知智算 · 物流企业碳感知智能决策系统

面向物流企业碳核算与运营决策的竞赛演示系统。当前版本以五个页面统一串联发票、传感器、路线优化和碳台账，不再注册旧版多页面入口。

## 五个页面

| 页面 | 核心能力 |
|---|---|
| 首页概览 | 展示总排放、配额缺口、财务风险和实时碳脉动 |
| 发票数据感知 | DeepSeek 多模态识别、缺失油量 50 L 容错、统一碳核算 |
| 云边端硬件监控 | ESP32 → Flask → CSV → Streamlit，2 秒刷新与 CO2 超标预警；监测窗口滑条联动实时窗口预览图；**第二个 tab 是 OBD+GPS 实测碳排**（守恒法，每车碳排） |
| 减碳路线沙盘 | 高德 JS API 2.0 精细地图、四维 NSGA-II、Q-learning 自适应变异与路线记忆；碳价滑条联动碳价敏感度图 |
| 碳核算与台账 | 碳价和配额设置、动态配额缺口、披露附注与 CSV 下载 |

## OBD + GPS 实测碳排（工科测量方向）

项目新增**第三条证据链**：原有两条（发票油耗、VSP 估算）都是间接算碳，
缺一个"测出来"的锚点。新通道测的是**实时油耗**，用燃料守恒法推出
**每台车（每台发动机）的碳排放**。

| 环节 | 做法 |
|---|---|
| 测量 | OBD（转速 / 负荷 / 车速 / 瞬时油耗 / 累计油耗）+ GPS（位置 / 速度 / 航向） |
| 守恒法 | `CO₂(kg) = 油耗(L) × ρ × w_C × (44.01/12.011)`；出账系数取自 `config/calc_factors.json`（唯一真源），守恒推导值作对拍校验（柴油偏差 0.628%） |
| 契约 | `config/obd_gps_contract.json` —— 字段、别名、质控标记、接入方式，设备端与后端共用一份 |
| 接收 | `POST /api/upload_obd_gps` —— 与传感器通道同一套令牌鉴权 / 限流 / 审计，默认 fail-closed |
| 落地 | `src/obd_gps.py`（校验+算碳+分车聚合）、`src/obd_pipeline.py`（原始流→结果表） |
| 前端 | 云边端监控页第二个 tab：累计碳排、工况三联图、碳强度、GPS 轨迹、每车碳排聚合表 |

> **边界**：本通道是独立轨道 —— **不写入 E₀ 主口径、不参与主闸门对拍**，
> 主账口径与 28 条判据保持冻结。设计细节见 `docs/52_OBD_GPS_实测碳排方向.md`。

```bash
python -m src.obd_pipeline --demo      # 生成演示结果表（确定性演示序列）
python -m src.obd_pipeline              # 读 data/raw/obd_gps/ 的真实流
```

## 碳账交付（碳会计线）

`carbon_ledger/` 是碳会计线按 **A0–A6 阶段**组织的交付物（Excel 工作簿 + 阶段说明），
回答「每笔碳排怎么算出来的、凭什么能入账、谁来批、证据在哪」。

| 目录 | 阶段 | 内容 |
|---|---|---|
| `a0_input/` | A0 | 输入清单、来源登记表 |
| `a1_rules/` | A1 | 核算规则卡 v2 |
| `a2_activity_data/` | A2 | 字段字典、活动数据底稿模板 |
| `a3_scope/` | A3 | 排放源清单、Scope 候选划分 |
| `a4_ledger/` | A4 | 碳账主表、配额台账、附注 |
| `a5_evidence/` | A5 | 证据索引 |
| `a6_verification/` | A6 | 测试记录、自审、阶段状态、交付清单 |
| `validation/` | — | 多组数据可行性验证工作簿 |

> **口径边界（务必先读）**：碳账侧用 **WTW 全生命周期**（柴油 3.16 kgCO₂e/L
> = 本项目 `diesel_ttw` 2.63 + `diesel_wtt` 0.53），本项目 E₀ 主账用 **TTW 尾管燃烧**
> （2.63）。两者相差 20% 是**核算边界差异，不是数据错误**，对账前必须显式标注。
> 详见 `docs/53_碳账交付接入与口径对齐.md`。

## 数据与前端边界

数据准备只发生一次：

```text
data/raw 原始脏表
  → src/preprocess.py：清洗 / 关联 / 缺失填补 / Flag / 碳因子 / 双轨碳排
  → data/processed/wide_data.parquet（一张宽表）
  → src/optimizer.py + src/evaluator.py + src/pipeline.py
  → data/processed/*.parquet / *.json（结果表）
  → Streamlit 只读展示
```

- 车型找不到、里程缺失、时间戳异常、发票匹配不上等情况不抛异常，统一使用车型额定值、同线历史均值、行业均速或行业绕行系数填补，并写入 `flag_estimated` 与 `estimate_note`。
- 宽表已包含 `factor_gkm / kg_co2 / kg_co2_phys / delta_pct / factor_version`，算法层不再重复套因子。
- `app/services.py` 只读结果表，不导入 pymoo、OCR、路线或披露算法。
- 用户主动触发的发票识别、传感器和路线场景放在 `app/live_services.py`，属于“实时演示通道”，结果不进入 E₀、KPI 或闸门；重型依赖按调用延迟导入。
- 结果表缺失时，首页和估算审计使用静态快照兜底；估算行在页面上以黄框和原因清单展示。

## 当前目录

```text
app/
  app.py                       # 唯一 Streamlit 主入口，只注册五页
  main_views.py                # 五个业务视图
  demo_state.py                # 跨页面共享状态与统一碳快照
  services.py                  # 只读结果表服务层（无核心算法）
  live_services.py             # 用户触发的实时演示通道（延迟导入重依赖）
  components/
    main_nav.py                # 顶部五页导航（悬浮玻璃药丸条）
    dashboard_home.py          # 暗色叙事首页 + 首页↔四页跳转联动
    route_sandbox.py           # 路线进化可视化
    slider_kit.py              # 高级滑条 + 联动可操作图（自绘，值由原生滑条承载）
    obd_panel.py               # OBD+GPS 实测碳排面板（监控页第二个 tab）
    theme.py                   # 当前全局样式与紧凑页头（令牌唯一真源）
  static/                      # 极光底图 / 碳网络纹理 / 品牌标志
src/
  invoice.py                   # DeepSeek 识别与统一 calc_carbon
  sensor.py                    # 传感器读取、超标判断与预警台账
  disclosure.py                # 智能披露附注
  obd_gps.py                   # OBD+GPS 契约校验、守恒法算碳、分车聚合
  obd_pipeline.py              # OBD 实测碳排流水线（独立轨道，不动主闸门）
  routing/                     # 高德、路线碳核算与四维进化
  ...                          # 数据复算与底层模型
server.py                      # Flask 传感器接收服务 + OBD/GPS 接收端点
config/                        # 因子表与本地密钥配置（含 OBD 契约与守恒物理量）
data/                          # 业务数据、结果表与路线记忆
carbon_ledger/                 # 碳账交付工作区（碳会计 A0–A6 阶段交付物）
tests/                         # 当前功能及底层契约测试
tools/                         # 启动、复算和命令行工具
```

## 启动

Windows 可直接双击 `启动.bat`，或在项目根目录运行：

```bash
pip install -r requirements.txt
python -m streamlit run app/app.py --client.showSidebarNavigation false
```

浏览器访问 `http://localhost:8501`。需要接入真实 ESP32 数据时，另开终端运行：

```bash
python server.py
```

传感器服务提供 `POST /api/upload_sensor`，数据追加到 `sensor_data.csv`。路线采纳记录保存在 `data/route_memory.csv`，两者都是运行时业务数据，不应作为缓存清理。

## 密钥配置

密钥只从环境变量或本地配置文件读取，不要提交到版本库：

| 能力 | 环境变量 | 本地文件 |
|---|---|---|
| DeepSeek 多模态与披露附注 | `DEEPSEEK_API_KEY` | `config/deepseek_key.txt` |
| 高德驾车路径规划 | `AMAP_WEB_SERVICE_KEY` | `config/amap_key.txt` |
| 高德 JS API 精细地图 | `AMAP_JS_API_KEY` | `config/amap_js_key.txt` |
| 高德 JS API 安全密钥 | `AMAP_JS_SECURITY_CODE` | `config/amap_js_security_code.txt` |

未配置 DeepSeek 时，披露附注使用本地模板；发票真实识别需要有效 Key。高德驾车路径规划 2.0 负责获取备选路线、成本与分段 `polyline`；路线沙盘使用独立的“Web 端（JS API）”Key 和安全密钥加载 WebGL 精细地图。JS Key 会进入浏览器，必须在控制台配置 `localhost` 与 `127.0.0.1` 域名白名单；Web 服务 Key 仍只留在后端。

## 数据口径

- 演示基准排放：`4502.04 tCO2e`。
- 发票新增排放：`kgCO2`，进入快照时换算为吨。
- 硬件超标排放：`tCO2`；突发碳成本：元。
- 总排放 = 基准排放 + 发票新增排放 + 硬件超标排放。
- 配额缺口 = `max(总排放 - 当期配额, 0)`。
- 财务风险 = 发票新增排放 × 碳价 + 硬件突发成本。

金额与排放量保持不同单位，不直接混加。虚拟进化路线只用于 What-if 分析，只有用户采纳的路线才写入记忆库。

## 视觉系统

暗色电影感：墨蓝底 + 潮汐极光背景 + 玻璃拟态面板 + 悬浮药丸顶栏。
设计来源与施工细节见 `docs/47_前端重设计v2_暗色电影感.md`（含令牌表、组件规范、
`.stApp` 定位陷阱与验收方法）。

- 令牌唯一真源：`app/components/theme.py` 的 `:root`，前端只读不算。
- 静态资源走 Streamlit 自带服务（`/app/static/…`），**断网可用**，不依赖 CDN。

### 高级滑条与联动图

硬件监控页的「折线图最近 N 条」与路线沙盘页的「碳价」使用自绘滑条
（`app/components/slider_kit.py`）：玻璃轨道 + 渐变填充 + 发丝环旋钮 + 数值气泡，
旁配一张随滑条实时联动的可操作 SVG 图（hover 读数、点击写值）。

值仍由 Streamlit 原生滑条承载（自绘滑条松手时派发真实 pointer 事件写回），
业务口径零改动。施工细节、踩坑与验收见 `docs/48_高级滑条与联动图.md`。

- 传感器无真实数据时使用**确定性演示序列**，页面与图上均显式标注「演示数据」；
  设 `SENSOR_MOCK=0` 可关闭演示。


## 验证

```bash
python -m pytest tests/ -q
python -m tools.verify_p0           # 企业级 P0 一键验收（10 条判据）
```

测试覆盖五页主入口、共享状态、顶部导航、DeepSeek 请求契约、高德响应解析、路线四维进化、探索与采纳、发票碳核算、OBD+GPS 实测碳排通道（契约/守恒/聚合/边界/接收端）以及底层数据契约。

视觉与联动验收（需要 Node 与本机 Edge/Chrome，服务先起在 8501）：

```bash
node tools/verify_pages.js http://localhost:8501 _shots   # 逐页截图 + 空白页/异常探测
node tools/verify_nav.js   http://localhost:8501            # 顶栏与首页卡片跳转联动
node tools/verify_sliders.js http://localhost:8501 _shots   # 滑条：真拖一次 + 值回传校验
node tools/verify_dashboard_layout.js http://localhost:8501 _shots  # 首页方块重叠与间距
node tools/verify_obd_tab.js http://localhost:8501 _shots   # OBD+GPS tab：切页 + 文案 + 图表数
```

需要复算底层结果表时，先把原始附件放入 `data/raw/`，再运行：

```bash
python -m src.pipeline
```

若 `data/raw/` 为空且结果表来源为真实数据，流水线会拒绝用演示样本覆盖；只有明确执行
`python -m src.pipeline --allow-demo-overwrite` 才会重建演示结果。

## 企业级 P0（交付形态 · 数据血缘 · 接入安全）

当前版本已把「能演示」推进到「能交付」，**口径、引擎与闸门判据零改动**，新增的全部是运行时：

| 能力 | 做法 | 入口 |
|---|---|---|
| 配置与密钥分层 | 环境变量 → 挂载密钥目录 → `config/` → 默认值；只报来源不报值 | `src/config.py` |
| run 版本化与发布闸门 | 每次复算存快照 + 写生效指针；**闸门 FAIL 不发布并自动回滚** | `src/run_manifest.py` |
| 传感器接入鉴权 | 设备注册表 + `X-Device-Token`，默认 fail-closed；CORS 收敛、限流、审计 | `src/device_auth.py`、`server.py` |
| 容器化交付 | Dockerfile + compose（app / sensor / caddy 内网 TLS） | `Dockerfile`、`deploy/README.md` |
| CI 扩展 | 新增 `docker-smoke` 作业：构建镜像 → 起服务 → 跑 4 个冒烟脚本 | `.github/workflows/ci.yml` |
| 备份与恢复演练 | 原始证据 + 代码打备份包，恢复演练逐文件校验 sha256 | `tools/backup.py`、`tools/restore_drill.py` |
| 首页信任信号 | 显示生效 run、闸门计数、数据来源、代码版本 | 首页 `.dh-trust` 条 |

常用命令：

```bash
python -m src.pipeline              # 复算 + 闸门 + 发布（失败自动回滚）
python -m src.pipeline --adopt-current   # 不重算：把现有结果认领为生效版本
python -m tools.device_tokens add --device-id PDM-GZ-001   # 登记设备（明文只打印一次）
python -m tools.backup --label nightly                     # 备份
python -m tools.restore_drill backups/<目录>               # 恢复演练
docker compose --env-file deploy/env up -d --build         # 容器化部署
```

> 传感器接口默认要求设备令牌；答辩演示需要临时关闭时设 `SENSOR_AUTH=0`（启动会打醒目告警）。
> 容器与 CI 部分**未在本机真跑**（本机无 Docker），详见 `docs/50` §10。

## 环境与硬件接入

```bash
python -m tools.doctor            # 全链路体检：一次说清"还差什么"
```

**两条部署路线**（这台机器是 Hyper-V 虚拟机，Docker 需要重启一次且取决于宿主是否透传嵌套虚拟化）：

```powershell
powershell -ExecutionPolicy Bypass -File deploy\setup_windows.ps1 -Native   # 原生部署：今天就能用
powershell -ExecutionPolicy Bypass -File deploy\install_docker.ps1         # 提权装 Docker（需重启）
powershell -ExecutionPolicy Bypass -File deploy\setup_windows.ps1 -Finish   # 重启后收尾并跑冒烟
```

**ESP32 接入**（仓库原先没有固件，本轮补齐）：

```bash
python -m tools.device_tokens add --device-id PDM-GZ-001   # 登记设备（明文令牌只打印一次）
python -m tools.sensor_probe --register                    # 主机侧探针：不用硬件先把链路跑通
python -m tools.sensor_probe --token <明文> --readback      # 上报 + 回读落盘
```

固件在 `hardware/esp32/`（Arduino + MicroPython 两版，零外部库，断网缓存 50 条），
物料清单、接线表、烧录与排障见 `hardware/README.md` 与 `docs/51_环境与硬件接入.md`。

## 演进规划

当前形态是**可单机/内网交付的决策台**：P0 已补齐配置分层、run 发布闸门、
设备鉴权、容器化编排、备份恢复与部署体检；运行形态仍是文件驱动的单机服务。
下一阶段重点是业务数据库、异步任务、用户身份与数据权限、可观测告警，
而不是重复建设碳因子或页面。

目标架构、分模块改进清单与 P0–P3 路线图见 `docs/49_企业级演进路线图.md`
（含现状证据索引与六项待决策事项）。该文档为规划稿，不改动任何现有口径与闸门。

历史设计文档保留在 `docs/` 作为过程记录；当前运行方式、页面结构和文件边界以本 README 为准。
