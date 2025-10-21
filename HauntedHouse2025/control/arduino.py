# control/arduino.py
import time as t
from pymata4 import pymata4
from utils.tools import log_event
from context import house

M1PORT = "COM4"
M1 = None
M1_available = False

def m1Digital_Write(pin, value):
    if M1_available:
        M1.digital_write(pin, value)
    else:
        log_event(f"[Arduino] (Simulated) m1Digital_Write(pin={pin}, value={value})")

def connectArduino():
    global M1, M1_available
    log_event("[Arduino] Attempting to establish connection with Arduino Mega...")

    try:
        # Use Firmata's standard baud (ensure StandardFirmata/StandardFirmataPlus or FirmataExpress is flashed)
        M1 = pymata4.Pymata4(com_port=M1PORT, baud_rate=57600, sleep_tune=0.3)
        log_event(f"[Arduino] Communication to board on {M1PORT} successfully started.")
        M1_available = True
    except Exception as e:
        log_event(f"[Arduino] Board not found on {M1PORT}. Connection not established. Error: [{e}]")
        M1_available = False
        return

    try:
        # Configure ALL digital pins (0–69 on Mega, including A0–A15) as outputs
        for pin in range(0, 70):
            try:
                M1.set_pin_mode_digital_output(pin)
            except Exception as e:
                log_event(f"[Arduino] Skipping pin {pin}: [{e}]")

        # Disable analog reporting so A0–A15 behave purely as digital
        for ch in range(16):
            try:
                M1.disable_analog_reporting(ch)
            except Exception:
                pass

        log_event("[Arduino] All pins configured as digital outputs (A0–A15 included).")
    except Exception as e:
        log_event(f"[Arduino] Error during pin configuration: [{e}]")

    t.sleep(1)
