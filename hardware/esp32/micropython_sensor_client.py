# -*- coding: utf-8 -*-
"""micropython_sensor_client.py —— ESP32 边缘传感器客户端（MicroPython 版）。

用 MicroPython 自带的 network / urequests / machine / time，**不装任何第三方包**。
适合"先跑通链路、再换 Arduino 固件"的快速验证场景。

烧录步骤（Windows PowerShell）
-----------------------------
1) 装好 MicroPython 固件（ESP32 版）：

       esptool --chip esp32 --port COM3 erase_flash
       esptool --chip esp32 --port COM3 write_flash 0x1000 esp32_generic_esp32.bin

   （COM3 换成你机器上的实际串口号；固件文件名见 micropython.org 下载页）

2) 把本文件传成设备上的 main.py：

       mpremote fs cp micropython_sensor_client.py :main.py
       mpremote reset

   或者用 Thonny：File → Save as → "Micropython device (ESP32)"，文件名 main.py。

3) 令牌：见下面「令牌从哪来」一节。

设备端配置（改这里）
--------------------
SERVER_URL  服务端地址，例如 http://192.168.1.10:5000（不能写 127.0.0.1）
DEVICE_ID   设备号，必须与主机注册表里的 device_id 一致
UART 引脚   MH-Z19 接 GPIO16(CH5) / GPIO17(CH4)，9600 波特
ADC 温度    演示用分压读数，**精度有限**，正式部署请换 DS18B20 / BME280

令牌从哪来
----------
MicroPython 没有 NVS，本实现用设备上的文件 ``/device_token.txt`` 代替：

    mpremote fs cp token.txt :/device_token.txt

其中 token.txt 只是一行明文令牌（没有换行也可以）。文件不存在时退回下面的
DEVICE_TOKEN 占位符（默认空 → 不上报，只打日志）。

⚠ 本文件在本机无真机验证（没有 ESP32 串口），只做过人工审读与逻辑推演。

与项目其它 Python 文件的差别：MicroPython 的编译器**不支持**
``from __future__ import annotations``（会直接报 SyntaxError），因此本文件
刻意省略该行与类型注解；设备端代码以"能跑起来"为准。
"""

import time

try:  # MicroPython 新固件把 urequests 改名为 requests
    import requests as _requests
except ImportError:  # pragma: no cover - 老固件
    import urequests as _requests

import machine
import network

# ---------------------------------------------------------------------------
# 配置区
# ---------------------------------------------------------------------------
SERVER_URL = "http://192.168.1.10:5000"
SERVER_PATH = "/api/upload_sensor"
TOKEN_HEADER = "X-Device-Token"
DEVICE_ID = "PDM-GZ-001"

#: 设备端令牌文件（MicroPython 没有 NVS，用文件代替）
TOKEN_FILE = "/device_token.txt"
#: 编译期占位符：真实令牌优先写进 TOKEN_FILE
DEVICE_TOKEN = ""

WIFI_SSID = "your-wifi-ssid"
WIFI_PASSWORD = "your-wifi-password"

#: 采样间隔（秒）
SAMPLE_INTERVAL = 10
#: 离线缓存条数
CACHE_SIZE = 50
#: 退避上限（秒）
BACKOFF_MAX = 60

#: MH-Z19 UART（默认 CH5=RX、CH4=TX）
UART_ID = 2
UART_RX_PIN = 16
UART_TX_PIN = 17
UART_BAUD = 9600

#: 演示用 ADC 温度脚（GPIO34 = ADC1_CH6，只读无输出，适合做分压输入）
ADC_PIN = 34
#: NTC 10k + 10k 分压的标定（默认按 25℃ = 0.5 V 粗算，仅演示）
ADC_MAX_MV = 3300

# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------
_cache = []          # 待补发的读数列表（先进先出）
_seq = 0             # 自增序号
_token = ""          # 明文令牌
_token_invalid = False  # 收到 401 后停止重试
_backoff = 1         # 退避秒数
_next_attempt = 0    # 下次允许请求的时刻
_wifi = None
_uart = None         # MH-Z19 的 UART（懒初始化）


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def log(msg):
    print("[ESP32-MP] %s" % msg)


def now_epoch():
    """当前 epoch 秒；没同步时间时返回 0。"""
    try:
        t = time.time()
        if t and t > 1700000000:
            return int(t)
    except Exception:
        pass
    return 0


def read_token():
    """从 /device_token.txt 读令牌；读不到就退回编译期占位符。"""
    try:
        with open(TOKEN_FILE, "r") as handle:
            text = handle.read().strip()
            if text:
                return text
    except OSError:
        pass
    return (DEVICE_TOKEN or "").strip()


# ---------------------------------------------------------------------------
# 传感器
# ---------------------------------------------------------------------------
def uart_init():
    """只初始化一次 UART（每次采样重建 UART 会打断总线）。"""
    global _uart
    if _uart is None:
        try:
            _uart = machine.UART(UART_ID, baudrate=UART_BAUD,
                                 rx=UART_RX_PIN, tx=UART_TX_PIN)
        except Exception as exc:  # pragma: no cover - 硬件异常
            log("UART 初始化失败：%s" % exc)
            _uart = False
    return _uart or None


def read_mhz19():
    """从 MH-Z19 读 CO2（ppm）。读 0xFF 0x86 帧，取第 3、4 字节（大端）。

    返回 None 表示本次没读到。
    """
    uart = uart_init()
    if uart is None:
        return None
    # 查询当前 CO2（0xFF 0x86 ... 0x79）
    uart.write(b"\xff\x86\x00\x00\x00\x00\x00\x00\x79")
    deadline = time.time() + 1.0
    buf = b""
    while time.time() < deadline and len(buf) < 9:
        if uart.any():
            buf += uart.read(9 - len(buf))
        else:
            time.sleep_ms(5)
    if len(buf) < 9 or buf[0] != 0xFF or buf[1] != 0x86:
        return None
    ppm = (buf[2] << 8) | buf[3]
    if ppm <= 0 or ppm > 5000:
        return None
    return float(ppm)


def read_temp_adc():
    """演示用温度：NTC 分压 → 粗略换算。

    ⚠ 精度有限（未做 β 补偿与线性化），**仅用于跑通链路**；
      正式部署请换 DS18B20（1-Wire）或 BME280（I2C）。
    """
    try:
        adc = machine.ADC(machine.Pin(ADC_PIN))
        raw = adc.read()
    except Exception as exc:  # pragma: no cover - 硬件异常
        log("ADC 读取失败：%s" % exc)
        return None
    mv = (raw * ADC_MAX_MV) // 4095
    # 简单直线近似：0.5V ↔ 25℃，每 100mV 约 -5℃（粗略，仅演示）
    temp = 25.0 - (mv - 1650) / 100.0 * 5.0
    if temp < -50.0 or temp > 100.0:
        return None
    return temp


# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------
def connect_wifi():
    """连 WiFi。返回 WLAN 对象或 None。"""
    global _wifi
    sta = network.WLAN(network.STA_IF)
    if sta.isconnected():
        _wifi = sta
        log("WiFi 已连接，IP=%s" % sta.ifconfig()[0])
        return sta
    sta.active(True)
    if not sta.isconnected():
        sta.connect(WIFI_SSID, WIFI_PASSWORD)
        deadline = time.time() + 30
        while not sta.isconnected() and time.time() < deadline:
            time.sleep(0.5)
    _wifi = sta
    if sta.isconnected():
        log("WiFi 已连接，IP=%s" % sta.ifconfig()[0])
    else:
        log("WiFi 连接失败，进入离线缓存模式")
    return sta


def sync_time():
    """用 NTP 对时（失败不致命，ts 会记 0）。"""
    try:
        import ntptime
        ntptime.settime()
        log("NTP 已同步")
        return True
    except Exception as exc:
        log("NTP 同步失败（ts 记 0）：%s" % exc)
        return False


def post_reading(item):
    """发一条读数。

    返回 'ok' / 'auth' / 'rate' / 'badreq' / 'error'。
    """
    device_id, co2, temp, ts, seq = item
    body = '{"device_id":"%s","co2":%.1f,"temp":%.1f,"ts":%d,"seq":%d}' % (
        device_id, co2, temp, ts, seq)
    url = SERVER_URL.rstrip("/") + SERVER_PATH
    try:
        resp = _requests.post(url, data=body, headers={
            TOKEN_HEADER: _token,
            "Content-Type": "application/json",
        })
    except Exception as exc:
        log("网络异常：%s" % exc)
        return "error", 0
    try:
        code = resp.status_code
        retry_after = 0
        try:
            headers = getattr(resp, "headers", {}) or {}
            value = headers.get("Retry-After")
            if value is not None:
                retry_after = int(value)
        except Exception:
            retry_after = 0
        try:
            resp.close()
        except Exception:
            pass
    except Exception as exc:
        log("解析响应失败：%s" % exc)
        return "error", 0
    if code == 200:
        return "ok", 0
    if code in (401, 403):
        return "auth", 0
    if code == 429:
        return "rate", retry_after if retry_after > 0 else 5
    if 400 <= code < 500:
        return "badreq", 0
    return "error", 0


# ---------------------------------------------------------------------------
# 离线缓存
# ---------------------------------------------------------------------------
def cache_push(item):
    """入队；满了丢最老的一条。"""
    if len(_cache) >= CACHE_SIZE:
        del _cache[0]
        log("离线缓存已满，丢弃最老的一条")
    _cache.append(item)


def cache_pop():
    """出队（发送成功后调用）。"""
    if _cache:
        del _cache[0]


def flush_cache():
    """把待发读数按顺序补发。返回本轮发送成功的条数。"""
    global _backoff, _next_attempt, _token_invalid
    sent = 0
    if not _cache or _token_invalid:
        return 0
    if time.time() < _next_attempt:
        return 0
    device_id, co2, temp, ts, seq = _cache[0]
    status, retry_after = post_reading(_cache[0])
    if status == "ok":
        cache_pop()
        _backoff = 1
        sent = 1
    elif status == "auth":
        _token_invalid = True
        log("令牌无效（401）——停止重试")
    elif status == "rate":
        log("触发限流（429），等待 %d 秒" % retry_after)
        _next_attempt = time.time() + retry_after
    elif status == "badreq":
        log("服务端认为该条数据非法（4xx），丢弃以免堵塞队列")
        cache_pop()
    else:
        _next_attempt = time.time() + _backoff
        _backoff = min(_backoff * 2, BACKOFF_MAX)
    return sent


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    global _token, _seq
    _token = read_token()
    if not _token:
        log("没有令牌：请把明文令牌写入 %s（否则只做本地采样，不上报）"
            % TOKEN_FILE)
    else:
        log("令牌已加载（长度 %d）" % len(_token))

    connect_wifi()
    sync_time()

    last_sample = 0
    last_log = 0
    last_co2 = None
    last_temp = None
    while True:
        now = time.time()
        if now - last_sample >= SAMPLE_INTERVAL:
            last_sample = now
            co2 = read_mhz19()
            temp = read_temp_adc()
            if co2 is not None and temp is not None:
                last_co2, last_temp = co2, temp
                _seq += 1
                item = (DEVICE_ID, co2, temp, now_epoch(), _seq)
                cache_push(item)
            else:
                log("传感器读取失败（co2=%s temp=%s），本次不入队"
                    % (co2, temp))

        flush_cache()

        if now - last_log >= 30:
            last_log = now
            log("dev=%s co2=%s temp=%s pending=%d seq=%d"
                % (DEVICE_ID, last_co2, last_temp, len(_cache), _seq))
        time.sleep(1)


if __name__ == "__main__":
    main()
