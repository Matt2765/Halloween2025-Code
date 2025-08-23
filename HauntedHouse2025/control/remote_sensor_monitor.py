# control/remote_sensor_monitor.py
import socket
import threading
import time

UDP_IP = "0.0.0.0"
UDP_PORT = 4210
BUFFER_SIZE = 1024

sensor_data = {}

def parse_sensor_message(message):
    try:
        parts = message.strip().split(',')
        if len(parts) != 5:
            return None
        return {
            'sensor_id': parts[0],
            'distance': int(parts[1]),
            'timestamp': int(parts[2]),
            'packet_id': int(parts[3]),
            'retries': int(parts[4])
        }
    except Exception:
        return None

def remote_sensor_value(sensor_id):
    return sensor_data.get(sensor_id, {}).get('distance', None)

def listen_for_data():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    print(f"Listening for ESP32 sensor data on {UDP_IP}:{UDP_PORT}")
    while True:
        try:
            data, _ = sock.recvfrom(BUFFER_SIZE)
            message = data.decode("utf-8")
            parsed = parse_sensor_message(message)
            if parsed:
                sensor_data[parsed['sensor_id']] = parsed
        except Exception as e:
            print(f"[Sensor] Error: {e}")

def start_sensor_listener():
    thread = threading.Thread(target=listen_for_data, daemon=True)
    thread.start()
    return thread, remote_sensor_value
