# rooms/cave.py
import time as t
from context import house
from control.audio_manager import play_to_named_channel_async
from control.arduino import m1Digital_Write, m2Read_Analog
from control.doors import setDoorState
from utils.tools import BreakCheck, log_event


def run():
    log_event("[CaveRoom] Starting...")
    house.CRstate = "ACTIVE"

    try:
        while house.HouseActive or house.Demo:
            # Sensor or demo trigger
            if m2Read_Analog(7) > 200 or house.Demo:
                setDoorState(1, "CLOPEN")
                #play_to_named_channel_async("cannon1.wav", "frontLeft")
                m1Digital_Write(27, 1)  # Start animatronic
                log_event("[CaveRoom] Activated animatronic 27")
                t.sleep(5)
                m1Digital_Write(27, 0)
                log_event("[CaveRoom] Deactivated animatronic 27")
                setDoorState(1, "CLOSED")

                if house.Demo:
                    break

            if BreakCheck() or house.Demo: # end on breakCheck or if demo'ing
                house.Demo = False
                break

            t.sleep(0.5)

    except Exception as e:
        log_event(f"[CaveRoom] Error: {e}")

    house.CRstate = "INACTIVE"
    log_event("[CaveRoom] Exiting.")
