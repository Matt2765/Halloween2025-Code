# rooms/gangway.py
import time as t
from context import house
from control.audio_manager import play_audio
from control.arduino import m1Digital_Write
from control.doors import setDoorState
from utils.tools import BreakCheck, log_event
from control import remote_sensor_monitor as rsm


def run():
    log_event("[gangway] Starting...")
    house.TRstate = "ACTIVE"
    try:
        while house.HouseActive or house.Demo:
            t.sleep(1)

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
