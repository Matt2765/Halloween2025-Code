# utils/debug.py
import time
from control.doors import setDoorState
from control.arduino import m2Read_Analog


def debugDoors():
    print("[Debug] Toggling doors...")
    while True:
        setDoorState(1, "OPEN")
        time.sleep(1)
        setDoorState(1, "CLOSED")
        time.sleep(1)


def debugSensors():
    print("[Debug] Reading analog sensors...")
    while True:
        val = m2Read_Analog(7)
        print(f"[Debug] Sensor value: {val}")
        time.sleep(1)
