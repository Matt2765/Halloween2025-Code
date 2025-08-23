# rooms/mirror.py
import time as t
from context import house
from control.audio_manager import play_to_named_channel

def run():
    print("[MirrorRoom] Starting...")
    house.MRstate = "ACTIVE"

    while house.HouseActive or house.Demo:
        print("[MirrorRoom] Running loop...")
        #play_to_named_channel("cannon1.wav", "atticSpeaker")
        t.sleep(5)

        if not house.HouseActive and not house.Demo:
            break

    house.MRstate = "INACTIVE"
    print("[MirrorRoom] Exiting.")
