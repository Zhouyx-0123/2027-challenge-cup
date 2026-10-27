/*
 * esp32_sensor_client.ino —— ESP32 边缘传感器客户端（Arduino 版）
 *
 * 目标：把 CO2 / 温度读数 POST 到云端接收端 server.py（端口 5000）
 *      接口：POST /api/upload_sensor
 *      请求头：X-Device-Token: <明文令牌>
 *      请求体：{"device_id":"PDM-GZ-001","co2":812.3,"temp":24.1,"ts":...,"seq":...}
 *
 * 设计约束（与项目铁律一致）
 * ------------------------
 *   1. **零外部库**：只用 ESP32 核心自带的 WiFi.h / HTTPClient.h / Preferences.h /
 *      Wire.h / time.h；JSON 用 snprintf 手工拼，传感器协议（DS18B20 1-Wire、
 *      DHT22 时序、SGP30/SCD41/BME280 I2C）全部手写，不装 ArduinoJson、
 *      OneWire、Adafruit BME280、DHT library。
 *   2. **不硬编码真实令牌**：令牌优先从 NVS 读；NVS 没有才回落 config.h 里的
 *      编译期常量（config.h 不入库）。串口 `set <token>` 可运行时写入 NVS。
 *   3. **断网不丢数据**：环形缓冲存最近 N 条（NVS 持久化），恢复网络后按
 *      顺序补发，带 ts + seq（为 P1 时序库与幂等去重预留）。
 *   4. **失败不打爆服务端**：401 停止重试；429 按 Retry-After 等；5xx / 网络
 *      失败指数退避（1s→2s→4s…上限 60s）。
 *
 * 烧录：见 hardware/README.md（config.h 需从 config.example.h 复制）。
 *
 * ⚠ 本文件在本机无真机验证（没有 ESP32 串口），只做过人工审读与逻辑推演。
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <Wire.h>
#include <time.h>

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

// ---------------------------------------------------------------------------
// 0. 配置区（config.h 不入库；由 config.example.h 复制而来）
// ---------------------------------------------------------------------------
#if __has_include("config.h")
#include "config.h"
#else
#error "缺少 config.h —— 请把同目录的 config.example.h 复制成 config.h 并填写 WiFi / 设备号 / 令牌"
#endif

// ---------------------------------------------------------------------------
// 1. 全局状态
// ---------------------------------------------------------------------------

//: 一条待上报的读数
struct Reading {
  uint32_t ts;   // 设备侧时间戳（epoch 秒）
  uint32_t seq;  // 设备侧自增序号
  float co2;     // ppm
  float temp;    // 摄氏度
};

//: 环形缓冲（断网缓存）
static Reading g_queue[BUFFER_CAPACITY];
static uint16_t g_qHead = 0;   // 最老一条的位置
static uint16_t g_qCount = 0;  // 当前条数

static char g_token[128] = {0};      // 明文令牌（只存在设备上）
static bool g_tokenInvalid = false;  // 收到 401 后置位：停止重试
static uint32_t g_seq = 0;           // 自增序号
static uint32_t g_nextAttempt = 0;   // 下次允许发请求的时刻（millis）
static uint32_t g_backoffMs = BACKOFF_INITIAL_MS;
static uint32_t g_lastLog = 0;
static bool g_wifiOk = false;
static bool g_timeOk = false;
static float g_lastCo2 = 0.0f;
static float g_lastTemp = 0.0f;
static bool g_lastSentOk = false;

static Preferences g_nvs;            // NVS 命名空间
static const char *NVS_NS = "sensor";
static const char *NVS_KEY_TOKEN = "token";
static const char *NVS_KEY_BUF = "buf";
static const char *NVS_KEY_SEQ = "seq";

static portMUX_TYPE g_dhtMux = portMUX_INITIALIZER_UNLOCKED;

// ---------------------------------------------------------------------------
// 2. 工具函数：日志 / 时间 / JSON
// ---------------------------------------------------------------------------

// 统一日志出口（串口 115200）
static void logLine(const char *fmt, ...) {
  char buf[256];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  Serial.println(buf);
}

// 取当前 epoch 秒；NTP 没同步时返回 0
static uint32_t nowEpoch() {
  time_t t = time(nullptr);
  if (t < 1700000000) {  // 明显没同步（早于 2023-11）
    return 0;
  }
  return (uint32_t)t;
}

// ---------------------------------------------------------------------------
// 3. 环形缓冲（内存 + NVS 持久化）
// ---------------------------------------------------------------------------

// 每条读数占 16 字节：ts(4) + seq(4) + co2(4) + temp(4)
#define REC_BYTES 16

// 把队列序列化到字节数组（从最老的一条开始）
static void queueSerialize(uint8_t *out) {
  for (uint16_t i = 0; i < g_qCount; i++) {
    uint16_t idx = (uint16_t)((g_qHead + i) % BUFFER_CAPACITY);
    const Reading &r = g_queue[idx];
    uint8_t *p = out + (size_t)i * REC_BYTES;
    memcpy(p, &r.ts, 4);
    memcpy(p + 4, &r.seq, 4);
    memcpy(p + 8, &r.co2, 4);
    memcpy(p + 12, &r.temp, 4);
  }
}

// 落盘到 NVS（掉电不丢缓存）
static void queueSave() {
  if ((size_t)BUFFER_CAPACITY * REC_BYTES > 3900) {
    logLine("[warn] BUFFER_CAPACITY 过大，NVS 存不下；请把容量调到 200 以内");
    return;
  }
  static uint8_t blob[REC_BYTES * 256];
  memset(blob, 0, sizeof(blob));
  queueSerialize(blob);
  g_nvs.putBytes(NVS_KEY_BUF, blob, (size_t)BUFFER_CAPACITY * REC_BYTES);
}

// 从 NVS 恢复缓存
static void queueLoad() {
  static uint8_t blob[REC_BYTES * 256];  // static：别占 loop 任务栈
  size_t len = g_nvs.getBytesLength(NVS_KEY_BUF);
  if (len != (size_t)BUFFER_CAPACITY * REC_BYTES) {
    return;  // 没存过或版本不一致 → 从空队列开始
  }
  g_nvs.getBytes(NVS_KEY_BUF, blob, len);
  uint16_t count = 0;
  for (uint16_t i = 0; i < BUFFER_CAPACITY; i++) {
    uint8_t *p = blob + (size_t)i * REC_BYTES;
    Reading r;
    memcpy(&r.ts, p, 4);
    memcpy(&r.seq, p + 4, 4);
    memcpy(&r.co2, p + 8, 4);
    memcpy(&r.temp, p + 12, 4);
    if (r.ts == 0 && r.seq == 0 && r.co2 == 0.0f && r.temp == 0.0f) {
      continue;  // 空槽
    }
    g_queue[count] = r;
    count++;
  }
  g_qHead = 0;
  g_qCount = count;
  if (count) {
    logLine("[ESP32] 从 NVS 恢复离线缓存 %u 条", (unsigned)count);
  }
}

// 入队（满了丢最老的一条）
static void queuePush(const Reading &r) {
  if (g_qCount >= BUFFER_CAPACITY) {
    g_qHead = (uint16_t)((g_qHead + 1) % BUFFER_CAPACITY);
    g_qCount--;
    logLine("[warn] 离线缓存已满，丢弃最老的一条");
  }
  uint16_t idx = (uint16_t)((g_qHead + g_qCount) % BUFFER_CAPACITY);
  g_queue[idx] = r;
  g_qCount++;
}

// 出队（发送成功后调用）
static void queuePop() {
  if (g_qCount == 0) {
    return;
  }
  g_qHead = (uint16_t)((g_qHead + 1) % BUFFER_CAPACITY);
  g_qCount--;
}

// ---------------------------------------------------------------------------
// 4. NVS：令牌与序号
// ---------------------------------------------------------------------------

static void nvsBegin() {
  g_nvs.begin(NVS_NS, false);
}

static void loadToken() {
  String stored = g_nvs.getString(NVS_KEY_TOKEN, "");
  if (stored.length() > 0) {
    strncpy(g_token, stored.c_str(), sizeof(g_token) - 1);
    g_token[sizeof(g_token) - 1] = '\0';
    logLine("[ESP32] 令牌来自 NVS（长度 %u）", (unsigned)strlen(g_token));
    return;
  }
  // 回落编译期常量
  if (strlen(DEVICE_TOKEN) > 0 && strcmp(DEVICE_TOKEN, "REPLACE_ME") != 0) {
    strncpy(g_token, DEVICE_TOKEN, sizeof(g_token) - 1);
    g_token[sizeof(g_token) - 1] = '\0';
    logLine("[ESP32] 令牌来自 config.h（编译期常量）");
    return;
  }
  g_token[0] = '\0';
  logLine("[warn] 没有令牌：请用串口命令 `set <token>` 写入 NVS");
}

static void saveToken(const char *token) {
  g_nvs.putString(NVS_KEY_TOKEN, token);
  strncpy(g_token, token, sizeof(g_token) - 1);
  g_token[sizeof(g_token) - 1] = '\0';
  g_tokenInvalid = false;
}

// ---------------------------------------------------------------------------
// 5. 传感器读取
// ---------------------------------------------------------------------------

// ---- 5.1 CO2：MH-Z19（UART）----------------------------------------------
// 读帧 0xFF 0x86，ppm 在第 3、4 字节（大端）。
static bool readMhz19(float &out) {
  uint8_t cmd[9] = {0xFF, 0x86, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x79};
  Serial2.write(cmd, sizeof(cmd));

  uint8_t buf[9];
  uint8_t idx = 0;
  uint32_t t0 = millis();
  while (millis() - t0 < 1000 && idx < 9) {
    while (Serial2.available()) {
      uint8_t b = (uint8_t)Serial2.read();
      if (idx == 0 && b != 0xFF) {
        continue;  // 跳过噪声，等帧头
      }
      buf[idx++] = b;
      if (idx >= 9) {
        break;
      }
    }
    delay(1);
  }
  if (idx < 9 || buf[0] != 0xFF || buf[1] != 0x86) {
    return false;
  }
  uint16_t ppm = (uint16_t)((buf[2] << 8) | buf[3]);
  if (ppm == 0 || ppm > 5000) {
    return false;  // 明显异常（0 表示还没预热好）
  }
  out = (float)ppm;
  return true;
}

// ---- 5.2 I2C 公共：SHT CRC8（poly 0x31）-----------------------------------
static uint8_t shtCrc8(const uint8_t *data, size_t len) {
  uint8_t crc = 0xFF;
  for (size_t i = 0; i < len; i++) {
    crc ^= data[i];
    for (uint8_t b = 0; b < 8; b++) {
      crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x31) : (uint8_t)(crc << 1);
    }
  }
  return crc;
}

// ---- 5.3 CO2：SGP30（I2C）-------------------------------------------------
// init_air_quality(0x2003) + measure_air_quality(0x2008)
// 响应 6 字节：CO2(2, 大端) + TVOC(2) + CRC(1) + CRC(1)
static bool sgp30Write(const uint8_t *cmd, size_t len) {
  uint8_t buf[16];
  if (len + 1 > sizeof(buf)) {
    return false;
  }
  memcpy(buf, cmd, len);
  buf[len] = shtCrc8(cmd, len);
  Wire.beginTransmission(SGP30_I2C_ADDR);
  Wire.write(buf, len + 1);
  return Wire.endTransmission() == 0;
}

static bool sgp30Read(uint8_t *out, size_t len) {
  Wire.beginTransmission(SGP30_I2C_ADDR);
  Wire.write((uint8_t)0x00);
  if (Wire.endTransmission() != 0) {
    return false;
  }
  if (Wire.requestFrom((uint8_t)SGP30_I2C_ADDR, (uint8_t)len) != len) {
    return false;
  }
  for (size_t i = 0; i < len; i++) {
    out[i] = (uint8_t)Wire.read();
  }
  return true;
}

static void sgp30Init() {
  uint8_t cmd[2] = {0x20, 0x03};
  sgp30Write(cmd, 2);
  delay(100);
}

static bool readSgp30(float &out) {
  uint8_t cmd[2] = {0x20, 0x08};
  if (!sgp30Write(cmd, 2)) {
    return false;
  }
  delay(1000);  // SGP30 每秒最多测一次
  uint8_t resp[6];
  if (!sgp30Read(resp, 6)) {
    return false;
  }
  if (shtCrc8(resp, 3) != resp[4]) {
    return false;  // CRC 不符 → 读数不可信
  }
  uint16_t ppm = (uint16_t)((resp[0] << 8) | resp[1]);
  if (ppm < 250 || ppm > 65000) {
    return false;
  }
  out = (float)ppm;
  return true;
}

// ---- 5.4 CO2：SCD41（I2C）-------------------------------------------------
// start_periodic_measurement(0xE156, 参数 0x0000) + read_measurement(0xE05B)
// 响应 9 字节：CO2(2) + 温度(2) + 湿度(2) + CRC(3)
static bool scd41Write(const uint8_t *cmd, size_t len) {
  uint8_t buf[24];
  if (len + 1 > sizeof(buf)) {
    return false;
  }
  memcpy(buf, cmd, len);
  buf[len] = shtCrc8(cmd, len);
  Wire.beginTransmission(SCD41_I2C_ADDR);
  Wire.write(buf, len + 1);
  return Wire.endTransmission() == 0;
}

static bool scd41Read(uint8_t *out, size_t len) {
  Wire.beginTransmission(SCD41_I2C_ADDR);
  Wire.write((uint8_t)0x00);
  if (Wire.endTransmission() != 0) {
    return false;
  }
  if (Wire.requestFrom((uint8_t)SCD41_I2C_ADDR, (uint8_t)len) != len) {
    return false;
  }
  for (size_t i = 0; i < len; i++) {
    out[i] = (uint8_t)Wire.read();
  }
  return true;
}

static bool readScd41(float &out) {
  uint8_t cmd[4] = {0xE1, 0x56, 0x00, 0x00};
  if (!scd41Write(cmd, 4)) {
    return false;
  }
  delay(5000);  // 周期测量模式：等一次测量完成
  uint8_t readCmd[2] = {0xE0, 0x5B};
  if (!scd41Write(readCmd, 2)) {
    return false;
  }
  // 响应 = 9 字节数据 + 3 字节校验（CRC0 覆盖 byte0-1，CRC1 覆盖 byte3-5，CRC2 覆盖 byte6-8）
  uint8_t resp[12];
  if (!scd41Read(resp, 12)) {
    return false;
  }
  if (shtCrc8(resp, 2) != resp[9]) {
    return false;
  }
  uint16_t ppm = (uint16_t)((resp[0] << 8) | resp[1]);
  if (ppm < 250 || ppm > 65000) {
    return false;
  }
  out = (float)ppm;
  return true;
}

// ---- 5.5 温度：DS18B20（1-Wire，手写时序）---------------------------------
// 需要外部 4.7kΩ 上拉电阻（模块上一般已自带）。
// ⚠ ESP32 上手写 1-Wire 时序余量不大；若读数不稳，优先换 BME280。
static uint8_t owCrc8(const uint8_t *data, uint8_t len) {
  uint8_t crc = 0;
  for (uint8_t i = 0; i < len; i++) {
    uint8_t inByte = data[i];
    for (uint8_t j = 0; j < 8; j++) {
      uint8_t mix = (uint8_t)((crc ^ inByte) & 0x01);
      crc >>= 1;
      if (mix) {
        crc ^= 0x8C;
      }
      inByte >>= 1;
    }
  }
  return crc;
}

static void owPullLow() {
  pinMode(DS18B20_PIN, OUTPUT);
  digitalWrite(DS18B20_PIN, LOW);
}

static void owRelease() {
  pinMode(DS18B20_PIN, INPUT);  // 交给外部 4.7k 上拉
}

static bool owReset() {
  owPullLow();
  delayMicroseconds(500);
  owRelease();
  delayMicroseconds(70);
  bool present = (digitalRead(DS18B20_PIN) == LOW);
  delayMicroseconds(400);
  return present;
}

static void owWriteBit(bool one) {
  if (one) {
    owPullLow();
    delayMicroseconds(6);
    owRelease();
    delayMicroseconds(64);
  } else {
    owPullLow();
    delayMicroseconds(60);
    owRelease();
    delayMicroseconds(2);
  }
}

static bool owReadBit() {
  owPullLow();
  delayMicroseconds(6);
  owRelease();
  delayMicroseconds(9);
  bool bit = (digitalRead(DS18B20_PIN) == HIGH);
  delayMicroseconds(55);
  return bit;
}

static void owWriteByte(uint8_t value) {
  for (uint8_t i = 0; i < 8; i++) {
    owWriteBit((value & 0x01) != 0);
    value >>= 1;
  }
}

static uint8_t owReadByte() {
  uint8_t value = 0;
  for (uint8_t i = 0; i < 8; i++) {
    if (owReadBit()) {
      value |= (uint8_t)(1 << i);
    }
  }
  return value;
}

static bool readDs18b20(float &out) {
  if (!owReset()) {
    return false;  // 没检测到器件 → 接线/上拉/供电问题
  }
  owWriteByte(0xCC);  // SKIP ROM（单器件）
  owWriteByte(0x44);  // CONVERT T
  delay(800);         // 12 位分辨率转换时间约 750ms
  if (!owReset()) {
    return false;
  }
  owWriteByte(0xCC);
  owWriteByte(0xBE);  // READ SCRATCHPAD
  uint8_t sp[9];
  for (uint8_t i = 0; i < 9; i++) {
    sp[i] = owReadByte();
  }
  if (owCrc8(sp, 8) != sp[8]) {
    return false;  // CRC 错 → 时序或干扰
  }
  int16_t raw = (int16_t)((uint16_t)sp[0] | ((uint16_t)sp[1] << 8));
  out = (float)raw / 16.0f;
  return (out > -55.0f && out < 125.0f);
}

// ---- 5.6 温度：DHT22（单总线时序，手写）-----------------------------------
// ⚠ ESP32 上 26/70µs 判定余量偏小，可能偶发校验失败；
//   需要更稳的话换 DS18B20 / BME280（官方 DHT sensor library 是可选的外部依赖）。
static bool readDht22(float &out) {
  uint8_t data[5] = {0, 0, 0, 0, 0};
  // 起始信号：拉低 ≥18ms，再**释放总线**（切回输入，靠上拉电阻拉高）
  // ⚠ 读取阶段必须释放，否则 ESP32 推高、DHT22 拉低会直接对打（烧 IO）。
  pinMode(DHT22_PIN, OUTPUT);
  digitalWrite(DHT22_PIN, HIGH);
  delay(10);
  digitalWrite(DHT22_PIN, LOW);
  delay(20);
  pinMode(DHT22_PIN, INPUT_PULLUP);

  portENTER_CRITICAL(&g_dhtMux);
  // 等 DHT22 应答：80µs 低 + 80µs 高
  int waited = 0;
  while (digitalRead(DHT22_PIN) != LOW) {
    delayMicroseconds(1);
    if (++waited > 150) {
      portEXIT_CRITICAL(&g_dhtMux);
      return false;
    }
  }
  waited = 0;
  while (digitalRead(DHT22_PIN) == LOW) {  // 80µs 低电平
    delayMicroseconds(1);
    if (++waited > 150) {
      portEXIT_CRITICAL(&g_dhtMux);
      return false;
    }
  }
  waited = 0;
  while (digitalRead(DHT22_PIN) == HIGH) {  // 80µs 高电平
    delayMicroseconds(1);
    if (++waited > 150) {
      portEXIT_CRITICAL(&g_dhtMux);
      return false;
    }
  }
  // 40 位数据：每位前 50µs 低，随后 26µs(0) 或 70µs(1) 高
  for (uint8_t i = 0; i < 40; i++) {
    waited = 0;
    while (digitalRead(DHT22_PIN) != LOW) {  // 等 50µs 低电平
      delayMicroseconds(1);
      if (++waited > 150) {
        portEXIT_CRITICAL(&g_dhtMux);
        return false;
      }
    }
    waited = 0;
    while (digitalRead(DHT22_PIN) == LOW) {  // 等低电平结束
      delayMicroseconds(1);
      if (++waited > 150) {
        portEXIT_CRITICAL(&g_dhtMux);
        return false;
      }
    }
    int high = 0;
    while (digitalRead(DHT22_PIN) == HIGH) {  // 量高电平宽度
      delayMicroseconds(1);
      if (++high > 150) {
        break;
      }
    }
    if (high > 40) {
      data[i / 8] |= (uint8_t)(1 << (7 - (i % 8)));
    }
  }
  portEXIT_CRITICAL(&g_dhtMux);

  uint8_t sum = (uint8_t)(data[0] + data[1] + data[2] + data[3]);
  if (sum != data[4]) {
    return false;  // 校验失败
  }
  float t = data[2] + (data[3] / 10.0f);
  if (data[1] & 0x80) {
    t = -((256 - data[2]) + (data[3] / 10.0f));
  }
  out = t;
  return (out > -50.0f && out < 100.0f);
}

// ---- 5.7 温度：BME280（I2C，手写寄存器读 + 官方补偿公式）-----------------
struct Bme280Cal {
  uint16_t digT1;
  int16_t digT2, digT3;
};

static Bme280Cal g_bme280;

static bool bme280Init() {
  // 读标定数据 0x88..0xA1
  Wire.beginTransmission(BME280_I2C_ADDR);
  Wire.write((uint8_t)0x88);
  if (Wire.endTransmission() != 0) {
    return false;
  }
  if (Wire.requestFrom((uint8_t)BME280_I2C_ADDR, (uint8_t)24) != 24) {
    return false;
  }
  uint8_t c[24];
  for (uint8_t i = 0; i < 24; i++) {
    c[i] = (uint8_t)Wire.read();
  }
  g_bme280.digT1 = (uint16_t)((c[1] << 8) | c[0]);
  g_bme280.digT2 = (int16_t)((c[3] << 8) | c[2]);
  g_bme280.digT3 = (int16_t)((c[5] << 8) | c[4]);
  // ctrl_meas：温度过采样 x1、压力 x1、正常模式
  Wire.beginTransmission(BME280_I2C_ADDR);
  Wire.write((uint8_t)0xF4);
  Wire.write((uint8_t)0x27);
  if (Wire.endTransmission() != 0) {
    return false;
  }
  return true;
}

static bool readBme280(float &out) {
  // 读原始温度 0xFA..0xFC
  Wire.beginTransmission(BME280_I2C_ADDR);
  Wire.write((uint8_t)0xFA);
  if (Wire.endTransmission() != 0) {
    return false;
  }
  if (Wire.requestFrom((uint8_t)BME280_I2C_ADDR, (uint8_t)3) != 3) {
    return false;
  }
  // ⚠ 必须分三次取字节：C++ 里同一个表达式里的求值顺序不保证
  uint8_t msb = (uint8_t)Wire.read();
  uint8_t lsb = (uint8_t)Wire.read();
  uint8_t lsb2 = (uint8_t)Wire.read();
  int32_t adcT = ((int32_t)msb << 12) | ((int32_t)lsb << 4) | ((int32_t)lsb2 >> 4);
  if (adcT == 0x80000) {
    return false;  // 跳过未就绪
  }
  int32_t var1 = ((((adcT >> 3) - ((int32_t)g_bme280.digT1 << 1))) *
                  ((int32_t)g_bme280.digT2)) >> 11;
  int32_t var2 = (((((adcT >> 4) - ((int32_t)g_bme280.digT1)) *
                    ((adcT >> 4) - ((int32_t)g_bme280.digT1))) >> 12) *
                   ((int32_t)g_bme280.digT3)) >> 14;
  int32_t tFine = var1 + var2;
  float tempC = ((float)(tFine * 5 + 128)) / 25600.0f;  // datasheet：先算 0.01℃ 再除 100
  out = tempC;
  return (out > -50.0f && out < 100.0f);
}

// ---- 5.8 传感器调度 -------------------------------------------------------
static bool readCo2(float &out) {
  const char *type = CO2_SENSOR_TYPE;
  if (strcmp(type, "MH_Z19_UART") == 0) {
    return readMhz19(out);
  }
  if (strcmp(type, "SGP30_I2C") == 0) {
    return readSgp30(out);
  }
  if (strcmp(type, "SCD41_I2C") == 0) {
    return readScd41(out);
  }
  logLine("[warn] 未知的 CO2_SENSOR_TYPE：%s", type);
  return false;
}

static bool readTemp(float &out) {
  const char *type = TEMP_SENSOR_TYPE;
  if (strcmp(type, "DS18B20") == 0) {
    return readDs18b20(out);
  }
  if (strcmp(type, "BME280") == 0) {
    return readBme280(out);
  }
  if (strcmp(type, "DHT22") == 0) {
    return readDht22(out);
  }
  logLine("[warn] 未知的 TEMP_SENSOR_TYPE：%s", type);
  return false;
}

// ---------------------------------------------------------------------------
// 6. 网络
// ---------------------------------------------------------------------------

static bool wifiConnect() {
  if (WiFi.status() == WL_CONNECTED) {
    g_wifiOk = true;
    return true;
  }
  logLine("[ESP32] 连接 WiFi：%s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 30000) {
    delay(500);
    Serial.print('.');
  }
  Serial.println();
  g_wifiOk = (WiFi.status() == WL_CONNECTED);
  if (g_wifiOk) {
    logLine("[ESP32] WiFi 已连接，IP=%s", WiFi.localIP().toString().c_str());
  } else {
    logLine("[warn] WiFi 连接失败（30s 超时），进入离线缓存模式");
  }
  return g_wifiOk;
}

// 同步 NTP 时间（同步前 ts 记 0，服务端会用自己的时间）
static void syncTime() {
  configTime(UTC_OFFSET_SECONDS, 0, NTP_SERVER);
  struct tm info;
  if (getLocalTime(&info, 10000)) {
    g_timeOk = true;
    logLine("[ESP32] NTP 已同步：%04d-%02d-%02d %02d:%02d:%02d",
            info.tm_year + 1900, info.tm_mon + 1, info.tm_mday,
            info.tm_hour, info.tm_min, info.tm_sec);
  } else {
    g_timeOk = false;
    logLine("[warn] NTP 同步失败，ts 暂记 0");
  }
}

// 上报结果分类
enum PostResult {
  POST_OK = 0,      // 200
  POST_AUTH,        // 401/403 → 令牌无效，停止重试
  POST_RATE,        // 429 → 按 Retry-After 等待
  POST_SERVER,      // 5xx → 退避重试
  POST_BADREQ,      // 其它 4xx → 这条数据有问题，丢弃以免堵住队列
  POST_NET          // 网络不可达 → 退避重试
};

static uint32_t g_retryAfterSec = 5;

static PostResult postReading(const Reading &r) {
  char body[192];
  snprintf(body, sizeof(body),
           "{\"device_id\":\"%s\",\"co2\":%.1f,\"temp\":%.1f,\"ts\":%lu,\"seq\":%lu}",
           DEVICE_ID, r.co2, r.temp, (unsigned long)r.ts, (unsigned long)r.seq);

  WiFiClient client;
  HTTPClient http;
  String url = String(SERVER_URL) + SERVER_PATH;
  if (!http.begin(client, url)) {
    return POST_NET;
  }
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.addHeader(TOKEN_HEADER, g_token);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(body);

  PostResult result;
  if (code == 200) {
    result = POST_OK;
  } else if (code == 401 || code == 403) {
    result = POST_AUTH;
  } else if (code == 429) {
    String retry = http.getHeader("Retry-After");
    g_retryAfterSec = retry.length() > 0 ? (uint32_t)retry.toInt() : 5;
    if (g_retryAfterSec < 1) {
      g_retryAfterSec = 1;
    }
    result = POST_RATE;
  } else if (code >= 400 && code < 500) {
    result = POST_BADREQ;
  } else if (code > 0) {
    result = POST_SERVER;
  } else {
    result = POST_NET;
  }
  if (result != POST_OK) {
    logLine("[warn] 上报失败 HTTP=%d（body=%s）", code, body);
  }
  http.end();
  return result;
}

// 冲刷缓存：把待发的读数按顺序补发
static void flushQueue() {
  if (g_qCount == 0 || g_tokenInvalid || !g_wifiOk) {
    return;
  }
  if ((int32_t)(millis() - g_nextAttempt) < 0) {
    return;  // 还在退避等待中
  }
  Reading head = g_queue[g_qHead];
  PostResult res = postReading(head);
  switch (res) {
    case POST_OK:
      queuePop();
      queueSave();
      g_backoffMs = BACKOFF_INITIAL_MS;
      g_lastSentOk = true;
      g_nextAttempt = millis();
      break;
    case POST_AUTH:
      g_tokenInvalid = true;
      logLine("[ESP32] 令牌无效（401）——停止重试。请用串口 `set <token>` 重新写入令牌");
      break;
    case POST_RATE:
      logLine("[warn] 触发限流（429），按 Retry-After=%lus 等待", (unsigned long)g_retryAfterSec);
      g_nextAttempt = millis() + g_retryAfterSec * 1000UL;
      break;
    case POST_BADREQ:
      logLine("[warn] 服务端认为该条数据非法（4xx），丢弃以免堵塞队列");
      queuePop();
      queueSave();
      break;
    default:  // POST_SERVER / POST_NET
      g_nextAttempt = millis() + g_backoffMs;
      g_backoffMs = (g_backoffMs * 2 > BACKOFF_MAX_MS) ? BACKOFF_MAX_MS : g_backoffMs * 2;
      break;
  }
}

// ---------------------------------------------------------------------------
// 7. 串口命令
// ---------------------------------------------------------------------------

static void handleSerial() {
  static String line;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      line.trim();
      if (line.length() > 0) {
        if (line.startsWith("set ")) {
          String token = line.substring(4);
          token.trim();
          if (token.length() < 8) {
            logLine("[warn] 令牌太短，未写入");
          } else {
            saveToken(token.c_str());
            logLine("[OK] 令牌已写入 NVS（长度 %u），恢复上报", (unsigned)strlen(g_token));
          }
        } else if (line == "status") {
          logLine("[ESP32] dev=%s wifi=%s token=%s pending=%u seq=%lu",
                  DEVICE_ID, g_wifiOk ? "ok" : "down",
                  (g_tokenInvalid ? "invalid" : (strlen(g_token) ? "ok" : "none")),
                  (unsigned)g_qCount, (unsigned long)g_seq);
        } else if (line == "help") {
          logLine("命令：set <token> | status | help");
        } else {
          logLine("未知命令：%s（help 看用法）", line.c_str());
        }
      }
      line = "";
    } else if (line.length() < 200) {
      line += c;
    }
  }
}

// ---------------------------------------------------------------------------
// 8. 主流程
// ---------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  delay(200);
  logLine("");
  logLine("[ESP32] 物流碳感知 —— 边缘传感器客户端启动");

  nvsBegin();
  loadToken();
  g_seq = (uint32_t)g_nvs.getUInt(NVS_KEY_SEQ, 0);
  queueLoad();

  // CO2 传感器初始化
  if (strcmp(CO2_SENSOR_TYPE, "SGP30_I2C") == 0) {
    Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN, I2C_FREQUENCY);
    sgp30Init();
  } else if (strcmp(CO2_SENSOR_TYPE, "SCD41_I2C") == 0) {
    Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN, I2C_FREQUENCY);
  } else if (strcmp(CO2_SENSOR_TYPE, "MH_Z19_UART") == 0) {
    Serial2.begin(MHZ19_BAUD, SERIAL_8N1, MHZ19_RX_PIN, MHZ19_TX_PIN);
  }

  // 温度传感器初始化
  if (strcmp(TEMP_SENSOR_TYPE, "BME280") == 0) {
    Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN, I2C_FREQUENCY);
    if (!bme280Init()) {
      logLine("[warn] BME280 初始化失败（检查 I2C 接线与 0x76/0x77 地址）");
    }
  } else if (strcmp(TEMP_SENSOR_TYPE, "DS18B20") == 0) {
    pinMode(DS18B20_PIN, INPUT);
  }

  wifiConnect();
  if (g_wifiOk) {
    syncTime();
  }
  logLine("[ESP32] 就绪。串口输入 `help` 看命令，`set <token>` 写令牌");
}

void loop() {
  handleSerial();

  // WiFi 断了就重连（不阻塞太久）
  if (g_wifiOk && WiFi.status() != WL_CONNECTED) {
    g_wifiOk = false;
    logLine("[warn] WiFi 断开，进入离线缓存模式");
  } else if (!g_wifiOk && (millis() % 10000) < 50) {
    wifiConnect();
    if (g_wifiOk && !g_timeOk) {
      syncTime();
    }
  }

  // 定时采样 → 入队 → 尝试发送
  static uint32_t lastSample = 0;
  if (millis() - lastSample >= SAMPLE_INTERVAL_MS) {
    lastSample = millis();
    float co2 = 0.0f;
    float temp = 0.0f;
    bool co2Ok = readCo2(co2);
    bool tempOk = readTemp(temp);
    if (co2Ok && tempOk) {
      g_lastCo2 = co2;
      g_lastTemp = temp;
      Reading r;
      r.ts = nowEpoch();
      r.seq = ++g_seq;
      r.co2 = co2;
      r.temp = temp;
      g_nvs.putUInt(NVS_KEY_SEQ, g_seq);
      queuePush(r);
      queueSave();
    } else {
      logLine("[warn] 传感器读取失败 co2=%s temp=%s（本次不上报）",
              co2Ok ? "ok" : "fail", tempOk ? "ok" : "fail");
    }
  }

  flushQueue();

  // 周期日志（默认 30s 一条）
  if (millis() - g_lastLog >= LOG_INTERVAL_MS) {
    g_lastLog = millis();
    logLine("[ESP32] wifi=%s dev=%s co2=%.0f temp=%.1f sent=%s seq=%lu pending=%u",
            g_wifiOk ? "ok" : "down", DEVICE_ID, g_lastCo2, g_lastTemp,
            g_lastSentOk ? "ok" : "pending", (unsigned long)g_seq,
            (unsigned)g_qCount);
    g_lastSentOk = false;
  }

  delay(50);
}
