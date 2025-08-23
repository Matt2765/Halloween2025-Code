#include <WiFi.h>
#include <esp_now.h>

struct SensorMessage {
  char sensor_id[6];
  uint16_t distance;
  uint32_t timestamp;
  uint16_t packet_id;
  uint8_t ack_retries;
};

void onDataRecv(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
  if (len != sizeof(SensorMessage)) return;

  // Auto-add sender if needed
  if (!esp_now_is_peer_exist(info->src_addr)) {
    esp_now_peer_info_t peerInfo = {};
    memcpy(peerInfo.peer_addr, info->src_addr, 6);
    peerInfo.channel = 0;
    peerInfo.encrypt = false;
    esp_now_add_peer(&peerInfo);
  }

  SensorMessage msg;
  memcpy(&msg, data, sizeof(msg));

  // CSV format: ID,Distance,Timestamp,PacketID,AckRetries
  Serial.printf("%s,%u,%lu,%u,%u\n",
    msg.sensor_id,
    msg.distance,
    msg.timestamp,
    msg.packet_id,
    msg.ack_retries);

  esp_now_send(info->src_addr, data, len);  // ACK
}

void setup() {
  Serial.begin(115200);
  delay(100);

  WiFi.mode(WIFI_STA);
  esp_now_init();
  esp_now_register_recv_cb(onDataRecv);
  Serial.println("✅ TOF Receiver Ready");
}

void loop() {
  // Passive
}
