# rooms/swamp.py
import time as t
from context import house
from control.audio_manager import play_audio
from utils.tools import BreakCheck, log_event
from control import dimmer_controller as dim

def run():
    log_event("[SwampRoom] Starting...")
    house.SRstate = "ACTIVE"

    while house.HouseActive or house.Demo:
        log_event("[SwampRoom] Running loop...")

        for i in range(500):
            if BreakCheck():
                return
            t.sleep(1)

        if BreakCheck() or house.Demo: # end on breakCheck or if demo'ing
            house.Demo = False
            break

    house.SRstate = "INACTIVE"
    log_event("[SwampRoom] Exiting.")
