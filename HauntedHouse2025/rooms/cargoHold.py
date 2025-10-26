# rooms/Cargo Hold.py
import time as t
from context import house
from control.audio_manager import play_audio
from utils.tools import BreakCheck, log_event
from control import dimmer_controller as dim

def run():
    log_event("[cargoHold] Starting...")
    house.MkRstate = "ACTIVE"

    while house.HouseActive or house.Demo:
        log_event("[cargoHold] Running loop...")

        t.sleep(1)
            
        if BreakCheck() or house.Demo: # end on breakCheck or if demo'ing
            house.Demo = False
            break

    house.MkRstate = "INACTIVE"
    log_event("[cargoHold] Exiting.")
