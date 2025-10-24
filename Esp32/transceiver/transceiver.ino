// receiver_v2.ino
// ESP-NOW receiver <-> USB Serial bridge (NDJSON out + simple TX in).
// - RX: ESP-NOW -> queues NDJSON lines (unchanged behaviors for ToF/JSON).
// - Button binary payloads -> emits normalized JSON and learns (id -> mac).
// - Learns (id -> mac) from any JSON payload containing "id":"..."
// - TX: From Serial, lines "TX <ID> <JSON>\n" or "TXMAC <mac> <JSON>\n" send via ESP-NOW unicast.
// - Stays on fixed channel via esp_wifi_set_channel.
// - CHG: Time-bounded de-dupe (id,seq) so reboots don't lock out repeated seq numbers.

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

// ----------------- Peer helper -----------------
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

// ----------------- Registry (id -> mac) -----------------
struct IdMac {
  char id[24];
  uint8_t mac[6];
  uint32_t last_ms;
};

static IdMac idTable[64];
static uint8_t idTableNext = 0;

static void learnIdMac(const char* id, const uint8_t mac[6]) {
  if (!id || !id[0]) return;
  for (auto &e : idTable) {
    if (e.id[0] && strncmp(e.id, id, sizeof(e.id)) == 0) {
      memcpy(e.mac, mac, 6);
      e.last_ms = millis();
      return;
    }
  }
  strncpy(idTable[idTableNext].id, id, sizeof(idTable[idTableNext].id) - 1);
  idTable[idTableNext].id[sizeof(idTable[idTableNext].id) - 1] = '\0';
  memcpy(idTable[idTableNext].mac, mac, 6);
  idTable[idTableNext].last_ms = millis();
  idTableNext = (idTableNext + 1) % (sizeof(idTable) / sizeof(idTable[0]));
}

static bool lookupMacById(const char* id, uint8_t outMac[6]) {
  for (auto &e : idTable) {
    if (e.id[0] && strncmp(e.id, id, sizeof(e.id)) == 0) {
      memcpy(outMac, e.mac, 6);
      return true;
    }
  }
  return false;
}

// ----------------- Button payloads (binary) -----------------
typedef struct __attribute__((packed)) {
  char id[12];              // "BTN-32-A" / "BTN3"
  bool pressed;
  uint32_t seq;
  uint32_t ms_since_boot;
} ButtonMsg1;

typedef struct __attribute__((packed)) {
  char id[16];              // "Multi_BTN1"
  uint8_t btn;              // 1..4
  bool pressed;
  uint32_t seq;
  uint32_t ms_since_boot;
} ButtonMsg4;

// ----------------- De-dupe (time-bounded) -----------------
struct SeenKey {
  char id[16];
  uint32_t seq;
  uint32_t last_ms;   // when we last accepted this (id,seq)
};
static SeenKey seen[64];
static uint8_t seen_idx = 0;
static const uint32_t DEDUPE_WINDOW_MS = 3000; // drop only if seen within last 3s

static inline bool already_seen(const char* id, uint32_t seq) {
  uint32_t now = millis();
  for (uint32_t i = 0; i < (sizeof(seen) / sizeof(seen[0])); i++) {
    SeenKey &k = seen[i];
    if (k.id[0] && k.seq == seq && strncmp(k.id, id, sizeof(k.id)) == 0) {
      if ((uint32_t)(now - k.last_ms) <= DEDUPE_WINDOW_MS) {
        return true;                  // duplicate within window -> drop
      } else {
        k.last_ms = now;              // refresh; allow through this time
        return false;
      }
    }
  }
  // record new
  SeenKey &slot = seen[seen_idx];
  memset(&slot, 0, sizeof(slot));
  strncpy(slot.id, id, sizeof(slot.id) - 1);
  slot.seq = seq;
  slot.last_ms = now;
  seen_idx = (seen_idx + 1) % (sizeof(seen) / sizeof(seen[0]));
  return false;
}

// ----------------- RX handling -----------------
static inline void emitJsonFromMac(const uint8_t mac[6], const char* json) {
  rx_item_t item{};
  memcpy(item.mac, mac, 6);
  item.rx_ms = millis();
  strncpy(item.line, json, MAX_LINE_LEN);
  item.line[MAX_LINE_LEN] = '\0';
  xQueueSendFromISR(rxQueue, &item, nullptr);
}

static inline void handlePacketFromMac(const uint8_t mac[6], const uint8_t *data, int len) {
  // A) Multi-button payloads
  if (len == (int)sizeof(ButtonMsg4)) {
    ButtonMsg4 b{};
    memcpy(&b, data, sizeof(b));
    b.id[sizeof(b.id)-1] = '\0';
    if (strstr(b.id, "BTN") && b.btn >= 1 && b.btn <= 4) {
      learnIdMac(b.id, mac);
      if (!already_seen(b.id, b.seq)) {
        char buf[MAX_LINE_LEN+1];
        snprintf(buf, sizeof(buf),
                 "{\"type\":\"button\",\"id\":\"%s\",\"btn\":%u,\"pressed\":%s,\"seq\":%lu}",
                 b.id, (unsigned)b.btn, b.pressed ? "true" : "false", (unsigned long)b.seq);
        emitJsonFromMac(mac, buf);
      }
      return;
    }
  }
  // B) Single-button payloads
  if (len == (int)sizeof(ButtonMsg1)) {
    ButtonMsg1 b{};
    memcpy(&b, data, sizeof(b));
    char idbuf[16]; memset(idbuf, 0, sizeof(idbuf));
    strncpy(idbuf, b.id, sizeof(b.id));
    if (strstr(idbuf, "BTN")) {
      learnIdMac(idbuf, mac);
      if (!already_seen(idbuf, b.seq)) {
        char buf[MAX_LINE_LEN+1];
        snprintf(buf, sizeof(buf),
                 "{\"type\":\"button\",\"id\":\"%s\",\"btn\":1,\"pressed\":%s,\"seq\":%lu}",
                 idbuf, b.pressed ? "true" : "false", (unsigned long)b.seq);
        emitJsonFromMac(mac, buf);
      }
      return;
    }
  }

  // C) Raw JSON pass-through (e.g., ToF or hello packets)
  if (len > 0 && data[0] == '{' && len <= MAX_LINE_LEN) {
    const char* start = (const char*)data;
    const char* idKey = strstr(start, "\"id\"");
    if (!idKey) idKey = strstr(start, "\"device_id\"");
    if (idKey) {
      const char* colon = strchr(idKey, ':');
      if (colon) {
        const char* q1 = strchr(colon, '\"');
        if (q1) {
          const char* q2 = strchr(q1+1, '\"');
          if (q2 && q2 > q1+1) {
            char idbuf[24]; size_t n = (size_t)(q2 - (q1+1));
            if (n >= sizeof(idbuf)) n = sizeof(idbuf) - 1;
            memcpy(idbuf, q1+1, n); idbuf[n] = '\0';
            learnIdMac(idbuf, mac);
          }
        }
      }
    }
    rx_item_t item{};
    memcpy(item.mac, mac, 6);
    item.rx_ms = millis();
    memcpy(item.line, data, len);
    item.line[len] = '\0';
    xQueueSendFromISR(rxQueue, &item, nullptr);
    return;
  }

  // D) Optional binary header path: [sender_id(uint32), seq(uint32)] + JSON text
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

  if (textLen > 0 && textLen <= MAX_LINE_LEN && textPtr[0] == '{') {
    const char* start2 = (const char*)textPtr;
    const char* idKey2 = strstr(start2, "\"id\"");
    if (!idKey2) idKey2 = strstr(start2, "\"device_id\"");
    if (idKey2) {
      const char* colon = strchr(idKey2, ':');
      if (colon) {
        const char* q1 = strchr(colon, '\"');
        if (q1) {
          const char* q2 = strchr(q1+1, '\"');
          if (q2 && q2 > q1+1) {
            char idbuf[24]; size_t n = (size_t)(q2 - (q1+1));
            if (n >= sizeof(idbuf)) n = sizeof(idbuf) - 1;
            memcpy(idbuf, q1+1, n); idbuf[n] = '\0';
            learnIdMac(idbuf, mac);
          }
        }
      }
    }

    rx_item_t item{};
    memcpy(item.mac, mac, 6);
    item.rx_ms = millis();
    memcpy(item.line, textPtr, textLen);
    item.line[textLen] = '\0';
    xQueueSendFromISR(rxQueue, &item, nullptr);
    if (sender_id != 0) sendAck(mac, sender_id, seq);
  }
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

// ---------- Writer task (Serial OUT) ----------
void writerTask(void* arg) {
  rx_item_t item;
  uint32_t lastBeat = millis();
  for (;;) {
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

// ---------- Serial RX task (PC -> ESP-NOW TX) ----------
static char inbuf[512];
static size_t inlen = 0;

static int hexVal(char c) {
  if (c>='0' && c<='9') return c-'0';
  if (c>='A' && c<='F') return c-'A'+10;
  if (c>='a' && c<='f') return c-'a'+10;
  return -1;
}

static bool parseMac(const char* s, uint8_t mac[6]) {
  int n=0;
  for (int i=0; i<17 && s[i]; ++i) {
    if (i%3==2) { if (s[i] != ':') return false; continue; }
    int hi = hexVal(s[i]);
    int lo = hexVal(s[i+1]);
    if (hi<0 || lo<0) return false;
    mac[n++] = (uint8_t)((hi<<4) | lo);
    i++;
    if (n==6) return (s[i+1] == '\0' || s[i+1] == ' ' || s[i+1]=='\n' || s[i+1]=='\r');
  }
  return false;
}

static void serialRxTask(void* arg) {
  for(;;) {
    while (Serial.available()) {
      char c = (char)Serial.read();
      if (c == '\n' || c == '\r') {
        if (inlen > 0) {
          inbuf[inlen] = '\0';
          char* p = inbuf;
          while (*p==' ' || *p=='\t') p++;
          if (strncmp(p, "TXMAC ", 6) == 0) {
            p += 6;
            char* macTok = p;
            while (*p && *p!=' ' && *p!='\t') p++;
            char save = *p; *p = '\0';
            uint8_t mac[6];
            bool ok = parseMac(macTok, mac);
            *p = save;
            while (*p==' '||*p=='\t') p++;
            if (ok && *p) {
              ensurePeer(mac);
              size_t plen = strnlen(p, MAX_LINE_LEN);
              esp_now_send(mac, (const uint8_t*)p, (int)plen);
              Serial.println("{\"level\":\"info\",\"msg\":\"txmac sent\"}");
            } else {
              Serial.println("{\"level\":\"error\",\"msg\":\"txmac parse error\"}");
            }
          } else if (strncmp(p, "TX ", 3) == 0) {
            p += 3;
            char* idTok = p;
            while (*p && *p!=' ' && *p!='\t') p++;
            char save = *p; *p = '\0';
            char idbuf[24]; strncpy(idbuf, idTok, sizeof(idbuf)-1); idbuf[sizeof(idbuf)-1]='\0';
            *p = save;
            while (*p==' '||*p=='\t') p++;
            if (*p) {
              uint8_t mac[6];
              if (lookupMacById(idbuf, mac)) {
                ensurePeer(mac);
                size_t plen = strnlen(p, MAX_LINE_LEN);
                esp_now_send(mac, (const uint8_t*)p, (int)plen);
                Serial.println("{\"level\":\"info\",\"msg\":\"tx sent\",\"id\":\""+String(idbuf)+"\"}");
              } else {
                Serial.println("{\"level\":\"warn\",\"msg\":\"unknown id for TX\",\"id\":\""+String(idbuf)+"\"}");
              }
            } else {
              Serial.println("{\"level\":\"error\",\"msg\":\"tx missing payload\"}");
            }
          }
          inlen = 0;
        }
      } else if (inlen < sizeof(inbuf)-1) {
        inbuf[inlen++] = c;
      } else {
        inlen = 0;
      }
    }
    vTaskDelay(5 / portTICK_PERIOD_MS);
  }
}

// ---------- Bring-up helpers ----------
static bool bringUpWifiAndEspNow() {
  WiFi.mode(WIFI_STA);
  delay(20);

  esp_err_t e;
  e = esp_wifi_set_promiscuous(true);
  if (e != ESP_OK) return false;
  e = esp_wifi_set_channel(ESPNOW_WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);
  if (e != ESP_OK) return false;
  e = esp_wifi_set_promiscuous(false);
  if (e != ESP_OK) return false;

  if (esp_now_init() != ESP_OK) return false;

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

  memset(seen, 0, sizeof(seen));
  seen_idx = 0;

  rxQueue = xQueueCreate(QUEUE_DEPTH, sizeof(rx_item_t));
  xTaskCreatePinnedToCore(writerTask, "writerTask", 4096, nullptr, 1, nullptr, 1);
  xTaskCreatePinnedToCore(serialRxTask, "serialRxTask", 4096, nullptr, 1, nullptr, 1);

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
      delay(500);
    }
  }
}

void loop() {
  // all work in callbacks/tasks
}
