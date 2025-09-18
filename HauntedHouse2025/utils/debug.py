# utils/debug.py
import time
from control.doors import setDoorState
from control.arduino import m2Read_Analog
from utils.tools import log_event


def debugDoors():
    log_event("[Debug] Toggling doors...")
    while True:
        setDoorState(1, "OPEN")
        time.sleep(1)
        setDoorState(1, "CLOSED")
        time.sleep(1)


def debugSensors():
    log_event("[Debug] Reading analog sensors...")
    while True:
        val = m2Read_Analog(7)
        log_event(f"[Debug] Sensor value: {val}")
        time.sleep(1)
