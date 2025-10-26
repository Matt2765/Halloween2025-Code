// File: DimmerUno_Serial.ino
// Board: Arduino UNO
// Library: RBDdimmer (RobotDyn)
// Wiring: Z-C -> D2 (INT0), GATE/OUT -> D3

#include <Arduino.h>
#include <RBDdimmer.h>

const int PIN_DIM = 3;   // triac gate pin
// ZC is implicitly D2 for UNO in this library

dimmerLamp dimmer(PIN_DIM);  // one-arg ctor per your library

const uint8_t CEILING = 93;      // 93% is treated as "100%"
const uint8_t SAFETY_FLOOR = 15; // starting guess; find true floor with RAW

String inLine;

static inline void applyRaw(uint8_t raw) {
  if (raw > 100) raw = 100;
  dimmer.setPower(raw);
}

static inline void applyScaled(uint8_t pct) {
  if (pct == 0) { dimmer.setPower(0); return; }
  if (pct > 100) pct = 100;
  uint8_t raw = (uint8_t)((pct * CEILING + 50) / 100); // round
  if (raw > 0 && raw < SAFETY_FLOOR) raw = SAFETY_FLOOR;
  dimmer.setPower(raw);
}

void setup() {
  Serial.begin(115200);
  while (!Serial) { ; }

  dimmer.begin(NORMAL_MODE, ON); // library handles ZC on D2
  applyScaled(SAFETY_FLOOR);

  Serial.println(F("PWR:READY"));
  Serial.print(F("INFO RBDDIMMER UNO PIN=")); Serial.print(PIN_DIM);
  Serial.print(F(" ZC=D2 CEILING=")); Serial.print(CEILING);
  Serial.print(F(" FLOOR~")); Serial.println(SAFETY_FLOOR);
}

void loop() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      inLine.trim();
      if (inLine.length()) {
        if (inLine.startsWith("SET ")) {
          int v = inLine.substring(4).toInt();
          if (v < 0) v = 0; if (v > 100) v = 100;
          applyScaled((uint8_t)v);
          Serial.print(F("ACK SET ")); Serial.println(v);
        } else if (inLine.startsWith("RAW ")) {
          int v = inLine.substring(4).toInt();
          if (v < 0) v = 0; if (v > 100) v = 100;
          applyRaw((uint8_t)v);
          Serial.print(F("ACK RAW ")); Serial.println(v);
        } else if (inLine.equalsIgnoreCase("PING")) {
          Serial.println(F("PONG"));
        } else if (inLine.equalsIgnoreCase("INFO")) {
          Serial.print(F("INFO RBDDIMMER UNO PIN=")); Serial.print(PIN_DIM);
          Serial.print(F(" ZC=D2 CEILING=")); Serial.print(CEILING);
          Serial.print(F(" FLOOR~")); Serial.println(SAFETY_FLOOR);
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
