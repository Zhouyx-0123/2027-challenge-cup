# 配置说明

本目录保存当前系统的因子、业务参数和本地密钥。密钥文件已加入 `.gitignore`，不得写入代码、文档或提交到版本库。

## 密钥

| 文件 | 用途 | 对应环境变量 |
|---|---|---|
| `amap_key.txt` | 高德 Web 服务：驾车路径规划 2.0 | `AMAP_WEB_SERVICE_KEY` |
| `amap_js_key.txt` | 高德 Web 端（JS API）：WebGL 精细路线地图 | `AMAP_JS_API_KEY` |
| `amap_js_security_code.txt` | JS API 安全密钥，与 JS Key 配套 | `AMAP_JS_SECURITY_CODE` |
| `deepseek_key.txt` | DeepSeek 多模态发票识别与披露附注 | `DEEPSEEK_API_KEY` |

文件内容只填写值本身，保持单行。环境变量优先于本地文件。

高德地图使用两种不同类型的 Key：

- **Web 服务 Key**：只用于后端驾车规划 REST，不能加载前端地图。
- **Web 端（JS API）Key + 安全密钥**：只用于浏览器内的 3D 矢量地图。必须在控制台为该 Key 配置域名白名单，至少包含 `localhost` 和 `127.0.0.1`。JS Key 必然会进入前端页面，因此不要把商业主 Key 用在这里。

高德配置检查和真实接口验证：

```bash
python -m tools.amap_setup --check
python -m tools.amap_setup --check --online
```

## 业务配置

| 文件 | 用途 |
|---|---|
| `calc_factors.json` | 车型、载重和燃料碳核算参数 |
| `invoice_spend_factors.json` | 通用发票金额估算里程的兜底系数 |
| `fuel_price_history.json` | 车辆能效分析使用的历史燃料价格 |
| `telemetry_contract.json` | 颗粒物遥测命令行链路的数据契约 |

当前 CO2 硬件闭环使用项目根目录的 `sensor_data.csv`，路线采纳记忆使用 `data/route_memory.csv`。这两个文件属于运行时业务数据，不是可随意清空的缓存。
