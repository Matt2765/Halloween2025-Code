# rooms/Cargo Hold.py
import time as t
from context import house
from control.audio_manager import play_to_named_channel_async
from utils.tools import BreakCheck, log_event
from control import dimmer_controller as dim


def run():
    log_event("[cargoHold] Starting...")
    house.MkRstate = "ACTIVE"

    while house.HouseActive or house.Demo:
        log_event("[cargoHold] Running loop...")
        #play_to_named_channel_async("cannon1.wav", "closetCreak")

        dim.dimmer(1,0)
        log_event(f"[cargoHold] Dimmer set to {0}")
        t.sleep(3)
        dim.dimmer(1,50)
        log_event(f"[cargoHold] Dimmer set to {50}")
        t.sleep(3)
        dim.dimmer(1,100)
        log_event(f"[cargoHold] Dimmer set to {100}")
        t.sleep(3)
        dim.dimmer(1,0)
        t.sleep(3)
        dim.dimmer(1,25)
        t.sleep(3)
        dim.dimmer(1,50)
        t.sleep(3)
        dim.dimmer(1,75)
        t.sleep(3)
        dim.dimmer(1,100)
        t.sleep(3)

        for i in range(100):
            dim.dimmer(1,i)
            t.sleep(.1)
            log_event(f"[cargoHold] Dimmer set to {i}")
            if BreakCheck():
                return
        dim.dimmer(1,0)

        if BreakCheck() or house.Demo: # end on breakCheck or if demo'ing
            house.Demo = False
            break

    house.MkRstate = "INACTIVE"
    log_event("[cargoHold] Exiting.")
