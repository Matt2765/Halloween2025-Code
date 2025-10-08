// receiver_v2.ino
// ESP-NOW receiver -> NDJSON over USB Serial (921600 baud).
// Compatible with Arduino-ESP32 cores using old or new esp_now recv callback.

#include <WiFi.h>
#include <esp_now.h>
#include "esp_idf_version.h"

#define ESPNOW_WIFI_CHANNEL 1
#define SERIAL_BAUD 921600
#define MAX_LINE_LEN 240
#define QUEUE_DEPTH 128
#define ACK_MAGIC 0xA1C3

typedef struct {
  uint8_t mac[6];
  uint32_t rx_ms;
  char line[MAX_LINE_LEN + 1];
} rx_item_t;

static QueueHandle_t rxQueue;

bool ensurePeer(const uint8_t mac[6]) {
  esp_now_peer_info_t peer{};
  if (esp_now_is_peer_exist(mac)) return true;
  memcpy(peer.peer_addr, mac, 6);
  peer.channel = ESPNOW_WIFI_CHANNEL;
  peer.encrypt = false;
  return (esp_now_add_peer(&peer) == ESP_OK);
}

typedef struct __attribute__((packed)) {
  uint16_t magic;
  uint32_t sender_id;
  uint32_t seq;
} ack_t;

void sendAck(const uint8_t mac[6], uint32_t sender_id, uint32_t seq) {
  if (!ensurePeer(mac)) return;
  ack_t ack{ACK_MAGIC, sender_id, seq};
  esp_now_send(mac, reinterpret_cast<uint8_t*>(&ack), sizeof(ack));
}

// ------------------ Common RX handling ------------------
static inline void handlePacketFromMac(const uint8_t mac[6], const uint8_t *data, int len) {
  uint32_t sender_id = 0;
  uint32_t seq = 0;
  const uint8_t* textPtr = data;
  int textLen = len;

  if (!(len > 0 && data[0] == '{')) {
    if (len >= 12) {
      sender_id = ((const uint32_t*)data)[0];
      seq       = ((const uint32_t*)data)[1];
      textPtr   = data + 8;
      textLen   = len - 8;
    }
  }
  if (textLen <= 0 || textLen > MAX_LINE_LEN) return;

  rx_item_t item{};
  memcpy(item.mac, mac, 6);
  item.rx_ms = millis();
  memcpy(item.line, textPtr, textLen);
  item.line[textLen] = '\0';
  xQueueSendFromISR(rxQueue, &item, nullptr);

  if (sender_id != 0) sendAck(mac, sender_id, seq);
}

// ------------------ Callback(s) ------------------
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5,0,0)
// New signature: (const esp_now_recv_info*, const uint8_t*, int)
void onDataRecvNew(const esp_now_recv_info *info, const uint8_t *data, int len) {
  if (!info) return;
  handlePacketFromMac(info->src_addr, data, len);
}
#else
// Old signature: (const uint8_t*, const uint8_t*, int)
void onDataRecvOld(const uint8_t *mac, const uint8_t *data, int len) {
  if (!mac) return;
  handlePacketFromMac(mac, data, len);
}
#endif

// ------------------ Writer task ------------------
void writerTask(void* arg) {
  rx_item_t item;
  for (;;) {
    if (xQueueReceive(rxQueue, &item, portMAX_DELAY) == pdTRUE) {
      char macStr[18];
      snprintf(macStr, sizeof(macStr), "%02X:%02X:%02X:%02X:%02X:%02X",
               item.mac[0], item.mac[1], item.mac[2], item.mac[3], item.mac[4], item.mac[5]);

      Serial.print("{\"rx_ms\":");
      Serial.print(item.rx_ms);
      Serial.print(",\"mac\":\"");
      Serial.print(macStr);
      Serial.print("\",\"data\":");
      Serial.print(item.line);
      Serial.println("}");
    }
  }
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(50);

  WiFi.mode(WIFI_STA);
  WiFi.softAP("espnow-chan", nullptr, ESPNOW_WIFI_CHANNEL, 1, 0);
  WiFi.softAPdisconnect(true);

  if (esp_now_init() != ESP_OK) {
    Serial.println("{\"level\":\"error\",\"msg\":\"esp_now_init failed\"}");
    ESP.restart();
  }

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5,0,0)
  esp_now_register_recv_cb(onDataRecvNew);
#else
  esp_now_register_recv_cb(onDataRecvOld);
#endif

  rxQueue = xQueueCreate(QUEUE_DEPTH, sizeof(rx_item_t));
  xTaskCreatePinnedToCore(writerTask, "writerTask", 4096, nullptr, 1, nullptr, 1);

  Serial.print("{\"level\":\"info\",\"msg\":\"Receiver ready\",\"chan\":");
  Serial.print(ESPNOW_WIFI_CHANNEL);
  Serial.println("}");
}

void loop() {
  // All work in callbacks/tasks
}
