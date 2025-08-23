#include <WiFi.h>
#include <esp_now.h>
#include <Wire.h>
#include <VL53L1X.h>  // Pololu library

#define I2C_SDA 21
#define I2C_SCL 22
#define SENSOR_ID "TOF1"           // Change per unit: TOF2, TOF3, etc.
#define LOOP_INTERVAL_MS 50        // 20Hz = 50ms
#define ACK_TIMEOUT 100
#define MAX_RETRIES 5

uint8_t receiverMac[] = { 0x3C, 0x8A, 0x1F, 0x9A, 0x63, 0x64 };  // Update if needed

struct SensorMessage {
  char sensor_id[6];      // e.g., TOF1
  uint16_t distance;      // in mm
  uint32_t timestamp;     // millis at send
  uint16_t packet_id;     // rolling counter
  uint8_t ack_retries;    // retries needed
};

VL53L1X vl53;
volatile bool ackReceived = false;
uint16_t packetCounter = 0;

void onDataRecv(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
  ackReceived = true;
}

void setup() {
  Serial.begin(115200);
  delay(100);

  Wire.begin(I2C_SDA, I2C_SCL);
  vl53.setTimeout(500);
  if (!vl53.init()) {
    Serial.println("❌ VL53L1X not found");
    while (true);
  }

  vl53.setDistanceMode(VL53L1X::Long);
  vl53.setMeasurementTimingBudget(50000);  // 50ms for stable accuracy
  vl53.startContinuous(LOOP_INTERVAL_MS);  // instead of .startContinuous();

  WiFi.mode(WIFI_STA);
  if (esp_now_init() != ESP_OK) {
    Serial.println("❌ ESP-NOW init failed");
    return;
  }

  esp_now_register_recv_cb(onDataRecv);

  esp_now_peer_info_t peerInfo = {};
  memcpy(peerInfo.peer_addr, receiverMac, 6);
  peerInfo.channel = 0;
  peerInfo.encrypt = false;
  esp_now_add_peer(&peerInfo);

  Serial.println("✅ TOF Sender Ready");
}

void loop() {
  unsigned long loopStart = millis();

  uint16_t distance = vl53.read();
  if (vl53.timeoutOccurred()) return;

  SensorMessage msg;
  strncpy(msg.sensor_id, SENSOR_ID, sizeof(msg.sensor_id));
  msg.distance = distance;
  msg.timestamp = millis();
  msg.packet_id = packetCounter++;

  ackReceived = false;
  int retries = 0;

  while (!ackReceived && retries < MAX_RETRIES) {
    msg.ack_retries = retries;
    esp_now_send(receiverMac, (uint8_t *)&msg, sizeof(msg));

    unsigned long waitStart = millis();
    while (!ackReceived && millis() - waitStart < ACK_TIMEOUT) {
      delay(2);
    }

    retries++;
  }

  // Ensure fixed 20Hz loop timing
  unsigned long loopDuration = millis() - loopStart;
  if (loopDuration < LOOP_INTERVAL_MS) {
    delay(LOOP_INTERVAL_MS - loopDuration);
  }
}
