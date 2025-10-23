// ESP32 Multi-Button (no rewiring) — ANY button wakes from LIGHT SLEEP (GPIO LOW)
// Wiring: each button -> GND (LOW = pressed). Pins are INPUT_PULLUP.
// Fix: make the sleep → wake → send cycle repeat in loop(), not just once in setup().

#include <WiFi.h>
#include <esp_wifi.h>
#include <esp_now.h>
#include "driver/gpio.h"

#define SEND_RELEASE_EVENT 1
#define ESPNOW_CHANNEL     1
const char* DEVICE_ID = "Multi_BTN1";

// Set your button GPIOs here (wired to GND)
static const int BTN_PINS[] = {26, 25, 33, 32};
static const int BTN_COUNT  = sizeof(BTN_PINS) / sizeof(BTN_PINS[0]);

static bool espnow_ready = false;
static uint32_t seqCounter = 0;
static const uint8_t BROADCAST_MAC[6] = {0xFF,0xFF,0xFF,0xFF,0xFF,0xFF};

typedef struct __attribute__((packed)) {
  char id[16];
  uint8_t btn;         // 1..N
  bool pressed;        // true on press, false on release
  uint32_t seq;
  uint32_t ms_since_boot;
} ButtonMsg;

static void radio_on() {
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  esp_wifi_start();
  esp_wifi_set_ps(WIFI_PS_NONE);
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(false);

  if (!espnow_ready) {
    if (esp_now_init() != ESP_OK) return;
    // ensure broadcast peer
    esp_now_peer_info_t peer{}; memset(&peer, 0, sizeof(peer));
    memcpy(peer.peer_addr, BROADCAST_MAC, 6);
    peer.channel = ESPNOW_CHANNEL; peer.encrypt = false;
    if (!esp_now_is_peer_exist(BROADCAST_MAC)) esp_now_add_peer(&peer);
    espnow_ready = true;
  }
}

static void radio_off() {
  if (espnow_ready) {
    esp_now_deinit();
    espnow_ready = false;
  }
  esp_wifi_stop();
}

static int digitalReadStable(int pin, uint16_t ms_window = 30, uint8_t samples = 5) {
  uint8_t lows=0, highs=0;
  for (uint8_t i=0;i<samples;i++) {
    (digitalRead(pin)==LOW ? lows : highs)++;
    delay(ms_window / samples);
  }
  return (lows > highs) ? LOW : HIGH;
}

static void configureInputsPullup() {
  for (int i=0;i<BTN_COUNT;i++) pinMode(BTN_PINS[i], INPUT_PULLUP);
}

static void waitAllReleased(uint32_t ms=600) {
  uint32_t t0 = millis();
  while ((millis() - t0) < ms) {
    bool anyLow=false;
    for (int i=0;i<BTN_COUNT;i++) { if (digitalRead(BTN_PINS[i])==LOW) { anyLow=true; break; } }
    if (!anyLow) break;
    delay(5);
  }
}

static void sendTriple(const ButtonMsg& msg) {
  for (int i=0;i<3;i++) {
    esp_now_send(BROADCAST_MAC, (const uint8_t*)&msg, sizeof(msg));
    delay(8 + (esp_random() % 8));
  }
}

static void armLightSleepAnyLow_andSleep() {
  esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_ALL);
  for (int i=0;i<BTN_COUNT;i++) {
    gpio_wakeup_disable((gpio_num_t)BTN_PINS[i]);
    gpio_wakeup_enable((gpio_num_t)BTN_PINS[i], GPIO_INTR_LOW_LEVEL);
  }
  esp_sleep_enable_gpio_wakeup();
  esp_sleep_pd_config(ESP_PD_DOMAIN_RTC_PERIPH, ESP_PD_OPTION_ON);
  esp_light_sleep_start();   // returns when any pin goes LOW
}

// Handle exactly one wake/press cycle then return (caller will loop)
static void handle_one_wake_cycle() {
  // Sleep until any button goes LOW
  armLightSleepAnyLow_andSleep();

  // Debounce which buttons are pressed (no radio yet)
  bool pressedMask[BTN_COUNT] = {false};
  bool anyPressed = false;
  for (int i=0;i<BTN_COUNT;i++) {
    if (digitalReadStable(BTN_PINS[i]) == LOW) {
      pressedMask[i] = true;
      anyPressed = true;
    }
  }
  if (!anyPressed) return; // spurious wake

  // Bring up radio and send press events for all currently pressed buttons
  radio_on();
  for (int i=0;i<BTN_COUNT;i++) {
    if (!pressedMask[i]) continue;
    ButtonMsg msg{};
    strncpy(msg.id, DEVICE_ID, sizeof(msg.id)-1);
    msg.btn = i + 1;
    msg.pressed = true;
    msg.seq = ++seqCounter;
    msg.ms_since_boot = millis();
    sendTriple(msg);
  }

#if SEND_RELEASE_EVENT
  // Wait briefly for release(s) and send release events
  uint32_t overallStart = millis();
  for (int i=0;i<BTN_COUNT;i++) {
    if (!pressedMask[i]) continue;
    uint32_t t0 = millis();
    while ((millis() - t0) < 2000 && digitalRead(BTN_PINS[i]) == LOW) delay(5);
    if (digitalReadStable(BTN_PINS[i]) == HIGH) {
      ButtonMsg msg2{};
      strncpy(msg2.id, DEVICE_ID, sizeof(msg2.id)-1);
      msg2.btn = i + 1;
      msg2.pressed = false;
      msg2.seq = ++seqCounter;
      msg2.ms_since_boot = millis();
      sendTriple(msg2);
    }
    if ((millis() - overallStart) > 2500) break;
  }
#endif

  radio_off();
  // Guard to avoid immediate re-wake if a button is still held
  waitAllReleased(300);
}

void setup() {
  configureInputsPullup();
  waitAllReleased(400); // avoid boot loops if a button is held
}

void loop() {
  handle_one_wake_cycle(); // repeats forever
  delay(5);
}
