# control/sensor_monitor.py
import time
from context import house
from control.arduino import m2Read_Analog
from utils.tools import log_event


def analog_update_loop():
    """Continuously updates analog sensor values in the shared house state."""
    log_event("[Sensor] Starting analog update loop...")
    try:
        while house.HouseActive or house.Demo:
            for pin in range(len(house.M2AnalogValues)):
                value = m2Read_Analog(pin)
                house.M2AnalogValues[pin] = value
            time.sleep(0.2)  # adjust for responsiveness vs. CPU load
    except Exception as e:
        log_event(f"[Sensor] Error in analog update loop: {e}")

    log_event("[Sensor] Analog update loop stopped.")
