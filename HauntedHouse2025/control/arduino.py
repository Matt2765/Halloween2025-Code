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
        M1 = pymata4.Pymata4(com_port=M1PORT, baud_rate=250000, sleep_tune=0.05)
        log_event(f"[Arduino] Communication to board on {M1PORT} successfully started.")
        M1_available = True
    except Exception as e:
        log_event(f"[Arduino] Board not found on {M1PORT}. Connection not established. Error: [{e}]")
        M1_available = False
        return

    try:
        # Configure ALL available digital pins (0–69 on the Mega, including A0–A15)
        for pin in range(0, 70):
            M1.set_pin_mode_digital_output(pin)

        # Disable analog reporting to ensure A0–A15 act purely as digital
        for ch in range(16):
            try:
                M1.disable_analog_reporting(ch)
            except Exception:
                pass

        log_event("[Arduino] All pins configured as digital outputs (A0–A15 included).")
    except Exception as e:
        log_event(f"[Arduino] Error during pin configuration: [{e}]")

    t.sleep(1)
