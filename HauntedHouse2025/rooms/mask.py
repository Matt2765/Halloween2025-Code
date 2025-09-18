# rooms/mask.py
import time as t
from context import house
from control.audio_manager import play_to_named_channel_async
from utils.tools import BreakCheck, log_event

def run():
    log_event("[MaskRoom] Starting...")
    house.MkRstate = "ACTIVE"

    while house.HouseActive or house.Demo:
        log_event("[MaskRoom] Running loop...")
        #play_to_named_channel_async("cannon1.wav", "closetCreak")
        t.sleep(5)

        if not house.HouseActive and not house.Demo:
            break

    house.MkRstate = "INACTIVE"
    log_event("[MaskRoom] Exiting.")
