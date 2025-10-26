// sender_diag_v2.ino
// Robust, step-by-step sender for ESP32 + VL53L1X on SDA=21 SCL=22.
// Prints progress so we can see where it resets. Sends packets even if sensor fails.
// Baud = 115200 (easy to read). After it’s stable, we can revert to the faster version.

#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include "esp_idf_version.h"
#include <Wire.h>
#include <Adafruit_VL53L1X.h>

#define I2C_SDA              21
#define I2C_SCL              22
#define ESPNOW_WIFI_CHANNEL  1
#define SEND_HZ              10        // start conservative
#define ACK_WAIT_MS          20
#define ACK_MAX_TRIES        2
#define SERIAL_BAUD          115200

// Give this board a clear ID for the JSON:
#define SENSOR_ID            "TOF3"

// Unicast (paste receiver MAC) or broadcast (all FFs)
uint8_t RECEIVER_MAC[6] = {0xFF,0xFF,0xFF,0xFF,0xFF,0xFF};

// ---------------- Types ----------------
typedef struct __attribute__((packed)) {
  uint16_t magic;      // 0xA1C3
  uint32_t sender_id;  // our ID
  uint32_t seq;        // echoed seq
} ack_t;
static const uint16_t ACK_MAGIC = 0xA1C3;

// ---------------- Globals --------------
Adafruit_VL53L1X vl;
uint32_t sender_id = 0;
volatile uint32_t last_acked_seq = 0;
volatile bool last_send_ok = false;
uint32_t seq = 0;
uint32_t send_interval_ms = 1000 / SEND_HZ;
bool use_broadcast = true;
bool sensor_ok = false;

// ---------------- Utils ----------------
static bool isAllFF(const uint8_t mac[6]) {
  for (int i=0;i<6;i++) if (mac[i] != 0xFF) return false;
  return true;
}

void macToStr(const uint8_t m[6], char* out, size_t n) {
  snprintf(out, n, "%02X:%02X:%02X:%02X:%02X:%02X", m[0],m[1],m[2],m[3],m[4],m[5]);
}

bool ensurePeer(const uint8_t mac[6]) {
  if (esp_now_is_peer_exist(mac)) return true;
  esp_now_peer_info_t peer{};
  memcpy(peer.peer_addr, mac, 6);
  peer.channel = ESPNOW_WIFI_CHANNEL;
  peer.encrypt = false;
  return esp_now_add_peer(&peer) == ESP_OK;
}

// ---------------- Callbacks ------------
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5,0,0)
void onSendCb(const wifi_tx_info_t *tx_info, esp_now_send_status_t status) {
  (void)tx_info;
  last_send_ok = (status == ESP_NOW_SEND_SUCCESS);
}
void onRecvCbNew(const esp_now_recv_info *info, const uint8_t *data, int len) {
  if (!info || len < (int)sizeof(ack_t)) return;
  const ack_t* a = reinterpret_cast<const ack_t*>(data);
  if (a->magic == ACK_MAGIC && a->sender_id == sender_id) last_acked_seq = a->seq;
}
#else
void onSendCb(const uint8_t *mac_addr, esp_now_send_status_t status) {
  (void)mac_addr;
  last_send_ok = (status == ESP_NOW_SEND_SUCCESS);
}
void onRecvCbOld(const uint8_t *mac, const uint8_t *data, int len) {
  (void)mac;
  if (len < (int)sizeof(ack_t)) return;
  const ack_t* a = reinterpret_cast<const ack_t*>(data);
  if (a->magic == ACK_MAGIC && a->sender_id == sender_id) last_acked_seq = a->seq;
}
#endif

// --------------- Setup -----------------
void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(200);
  Serial.println("\n[SENDER] Boot");

  // 0) Basic power sanity: brief heartbeat before doing anything heavy
  for (int i=0;i<3;i++) { delay(50); Serial.print("."); }
  Serial.println();

  // 1) Wi-Fi/Channel
  Serial.println("[SENDER] WiFi.mode(WIFI_STA)");
  WiFi.mode(WIFI_STA);
  delay(10);

  // Force known channel for ESP-NOW
  Serial.print("[SENDER] Forcing channel "); Serial.println(ESPNOW_WIFI_CHANNEL);
  WiFi.softAP("espnow-chan", nullptr, ESPNOW_WIFI_CHANNEL, 1, 0);
  delay(10);
  WiFi.softAPdisconnect(true);
  delay(10);

  // 2) MAC / sender_id
  uint8_t mac[6] = {0};
  esp_wifi_get_mac(WIFI_IF_STA, mac);
  sender_id = (uint32_t)mac[2]<<24 | (uint32_t)mac[3]<<16 | (uint32_t)mac[4]<<8 | (uint32_t)mac[5];
  char macStr[18]; macToStr(mac, macStr, sizeof(macStr));
  Serial.print("[SENDER] MAC="); Serial.println(macStr);
  Serial.print("[SENDER] sender_id="); Serial.println(sender_id);

  // 3) ESP-NOW
  Serial.println("[SENDER] esp_now_init()");
  if (esp_now_init() != ESP_OK) {
    Serial.println("[SENDER][ERR] esp_now_init failed -> restarting");
    delay(200);
    ESP.restart();
  }
  Serial.println("[SENDER] Register callbacks");
  esp_now_register_send_cb(onSendCb);
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5,0,0)
  esp_now_register_recv_cb(onRecvCbNew);
#else
  esp_now_register_recv_cb(onRecvCbOld);
#endif

  // 4) Peer (broadcast or unicast)
  if (!isAllFF(RECEIVER_MAC)) {
    use_broadcast = false;
    if (!ensurePeer(RECEIVER_MAC)) Serial.println("[SENDER][ERR] add peer failed");
  } else {
    uint8_t bcast[6] = {0xFF,0xFF,0xFF,0xFF,0xFF,0xFF};
    if (!ensurePeer(bcast)) Serial.println("[SENDER][ERR] add broadcast peer failed");
  }

  // 5) I2C + Sensor (don’t block forever)
  Serial.println("[SENDER] I2C begin");
  Wire.begin(I2C_SDA, I2C_SCL);
  delay(10);
  Serial.println("[SENDER] VL53L1X begin()");
  if (vl.begin(0x29, &Wire)) {
    sensor_ok = true;
    Serial.println("[SENDER] VL53L1X OK, startRanging()");
    vl.startRanging();  // Adafruit API starts continuous mode
  } else {
    sensor_ok = false;
    Serial.println("[SENDER][WARN] VL53L1X NOT FOUND at 0x29 — will send -1");
  }

  Serial.print("[SENDER] Ready on channel "); Serial.print(ESPNOW_WIFI_CHANNEL);
  Serial.print("  mode="); Serial.println(use_broadcast ? "broadcast" : "unicast");
}

// --------------- Send ------------------
void sendPacket(int dist_mm, int status) {
  char json[220];
  snprintf(json, sizeof(json),
           "{\"id\":\"%s\",\"seq\":%lu,\"t\":%lu,\"vals\":{\"dist_mm\":%d,\"status\":%d}}",
           SENSOR_ID, (unsigned long)seq, (unsigned long)millis(), dist_mm, status);

  const int jsonLen  = strnlen(json, sizeof(json));
  const int totalLen = 8 + jsonLen;
  static uint8_t pkt[256];
  memcpy(pkt,   &sender_id, 4);
  memcpy(pkt+4, &seq,       4);
  memcpy(pkt+8, json,       jsonLen);

  uint8_t bcast[6] = {0xFF,0xFF,0xFF,0xFF,0xFF,0xFF};
  uint8_t* dest = use_broadcast ? bcast : RECEIVER_MAC;

  const uint32_t this_seq = seq;
  for (int attempt=0; attempt<ACK_MAX_TRIES; ++attempt) {
    last_send_ok = false;
    esp_now_send(dest, pkt, totalLen);
    uint32_t t0 = millis();
    while ((millis() - t0) < ACK_WAIT_MS) {
      if (last_acked_seq == this_seq) return; // acked
      delay(1);
    }
    delay(2);
  }
}

// --------------- Loop ------------------
void loop() {
  static uint32_t last = 0;
  const uint32_t now = millis();
  if (now - last < send_interval_ms) { delay(1); return; }
  last = now;

  int dist = -1;
  int stat = 0;
  if (sensor_ok) {
    if (vl.dataReady()) {
      dist = (int)vl.distance();  // mm
      vl.clearInterrupt();
    } else {
      stat = -2;
    }
  } else {
    dist = -1;
    stat = -3; // sensor absent
  }

  sendPacket(dist, stat);
  seq++;
}
