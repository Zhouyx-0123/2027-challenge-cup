// OBD 模拟上报固件：板子生成一批 OBD 记录 -> HTTP POST 到后端
// 目的：先验证「板子 -> 后端 -> 碳排流水线」回路，不接真实 CAN/OBD。
//
// 现场只改下面「联调参数块」里的常量；WiFi 必须是 2.4GHz。
// 参数含义与逐步操作见 docs/67_联调参数单.md。
#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <time.h>

// ========================= 联调参数块（只改这里） =========================
// 顺序 = 现场设置顺序：WiFi -> 后端地址 -> 设备令牌 -> 车号。

// ① WiFi：必须是 2.4GHz（ESP32-C3 只支持 2.4G）；手机热点 / 路由器均可。
//    ⚠ 这里只放占位符：真实热点名 / 密码属于私有凭据，不进版本库。
//      现场改成自己的值再编译，改完不要把真实值提交（编译产物也别提交）。
static const char* WIFI_SSID = "YOUR_WIFI_SSID";
static const char* WIFI_PASS = "YOUR_WIFI_PASSWORD";

// ② 后端地址：http://<PC 的 IPv4>:5000/api/upload_obd_gps
//    端口 5000 = Flask 网关（python -m server）；8501 是页面端口，板子不要往那发。
//    换网络后用 ipconfig 查 PC 的 IPv4，改这一行（板子连的是 PC 的局域网 IP，不能写 127.0.0.1）。
//    2026-11-08 实测：PC = 10.104.107.47（WLAN），旧热点网段 192.168.43.9 已失效。
static const char* SERVER_URL = "http://10.104.107.47:5000/api/upload_obd_gps";

// ③ 设备令牌：后端 SENSOR_AUTH=1（默认开启、fail-closed）时必须填
//    `python -m tools.device_tokens add --device-id TRK-LOOP-001` 打印出来的明文；
//    后端按记录里的 vehicle_id 去 config/devices.json 校验，所以下面的 VEHICLE_ID
//    必须和登记时的 --device-id 完全一致。SENSOR_AUTH=0（仅本机演示）时才可留空。
//    明文只留在固件里，不要提交进版本库。
static const char* DEVICE_TOKEN = "";       // SENSOR_AUTH=1 时填明文；=0 可留空

// ④ 车号 / 车牌：车号进 CSV 的 vehicle_id，同时是鉴权用的 device_id。
static const char* VEHICLE_ID = "TRK-LOOP-001";
static const char* PLATE = "苏A12345";
// =========================================================================

static const int   BATCH_N = 30;            // 每批 30 条
static const int   SAMPLE_SECONDS = 10;     // 10 秒一条
static const bool  SEND_ONCE = true;        // 先只发一批，验证回路
static const long  SIM_START_EPOCH = 1794026400L; // 2026-11-07 10:00:00 +08:00

static bool sent = false;

static String iso8601_now(int offset_seconds) {
  time_t now = time(nullptr);
  if (now < 1700000000L) {
    now = SIM_START_EPOCH + (millis() / 1000L);
  }
  now += offset_seconds;
  time_t local = now + 8 * 3600;  // 固定 +08:00，避免依赖时区数据库
  struct tm tmv;
  gmtime_r(&local, &tmv);
  char buf[32];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S+08:00", &tmv);
  return String(buf);
}

static String build_payload() {
  String payload;
  payload.reserve(4096);
  payload += "{\"records\":[";
  double fuel = 1000.0;
  double odo = 10000.0;
  for (int i = 0; i < BATCH_N; i++) {
    // 30 个采样点只有 29 个区间；首点代表起始累计油耗，不再加 0.05 L。
    if (i) fuel += 0.05;             // 18 L/h × 10s = 0.05 L
    odo += 50.0 / 3600.0 * SAMPLE_SECONDS;
    if (i) payload += ",";
    payload += "{\"ts\":\"" + iso8601_now(i * SAMPLE_SECONDS) + "\"";
    payload += ",\"vehicle_id\":\"" + String(VEHICLE_ID) + "\"";
    payload += ",\"plate\":\"" + String(PLATE) + "\"";
    payload += ",\"fuel_type\":\"diesel\"";
    payload += ",\"instant_fuel_lph\":18.0";
    payload += ",\"total_fuel_l\":" + String(fuel, 4);
    payload += ",\"engine_rpm\":1500";
    payload += ",\"vehicle_speed_kmh\":50.0";
    payload += ",\"throttle_pos_pct\":35.0";
    payload += ",\"engine_load_pct\":45.0";
    payload += ",\"engine_temp_c\":85.0";
    payload += ",\"odo_km\":" + String(odo, 4);
    payload += ",\"lat\":" + String(31.2304 + i * 0.0001, 6);
    payload += ",\"lng\":" + String(121.4737 + i * 0.0001, 6);
    payload += ",\"gps_speed_kmh\":49.5";
    payload += ",\"satellites\":10";
    payload += ",\"fix_quality\":1";
    payload += ",\"load_kg\":12000";
    payload += ",\"ignition\":true}";
  }
  payload += "]}";
  return payload;
}

static void send_batch() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial0.println("[WIFI] not connected");
    return;
  }
  String payload = build_payload();
  HTTPClient http;
  http.setTimeout(15000);
  if (!http.begin(SERVER_URL)) {
    Serial0.println("[HTTP] begin failed");
    return;
  }
  http.addHeader("Content-Type", "application/json");
  if (strlen(DEVICE_TOKEN) > 0) {
    http.addHeader("X-Device-Token", DEVICE_TOKEN);
  }
  int code = http.POST(payload);
  String body = http.getString();
  Serial0.printf("[HTTP] %d %s\n", code, body.c_str());
  http.end();
}

void setup() {
  Serial0.begin(115200);
  delay(300);
  Serial0.println();
  Serial0.println("OBD_SIM_BOOT 2026-11-07");
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial0.printf("[WIFI] connecting to %s\n", WIFI_SSID);
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) {
    delay(500);
    Serial0.print(".");
  }
  Serial0.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial0.print("[WIFI] IP=");
    Serial0.println(WiFi.localIP());
  } else {
    Serial0.println("[WIFI] FAILED (check 2.4GHz SSID/password)");
    return;
  }
  configTime(8 * 3600, 0, "ntp.aliyun.com", "pool.ntp.org");
  delay(1500);
  send_batch();
  sent = true;
}

void loop() {
  if (!SEND_ONCE && WiFi.status() == WL_CONNECTED) {
    static unsigned long last = 0;
    if (millis() - last > 30000) {
      last = millis();
      send_batch();
    }
  }
  delay(1000);
}
