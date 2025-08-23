# rooms/mask.py
import time as t
from context import house
from control.audio_manager import play_to_named_channel

def run():
    print("[MaskRoom] Starting...")
    house.MkRstate = "ACTIVE"

    while house.HouseActive or house.Demo:
        print("[MaskRoom] Running loop...")
        play_to_named_channel("cannon1.wav", "closetCreak")
        t.sleep(5)

        if not house.HouseActive and not house.Demo:
            break

    house.MkRstate = "INACTIVE"
    print("[MaskRoom] Exiting.")
