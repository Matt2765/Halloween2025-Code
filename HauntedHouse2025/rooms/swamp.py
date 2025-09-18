# rooms/swamp.py
import time as t
from context import house
from control.audio_manager import play_to_named_channel_async
from utils.tools import BreakCheck, log_event

def run():
    log_event("[SwampRoom] Starting...")
    house.SRstate = "ACTIVE"

    log_event(f'[SwampRoom] REMOTE_TEST: {house.remote_sensor_value("TOF1")}')

    while house.HouseActive or house.Demo:
        log_event("[SwampRoom] Running loop...")
        #play_to_named_channel_async("cannon1.wav", "swampRoom")
        t.sleep(5)

        if not house.HouseActive and not house.Demo:
            break

    house.SRstate = "INACTIVE"
    log_event("[SwampRoom] Exiting.")
