# rooms/Treasure Room.py
import time as t
from context import house
from control.audio_manager import play_audio
from utils.tools import BreakCheck, log_event

def run():
    log_event("[treasureRoom] Starting...")
    house.TRstate = "ACTIVE"

    while house.HouseActive or house.Demo:
        log_event("[treasureRoom] Running loop...")
        t.sleep(5)

        if BreakCheck() or house.Demo: # end on breakCheck or if demo'ing
            house.Demo = False
            break

    house.TRstate = "INACTIVE"
    log_event("[treasureRoom] Exiting.")
