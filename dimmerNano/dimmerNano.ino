/* ------------------------------------------------------------
 * UNO 8-ch Phase-Angle Dimmer (by PIN map)
 * - Zero-Cross on D2 (INT0, RISING by default)
 * - Channels are mapped to arbitrary Arduino pins via CH_PINS[]
 * - Serial protocol (compatible with your Python mixer):
 *     PING            -> PONG
 *     INFO            -> HALF_US, LEVELS, ACTIVE_HIGH
 *     D,<ch>,<0-100>  -> set single channel (1..8)
 *     A,<8 values>    -> set all channels (8 comma values)
 *
 * Notes:
 * - Set CH_PINS[] to the exact Arduino pins you’ve wired to each module.
 * - Set CH_ACTIVE_HIGH[] per output if any module expects active-LOW gate.
 * - LED-friendly timing: long gate pulse, min delay near ZC, tail guard.
 * ------------------------------------------------------------ */

#include <Arduino.h>

// ----------------- Hardware mapping -----------------
// ZC input (many dimmer boards output an open-collector pulse)
const uint8_t PIN_SYNC = 2; // INT0

// Map “channels” 1..8 to ANY Arduino pins you want to use for the gate outputs.
const uint8_t CH_PINS[8] = {
  3,  // CH1   (change these to your actual gate pins)
  4,  // CH2 
  5,  // CH3 
  6,  // CH4 
  7,  // CH5 
  8,  // CH6 
  9,  // CH7 
  10  // CH8 
};

// Per-channel polarity: true = active-HIGH gate, false = active-LOW gate
const bool CH_ACTIVE_HIGH[8] = {
  true, true, true, true, true, true, true, true
};

// ----------------- Timing & filters -----------------
volatile uint32_t lastZcMicros = 0;
volatile uint32_t halfCycleUs  = 8333;   // auto-tracked to 50/60 Hz
const uint32_t    ZC_MIN_SPACING  = 4000; // ignore chatter <4 ms
const uint8_t     HALF_FILTER_N   = 8;    // LPF for half-cycle estimate

// LED-friendly gate timing
const uint16_t TRIAC_PULSE_US  = 1200; // long latch pulse
const uint16_t MIN_DELAY_US    = 350;  // avoid firing at ZC
const uint16_t END_GUARD_US    = 1800; // keep pulse inside half-cycle

// ----------------- Levels & schedule -----------------
volatile uint8_t levelPct[8] = {0};   // commanded 0..100 per channel
uint8_t          levelSnap[8] = {0};  // snapshot each half-cycle

struct FireGroup { uint16_t t_us; uint8_t mask; }; // mask bit i -> channel i
FireGroup groups[8];
uint8_t groupCount = 0;

volatile bool zcFlag = false;
volatile bool timerArmed = false;
volatile uint8_t gIdx = 0;
volatile bool inPulse = false;

// ----------------- Helpers -----------------
inline void writeGateRaw(uint8_t chIdx, bool driveOn) {
  // Apply per-channel polarity
  bool out = CH_ACTIVE_HIGH[chIdx] ? driveOn : !driveOn;
  digitalWrite(CH_PINS[chIdx], out ? HIGH : LOW);
}

inline void driveMask(bool on, uint8_t mask) {
  for (uint8_t i = 0; i < 8; i++) {
    if (mask & (1u << i)) writeGateRaw(i, on);
  }
}

inline void allIdle() {
  // Idle = opposite of “on”
  for (uint8_t i = 0; i < 8; i++) {
    bool idleHigh = CH_ACTIVE_HIGH[i] ? false : true;
    digitalWrite(CH_PINS[i], idleHigh ? HIGH : LOW);
  }
}

// Map 0..100% to delay us within half-cycle
uint16_t levelToDelayUs(uint8_t pct, uint32_t halfUs) {
  if (pct == 0) return 0xFFFF;              // OFF sentinel
  uint8_t pe = (pct >= 99) ? 98 : pct;      // tame top end (avoid ZC)
  uint32_t d = (uint32_t)((100 - pe) * halfUs) / 100;
  if (d < MIN_DELAY_US) d = MIN_DELAY_US;
  if (d > halfUs - END_GUARD_US) d = halfUs - END_GUARD_US;
  return (uint16_t)d;
}

void buildSchedule(uint32_t hUs) {
  groupCount = 0;
  uint16_t delayUs[8]; bool used[8] = {false,false,false,false,false,false,false,false};

  for (uint8_t i = 0; i < 8; i++) delayUs[i] = levelToDelayUs(levelSnap[i], hUs);

  // Group identical times to reduce ISR work
  for (uint8_t i = 0; i < 8; i++) {
    if (used[i] || delayUs[i] == 0xFFFF) { used[i] = true; continue; }
    uint16_t d = delayUs[i];
    uint8_t  m = (1u << i);
    used[i] = true;
    for (uint8_t j = i + 1; j < 8; j++) {
      if (!used[j] && delayUs[j] == d) { m |= (1u << j); used[j] = true; }
    }
    groups[groupCount++] = { d, m };
  }

  // sort ascending by t_us (insertion sort)
  for (uint8_t i = 1; i < groupCount; i++) {
    FireGroup key = groups[i];
    int8_t k = i - 1;
    while (k >= 0 && groups[k].t_us > key.t_us) { groups[k+1] = groups[k]; k--; }
    groups[k+1] = key;
  }
}

// ----------------- Interrupts -----------------
void onZC() {
  uint32_t now = micros();
  uint32_t dt  = now - lastZcMicros;

  if (dt > ZC_MIN_SPACING && dt < 20000) {
    if (dt > 6000 && dt < 12000) {
      halfCycleUs = (halfCycleUs * (HALF_FILTER_N - 1) + dt) / HALF_FILTER_N;
    }
    lastZcMicros = now;
    zcFlag = true;
  }
}

// Timer1 compare ISR: starts/ends pulses according to schedule
ISR(TIMER1_COMPA_vect) {
  if (!timerArmed || groupCount == 0) { allIdle(); return; }

  if (!inPulse) {
    // start pulse for current group
    driveMask(true, groups[gIdx].mask);
    inPulse = true;
    OCR1A = TCNT1 + (TRIAC_PULSE_US * 2); // prescale=8 -> 0.5us/tick
  } else {
    // end pulse; advance
    driveMask(false, groups[gIdx].mask);
    inPulse = false;
    gIdx++;
    if (gIdx >= groupCount) {
      timerArmed = false;
    } else {
      OCR1A = (uint16_t)(groups[gIdx].t_us * 2);
    }
  }
}

// ----------------- Serial protocol -----------------
void handleLine(String s) {
  s.trim(); if (!s.length()) return;

  if (s.equalsIgnoreCase("PING")) { Serial.println("PONG"); return; }

  if (s.equalsIgnoreCase("INFO")) {
    noInterrupts();
    uint32_t hu = halfCycleUs;
    uint8_t lv[8]; for (uint8_t i=0;i<8;i++) lv[i] = levelPct[i];
    interrupts();
    Serial.print("HALF_US="); Serial.print(hu);
    Serial.print(" LEVELS=");
    for (uint8_t i=0;i<8;i++){ Serial.print(lv[i]); if (i<7) Serial.print(','); }
    // For compatibility with your parser we expose a single ACTIVE_HIGH:
    // If *all* channels share the same polarity it reflects that, else 'X'.
    bool allSame = true; bool base = CH_ACTIVE_HIGH[0];
    for (uint8_t i=1;i<8;i++) if (CH_ACTIVE_HIGH[i]!=base) { allSame=false; break; }
    Serial.print(" ACTIVE_HIGH="); Serial.println(allSame ? (base ? "1":"0") : "X");
    return;
  }

  // D,<ch>,<lvl>
  if (s.charAt(0) == 'D' || s.charAt(0) == 'd') {
    int c1 = s.indexOf(','), c2 = s.indexOf(',', c1+1);
    if (c1 > 0 && c2 > c1) {
      int ch  = s.substring(c1+1, c2).toInt();   // 1..8 (logical)
      int lvl = s.substring(c2+1).toInt();       // 0..100
      ch  = constrain(ch, 1, 8);
      lvl = constrain(lvl, 0, 100);
      noInterrupts(); levelPct[ch-1] = (uint8_t)lvl; interrupts();
      Serial.println("OK");
      return;
    }
  }

  // A,<8 values>
  if (s.charAt(0) == 'A' || s.charAt(0) == 'a') {
    if (s.length() >= 3 && s.charAt(1) == ',') {
      uint8_t vals[8]; uint8_t n = 0; int start = 2;
      while (n < 8) {
        int sep = s.indexOf(',', start);
        String tok = (sep == -1) ? s.substring(start) : s.substring(start, sep);
        tok.trim();
        int v = tok.toInt();
        vals[n++] = (uint8_t)constrain(v, 0, 100);
        if (sep == -1) break;
        start = sep + 1;
      }
      if (n == 8) {
        noInterrupts(); for (uint8_t i=0;i<8;i++) levelPct[i] = vals[i]; interrupts();
        Serial.println("OK");
        return;
      }
    }
  }

  Serial.println("ERR");
}

// ----------------- Setup / Loop -----------------
void setup() {
  Serial.begin(115200);
  Serial.println(F("READY UNO Phase-Angle by-PIN map"));

  // Gate pins -> OUTPUT + idle
  for (uint8_t i=0;i<8;i++){ pinMode(CH_PINS[i], OUTPUT); }
  allIdle();

  // ZC input (often open-collector): enable pull-up
  pinMode(PIN_SYNC, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_SYNC), onZC, RISING); // try FALLING if your ZC goes low

  // Timer1: CTC, prescale=8 (0.5 us/tick)
  TCCR1A = 0;
  TCCR1B = _BV(WGM12) | _BV(CS11);
  OCR1A  = 0xFFFF;
  TIMSK1 = _BV(OCIE1A);
}

void loop() {
  // Serial line buffer
  static String line;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') { if (line.length()) { handleLine(line); line = ""; } }
    else if (line.length() < 120) line += c;
  }

  // On ZC: snapshot levels, build schedule, arm timer
  if (zcFlag) {
    noInterrupts(); zcFlag = false; interrupts();

    noInterrupts(); for (uint8_t i=0;i<8;i++) levelSnap[i] = levelPct[i]; interrupts();
    buildSchedule(halfCycleUs);

    noInterrupts();
    inPulse = false; gIdx = 0; TCNT1 = 0;
    if (groupCount > 0) {
      OCR1A = (uint16_t)(groups[0].t_us * 2);
      timerArmed = true;
    } else {
      timerArmed = false;
      allIdle();
    }
    interrupts();
  }
}
