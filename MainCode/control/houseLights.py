# control/lights.py
from context import house
from control.arduino import m1Digital_Write
from utils.tools import log_event
from control import dimmer_controller as dim


def toggleHouseLights(enable: bool = None):
    """
    Toggles or sets the house lights.
    - If 'enable' is True, turns lights ON.
    - If 'enable' is False, turns lights OFF.
    - If 'enable' is None (default), toggles the current state.
    """
    if enable is None:
        enable = not house.houseLights

    log_event(f"[Lights] House lights {'ON' if enable else 'OFF'}")
    house.houseLights = enable
    m1Digital_Write(22, 1 if enable else 0)
    dim.dim(100 if enable else 0)
    m1Digital_Write(23, 0 if enable else 1)
    m1Digital_Write(26, 0 if enable else 1)
    m1Digital_Write(6, 0 if enable else 1) # ship lights ON
    m1Digital_Write(7, 0 if enable else 1) # ship lights ON
