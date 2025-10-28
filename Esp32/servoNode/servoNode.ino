// servoNode.ino
// ESP32 ESP-NOW servo node (angle-only) with smoothing ramp,
// persistent default (NVS), and 2 Hz status beacons.
// - HARD-CODE boot default with DEFAULT_ANGLE (compile-time)
// - UPDATE default over-the-air with: {"id":"SERVO1","set_default":<deg>}
// - Normal moves: {"id":"SERVO1","angle":<deg>[,"ramp_ms":<ms>]}
// - Status beacons (2 Hz) broadcast: {"type":"servo","id":"SERVO1","angle":...,"us":...,"ramp_ms_remaining":...,"seq":...,"ts":...}

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include "esp_idf_version.h"
#include "driver/ledc.h"
#include <math.h>
#include <Preferences.h>

// ---------- User config ----------
#define SERVO_ID          "SERVO1"
#define SERVO_PIN         18
#define DEFAULT_ANGLE     90          // <---- HARD-CODED BOOT DEFAULT (deg)
#define DEFAULT_RAMP_MS   300
#define ESPNOW_CHANNEL    1

// Servo pulse (tune to your servo if needed)
#define SERVO_MIN_US      500
#define SERVO_MAX_US      2500

// ---------- Status beacon (2 Hz) ----------
#define STATUS_PERIOD_MS  500         // 2x per second
static uint32_t lastStatusMs = 0;
static uint32_t statusSeq = 0;

// ---------- LEDC (IDF API) ----------
#define LEDC_TIMER_NUM        LEDC_TIMER_0
#define LEDC_MODE             LEDC_HIGH_SPEED_MODE
#define LEDC_CHANNEL_NUM      LEDC_CHANNEL_0
#define LEDC_FREQ_HZ          50
#define LEDC_RES_BITS         LEDC_TIMER_16_BIT
static const uint32_t LEDC_TOP = (1u << 16) - 1;
static const float PERIOD_US = 1000000.0f / (float)LEDC_FREQ_HZ; // 20000 us

static const uint8_t BROADCAST_MAC[6] = {0xFF,0xFF,0xFF,0xFF,0xFF,0xFF};

// ---------- State ----------
static volatile uint32_t lastUpdateMs = 0;

// Ramping state (microseconds)
static int cur_us = 1500;
static int start_us = 1500;
static int target_us = 1500;
static uint32_t ramp_start_ms = 0;
static uint32_t ramp_end_ms   = 0;

// Persistent default angle
Preferences prefs;
static int boot_angle_deg = DEFAULT_ANGLE;   // loaded from NVS or DEFAULT_ANGLE

// ---------- Helpers ----------
static inline int clampi(int v, int lo, int hi) {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}

static inline uint32_t us_to_duty(int us) {
  us = clampi(us, SERVO_MIN_US, SERVO_MAX_US);
  float dutyF = ((float)us / PERIOD_US) * (float)LEDC_TOP;
  if (dutyF < 0) dutyF = 0;
  if (dutyF > LEDC_TOP) dutyF = LEDC_TOP;
  return (uint32_t)(dutyF + 0.5f);
}

static inline int angle_to_us(int deg) {
  deg = clampi(deg, 0, 180);
  return SERVO_MIN_US + (int)((SERVO_MAX_US - SERVO_MIN_US) * (deg / 180.0f));
}

static void ledc_write_us(int us) {
  cur_us = clampi(us, SERVO_MIN_US, SERVO_MAX_US);
  uint32_t duty = us_to_duty(cur_us);
  ledc_set_duty(LEDC_MODE, LEDC_CHANNEL_NUM, duty);
  ledc_update_duty(LEDC_MODE, LEDC_CHANNEL_NUM);
}

static void start_ramp_to(int new_target_us, uint32_t ramp_ms) {
  start_us = cur_us;
  target_us = clampi(new_target_us, SERVO_MIN_US, SERVO_MAX_US);
  uint32_t now = millis();
  ramp_start_ms = now;
  ramp_end_ms = now + (ramp_ms == 0 ? 1u : ramp_ms);
}

// Naive JSON (no ArduinoJson)
static bool json_find_int(const char* s, const char* key, int* outVal) {
  if (!s || !key || !outVal) return false;
  const char* k = strstr(s, key);
  if (!k) return false;
  const char* colon = strchr(k, ':');
  if (!colon) return false;
  const char* p = colon + 1;
  while (*p==' '||*p=='\t') p++;
  if (*p=='\"') p++; // tolerate quoted numbers
  bool neg=false; if (*p=='-'){neg=true; p++;}
  int val=0; bool any=false;
  while (*p>='0' && *p<='9') { any=true; val = val*10 + (*p - '0'); p++; }
  if (!any) return false;
  if (neg) val = -val;
  *outVal = val;
  return true;
}

static bool json_id_matches(const char* s, const char* myId) {
  if (!s || !myId) return false;
  const char* idk = strstr(s, "\"id\"");
  if (!idk) return false;
  const char* colon = strchr(idk, ':');
  if (!colon) return false;
  const char* q1 = strchr(colon, '\"');
  if (!q1) return false;
  const char* q2 = strchr(q1+1, '\"');
  if (!q2 || q2 <= q1+1) return false;
  char buf[32]; size_t n = (size_t)(q2 - (q1 + 1));
  if (n >= sizeof(buf)) n = sizeof(buf) - 1;
  memcpy(buf, q1+1, n); buf[n] = '\0';
  return (strcmp(buf, myId) == 0);
}

static bool ensurePeer(const uint8_t mac[6]) {
  if (esp_now_is_peer_exist(mac)) return true;
  esp_now_peer_info_t peer{};
  memcpy(peer.peer_addr, mac, 6);
  peer.channel = ESPNOW_CHANNEL;
  peer.encrypt = false;
  return (esp_now_add_peer(&peer) == ESP_OK);
}

// ---------- Persistent default (NVS) ----------
static void load_boot_angle() {
  if (!prefs.begin("servo", /*readOnly=*/true)) {
    boot_angle_deg = DEFAULT_ANGLE;
    return;
  }
  boot_angle_deg = prefs.getInt("boot_deg", DEFAULT_ANGLE);
  prefs.end();
  boot_angle_deg = clampi(boot_angle_deg, 0, 180);
}

static void save_boot_angle(int deg) {
  deg = clampi(deg, 0, 180);
  if (!prefs.begin("servo", /*readOnly=*/false)) return;
  prefs.putInt("boot_deg", deg);
  prefs.end();
  boot_angle_deg = deg;
}

// ---------- Status beacon ----------
static void send_status(bool boot)
{
  // Compute current angle from cur_us
  int cur_deg = (int)lroundf( ((cur_us - SERVO_MIN_US) * 180.0f) / (SERVO_MAX_US - SERVO_MIN_US) );
  cur_deg = clampi(cur_deg, 0, 180);

  // Ramp remaining
  uint32_t now = millis();
  uint32_t rem = (now < ramp_end_ms) ? (ramp_end_ms - now) : 0;

  char msg[192];
  snprintf(msg, sizeof(msg),
    "{\"type\":\"servo\",\"id\":\"%s\",\"angle\":%d,\"us\":%d,"
    "\"ramp_ms_remaining\":%lu,\"seq\":%lu,\"ts\":%lu%s}",
    SERVO_ID, cur_deg, cur_us,
    (unsigned long)rem,
    (unsigned long)(statusSeq++),
    (unsigned long)now,
    boot ? ",\"boot\":true" : ""
  );

  if (ensurePeer(BROADCAST_MAC)) {
    esp_now_send(BROADCAST_MAC, (const uint8_t*)msg, strlen(msg));
  }
}

// ---------- Packet handler ----------
static void handle_packet(const uint8_t* /*mac*/, const uint8_t* data, int len) {
  if (!data || len <= 0) return;
  if (data[0] != '{') return;

  char buf[256];
  int n = len; if (n >= (int)sizeof(buf)) n = (int)sizeof(buf) - 1;
  memcpy(buf, data, n); buf[n] = '\0';

  if (!json_id_matches(buf, SERVO_ID)) return;

  // 1) Persist new default: {"set_default":<deg>}
  int set_def = -1;
  if (json_find_int(buf, "\"set_default\"", &set_def)) {
    save_boot_angle(set_def);
    // Move there now with a quick ramp so position matches the new default
    start_ramp_to(angle_to_us(set_def), 300);
    lastUpdateMs = millis();
    send_status(false);   // immediate status after command
    return;
  }

  // 2) Normal commanded move: {"angle":<deg>[,"ramp_ms":N]}
  int angle = -1;
  if (json_find_int(buf, "\"angle\"", &angle)) {
    int ramp_ms = DEFAULT_RAMP_MS;
    (void)json_find_int(buf, "\"ramp_ms\"", &ramp_ms);
    ramp_ms = clampi(ramp_ms, 0, 30000);
    int goal_us = angle_to_us(angle);
    if (ramp_ms == 0) {
      ledc_write_us(goal_us);
    } else {
      start_ramp_to(goal_us, (uint32_t)ramp_ms);
    }
    lastUpdateMs = millis();
    send_status(false);   // immediate status after command
  }
}

// ---------- Callbacks for IDF v5 vs v4 ----------
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5,0,0)
static void onDataRecvNew(const esp_now_recv_info *info, const uint8_t *data, int len) {
  if (!info) return;
  handle_packet(info->src_addr, data, len);
}
#else
static void onDataRecvOld(const uint8_t *mac, const uint8_t *data, int len) {
  handle_packet(mac, data, len);
}
#endif

// ---------- Bring-up ----------
static bool bringUpWifiEspNow() {
  WiFi.mode(WIFI_STA);
  delay(20);

  if (esp_wifi_set_promiscuous(true) != ESP_OK) return false;
  if (esp_wifi_set_channel(ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE) != ESP_OK) return false;
  if (esp_wifi_set_promiscuous(false) != ESP_OK) return false;

  if (esp_now_init() != ESP_OK) return false;

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5,0,0)
  esp_now_register_recv_cb(onDataRecvNew);
#else
  esp_now_register_recv_cb(onDataRecvOld);
#endif
  return true;
}

// ---------- Arduino entry ----------
void setup() {
  // LEDC timer
  ledc_timer_config_t tcfg{};
  tcfg.speed_mode       = LEDC_MODE;
  tcfg.timer_num        = LEDC_TIMER_NUM;
  tcfg.duty_resolution  = LEDC_RES_BITS;
  tcfg.freq_hz          = LEDC_FREQ_HZ;
  tcfg.clk_cfg          = LEDC_AUTO_CLK;
  ledc_timer_config(&tcfg);

  // LEDC channel
  ledc_channel_config_t ccfg{};
  ccfg.gpio_num   = SERVO_PIN;
  ccfg.speed_mode = LEDC_MODE;
  ccfg.channel    = LEDC_CHANNEL_NUM;
  ccfg.intr_type  = LEDC_INTR_DISABLE;
  ccfg.timer_sel  = LEDC_TIMER_NUM;
  ccfg.duty       = 0; // set precisely after we load the boot angle
  ccfg.hpoint     = 0;
  ledc_channel_config(&ccfg);

  // Load persisted default; fall back to compile-time DEFAULT_ANGLE
  load_boot_angle();
  int boot_us = angle_to_us(boot_angle_deg);
  cur_us = start_us = target_us = boot_us;
  ledc_write_us(boot_us);

  // ESP-NOW
  for (int attempt=1;;++attempt) {
    if (bringUpWifiEspNow()) break;
    delay(400);
  }

  // Send boot status so the bridge learns id->mac immediately
  send_status(true);
  lastUpdateMs = millis();
}

void loop() {
  // Smooth ramp (linear)
  uint32_t now = millis();
  if (now < ramp_end_ms) {
    float t = (float)(now - ramp_start_ms) / (float)(ramp_end_ms - ramp_start_ms);
    if (t < 0) t = 0;
    if (t > 1) t = 1;
    int us = start_us + (int)lroundf((target_us - start_us) * t);
    if (us != cur_us) ledc_write_us(us);
  } else if (cur_us != target_us) {
    ledc_write_us(target_us);
  }

  // Periodic status (2 Hz)
  if ((uint32_t)(now - lastStatusMs) >= STATUS_PERIOD_MS) {
    send_status(false);
    lastStatusMs = now;
  }

  // Optional failsafe:
  // if ((uint32_t)(now - lastUpdateMs) > 15000) {
  //   start_ramp_to(angle_to_us(boot_angle_deg), 500);
  //   lastUpdateMs = now;
  // }

  delay(5);
}
