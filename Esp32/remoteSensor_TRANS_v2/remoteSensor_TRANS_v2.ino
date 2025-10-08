// remoteSensor_TRANS_v2.ino
// ESP32 sender for VL53L1X (TOF400C-VL53L1X) on SDA=21, SCL=22
// Sends ESP-NOW packets: [8-byte header: sender_id, seq][JSON object]
// Compatible with Arduino-ESP32 (IDF v4/v5): fixes MAC+callback signatures and uses Adafruit VL53L1X defaults.

#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_mac.h>
#include "esp_idf_version.h"

#include <Wire.h>
#include <Adafruit_VL53L1X.h>

// ---------- Config ----------
#define I2C_SDA              21
#define I2C_SCL              22
#define ESPNOW_WIFI_CHANNEL  1        // must match receiver
#define SEND_HZ              20       // 20 Hz per node is a good starting point
#define ACK_WAIT_MS          20
#define ACK_MAX_TRIES        3
#define SERIAL_BAUD          115200   // debug only

// Optional: set your receiver's MAC for unicast; leave FFs for broadcast
uint8_t RECEIVER_MAC[6] = {0xFF,0xFF,0xFF,0xFF,0xFF,0xFF};

// ---------- Types ----------
typedef struct __attribute__((packed)) {
  uint16_t magic;      // 0xA1C3
  uint32_t sender_id;  // our ID
  uint32_t seq;        // echoed seq
} ack_t;

static const uint16_t ACK_MAGIC = 0xA1C3;

// ---------- Globals ----------
Adafruit_VL53L1X vl = Adafruit_VL53L1X();
uint32_t sender_id = 0;
volatile uint32_t last_acked_seq = 0;
volatile bool last_send_ok = false;

uint32_t seq = 0;
uint32_t send_interval_ms = 1000 / SEND_HZ;

bool use_broadcast = true;

// ---------- Helpers ----------
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

// ---------- Callbacks ----------
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5,0,0)
// New send signature (IDF v5): (const wifi_tx_info_t*, esp_now_send_status_t)
void onSendCb(const wifi_tx_info_t *tx_info, esp_now_send_status_t status) {
  (void)tx_info;
  last_send_ok = (status == ESP_NOW_SEND_SUCCESS);
}
#else
// Old send signature
void onSendCb(const uint8_t *mac_addr, esp_now_send_status_t status) {
  (void)mac_addr;
  last_send_ok = (status == ESP_NOW_SEND_SUCCESS);
}
#endif

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5,0,0)
// New recv signature
void onRecvCbNew(const esp_now_recv_info *info, const uint8_t *data, int len) {
  if (!info || len < (int)sizeof(ack_t)) return;
  const ack_t* a = reinterpret_cast<const ack_t*>(data);
  if (a->magic == ACK_MAGIC && a->sender_id == sender_id) last_acked_seq = a->seq;
}
#else
// Old recv signature
void onRecvCbOld(const uint8_t *mac, const uint8_t *data, int len) {
  (void)mac;
  if (len < (int)sizeof(ack_t)) return;
  const ack_t* a = reinterpret_cast<const ack_t*>(data);
  if (a->magic == ACK_MAGIC && a->sender_id == sender_id) last_acked_seq = a->seq;
}
#endif

// ---------- Setup ----------
void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(50);

  // Lock to a known channel for ESP-NOW
  WiFi.mode(WIFI_STA);
  WiFi.softAP("espnow-chan", nullptr, ESPNOW_WIFI_CHANNEL, 1, 0);
  WiFi.softAPdisconnect(true);

  // Derive a compact sender_id from STA MAC (IDF v4/v5 safe)
  uint8_t mac[6] = {0};
  esp_wifi_get_mac(WIFI_IF_STA, mac);
  sender_id = (uint32_t)mac[2]<<24 | (uint32_t)mac[3]<<16 | (uint32_t)mac[4]<<8 | (uint32_t)mac[5];

  if (esp_now_init() != ESP_OK) {
    Serial.println("[ERR] esp_now_init failed");
    ESP.restart();
  }

  esp_now_register_send_cb(onSendCb);
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5,0,0)
  esp_now_register_recv_cb(onRecvCbNew);
#else
  esp_now_register_recv_cb(onRecvCbOld);
#endif

  // Peer setup (broadcast or unicast)
  if (!isAllFF(RECEIVER_MAC)) {
    use_broadcast = false;
    if (!ensurePeer(RECEIVER_MAC)) Serial.println("[ERR] add peer failed");
  } else {
    uint8_t bcast[6] = {0xFF,0xFF,0xFF,0xFF,0xFF,0xFF};
    if (!ensurePeer(bcast)) Serial.println("[ERR] add broadcast peer failed");
  }

  // I2C + VL53L1X init (Adafruit lib defaults; keep API-compatible)
  Wire.begin(I2C_SDA, I2C_SCL);
  if (!vl.begin(0x29, &Wire)) {
    Serial.println("[ERR] VL53L1X not found at 0x29");
    for(;;) delay(1000);
  }
  // Adafruit lib: defaults are okay; start continuous ranging
  vl.startRanging();

  char macStr[18]; macToStr(mac, macStr, sizeof(macStr));
  Serial.print("[INFO] Sender ready. MAC="); Serial.print(macStr);
  Serial.print("  sender_id="); Serial.print(sender_id);
  Serial.print("  channel="); Serial.print(ESPNOW_WIFI_CHANNEL);
  Serial.print("  mode="); Serial.println(use_broadcast ? "broadcast" : "unicast");
}

// ---------- Send one packet with small retry/ACK loop ----------
void sendPacket(int dist_mm, int status) {
  // JSON body (≤ ~220 bytes)
  // Example: {"id":"TOF1","seq":1234,"t":123456,"vals":{"dist_mm":823,"status":0}}
  char json[220];
  snprintf(json, sizeof(json),
           "{\"id\":\"%s\",\"seq\":%lu,\"t\":%lu,\"vals\":{\"dist_mm\":%d,\"status\":%d}}",
           "TOF1", (unsigned long)seq, (unsigned long)millis(), dist_mm, status);

  // Packet buffer = [8 bytes header][json]
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
    delay(2); // short backoff
  }
  // No ACK after tries; proceed to keep realtime behavior
}

// ---------- Loop ----------
void loop() {
  static uint32_t last = 0;
  const uint32_t now = millis();
  if (now - last < send_interval_ms) return;
  last = now;

  int dist = -1;
  int stat = 0;

  // Adafruit VL53L1X: dataReady() + distance(); clear interrupt after read
  if (vl.dataReady()) {
    dist = (int)vl.distance();  // millimeters
    vl.clearInterrupt();
  } else {
    stat = -2; // no new sample yet this cycle
  }

  sendPacket(dist, stat);
  seq++;
}
