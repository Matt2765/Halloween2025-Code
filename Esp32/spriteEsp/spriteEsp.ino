// spriteEsp.ino — ESP32 Sprite Controller (ESP-NOW -> UART w/ DEBUG)
// Compatible with your RSM/receiver JSON style.
// Listens for ESP-NOW JSON like:
//   {"id":"SPRITE1","cmd":"play","index":7}
//   {"to":"SPRITE1","index":3}   // shorthand = play
//   {"id":"SPRITE1","cmd":"next"}
//
// Wiring (TTL UART; NOT RS-232):
//   ESP32 GND  <-> Sprite GND
//   ESP32 TX17  -> Sprite RX (RED on TRRS/RCA harness)
//   ESP32 RX16 <-  Sprite TX (YELLOW, optional for feedback)
// Set Sprite menu to Serial Control; match baud.

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <ArduinoJson.h>

// ===================== CONFIG =====================
static const char*  DEVICE_ID       = "SPRITE1";
static const uint8_t WIFI_CHANNEL   = 1;           // Must match your receiver
static const unsigned long SPR_BAUD = 9600;        // Must match Sprite menu
static const int SPR_TX_PIN         = 17;          // ESP32 TX -> Sprite RX
static const int SPR_RX_PIN         = 16;          // ESP32 RX <- Sprite TX (optional)
static const uint8_t MAX_FILE_INDEX = 200;         // 000..200.xxx
static const uint8_t STARTUP_INDEX  = 0;           // play this on boot

// Discovery beacons (broadcast hello/ack for visibility)
static const uint32_t HELLO_FAST_MS   = 5000;
static const uint8_t  HELLO_FAST_COUNT= 6;
static const uint32_t HELLO_SLOW_MS   = 60000;

// Duplicate suppression (if sender includes "seq")
static const uint32_t DUP_WINDOW_MS   = 250;
// ==================================================

static uint8_t BROADCAST_ADDR[6] = {0xFF,0xFF,0xFF,0xFF,0xFF,0xFF};
static esp_now_peer_info_t peerBroadcast{};
static uint8_t  currentIndex = STARTUP_INDEX;
static uint8_t  helloCount   = 0;
static uint32_t nextHelloAt  = 0;
static uint32_t lastSeq      = 0;

// ---------------- UART -> Sprite helpers ----------------
inline void spritePlay(uint8_t index) {
  if (index > MAX_FILE_INDEX) index = 0;
  Serial.printf("[UART->Sprite] Sending index=%u (0x%02X)\n", index, index);
  Serial1.write(index);   // single binary byte selects 000..200
  Serial1.flush();
  currentIndex = index;
}

inline void spriteNext() {
  uint8_t next = (currentIndex >= MAX_FILE_INDEX) ? 0 : (currentIndex + 1);
  Serial.printf("[CMD] NEXT -> %u\n", next);
  spritePlay(next);
}

// ---------------- ESPNOW helpers ----------------
static void sendJsonBroadcast(const JsonDocument& doc) {
  static char out[240];
  size_t n = serializeJson(doc, out, sizeof(out));
  if (n > 0 && n < sizeof(out)) {
    esp_err_t e = esp_now_send(BROADCAST_ADDR, reinterpret_cast<const uint8_t*>(out), n);
    if (e != ESP_OK) {
      Serial.printf("[WARN] esp_now_send(broadcast) failed: %d\n", (int)e);
    }
  } else {
    Serial.println("[WARN] serializeJson() produced empty/oversize payload");
  }
}

static void helloTick() {
  uint32_t now = millis();
  if (now < nextHelloAt) return;
  DynamicJsonDocument js(192);
  js["id"]    = DEVICE_ID;
  js["hello"] = true;
  js["fw"]    = "sprite_serial_v1_dbg";
  js["t"]     = now;
  js["index"] = currentIndex;
  sendJsonBroadcast(js);
  helloCount++;
  nextHelloAt = now + (helloCount < HELLO_FAST_COUNT ? HELLO_FAST_MS : HELLO_SLOW_MS);
}

static bool idMatches(const JsonVariantConst& v) {
  if (v.is<const char*>()) {
    const char* s = v.as<const char*>();
    return s && strcasecmp(s, DEVICE_ID) == 0;
  }
  return false;
}

// ---------------- ESPNOW receive (IDF 5.x signature) ----------------
static void onEspNowRecv(const esp_now_recv_info* info, const uint8_t* data, int len) {
  if (!data || len <= 0) return;

  // Print raw ESPNOW payload (best-effort as ASCII)
  Serial.printf("[RX] %d bytes ESPNOW: %.*s\n", len, len, (const char*)data);

  static char buf[ESP_NOW_MAX_DATA_LEN + 1];
  if (len > ESP_NOW_MAX_DATA_LEN) len = ESP_NOW_MAX_DATA_LEN;
  memcpy(buf, data, len);
  buf[len] = '\0';

  DynamicJsonDocument js(512);
  DeserializationError err = deserializeJson(js, buf);
  if (err) {
    Serial.printf("[RX] JSON parse error: %s\n", err.c_str());
    return;
  }

  if (!(idMatches(js["id"]) || idMatches(js["to"]))) {
    Serial.println("[RX] Ignored (wrong ID)");
    return;
  }
  Serial.println("[RX] ID match");

  // Duplicate suppression if 'seq' present
  uint32_t seq = js["seq"] | 0u;
  if (seq != 0) {
    static uint32_t lastSeqTime = 0;
    uint32_t now = millis();
    if (seq == lastSeq && (now - lastSeqTime) < DUP_WINDOW_MS) {
      Serial.printf("[RX] Duplicate seq %lu ignored within %lu ms\n", (unsigned long)seq, (unsigned long)DUP_WINDOW_MS);
      return;
    }
    lastSeq = seq;
    lastSeqTime = now;
  }

  // Interpret commands
  const char* cmd = js["cmd"] | "";
  bool hasIndex = js.containsKey("index") || js.containsKey("file");
  int idx = js["index"] | js["file"] | -1;

  if (strcasecmp(cmd, "next") == 0) {
    Serial.println("[CMD] NEXT");
    spriteNext();
  } else if ((strcasecmp(cmd, "play") == 0 && hasIndex) || (strlen(cmd) == 0 && hasIndex)) {
    if (idx < 0) {
      Serial.println("[CMD] PLAY missing/invalid index");
      return;
    }
    if (idx > (int)MAX_FILE_INDEX) {
      Serial.printf("[CMD] PLAY index %d > %u, clamping to 0\n", idx, MAX_FILE_INDEX);
      idx = 0;
    }
    Serial.printf("[CMD] PLAY %d\n", idx);
    spritePlay((uint8_t)idx);
  } else if (strlen(cmd) == 0) {
    // addressed but no specifics -> advance (kept for backward-compat)
    Serial.println("[CMD] (no cmd, no index) -> NEXT");
    spriteNext();
  } else {
    Serial.printf("[CMD] UNKNOWN: '%s'\n", cmd);
    return;
  }

  // ACK back (broadcast so receiver can hear)
  DynamicJsonDocument ack(192);
  ack["id"]    = DEVICE_ID;
  ack["ack"]   = (strlen(cmd) ? cmd : "next");
  ack["index"] = currentIndex;
  ack["t"]     = millis();
  sendJsonBroadcast(ack);
}

void setup() {
  Serial.begin(115200);
  delay(50);
  Serial.println();
  Serial.println("==================================================");
  Serial.println("[BOOT] Sprite ESP starting...");
  Serial.printf("[BOOT] DEVICE_ID=%s\n", DEVICE_ID);

  // UART to Sprite
  Serial1.begin(SPR_BAUD, SERIAL_8N1, SPR_RX_PIN, SPR_TX_PIN);
  delay(100);
  Serial.printf("[BOOT] UART ready @ %lu baud (TX=%d RX=%d)\n",
                SPR_BAUD, SPR_TX_PIN, SPR_RX_PIN);

  // Send startup index to Sprite
  spritePlay(STARTUP_INDEX);
  Serial.printf("[BOOT] Startup index %u sent to Sprite.\n", STARTUP_INDEX);

  // WiFi/ESP-NOW
  WiFi.mode(WIFI_STA);
  esp_err_t e;

  e = esp_wifi_set_promiscuous(true);
  if (e != ESP_OK) Serial.printf("[WARN] set_promiscuous(true) err=%d\n", (int)e);

  e = esp_wifi_set_channel(WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);
  if (e != ESP_OK) Serial.printf("[ERR] set_channel(%u) err=%d\n", WIFI_CHANNEL, (int)e);
  else Serial.printf("[BOOT] WiFi channel set to %u\n", WIFI_CHANNEL);

  e = esp_wifi_set_promiscuous(false);
  if (e != ESP_OK) Serial.printf("[WARN] set_promiscuous(false) err=%d\n", (int)e);

  if (esp_now_init() != ESP_OK) {
    Serial.println("[ERR] ESP-NOW init failed");
    while (true) { delay(1000); }
  } else {
    Serial.println("[OK] ESP-NOW initialized");
  }

  esp_now_register_recv_cb(onEspNowRecv);

  // Add broadcast peer (for hello + acks)
  memset(&peerBroadcast, 0, sizeof(peerBroadcast));
  memcpy(peerBroadcast.peer_addr, BROADCAST_ADDR, 6);
  peerBroadcast.channel = WIFI_CHANNEL;
  peerBroadcast.ifidx   = WIFI_IF_STA;   // IDF v5 type
  peerBroadcast.encrypt = false;
  e = esp_now_add_peer(&peerBroadcast);
  if (e != ESP_OK && e != ESP_ERR_ESPNOW_EXIST) {
    Serial.printf("[ERR] esp_now_add_peer(broadcast) failed: %d\n", (int)e);
  } else {
    Serial.println("[OK] Broadcast peer ready");
  }

  // First hello immediately
  nextHelloAt = 0;
  helloTick();

  Serial.println("[BOOT] Ready for ESPNOW messages");
  Serial.println("==================================================");
}

void loop() {
  // Optional: print any bytes Sprite sends back (status/echo)
  while (Serial1.available()) {
    int b = Serial1.read();
    Serial.printf("[Sprite<-] 0x%02X\n", b & 0xFF);
  }

  helloTick();
  delay(5);
}
