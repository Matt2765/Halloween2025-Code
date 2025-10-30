// spriteEsp.ino — ESP32 Sprite Trigger Node (ESP-NOW)
// - ID addressed via {"id":"SPRITE1", ...} or {"to":"SPRITE1", ...}
// - Commands:
//     {"cmd":"next"}                  -> pulse DEFAULT_PULSE_MS
//     {"pulse_ms":200}                -> pulse 200 ms
//     {"cmd":"next","pulse_ms":120}   -> pulse 120 ms
//   If addressed with no fields, defaults to "next" (DEFAULT_PULSE_MS).
//
// Wiring (IRLZ44N or small logic N-MOSFET):
//   ESP32 GND  -> MOSFET Source + Sprite GND
//   ESP32 GPIO23 (via ~470Ω) -> MOSFET Gate, with 100k Gate->GND pulldown
//   MOSFET Drain -> Sprite trigger input (e.g., NEXT)
// Trigger logic: drive GPIO HIGH ~150 ms to "press" NEXT (active-low at Sprite via ground).

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <ArduinoJson.h>

// ===================== CONFIG =====================
static const char* DEVICE_ID      = "SPRITE2";
static const uint8_t WIFI_CHANNEL = 1;        // match your network (1..13)
static const int TRIGGER_PIN      = 23;       // MOSFET gate
static const int DEFAULT_PULSE_MS = 150;      // button press length (ms)

// Broadcast cadence: fast at boot for discovery, then slow keepalive
static const uint32_t HELLO_FAST_MS = 5000;
static const uint8_t  HELLO_FAST_COUNT = 6;   // ~30s fast phase
static const uint32_t HELLO_SLOW_MS = 60000;  // ~60s after that

// Duplicate suppression (if sender includes "seq")
static const uint32_t DUP_WINDOW_MS = 250;
// ==================================================

// Broadcast peer
static uint8_t BROADCAST_ADDR[6] = {0xFF,0xFF,0xFF,0xFF,0xFF,0xFF};
static esp_now_peer_info_t peerBroadcast{};

static volatile uint32_t lastPulseAt = 0;
static uint32_t nextHelloAt = 0;
static uint8_t helloCount = 0;
static uint32_t lastSeq = 0;

// ---------- Helpers ----------
static void sendJsonBroadcast(const JsonDocument& doc) {
  static char out[250];
  size_t n = serializeJson(doc, out, sizeof(out));
  if (n > 0 && n < sizeof(out)) {
    esp_now_send(BROADCAST_ADDR, reinterpret_cast<const uint8_t*>(out), n);
  }
}

static void helloTick() {
  uint32_t now = millis();
  if (now < nextHelloAt) return;

  DynamicJsonDocument js(192);
  js["id"]    = DEVICE_ID;
  js["hello"] = true;
  js["fw"]    = "sprite_trigger_v1";
  js["t"]     = now;
  sendJsonBroadcast(js);

  helloCount++;
  nextHelloAt = now + (helloCount < HELLO_FAST_COUNT ? HELLO_FAST_MS : HELLO_SLOW_MS);
}

static void pulseTrigger(int ms) {
  // simple guard against back-to-back chatter
  uint32_t now = millis();
  if (now - lastPulseAt < 50) return;

  digitalWrite(TRIGGER_PIN, HIGH);            // ON -> pull Sprite input to GND via MOSFET
  delay(ms < 20 ? 20 : ms);
  digitalWrite(TRIGGER_PIN, LOW);             // OFF
  lastPulseAt = millis();

  // ACK
  DynamicJsonDocument js(160);
  js["id"]  = DEVICE_ID;
  js["ack"] = "next";
  js["t"]   = lastPulseAt;
  sendJsonBroadcast(js);
}

static bool idMatches(const JsonVariantConst& v) {
  if (v.is<const char*>()) {
    const char* s = v.as<const char*>();
    return s && strcasecmp(s, DEVICE_ID) == 0;
  }
  return false;
}

// ----- ESP-NOW Receive (IDF 5.x signature) -----
static void onEspNowRecv(const esp_now_recv_info* info, const uint8_t* data, int len) {
  // Ensure bounded, null-terminated buffer for JSON parse
  static char buf[ESP_NOW_MAX_DATA_LEN + 1];
  if (len <= 0) return;
  if (len > ESP_NOW_MAX_DATA_LEN) len = ESP_NOW_MAX_DATA_LEN;
  memcpy(buf, data, len);
  buf[len] = '\0';

  DynamicJsonDocument js(512);
  DeserializationError err = deserializeJson(js, buf);
  if (err) {
    // malformed payload; ignore
    return;
  }

  // Addressing: accept {"id":"SPRITE1",...} or {"to":"SPRITE1",...}
  if (!(idMatches(js["id"]) || idMatches(js["to"]))) return;

  // Duplicate suppression if sender includes "seq"
  uint32_t seq = js["seq"] | 0u;
  if (seq != 0) {
    // very basic filter: ignore exact repeat within short window
    static uint32_t lastSeqTime = 0;
    uint32_t now = millis();
    if (seq == lastSeq && (now - lastSeqTime) < DUP_WINDOW_MS) return;
    lastSeq = seq;
    lastSeqTime = now;
  }

  // Interpret command
  const char* cmd = js["cmd"] | "";
  int ms = js["pulse_ms"] | DEFAULT_PULSE_MS;

  // Default behavior: pulse if addressed even with empty cmd
  if (strcasecmp(cmd, "next") == 0 || js.containsKey("pulse_ms") || strlen(cmd) == 0) {
    pulseTrigger(ms);
  }
}

void setup() {
  pinMode(TRIGGER_PIN, OUTPUT);
  digitalWrite(TRIGGER_PIN, LOW);   // MOSFET off

  Serial.begin(115200);
  delay(50);

  WiFi.mode(WIFI_STA);

  // Lock ESPNOW to your chosen channel
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(false);

  if (esp_now_init() != ESP_OK) {
    Serial.println("ESP-NOW init failed");
    while (true) { delay(1000); }
  }

  // Register new-style callback (IDF 5.x)
  esp_now_register_recv_cb(onEspNowRecv);

  // Add broadcast peer (for hello + acks)
  memset(&peerBroadcast, 0, sizeof(peerBroadcast));
  memcpy(peerBroadcast.peer_addr, BROADCAST_ADDR, 6);
  peerBroadcast.channel = WIFI_CHANNEL;
  peerBroadcast.ifidx   = WIFI_IF_STA;   // (was ESP_IF_WIFI_STA on older cores)
  peerBroadcast.encrypt = false;
  esp_now_add_peer(&peerBroadcast);

  // Kick off first hello immediately; then fast cadence
  nextHelloAt = 0;
  helloTick();
}

void loop() {
  helloTick();
  delay(5);
}
