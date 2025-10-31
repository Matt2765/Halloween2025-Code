// ESP32 Button Node — Broadcast Triple-Send + Deep Sleep on GPIO32
// Wiring: Button between D32 and GND. Power 4xAA -> VIN, GND -> GND.
// Broadcast MAC: FF:FF:FF:FF:FF:FF (no pairing, no encryption)

#include <WiFi.h>
#include <esp_wifi.h>
#include <esp_now.h>
#include "driver/rtc_io.h"

#define BUTTON_PIN 32
#define WAKE_ON_LEVEL_LOW  0

// ====== CONFIGURE THESE ======
const char* DEVICE_ID = "BTN3";     // unique per node
#define ESPNOW_CHANNEL 1                // set to your receiver's ESP-NOW/Wi-Fi channel
#define SEND_RELEASE_EVENT 1            // 1 = also send "pressed=false" after release; 0 = only send press
// ============================

static uint8_t BROADCAST_MAC[] = {0xFF,0xFF,0xFF,0xFF,0xFF,0xFF};

typedef struct __attribute__((packed)) {
  char id[12];
  bool pressed;
  uint32_t seq;
  uint32_t ms_since_boot;
} ButtonMsg;

RTC_DATA_ATTR uint32_t seqCounter = 0;

bool readButtonStable(uint16_t ms_window = 30, uint8_t samples = 5) {
  uint8_t lows = 0, highs = 0;
  for (uint8_t i = 0; i < samples; i++) {
    int v = digitalRead(BUTTON_PIN);
    if (v == LOW) lows++; else highs++;
    delay(ms_window / samples);
  }
  return (lows > highs) ? LOW : HIGH;
}

void ensureRtcPullup() {
  rtc_gpio_deinit((gpio_num_t)BUTTON_PIN);
  rtc_gpio_init((gpio_num_t)BUTTON_PIN);
  rtc_gpio_set_direction((gpio_num_t)BUTTON_PIN, RTC_GPIO_MODE_INPUT_ONLY);
  rtc_gpio_pullup_en((gpio_num_t)BUTTON_PIN);
  rtc_gpio_pulldown_dis((gpio_num_t)BUTTON_PIN);
  rtc_gpio_hold_en((gpio_num_t)BUTTON_PIN);
}

void goToDeepSleepWaitForPress() {
  esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_ALL);
  esp_sleep_enable_ext0_wakeup((gpio_num_t)BUTTON_PIN, WAKE_ON_LEVEL_LOW);
  esp_deep_sleep_start();
}

void sendTriple(const ButtonMsg& msg) {
  // broadcast requires a "peer" on some cores
  esp_now_peer_info_t peer{};
  memcpy(peer.peer_addr, BROADCAST_MAC, 6);
  peer.ifidx = WIFI_IF_STA;
  peer.channel = ESPNOW_CHANNEL;
  peer.encrypt = false;
  if (!esp_now_is_peer_exist(BROADCAST_MAC)) {
    esp_now_add_peer(&peer);
  }

  // Triple send with tiny jitter; no ACK waiting
  for (int i = 0; i < 3; i++) {
    esp_now_send(BROADCAST_MAC, (const uint8_t*)&msg, sizeof(msg));
    delay(8 + (esp_random() % 8)); // 8–15 ms
  }
}

void setup() {
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  ensureRtcPullup();

  esp_sleep_wakeup_cause_t cause = esp_sleep_get_wakeup_cause();

  // If not a button wake (e.g., first power), arm and sleep
  if (cause != ESP_SLEEP_WAKEUP_EXT0) {
    // Avoid immediate re-wake if held during power-up
    if (digitalRead(BUTTON_PIN) == LOW) {
      uint32_t t0 = millis();
      while ((millis() - t0) < 2000 && digitalRead(BUTTON_PIN) == LOW) delay(5);
    }
    goToDeepSleepWaitForPress();
    return;
  }

  // We woke due to LOW on the button — treat as "pressed"
  bool isPressed = (readButtonStable() == LOW);

  // Bring up Wi-Fi STA for ESP-NOW (no AP join)
  WiFi.mode(WIFI_STA);
  // Lock ESP-NOW channel to match receiver
  esp_wifi_set_promiscuous(true); // required by some cores before channel set
  esp_wifi_set_channel(ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(false);

  if (esp_now_init() != ESP_OK) {
    goToDeepSleepWaitForPress();
    return;
  }

  // Optional: reduce TX power a bit to save battery (increase if needed)
  // esp_wifi_set_max_tx_power(40); // ~10 dBm

  // ---- Send "pressed = current state" ----
  ButtonMsg msg{};
  strncpy(msg.id, DEVICE_ID, sizeof(msg.id)-1);
  msg.pressed = isPressed;
  msg.seq = ++seqCounter;
  msg.ms_since_boot = millis();
  sendTriple(msg);

  // ---- Optionally send "released" after button is let go ----
  #if SEND_RELEASE_EVENT
    if (isPressed) {
      uint32_t t0 = millis();
      while ((millis() - t0) < 2000 && digitalRead(BUTTON_PIN) == LOW) delay(5);

      bool nowReleased = (readButtonStable() == HIGH);
      if (nowReleased) {
        ButtonMsg msg2{};
        strncpy(msg2.id, DEVICE_ID, sizeof(msg2.id)-1);
        msg2.pressed = false;
        msg2.seq = ++seqCounter;
        msg2.ms_since_boot = millis();
        sendTriple(msg2);
      }
    }
  #endif

  // Re-arm wake on next press
  goToDeepSleepWaitForPress();
}

void loop() {
  // not used
}
