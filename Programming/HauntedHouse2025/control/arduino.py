# control/arduino.py
from pymata4 import pymata4
from utils.tools import log_event

try:
    board = pymata4.Pymata4()
    log_event("[Arduino] Board connected successfully.")
    arduino_available = True
except Exception as e:
    log_event(f"[Arduino] Failed to connect: {e}")
    board = None
    arduino_available = False


def m1Digital_Write(pin, value):
    if arduino_available:
        board.digital_write(pin, value)
    else:
        log_event(f"[Arduino] (Simulated) m1Digital_Write(pin={pin}, value={value})")


def m2Digital_Write(pin, value):
    if arduino_available:
        board.digital_write(pin, value)
    else:
        log_event(f"[Arduino] (Simulated) m2Digital_Write(pin={pin}, value={value})")


def m2Read_Analog(pin):
    if arduino_available:
        return board.analog_read(pin)[0]
    else:
        log_event(f"[Arduino] (Simulated) m2Read_Analog(pin={pin}) returning 0")
        return 0


def connectArduino():
    if arduino_available:
        log_event("[Arduino] connectArduino() called: board already connected.")
    else:
        log_event("[Arduino] connectArduino() called: no board connected (simulated mode).")


def shutdownArduino():
    if arduino_available:
        board.shutdown()
        log_event("[Arduino] Board shutdown.")