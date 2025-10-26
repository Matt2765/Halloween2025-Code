// UNO + RBDDimmer (2 channels) with tiny update stagger
// Commands (newline-terminated):
//   PING
//   INFO
//   STATUS
//   D,<ch>,<0-100>
//   A,<v1>,<v2>            // extendable if you add more channels

#include <RBDdimmer.h>   // Library Manager: "RBDDimmer"

// ------------ Pins ------------
#define PIN_ZC   2       // INT0 - required by many examples
#define PIN_CH1  3
#define PIN_CH2  5
// If you add more channels, pick any digital pins for the triac gates.

// ------------ Config ------------
#define USE_PERCEPTUAL_CURVE  1      // 1=on (nicer low end)
#define STAGGER_US            300    // microseconds between channel updates (0 to disable)

// ------------ Objects ------------
dimmerLamp dim1(PIN_CH1, PIN_ZC);
dimmerLamp dim2(PIN_CH2, PIN_ZC);
static const uint8_t N_CH = 2;

// Current targets (0..100)
uint8_t levels[2] = {0, 0};

// Helpers
static inline uint8_t clamp01(int v){ return (v<0)?0:((v>100)?100:v); }

// Simple perceptual mapping (human brightness)
// linear -> square; tweak if you prefer another curve
static inline uint8_t perceptual(uint8_t pct){
  #if USE_PERCEPTUAL_CURVE
    float p = pct / 100.0f;
    p = p * p;                    // gamma ~2
    int out = (int)(p * 100.0f + 0.5f);
    if (out < 0) out = 0;
    if (out > 100) out = 100;
    return (uint8_t)out;
  #else
    return pct;
  #endif
}

void apply_levels(){
  // Apply with a tiny stagger to avoid simultaneous gate pulses on some boards.
  // NOTE: RBDDimmer schedules firing off the ZC ISR internally, this just spaces out *updates*.
  uint8_t p0 = perceptual(levels[0]);
  dim1.setPower(p0);
  if (STAGGER_US > 0) delayMicroseconds(STAGGER_US);

  uint8_t p1 = perceptual(levels[1]);
  dim2.setPower(p1);
  // If you add more channels, continue the pattern with small delays between setPower calls.
}

void print_info(){
  Serial.println(F("UNO + RBDDimmer"));
  Serial.print(F("ZC pin: ")); Serial.println(PIN_ZC);
  Serial.print(F("CH pins: ")); Serial.print(PIN_CH1); Serial.print(F(", ")); Serial.println(PIN_CH2);
  Serial.print(F("Stagger (us): ")); Serial.println(STAGGER_US);
  Serial.print(F("Perceptual curve: ")); Serial.println(USE_PERCEPTUAL_CURVE ? F("ON") : F("OFF"));
}

void setup(){
  Serial.begin(115200);
  while(!Serial){;}
  delay(50);
  Serial.println(F("\n[Dimmer UNO] Starting"));

  dim1.begin(NORMAL_MODE, ON);
  dim2.begin(NORMAL_MODE, ON);

  // Optional: ensure both start at 0
  levels[0] = 0; levels[1] = 0;
  apply_levels();

  print_info();
  Serial.println(F("[Dimmer UNO] Ready. Commands: PING, INFO, STATUS, D,<ch>,<0-100>, A,<v1>,<v2>"));
}

void loop(){
  // --- Serial parsing ---
  if (Serial.available()){
    String line = Serial.readStringUntil('\n'); line.trim();
    if (!line.length()) return;

    if (line.equalsIgnoreCase(F("PING"))){
      Serial.println(F("PONG"));
    }
    else if (line.equalsIgnoreCase(F("INFO"))){
      print_info();
    }
    else if (line.equalsIgnoreCase(F("STATUS"))){
      Serial.print(F("CH1=")); Serial.print(levels[0]); Serial.print(F("% "));
      Serial.print(F("CH2=")); Serial.print(levels[1]); Serial.println(F("%"));
    }
    else if (line.startsWith(F("D,"))){
      int ch, lvl;
      if (sscanf(line.c_str(), "D,%d,%d", &ch, &lvl) == 2 && ch >= 1 && ch <= N_CH){
        uint8_t v = clamp01(lvl);
        levels[ch-1] = v;
        apply_levels();
        Serial.print(F("OK CH")); Serial.print(ch); Serial.print('='); Serial.print((int)v); Serial.println('%');
      } else {
        Serial.println(F("ERR D syntax. Use: D,<ch 1..2>,<0..100>"));
      }
    }
    else if (line.startsWith(F("A,"))){
      int v0, v1;
      if (sscanf(line.c_str(), "A,%d,%d", &v0, &v1) == 2){
        levels[0] = clamp01(v0);
        levels[1] = clamp01(v1);
        apply_levels();
        Serial.println(F("OK"));
      } else {
        Serial.println(F("ERR A syntax. Use: A,<v1>,<v2>"));
      }
    }
    else {
      Serial.println(F("ERR Unknown cmd"));
    }
  }

  // Nothing else needed; timing handled inside RBDDimmer.
}
