// File: DimmerUno_Serial.ino
// Board: Arduino UNO
// Library: RBDdimmer (RobotDyn)
// Wiring: Z-C -> D2 (INT0), GATE/OUT -> D3
//
// Simplified version:
// - SET 0..100 linearly maps to RAW 32..90
// - RAW command still sets direct raw power
// - No smoothing, no fancy logic

#include <Arduino.h>
#include <RBDdimmer.h>

const int PIN_DIM = 3;   // triac gate pin
dimmerLamp dimmer(PIN_DIM);

// Scale limits
const uint8_t RAW_MIN = 32;
const uint8_t RAW_MAX = 90;

String inLine;

static inline uint8_t clamp100(int v) {
  if (v < 0) return 0;
  if (v > 100) return 100;
  return (uint8_t)v;
}

// Linear map: 0–100 → RAW_MIN–RAW_MAX
static inline uint8_t scale_pct_to_raw(uint8_t pct) {
  if (pct == 0) return RAW_MIN;
  return (uint8_t)(RAW_MIN + ((RAW_MAX - RAW_MIN) * pct) / 100);
}

void setup() {
  Serial.begin(115200);
  while (!Serial) {;}

  dimmer.begin(NORMAL_MODE, ON);
  dimmer.setPower(RAW_MIN);

  Serial.println(F("PWR:READY"));
  Serial.print(F("INFO RBDDIMMER UNO PIN=")); Serial.print(PIN_DIM);
  Serial.print(F(" ZC=D2 RANGE=")); Serial.print(RAW_MIN);
  Serial.print(F("-")); Serial.println(RAW_MAX);
}

void loop() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      inLine.trim();
      if (inLine.length()) {
        if (inLine.startsWith("SET ")) {
          int v = clamp100(inLine.substring(4).toInt());
          uint8_t raw = scale_pct_to_raw((uint8_t)v);
          dimmer.setPower(raw);
          Serial.print(F("ACK SET ")); Serial.print(v);
          Serial.print(F(" -> RAW ")); Serial.println(raw);
        } else if (inLine.startsWith("RAW ")) {
          int v = inLine.substring(4).toInt();
          if (v < 0) v = 0; if (v > 100) v = 100;
          dimmer.setPower((uint8_t)v);
          Serial.print(F("ACK RAW ")); Serial.println(v);
        } else if (inLine.equalsIgnoreCase("PING")) {
          Serial.println(F("PONG"));
        } else if (inLine.equalsIgnoreCase("INFO")) {
          Serial.print(F("INFO RBDDIMMER UNO PIN=")); Serial.print(PIN_DIM);
          Serial.print(F(" ZC=D2 RANGE=")); Serial.print(RAW_MIN);
          Serial.print(F("-")); Serial.println(RAW_MAX);
        } else {
          Serial.print(F("ERR ")); Serial.println(inLine);
        }
      }
      inLine = "";
    } else {
      inLine += c;
    }
  }
}
