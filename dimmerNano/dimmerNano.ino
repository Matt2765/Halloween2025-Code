/* ------------------------------------------------------------
 * Nano 8-ch Phase-Angle Dimmer (MOC3023 drivers)
 * - SYNC on D2 (INT0, RISING)
 * - CH1..CH8 on D3..D10
 * - Inputs are ACTIVE_HIGH (drive HIGH during gate pulse)
 * - LED-friendly: long gate pulse + min delay at 100%
 * - Low-end fix: large END_GUARD so pulse never crosses next ZC
 *
 * Serial @115200 (newline-terminated):
 *   PING            -> PONG
 *   INFO            -> HALF_US, LEVELS, ACTIVE_HIGH
 *   D,<ch>,<0-100>  -> set single channel (1..8)
 *   A,<8 values>    -> set all channels at once
 * ------------------------------------------------------------ */

#include <Arduino.h>

// -------- Pins --------
const uint8_t PIN_SYNC = 2;                    // INT0 (zero-cross input)
const uint8_t CH_PINS[8] = {3,4,5,6,7,8,9,10}; // CH1..CH8 outputs

// -------- Board polarity --------
// Your module expects a HIGH pulse to fire -> ACTIVE_HIGH = true.
const bool ACTIVE_HIGH = true;

// -------- Timing (60 Hz default; auto-tracked) --------
volatile uint32_t lastZcMicros = 0;
volatile uint32_t halfCycleUs  = 8333;   // ~60 Hz

// Gate timing tuned for LED stability
const uint16_t TRIAC_PULSE_US  = 1200;   // long gate pulse for LED latch
const uint16_t MIN_DELAY_US    = 350;    // avoid firing exactly at ZC (fixes 100% LED dropout)

// *** Critical fix: keep entire pulse inside half-cycle ***
const uint16_t END_GUARD_US    = 1800;   // >= TRIAC_PULSE_US + margin (prevents "1% looks like 100%")

const uint32_t ZC_MIN_SPACING  = 4000;   // ignore chatter < 4 ms (noise)
const uint8_t  HALF_FILTER_N   = 8;      // LPF to stabilize half-cycle time

// -------- Levels & schedule --------
volatile uint8_t levelPct[8] = {0};      // commanded 0..100
uint8_t levelShadow[8]       = {0};      // snapshotted each half-cycle

struct FireGroup { uint16_t t_us; uint8_t mask; };
FireGroup groups[8]; uint8_t groupCount = 0;

volatile bool zcFlag = false;
volatile bool timerArmed = false;
volatile uint8_t gIdx = 0;
volatile bool inPulse = false;

// -------- Helpers --------
inline void pinWriteRaw(uint8_t pin, bool high) {
  digitalWrite(pin, high ? HIGH : LOW);
}

inline void driveMask(bool on, uint8_t mask) {
  // ACTIVE_HIGH=true: on=HIGH during gate pulse; idle=LOW
  // ACTIVE_HIGH=false: on=LOW during pulse; idle=HIGH
  for (uint8_t i=0; i<8; i++) {
    if (mask & (1u<<i)) {
      bool out = ACTIVE_HIGH ? on : !on;
      pinWriteRaw(CH_PINS[i], out);
    }
  }
}

inline void allIdle() {
  // Idle = "off" level at the pin
  for (uint8_t i=0; i<8; i++) {
    bool idleHigh = ACTIVE_HIGH ? false : true;
    pinWriteRaw(CH_PINS[i], idleHigh);
  }
}

// Map 0..100% to a firing delay (us) within the half-cycle.
// 0% -> sentinel (no fire), 100% -> small delay (MIN_DELAY_US) to avoid ZC dropout on LEDs.
// Top-end clamp (≥99%) so "100%" doesn't ever hit exactly at ZC.
uint16_t levelToDelayUs(uint8_t pct, uint32_t halfUs) {
  if (pct == 0) return 0xFFFF;  // OFF
  uint8_t pe = (pct >= 99) ? 98 : pct;  // tame very top end

  uint32_t raw = (uint32_t)((100 - pe) * halfUs) / 100;
  if (raw < MIN_DELAY_US) raw = MIN_DELAY_US;
  // *** Keep full (delay + pulse) inside half-cycle with guard at the tail ***
  if (raw > halfUs - END_GUARD_US) raw = halfUs - END_GUARD_US;
  return (uint16_t)raw;
}

void buildSchedule(uint32_t hUs) {
  groupCount = 0;
  uint16_t delayUs[8]; bool used[8] = {false};

  for (uint8_t i=0; i<8; i++) delayUs[i] = levelToDelayUs(levelShadow[i], hUs);

  // Group channels that share identical delay to minimize ISR work
  for (uint8_t i=0; i<8; i++) {
    if (used[i] || delayUs[i] == 0xFFFF) { used[i] = true; continue; }
    uint16_t d = delayUs[i];
    uint8_t  m = (1u << i);
    used[i] = true;
    for (uint8_t j=i+1; j<8; j++) {
      if (!used[j] && delayUs[j] == d) { m |= (1u << j); used[j] = true; }
    }
    groups[groupCount++] = { d, m };
  }

  // Sort by time (insertion sort)
  for (uint8_t i=1; i<groupCount; i++) {
    FireGroup key = groups[i];
    int8_t k = i - 1;
    while (k >= 0 && groups[k].t_us > key.t_us) { groups[k+1] = groups[k]; k--; }
    groups[k+1] = key;
  }
}

// -------- Interrupts --------
void onZeroCross_RISING() {
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

// Timer1 ISR: emits gate pulses at scheduled times
ISR(TIMER1_COMPA_vect) {
  if (!timerArmed || groupCount == 0) { allIdle(); return; }

  if (!inPulse) {
    // Start gate pulse for this group
    driveMask(true, groups[gIdx].mask);
    inPulse = true;
    OCR1A = TCNT1 + (TRIAC_PULSE_US * 2); // prescale=8 -> 0.5 µs per tick
  } else {
    // End gate pulse, go back to idle
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

// -------- Serial protocol --------
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
    for (uint8_t i=0; i<8; i++) { Serial.print(lv[i]); if (i<7) Serial.print(','); }
    Serial.print(" ACTIVE_HIGH="); Serial.println(ACTIVE_HIGH ? "1" : "0");
    return;
  }

  if (s.charAt(0) == 'D' || s.charAt(0) == 'd') {
    int c1 = s.indexOf(','), c2 = s.indexOf(',', c1+1);
    if (c1 > 0 && c2 > c1) {
      int ch  = s.substring(c1+1, c2).toInt();
      int lvl = s.substring(c2+1).toInt();
      ch  = constrain(ch, 1, 8);
      lvl = constrain(lvl, 0, 100);
      noInterrupts(); levelPct[ch-1] = (uint8_t)lvl; interrupts();
      Serial.println("OK");
      return;
    }
  }

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

// -------- Setup / Loop --------
void setup() {
  Serial.begin(115200);
  Serial.println("READY Phase-Angle (MOC3023, ACTIVE_HIGH=1)");

  // Channel pins to idle
  for (uint8_t i=0; i<8; i++) { pinMode(CH_PINS[i], OUTPUT); }
  allIdle();

  // SYNC input (many boards present open-collector ZC -> enable pullup)
  pinMode(PIN_SYNC, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_SYNC), onZeroCross_RISING, RISING);

  // Timer1: CTC, prescale=8 (0.5 us per tick)
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

  // On each ZC, build the schedule and arm the timer
  if (zcFlag) {
    noInterrupts(); zcFlag = false; interrupts();

    // Snapshot levels once per half-cycle
    noInterrupts(); for (uint8_t i=0; i<8; i++) levelShadow[i] = levelPct[i]; interrupts();

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
