/*
 * config.example.h —— ESP32 固件配置模板
 *
 * 用法：
 *   1. 本文件复制成 config.h（同目录）：
 *        Windows PowerShell:  Copy-Item .\config.example.h .\config.h
 *        macOS / Linux    :  cp config.example.h config.h
 *   2. 在 config.h 里填 WiFi、服务端地址、设备号、令牌等。
 *   3. **config.h 不入库**（含明文令牌），.gitignore 里必须有：
 *        hardware/esp32/config.h
 *
 * 纪律：
 *   - DEVICE_TOKEN 只能是占位符或空串；真实令牌优先用串口 `set <token>` 写进 NVS；
 *   - 所有时间用 UTC+8 偏移写死（configTime 的 gmtOffset_sec）。
 */
#pragma once

// ---------------------------------------------------------------- Wi-Fi
#define WIFI_SSID "your-wifi-ssid"
#define WIFI_PASSWORD "your-wifi-password"

// ---------------------------------------------------------------- 服务端
// 主机的局域网 IP（不是 127.0.0.1！ESP32 连不到本机回环地址）
#define SERVER_URL "http://192.168.1.10:5000"
// 上报路径（与 server.py 的路由一致）
#define SERVER_PATH "/api/upload_sensor"
// 令牌请求头（与 server.py / src/device_auth.py 一致）
#define TOKEN_HEADER "X-Device-Token"

// ---------------------------------------------------------------- 设备身份
#define DEVICE_ID "PDM-GZ-001"
// 明文令牌占位符：留空或保持 REPLACE_ME 时，固件只用 NVS 里的令牌
#define DEVICE_TOKEN "REPLACE_ME"

// ---------------------------------------------------------------- 传感器选型
// CO2：MH_Z19_UART / SGP30_I2C / SCD41_I2C
#define CO2_SENSOR_TYPE "MH_Z19_UART"
// 温度：DHT22 / DS18B20 / BME280
#define TEMP_SENSOR_TYPE "DS18B20"

// ---------------------------------------------------------------- 采样与缓存
// 采样间隔（毫秒）—— 默认 10 秒一条；服务端限流默认 60 次/分钟，别调太小
#define SAMPLE_INTERVAL_MS 10000
// 断网环形缓冲条数（存最近 N 条，恢复网络后按顺序补发）
// 序列化后每条 12 字节，NVS 单个 blob 上限约 4000 字节 → 建议 ≤ 200
#define BUFFER_CAPACITY 50
// 串口日志间隔（毫秒）
#define LOG_INTERVAL_MS 30000

// ---------------------------------------------------------------- 时间
// NTP 服务器与时区偏移（秒）。东八区 = 8 * 3600
#define NTP_SERVER "pool.ntp.org"
#define UTC_OFFSET_SECONDS 28800

// ---------------------------------------------------------------- 引脚
// MH-Z19 UART：模块 TX -> ESP32 RX，模块 RX -> ESP32 TX
#define MHZ19_RX_PIN 16   // ESP32 收（接模块 TX）
#define MHZ19_TX_PIN 17   // ESP32 发（接模块 RX）
#define MHZ19_UART_NUM 2
#define MHZ19_BAUD 9600

// DS18B20（1-Wire）
#define DS18B20_PIN 4

// DHT22（单总线时序）
#define DHT22_PIN 5

// BME280 / SGP30 / SCD41 共用 I2C
#define I2C_SDA_PIN 21
#define I2C_SCL_PIN 22
#define I2C_FREQUENCY 100000
#define BME280_I2C_ADDR 0x76
#define SGP30_I2C_ADDR 0x58
#define SCD41_I2C_ADDR 0x62

// ---------------------------------------------------------------- 退避与超时
// 网络失败/5xx 指数退避：初始 1 秒，上限 60 秒
#define BACKOFF_INITIAL_MS 1000
#define BACKOFF_MAX_MS 60000
// HTTP 超时（毫秒）
#define HTTP_TIMEOUT_MS 8000
