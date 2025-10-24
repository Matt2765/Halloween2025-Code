/* ------------------------------------------------------------
 * dimmerNano_dimmableLight.ino  (8 channels, Dimmable-Light lib)
 * - Zero-cross on D2 (INT0)
 * - Channels on D3..D10 (active-HIGH gate)
 * - Serial 115200 (newline):
 *     PING            -> PONG
 *     INFO            -> HALF_US, LEVELS, ACTIVE_HIGH
 *     D,<ch>,<0-100>  -> OK
 *     A,<8 values>    -> OK
 * ------------------------------------------------------------ */

#include <Arduino.h>
#include <dimmable_light.h>   // correct header for "Dimmable Light for Arduino" lib

// ---- Pins ----
const uint8_t PIN_ZC = 2;                     // Zero-cross on D2
const uint8_t CH_PINS[8] = {3,4,5,6,7,8,9,10};
const bool    ACTIVE_HIGH = true;             // Gate pulses are active-HIGH

// ---- Create 8 distinct, non-copyable objects ----
DimmableLight dim0(CH_PINS[0]);
DimmableLight dim1(CH_PINS[1]);
DimmableLight dim2(CH_PINS[2]);
DimmableLight dim3(CH_PINS[3]);
DimmableLight dim4(CH_PINS[4]);
DimmableLight dim5(CH_PINS[5]);
DimmableLight dim6(CH_PINS[6]);
DimmableLight dim7(CH_PINS[7]);

// Pointer array so we can loop cleanly without copies
DimmableLight* dim[8] = { &dim0,&dim1,&dim2,&dim3,&dim4,&dim5,&dim6,&dim7 };

// ---- State ----
uint8_t levelPct[8] = {0};                    // echo for INFO

// Optional per-channel LED floors (helps some triac-dimmable LEDs latch).
// Set to 0 for incandescent-only channels if desired.
uint8_t LED_FLOOR[8] = {8,8,8,8,8,8,8,8};

// ---- Helpers ----
inline uint8_t clamp100(int v){ return (v<0)?0:((v>100)?100:(uint8_t)v); }
inline uint8_t pctToLib(uint8_t pct){ return (uint8_t)((pct * 255 + 50) / 100); }

void applySingle(uint8_t ch1, uint8_t pct){
  if (ch1 < 1 || ch1 > 8) return;
  uint8_t i = ch1 - 1;
  pct = clamp100(pct);
  if (pct > 0 && pct < LED_FLOOR[i]) pct = LED_FLOOR[i];
  levelPct[i] = pct;
  dim[i]->setBrightness(pctToLib(pct));
}

void applyAll(const uint8_t vals[8]){
  for (uint8_t i=0;i<8;i++){
    uint8_t p = clamp100(vals[i]);
    if (p > 0 && p < LED_FLOOR[i]) p = LED_FLOOR[i];
    levelPct[i] = p;
    dim[i]->setBrightness(pctToLib(p));
  }
}

void sendInfo(){
  // Library tracks mains timing internally; we report nominal half-cycle for compatibility.
  Serial.print("HALF_US=8333 LEVELS=");
  for (uint8_t i=0;i<8;i++){ Serial.print(levelPct[i]); if (i<7) Serial.print(','); }
  Serial.print(" ACTIVE_HIGH="); Serial.println(ACTIVE_HIGH ? "1" : "0");
}

void handleLine(String s){
  s.trim(); if (!s.length()) return;

  if (s.equalsIgnoreCase("PING")) { Serial.println("PONG"); return; }
  if (s.equalsIgnoreCase("INFO")) { sendInfo(); return; }

  if (s.charAt(0)=='D' || s.charAt(0)=='d'){
    int c1 = s.indexOf(','), c2 = s.indexOf(',', c1+1);
    if (c1>0 && c2>c1) {
      int ch = s.substring(c1+1, c2).toInt();
      int lv = s.substring(c2+1).toInt();
      applySingle((uint8_t)ch, (uint8_t)lv);
      Serial.println("OK"); return;
    }
  }

  if (s.charAt(0)=='A' || s.charAt(0)=='a'){
    if (s.length()>=3 && s.charAt(1)==','){
      uint8_t vals[8]; uint8_t n=0; int start=2;
      while (n<8){
        int sep = s.indexOf(',', start);
        String tok = (sep==-1)? s.substring(start) : s.substring(start, sep);
        tok.trim(); vals[n++] = (uint8_t)tok.toInt();
        if (sep==-1) break; start = sep+1;
      }
      if (n==8){ applyAll(vals); Serial.println("OK"); return; }
    }
  }

  Serial.println("ERR");
}

void setup(){
  Serial.begin(115200);
  delay(300);
  Serial.println("READY dimmerNano_dimmableLight");

  // Required by the library
  DimmableLight::setSyncPin(PIN_ZC);   // ZC on D2
  DimmableLight::begin();              // start scheduler/ISRs

  // Initialize all channels off
  for (uint8_t i=0;i<8;i++){
    pinMode(CH_PINS[i], OUTPUT);       // not strictly needed; safe
    dim[i]->setBrightness(0);
    levelPct[i] = 0;
  }
}

void loop(){
  static String line;
  while (Serial.available()){
    char c=(char)Serial.read();
    if (c=='\n' || c=='\r'){ if (line.length()){ handleLine(line); line=""; } }
    else if (line.length() < 160) line += c;
  }
}
