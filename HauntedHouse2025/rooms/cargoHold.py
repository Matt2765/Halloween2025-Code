# rooms/Cargo Hold.py
import time as t
from context import house
from control.audio_manager import play_audio
from utils.tools import BreakCheck, log_event
from control import dimmer_controller as dim
from control import cannons


def run():
    log_event("[cargoHold] Starting...")
    house.MkRstate = "ACTIVE"

    while house.HouseActive or house.Demo:
        log_event("[cargoHold] Running loop...")

        for i in range(30):
            cannons.fire_cannon(1)

            for i in range(10):
                t.sleep(1)
                if BreakCheck():
                    return
            
        if BreakCheck() or house.Demo: # end on breakCheck or if demo'ing
            house.Demo = False
            break

    house.MkRstate = "INACTIVE"
    log_event("[cargoHold] Exiting.")
