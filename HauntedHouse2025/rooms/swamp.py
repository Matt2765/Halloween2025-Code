# rooms/swamp.py
import time as t
from context import house
from control.audio_manager import play_to_named_channel_async

def run():
    print("[SwampRoom] Starting...")
    house.SRstate = "ACTIVE"

    print(f'[SwampRoom] REMOTE_TEST: {house.remote_sensor_value("TOF1")}')

    while house.HouseActive or house.Demo:
        print("[SwampRoom] Running loop...")
        #play_to_named_channel_async("cannon1.wav", "swampRoom")
        t.sleep(5)

        if not house.HouseActive and not house.Demo:
            break

    house.SRstate = "INACTIVE"
    print("[SwampRoom] Exiting.")
