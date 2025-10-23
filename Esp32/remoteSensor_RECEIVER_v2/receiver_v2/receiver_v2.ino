// receiver_v2.ino
// ESP-NOW receiver -> NDJSON over USB Serial.
// Robust version: no auto-restart on failure; retries bring-up; sets channel via esp_wifi_set_channel.

#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
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

// ---------- Button payloads (from our ESP32 senders) ----------
// Single-button node payload (matches BTN-32-A style)
typedef struct __attribute__((packed)) {
  char id[12];              // e.g. "BTN-32-A"
  bool pressed;             // true on press, false on release (if enabled)
  uint32_t seq;             // per-node sequence (RTC persistent)
  uint32_t ms_since_boot;
} ButtonMsg1;

// 4-button box payload (BTNBOX-01), adds btn index
typedef struct __attribute__((packed)) {
  char id[16];              // e.g. "BTNBOX-01"
  uint8_t btn;              // 1..4 within the box
  bool pressed;             // true on press, false on release (if enabled)
  uint32_t seq;             // per-node sequence (RTC persistent)
  uint32_t ms_since_boot;
} ButtonMsg4;

// Small dedupe cache (id, seq)
struct SeenKey { char id[16]; uint32_t seq; };
static SeenKey seen[64];
static uint8_t seen_idx = 0;

static inline bool already_seen(const char* id, uint32_t seq) {
  for (auto &k : seen) {
    if (k.seq == seq && strncmp(k.id, id, sizeof(k.id)) == 0) return true;
  }
  // record (truncate id to 15 chars to fit cache slot)
  strncpy(seen[seen_idx].id, id, sizeof(seen[seen_idx].id) - 1);
  seen[seen_idx].id[sizeof(seen[seen_idx].id) - 1] = '\0';
  seen[seen_idx].seq = seq;
  seen_idx = (seen_idx + 1) % (sizeof(seen)/sizeof(seen[0]));
  return false;
}

// ---------- Common RX handling ----------
static inline void handlePacketFromMac(const uint8_t mac[6], const uint8_t *data, int len) {
  // 0) Try to parse known button binary payloads first (so we don't mis-route them as text)
  if (len == (int)sizeof(ButtonMsg4)) {
    ButtonMsg4 b{};
    memcpy(&b, data, sizeof(b));
    b.id[sizeof(b.id)-1] = '\0';
    // basic sanity: id starts with "BTN", btn in 1..4
    if (strncmp(b.id, "BTN", 3) == 0 && b.btn >= 1 && b.btn <= 4) {
      if (!already_seen(b.id, b.seq)) {
        rx_item_t item{};
        memcpy(item.mac, mac, 6);
        item.rx_ms = millis();
        // Emit normalized JSON for Python
        // {"type":"button","id":"...","btn":N,"pressed":true/false,"seq":...}
        snprintf(item.line, sizeof(item.line),
                 "{\"type\":\"button\",\"id\":\"%s\",\"btn\":%u,\"pressed\":%s,\"seq\":%lu}",
                 b.id, (unsigned)b.btn, b.pressed ? "true" : "false", (unsigned long)b.seq);
        xQueueSendFromISR(rxQueue, &item, nullptr);
      }
      return; // handled
    }
  } else if (len == (int)sizeof(ButtonMsg1)) {
    ButtonMsg1 b{};
    memcpy(&b, data, sizeof(b));
    // Ensure NUL-termination
    char idbuf[16]; memset(idbuf, 0, sizeof(idbuf));
    strncpy(idbuf, b.id, sizeof(b.id));
    if (strncmp(idbuf, "BTN", 3) == 0) {
      if (!already_seen(idbuf, b.seq)) {
        rx_item_t item{};
        memcpy(item.mac, mac, 6);
        item.rx_ms = millis();
        // Single-button nodes report as btn=1
        snprintf(item.line, sizeof(item.line),
                 "{\"type\":\"button\",\"id\":\"%s\",\"btn\":1,\"pressed\":%s,\"seq\":%lu}",
                 idbuf, b.pressed ? "true" : "false", (unsigned long)b.seq);
        xQueueSendFromISR(rxQueue, &item, nullptr);
      }
      return; // handled
    }
  }

  // 1) Existing behavior: JSON text payloads (e.g., ToF) — pass through
  if (len > 0 && data[0] == '{') {
    if (len <= MAX_LINE_LEN) {
      rx_item_t item{};
      memcpy(item.mac, mac, 6);
      item.rx_ms = millis();
      memcpy(item.line, data, len);
      item.line[len] = '\0';
      xQueueSendFromISR(rxQueue, &item, nullptr);
    }
    return;
  }

  // 2) Optional binary header path: [sender_id(uint32), seq(uint32)] + JSON text
  uint32_t sender_id = 0;
  uint32_t seq = 0;
  const uint8_t* textPtr = data;
  int textLen = len;

  if (len >= 12) {
    sender_id = ((const uint32_t*)data)[0];
    seq       = ((const uint32_t*)data)[1];
    textPtr   = data + 8;
    textLen   = len - 8;
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

// ---------- Callbacks (IDF v5/v4) ----------
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5,0,0)
void onDataRecvNew(const esp_now_recv_info *info, const uint8_t *data, int len) {
  if (!info) return;
  handlePacketFromMac(info->src_addr, data, len);
}
#else
void onDataRecvOld(const uint8_t *mac, const uint8_t *data, int len) {
  if (!mac) return;
  handlePacketFromMac(mac, data, len);
}
#endif

// ---------- Writer task ----------
void writerTask(void* arg) {
  rx_item_t item;
  uint32_t lastBeat = millis();
  for (;;) {
    // Heartbeat if idle
    if ((millis() - lastBeat) > 5000) {
      Serial.println("{\"level\":\"debug\",\"msg\":\"receiver heartbeat\"}");
      lastBeat = millis();
    }
    if (xQueueReceive(rxQueue, &item, 250 / portTICK_PERIOD_MS) == pdTRUE) {
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
      lastBeat = millis();
    }
  }
}

// ---------- Bring-up helpers ----------
static bool bringUpWifiAndEspNow() {
  // STA mode
  WiFi.mode(WIFI_STA);
  delay(20);

  // Force channel using esp_wifi_set_channel (robust on IDF v5)
  esp_err_t e;
  e = esp_wifi_set_promiscuous(true);           // required before set_channel on some cores
  if (e != ESP_OK) return false;
  e = esp_wifi_set_channel(ESPNOW_WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);
  if (e != ESP_OK) return false;
  e = esp_wifi_set_promiscuous(false);
  if (e != ESP_OK) return false;

  // Init ESP-NOW
  if (esp_now_init() != ESP_OK) {
    return false;
  }

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5,0,0)
  esp_now_register_recv_cb(onDataRecvNew);
#else
  esp_now_register_recv_cb(onDataRecvOld);
#endif
  return true;
}

// ---------- Setup ----------
void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(50);

  rxQueue = xQueueCreate(QUEUE_DEPTH, sizeof(rx_item_t));
  xTaskCreatePinnedToCore(writerTask, "writerTask", 4096, nullptr, 1, nullptr, 1);

  // Retry loop instead of restart
  uint8_t mac[6]={0};
  esp_wifi_get_mac(WIFI_IF_STA, mac);
  char macStr[18];
  snprintf(macStr, sizeof(macStr), "%02X:%02X:%02X:%02X:%02X:%02X",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);

  for (int attempt=1;; ++attempt) {
    if (bringUpWifiAndEspNow()) {
      Serial.print("{\"level\":\"info\",\"msg\":\"Receiver ready\",\"chan\":");
      Serial.print(ESPNOW_WIFI_CHANNEL);
      Serial.print(",\"mac\":\"");
      Serial.print(macStr);
      Serial.println("\"}");
      break;
    } else {
      Serial.print("{\"level\":\"error\",\"msg\":\"esp-now bringup failed, retrying\",\"attempt\":");
      Serial.print(attempt);
      Serial.println("}");
      delay(500); // wait a bit then retry
    }
  }
}

void loop() {
  // all work in callbacks/task
}
