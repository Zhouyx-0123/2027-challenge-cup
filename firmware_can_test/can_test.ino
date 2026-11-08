/*
 * CAN 双机收发测试（ESP32 DevKit1 <-> ESP32-C3-MINI-1）
 *
 * 接线（每块板都一样）：
 *   ESP32 GPIO5 (D5)  -> CAN 收发器 TXD / CTX（收发器的输入）
 *   ESP32 GPIO4 (D4)  <- CAN 收发器 RXD / CRX（收发器的输出）
 *   3.3V              -> 收发器 VCC（SN65HVD230 用 3.3V；TJA1050 是 5V，不要接 3.3V）
 *   GND               -> 收发器 GND
 *
 * 两块收发器之间：
 *   CANH -> CANH
 *   CANL -> CANL
 *   GND  -> GND（两块板必须共地）
 *   总线两端各 120Ω（模块自带就不要再并；都不带就加两个 120Ω）
 *
 * 烧录：
 *   板 A：NODE_ID 改为 1
 *   板 B：NODE_ID 改为 2
 *   串口监视器 115200
 *
 * 说明：
 *   - 这是 ESP32 自带的 TWAI 控制器，不需要额外 CAN 库。
 *   - 250 kbps，标准帧 11-bit ID。
 *   - 节点 1 发 0x121，节点 2 发 0x122；双方都能收到对方。
 *   - 如果只有一块板，TX 会因为没人 ACK 而失败，这是正常的。
 */

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include "driver/gpio.h"
#include "driver/twai.h"
#include "esp_err.h"

// ======== 按你的接线改这里 ========
#define CAN_TX_PIN 5          // ESP32 D5 -> 收发器 TXD/CTX
#define CAN_RX_PIN 4          // ESP32 D4 <- 收发器 RXD/CRX
#ifndef NODE_ID
#define NODE_ID 1             // 默认板 A；编译时可用 -DNODE_ID=2
#endif
// =================================

#define CAN_BITRATE_KBPS 250
#define CAN_TX_ID (0x120 + NODE_ID)
#define SEND_INTERVAL_MS 1000
#define STATUS_INTERVAL_MS 5000

static uint32_t tx_count = 0;
static uint32_t rx_count = 0;
static uint32_t last_send_ms = 0;
static uint32_t last_status_ms = 0;

static bool can_init() {
  twai_general_config_t g_config = TWAI_GENERAL_CONFIG_DEFAULT(
      (gpio_num_t)CAN_TX_PIN, (gpio_num_t)CAN_RX_PIN, TWAI_MODE_NORMAL);
  twai_timing_config_t t_config = TWAI_TIMING_CONFIG_250KBITS();
  twai_filter_config_t f_config = TWAI_FILTER_CONFIG_ACCEPT_ALL();

  esp_err_t err = twai_driver_install(&g_config, &t_config, &f_config);
  if (err != ESP_OK) {
    Serial.printf("twai_driver_install failed: %s\n", esp_err_to_name(err));
    return false;
  }

  err = twai_start();
  if (err != ESP_OK) {
    Serial.printf("twai_start failed: %s\n", esp_err_to_name(err));
    return false;
  }
  return true;
}

static void print_status() {
  twai_status_info_t st;
  if (twai_get_status_info(&st) != ESP_OK) return;

  Serial.printf(
      "STATUS state=%d tx_err=%lu rx_err=%lu tx_fail=%lu rx_miss=%lu "
      "arb_lost=%lu bus_err=%lu\n",
      (int)st.state,
      (unsigned long)st.tx_error_counter,
      (unsigned long)st.rx_error_counter,
      (unsigned long)st.tx_failed_count,
      (unsigned long)st.rx_missed_count,
      (unsigned long)st.arb_lost_count,
      (unsigned long)st.bus_error_count);

  if (st.state == TWAI_STATE_BUS_OFF) {
    Serial.println("BUS-OFF: check CANH/CANL, 120ohm, common GND, then press RST");
  }
}

static void send_heartbeat() {
  twai_message_t tx = {};
  tx.identifier = CAN_TX_ID;
  tx.extd = 0;                 // 标准帧
  tx.rtr = 0;                  // 数据帧
  tx.data_length_code = 8;
  tx.data[0] = NODE_ID;
  tx.data[1] = tx_count & 0xFF;
  tx.data[2] = (tx_count >> 8) & 0xFF;
  tx.data[3] = 0xAA;
  tx.data[4] = 0x55;
  tx.data[5] = 0x01;
  tx.data[6] = 0x02;
  tx.data[7] = 0x03;

  esp_err_t err = twai_transmit(&tx, pdMS_TO_TICKS(100));
  if (err == ESP_OK) {
    tx_count++;
    Serial.printf("TX #%lu id=0x%03lX node=%d\n",
                  (unsigned long)tx_count, (unsigned long)CAN_TX_ID, NODE_ID);
  } else {
    Serial.printf("TX failed: %s (other node on? CANH/CANL? 120ohm? GND?)\n",
                  esp_err_to_name(err));
  }
}

static void drain_rx() {
  twai_message_t rx;
  while (twai_receive(&rx, 0) == ESP_OK) {
    rx_count++;
    Serial.printf("RX #%lu id=0x%03lX dlc=%u data=",
                  (unsigned long)rx_count,
                  (unsigned long)rx.identifier,
                  (unsigned)rx.data_length_code);
    for (uint8_t i = 0; i < rx.data_length_code && i < 8; i++) {
      Serial.printf("%02X ", rx.data[i]);
    }
    if (rx.data_length_code > 0) {
      Serial.printf(" from_node=%u", rx.data[0]);
    }
    Serial.println();
  }
}

void setup() {
  Serial.begin(115200);
  delay(300);

  Serial.println();
  Serial.println("=== CAN two-node test ===");
  Serial.printf("NODE_ID=%d  TX=GPIO%d  RX=GPIO%d  %d kbps\n",
                NODE_ID, CAN_TX_PIN, CAN_RX_PIN, CAN_BITRATE_KBPS);
  Serial.println("Flash the other board with NODE_ID=2.");

  if (!can_init()) {
    Serial.println("CAN init failed. Check 3.3V/GND and that you picked the ESP32 board.");
    while (true) delay(1000);
  }
  Serial.println("TWAI started. Waiting for frames...");
  last_send_ms = millis();
  last_status_ms = millis();
}

void loop() {
  const uint32_t now = millis();

  if (now - last_send_ms >= SEND_INTERVAL_MS) {
    last_send_ms = now;
    send_heartbeat();
  }

  drain_rx();

  if (now - last_status_ms >= STATUS_INTERVAL_MS) {
    last_status_ms = now;
    print_status();
  }

  delay(1);
}
