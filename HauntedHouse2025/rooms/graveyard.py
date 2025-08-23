# rooms/graveyard.py
import time as t
from context import house
from control.audio_manager import play_to_named_channel

def run():
    print("[Graveyard] Starting...")

    while house.HouseActive or house.Demo:
        print("[Graveyard] Running loop...")
        play_to_named_channel("cannon1.wav", "dungeon")
        t.sleep(10)

        if not house.HouseActive and not house.Demo:
            break

    print("[Graveyard] Exiting.")
