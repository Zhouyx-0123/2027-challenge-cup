# 硬件接入手册（云边端链路）

面向 2027 挑战杯「物流企业碳感知智能决策系统」的**云边端硬件接入**：
ESP32 在车间 / 冷库 / 车队节点采集 CO₂ 与温度，通过内网 HTTP 上报到
`server.py`（端口 5000），再进 `sensor_data.csv` → 决策台「传感器监测」页。

```
传感器(MH-Z19/BME280) ──UART/I2C──▶ ESP32 ──WiFi 内网──▶ server.py :5000
                                                        └─▶ sensor_data.csv ──▶ Streamlit :8501/sensor
```

> ⚠ 本目录下的固件**没有真机验证**（开发机没有 ESP32 串口），只做过人工审读、
> 主机侧链路联调与语法自检。第一次烧板请按「排障表」逐项确认。

---

## 1. BOM 清单

| # | 物料 | 型号 / 规格 | 接口 | 供电 | 购买关键词（淘宝 / 立创商城） | 大致价位 |
|---|------|-------------|------|------|------------------------------|---------|
| 1 | ESP32 主控板 ×1 | ESP32-WROOM-32E（30 脚）或 ESP32-S3-DevKitC-1 | — | 3.3V / USB 5V | 淘宝「ESP32-WROOM-32E 开发板 Type-C」；立创「ESP32-DevKitC V4」 | ¥25–45 |
| 2 | CO₂ 传感器 ×1 | **MH-Z19B**（UART，量程 0–5000 ppm） | UART (9600) | 5V（TX 3.3V TTL） | 淘宝「MH-Z19B 二氧化碳传感器 UART」 | ¥30–60 |
| 2' | CO₂ 传感器（进阶） | **SGP30**（I2C，NDIR+eCO₂，VOC 联动） | I2C | 3.3V / 5V | 淘宝「SGP30 空气质量传感器」 | ¥80–150 |
| 2'' | CO₂ 传感器（高准） | **SCD41**（I2C，光电式，±(40 ppm + 5%)） | I2C | 5V | 淘宝「SCD41 CO2 传感器」 | ¥400–700 |
| 3 | 温度传感器 ×1 | **DS18B20**（1-Wire，−55~125℃） | 1-Wire | 3.3–5V | 淘宝「DS18B20 防水温度探头」 | ¥5–12 |
| 3' | 温度传感器（进阶） | **BME280**（I2C，温湿度气压一体） | I2C | 3.3V | 淘宝「BME280 传感器模块」 | ¥15–30 |
| 4 | 电源 | USB 数据线 Type-C（供电 + 烧录两用） | — | 5V ≥1A | 淘宝「Type-C 数据线 1m」 | ¥5–15 |
| 4' | 电源（可选） | 5V/2A 直流适配器（MH-Z19 峰值电流较大） | — | 5V/2A | 淘宝「5V2A 电源适配器」 | ¥10–20 |
| 5 | 杜邦线 | 公对公 / 公对母 20cm 各 10 根 | — | — | 淘宝「杜邦线 20cm」 | ¥3–10 |
| 6 | 面包板 ×1 | 830 孔免焊面包板 | — | — | 淘宝「面包板 830」 | ¥5–15 |
| 7 | 电阻 ×2 | 4.7kΩ 1/4W（DS18B20 上拉） | — | — | 立创商城「RC0805 4.7K」；淘宝「4.7kΩ 电阻」 | ¥1–3 |

**选型建议**

- **够用就选 MH-Z19B + DS18B20**：便宜、资料多、和固件默认值一致。
- **要更高精度 / 要 VOC 联动**：换 SGP30 或 SCD41（I2C，与 BME280 共用同一对
  SDA/SCL 线，布线最省）。
- **DHT22 不在 BOM 里**：它需要外部 `DHT sensor library`（见 §3.4），本固件虽自带
  手写时序实现，但 ESP32 上 26/70µs 判定余量小、偶发校验失败，**不推荐作为正式方案**。

---

## 2. 接线表

所有 GND 必须共地；ESP32 与 MH-Z19B 之间用 **3.3V TTL 电平**（MH-Z19 的 TX 是
3.3V，可直连 ESP32 的 3.3V GPIO；**不要把 5V 灌进 ESP32 的 GPIO**）。

### 2.1 MH-Z19B（UART，默认选型）

| MH-Z19B 引脚 | → ESP32 | 说明 |
|-------------|---------|------|
| VCC | 5V | 模块供电；ESP32 的 5V/VBUS 引脚 |
| GND | GND | 共地 |
| RX | GPIO17 (TX2) | ESP32 发 → 模块收（查询帧 0xFF 0x86…） |
| TX | GPIO16 (RX2) | 模块发 → ESP32 收（读数帧 0xFF 0x86） |

> 若你的模块 TX 是 5V 电平，必须加电平转换（分压或双向电平模块），否则会烧 GPIO16。

### 2.2 DS18B20（1-Wire，默认选型）

| DS18B20 引脚 | → ESP32 | 说明 |
|-------------|---------|------|
| VCC | 3V3 | 建议 3.3V（5V 也可，但数据线需注意电平） |
| GND | GND | 共地 |
| DQ | GPIO4 | 单总线数据；**必须外接 4.7kΩ 上拉到 3V3** |

> 多只 DS18B20 可以并联在同一根 DQ 上（每只地址不同），固件当前用
> `SKIP ROM` 模式，只适合**单只**。

### 2.3 BME280 / SGP30 / SCD41（I2C）

| 模块引脚 | → ESP32 | 说明 |
|---------|---------|------|
| VCC | 3V3（BME280 / SGP30）/ 5V（SCD41） | 按模块额定电压 |
| GND | GND | 共地 |
| SDA | GPIO21 | I2C 数据 |
| SCL | GPIO22 | I2C 时钟 |
| SDO | GND（→ 地址 0x76）或 VCC（→ 0x77） | 仅 BME280 需要注意 |

### 2.4 DHT22（可选，1 -wire）

| DHT22 引脚 | → ESP32 | 说明 |
|-----------|---------|------|
| VCC | 3V3 / 5V | 模块自带上拉 |
| DATA | GPIO5 | 数据线（需 4.7k–10k 上拉到 VCC） |
| GND | GND | 共地 |

### 2.5 ESP32-S3 的差异

S3 的 GPIO 编号与 WROOM 基本一致，但 **GPIO19/20 是 USB 口的 D−/D+**，别占用；
本手册默认引脚（4/5/16/17/21/22）在 S3 上同样可用。

---

## 3. 烧录步骤

### 3.1 Arduino IDE（推荐给首次上手）

1. 安装 **Arduino IDE 2.x**（官网 <https://www.arduino.cc/en/software>）。
2. 「文件 → 首选项 → 附加开发板管理器网址」填：
   ```
   https://espressif.github.io/arduino-esp32/package_esp32_index.json
   ```
3. 「工具 → 开发板 → 开发板管理器」搜 `esp32`，安装 **esp32 by Espressif Systems**（3.x）。
4. **准备配置**（关键：`config.h` 不入库，只在本机存在）：
   ```powershell
   cd hardware\esp32
   Copy-Item .\config.example.h .\config.h      # Windows PowerShell
   # 然后编辑 config.h：WiFi / SERVER_URL / DEVICE_ID / 令牌
   ```
5. 「文件 → 打开」选 `hardware/esp32/esp32_sensor_client.ino`。
   Arduino IDE 要求 `.ino` 与 `config.h` **在同一目录**——保持 `hardware/esp32/` 结构即可。
6. 「工具 → 开发板」选 **ESP32 Dev Module**（S3 选 ESP32S3 Dev Module）；
   「工具 → 端口」选你的 COM 口；「工具 → Upload Speed」115200 或 921600。
7. 点上传（→）。首次编译约 1–3 分钟。
8. 「工具 → 串口监视器」波特率 **115200**，回车应看到：
   ```
   [ESP32] 物流碳感知 —— 边缘传感器客户端启动
   [ESP32] 令牌来自 NVS（长度 32）
   [ESP32] WiFi 已连接，IP=192.168.1.23
   [ESP32] NTP 已同步：2026-10-27 16:05:11
   ```

### 3.2 库依赖说明（重要）

本固件**不需要任何外部库**，只依赖 ESP32 核心自带的
`WiFi.h / HTTPClient.h / Preferences.h / Wire.h / time.h`：

| 需求 | 本固件的做法 | 备注 |
|------|-------------|------|
| JSON | `snprintf` 手工拼字符串 | 不装 ArduinoJson |
| DS18B20 | 手写 1-Wire 时序（CRC8 已实现） | 不装 OneWire；时序余量不大，读数不稳就换 BME280 |
| DHT22 | 手写单总线时序（校验和已实现） | 可选依赖：官方 **DHT sensor library**（Adafruit）——若你想用它，只需把 `readDht22()` 换成库调用；本固件默认不需要它 |
| BME280 / SGP30 / SCD41 | 手写 I²C 寄存器读写 + 官方补偿公式 | 不装 Adafruit BME280 / SparkFun SGP30 |
| NVS | 核心自带 `Preferences.h` | 不装 anything |

### 3.3 PlatformIO

在**项目根目录**新建 `platformio.ini`（本仓库不预置，避免和流水线冲突）：

```ini
[env:esp32dev]
platform = espressif32
board = esp32dev
framework = arduino
monitor_speed = 115200
src_dir = hardware/esp32
build_flags = -Wall
```

然后：

```powershell
pio run -t upload -t monitor
```

> `src_dir` 指到 `hardware/esp32` 后，`.ino` 与 `config.h` 同目录，`#include "config.h"` 才能解析到。

### 3.4 烧 MicroPython 版（轻量验证）

适合「先跑通链路、暂不写 C++」。

```powershell
# 1) 装固件（COM3 换成你的串口号；ESP32-S3 用 --chip esp32s3）
pip install esptool mpremote
esptool --chip esp32 --port COM3 erase_flash
esptool --chip esp32 --port COM3 write_flash 0x1000 esp32_generic_esp32.bin

# 2) 传脚本 + 令牌
mpremote fs cp hardware\esp32\micropython_sensor_client.py :main.py
mpremote fs cp token.txt :/device_token.txt
mpremote reset
```

`token.txt` 内容就是一行明文令牌（可带或不带换行）。MicroPython **没有 NVS**，
用 `/device_token.txt` 代替；文件不存在时脚本退回源码里的 `DEVICE_TOKEN` 占位符。

---

## 4. 令牌下发三步

服务端是 **fail-closed** 的：没登记的设备 / 错令牌 一律 401，所以令牌必须先在主机登记。

### 第 1 步：主机登记（打印一次明文）

```powershell
# 单台
python -m tools.device_tokens add --device-id PDM-GZ-001 --note "1 号冷库"

# 批量（现场几十台设备一次性配好）
python -m tools.device_tokens batch --prefix PDM-GZ --count 5 --out outputs/device_tokens.csv
```

> `batch` 导出的 CSV **含明文令牌，绝不入库**。默认路径 `outputs/device_tokens.csv`
> 已被 `.gitignore` 的 `*.csv` 规则挡住；用 `--out` 改了路径的话，务必自己确认那个
> 路径也被忽略。分发完立刻删掉这份 CSV。

### 第 2 步：复制明文令牌

`add` / `batch` 会在终端打印一次明文（注册表里**只存 sha256 摘要**）。抄走即可，
丢了就 `rotate`（旧令牌立即失效）。

### 第 3 步：写进设备（三选一）

| 方式 | 操作 | 适用 |
|------|------|------|
| A. 编译期 | 填 `hardware/esp32/config.h` 的 `DEVICE_TOKEN` | 批量烧录前；`config.h` 不入库 |
| B. 运行时（推荐） | 串口 115200 输入 `set <明文令牌>` 回车 | 单台现场换令牌，不用重烧 |
| C. MicroPython | `mpremote fs cp token.txt :/device_token.txt` | MicroPython 版 |

B 方式会把令牌写进 **NVS**（掉电不丢），开机自动读取；
NVS 里没有才回落 `config.h` 的编译期常量。

> 🔒 令牌就是设备的钥匙。`hardware/esp32/config.h` 必须被 `.gitignore` 挡住
> （规则见 §8）。

---

## 5. 联调顺序（先软后硬）

**第 0 步 · 起接收端**（另开一个终端，项目根目录）

```powershell
python server.py
# 看到 [OK] 传感器接口鉴权已启用（X-Device-Token）｜已登记设备 N 个
```

**第 1 步 · 纯软件探针（不需要任何硬件）**

```powershell
python -m tools.sensor_probe --health
python -m tools.sensor_probe --expect-401      # 不带令牌 → 期望 401
python -m tools.sensor_probe --register         # 登记探针设备，打印明文令牌
python -m tools.sensor_probe --token <明文令牌> # 带令牌 → 期望 200
python -m tools.sensor_probe --readback         # 确认读数落进 sensor_data.csv
```

**第 2 步 · 接硬件（先不插传感器）**

烧录 → 看串口日志有没有 `WiFi 已连接` / `NTP 已同步`；
没有硬件时读数会失败并打 `[warn] 传感器读取失败`，属正常。

**第 3 步 · 插传感器，按顺序接：先温度后 CO₂**

每接一个传感器看串口日志：`co2`/`temp` 有没有从 `fail` 变成数值。
DS18B20 记得先接 4.7kΩ 上拉。

**第 4 步 · 看决策台**

打开 <http://localhost:8501/sensor>，曲线应该每 10 秒多一个点。
启动决策台：

```powershell
streamlit run app/app.py --server.port 8501
```

---

## 6. Windows 防火墙（放行 5000 给内网）

ESP32 要访问主机的 5000 端口，Windows 默认会拦。**用管理员权限**的 PowerShell：

```powershell
New-NetFirewallRule -DisplayName "OceanBase 传感器接收 5000" `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5000 `
  -Profile Private -RemoteAddress 192.168.1.0/24
```

- `-RemoteAddress` 请换成你实际的**内网网段**（例如 `10.0.0.0/8`、`192.168.0.0/16`），
  这样只有内网设备能连。
- 网络配置文件要是「公用」，规则不生效：把当前 Wi-Fi 设为专用网络
  `Set-NetConnectionProfile -InterfaceAlias "WLAN" -NetworkCategory Private`。
- 撤销规则：
  ```powershell
  Remove-NetFirewallRule -DisplayName "OceanBase 传感器接收 5000"
  ```

> 🔒 **只在内网用，别开公网。** 传感器接口虽然有令牌鉴权 + 限流 + 审计，
> 但它不适合直接暴露到互联网。要跨网段访问，走 VPN / 反向代理 + TLS + 源 IP 白名单。

---

## 7. 排障表

| 症状 | 可能原因 | 处理 |
|------|---------|------|
| **401** | ①设备没在注册表里 ②令牌不对 ③请求头名字错 ④服务端 `SENSOR_AUTH=0` 被关了 | `python -m tools.device_tokens list` 看设备在不在；重新 `add`/`rotate` 拿明文；固件里头名必须是 `X-Device-Token`；`GET /api/health` 看 `auth.enabled` |
| **429 + Retry-After** | 每设备每分钟超过 `SENSOR_RATE_LIMIT_PER_MIN`（默认 60） | 固件会按 `Retry-After` 自动等待，不用管；主机探针用 `--watch` 复现；确需更高就调大环境变量 |
| **连接超时 / 连不上** | ①防火墙没放行 ②主机 IP 写错（用了 `127.0.0.1`/`localhost`） ③不在同一网段 ④`server.py` 没起 | 主机侧 `python -m tools.sensor_probe --health` 先确认服务活着；`config.h` 的 `SERVER_URL` 必须是**主机的局域网 IP**；见 §6 |
| **CO₂ 恒为 400 ppm** | MH-Z19 **需要预热约 60 秒**（UART 上电自检期间一直报 400）；另外要确认读的是 0xFF 0x86 帧的第 3、4 字节 | 上电后等 2 分钟再读；固件已把 `ppm==0` 或 `>5000` 判为无效并跳过上报；用串口日志看 `co2=` 是否在动 |
| **温度恒为 0 / 读数失败** | ①DS18B20 缺 4.7kΩ 上拉 ②接反了 ③`TEMP_SENSOR_TYPE` 选错 ④DHT22 读时序失败（ESP32 上偶发） | 先量 DQ 空闲电平应 ≈3.3V；核对 §2 接线表；把 `config.h` 的 `TEMP_SENSOR_TYPE` 改成实际型号；DHT22 校验失败建议直接换 DS18B20 / BME280 |
| **时间戳不对 / `ts` 是 0** | 没连上 NTP，或 WiFi 通了但 DNS/UDP 123 不通 | 串口看 `NTP 已同步`；换 `NTP_SERVER`（`ntp.aliyun.com` / `cn.pool.ntp.org`）；**注意 `ts` 只是附加字段，服务端时间戳以服务端为准**，不影响落盘 |
| **串口无任何输出** | 波特率不对 / 插错 USB 口（有些板子插口只供电） | 波特率 115200；换数据口；按住 BOOT 再按 EN 复位进入下载模式 |
| **固件编译报缺少 `config.h`** | 没做第 3 步的配置准备 | `Copy-Item config.example.h config.h` 后再编译 |
| **编译报 `__has_include` 相关错** | Arduino IDE 的 ESP32 core 太老 | 升级到 esp32 core 3.x |

---

## 8. 与 P0 / P1 的关系

| 事项 | 说明 |
|------|------|
| **P0（鉴权）** | 固件每条读数都带 `X-Device-Token`；服务端 fail-closed，401 时固件**停止重试**（只打一条「令牌无效」日志），避免把服务端口打爆。令牌优先存 NVS，代码里只有占位符 |
| **P0（限流）** | 命中 429 时固件读 `Retry-After` 响应头并按它等待，而不是硬重试 |
| **P0（审计）** | 服务端每次调用写 `outputs/sensor_audit.jsonl`（设备号 / 时间 / 结果 / 来源 IP，**不含令牌**），可在决策台追责到具体设备 |
| **P1（时序库 / 幂等）** | 固件每条读数都带 **`ts`（设备侧 epoch 秒）+ `seq`（自增序号）**，服务端当前**忽略**这两个字段（只为将来预留）。有了它，P1 的时序库可以：<br>①按 `device_id + ts` 做时间序列对齐；<br>②按 `device_id + seq` 做**幂等去重**——断网补发的重复数据不会污染趋势 |
| **断网缓存** | 环形缓冲保留最近 N 条（默认 50，NVS 持久化，掉电不丢），恢复网络后**按顺序补发**；补发的数据同样带 `ts`/`seq`，正是 P1 幂等去重的前提 |
| **原始数据不入库** | 读数落在 `sensor_data.csv`（已被 `.gitignore` 的 `*.csv` 挡住）；`hardware/esp32/config.h`（含明文令牌）**必须**加入 `.gitignore` |

### 需要父会话补进 `.gitignore` 的确切规则文本

```gitignore
# ===== 设备端配置：含明文令牌与 WiFi 密码，永不入库 =====
hardware/esp32/config.h
# 批量下发的明文令牌清单（含 device_id + token），永不入库
outputs/device_tokens.csv
```

> `outputs/*.csv` 与 `*.csv` 规则已能挡住默认导出路径，上面两条是**兜底**：
> 防止有人把 `config.h` 复制到别处、或把 `--out` 指到未忽略的目录。
