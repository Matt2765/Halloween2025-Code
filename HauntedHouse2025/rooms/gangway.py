# rooms/gangway.py
import time as t
from context import house
from control.audio_manager import play_to_named_channel_async, play_to_all_channels_async
from control.arduino import m1Digital_Write
from control.doors import setDoorState
from utils.tools import BreakCheck, log_event
from control import remote_sensor_monitor as rsm


def run():
    log_event("[gangway] Starting...")
    house.TRstate = "ACTIVE"
    try:
        while house.HouseActive or house.Demo:
            if rsm.obstructed("TOF1", block_mm=800, window_ms=250, min_consecutive=2) or house.Demo:
                print("TRIPPED")
                play_to_all_channels_async("gangway sensor tripped")
                setDoorState(1, "CLOPEN")
                #play_to_named_channel_async("cannon1.wav", "frontLeft")
                m1Digital_Write(27, 1)  # Start animatronic
                log_event("[gangway] Activated animatronic 27")
                t.sleep(5)
                m1Digital_Write(27, 0)
                log_event("[gangway] Deactivated animatronic 27")
                setDoorState(1, "CLOSED")

                if house.Demo:
                    break

            if BreakCheck() or house.Demo: # end on breakCheck or if demo'ing
                house.Demo = False
                break

            t.sleep(0.1)

    except Exception as e:
        log_event(f"[gangway] Error: {e}")

    house.TRstate = "INACTIVE"
    log_event("[gangway] Exiting.")
