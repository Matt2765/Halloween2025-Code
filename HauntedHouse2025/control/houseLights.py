# control/lights.py
from context import house
from control.arduino import m1Digital_Write


def toggleHouseLights(enable: bool):
    log_event(f"[Lights] House lights {'ON' if enable else 'OFF'}")
    house.houseLights = enable
    # Example: control pin 13
    m1Digital_Write(13, 1 if enable else 0)
