# 碳感知智算 · 物流企业碳感知智能决策系统

面向物流企业碳核算与运营决策的竞赛演示系统。当前版本以五个页面统一串联发票、传感器、路线优化和碳台账，不再注册旧版多页面入口。

## 五个页面

| 页面 | 核心能力 |
|---|---|
| 首页概览 | 展示总排放、配额缺口、财务风险和实时碳脉动 |
| 发票数据感知 | DeepSeek 多模态识别、缺失油量 50 L 容错、统一碳核算 |
| 云边端硬件监控 | ESP32 → Flask → CSV → Streamlit，2 秒刷新与 CO2 超标预警 |
| 减碳路线沙盘 | 高德 JS API 2.0 精细地图、四维 NSGA-II、Q-learning 自适应变异与路线记忆 |
| 碳核算与台账 | 碳价和配额设置、动态配额缺口、披露附注与 CSV 下载 |

## 当前目录

```text
app/
  app.py                       # 唯一 Streamlit 主入口，只注册五页
  main_views.py                # 五个业务视图
  demo_state.py                # 跨页面共享状态与统一碳快照
  services.py                  # 页面服务适配层
  components/
    main_nav.py                # 顶部五页导航（悬浮玻璃药丸条）
    dashboard_home.py          # 暗色叙事首页 + 首页↔四页跳转联动
    route_sandbox.py           # 路线进化可视化
    theme.py                   # 当前全局样式与紧凑页头（令牌唯一真源）
  static/                      # 极光底图 / 碳网络纹理 / 品牌标志
src/
  invoice.py                   # DeepSeek 识别与统一 calc_carbon
  sensor.py                    # 传感器读取、超标判断与预警台账
  disclosure.py                # 智能披露附注
  routing/                     # 高德、路线碳核算与四维进化
  ...                          # 数据复算与底层模型
server.py                      # Flask 传感器接收服务
config/                        # 因子表与本地密钥配置
data/                          # 业务数据、结果表与路线记忆
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

## 验证

```bash
pytest tests/ -q
```

测试覆盖五页主入口、共享状态、顶部导航、DeepSeek 请求契约、高德响应解析、路线四维进化、探索与采纳、发票碳核算以及底层数据契约。

视觉与联动验收（需要 Node 与本机 Edge/Chrome，服务先起在 8501）：

```bash
node tools/verify_pages.js http://localhost:8501 _shots   # 逐页截图 + 空白页/异常探测
node tools/verify_nav.js   http://localhost:8501            # 顶栏与首页卡片跳转联动
```

需要复算底层结果表时运行：

```bash
python -m src.pipeline
```

历史设计文档保留在 `docs/` 作为过程记录；当前运行方式、页面结构和文件边界以本 README 为准。
