# rooms/Cargo Hold.py
import time as t
from context import house
from control.audio_manager import play_audio
from utils.tools import BreakCheck, log_event
from control import dimmer_controller as dim
from utils import speakerTest
from control.houseLights import toggleHouseLights
from control.arduino import m1Digital_Write

def run():
    log_event("[cargoHold] Starting...")
    house.MkRstate = "ACTIVE"

    while house.HouseActive or house.Demo:
        log_event("[cargoHold] Running loop...")

        m1Digital_Write(34,0)  # ambient on
        m1Digital_Write(3,0)  # ambient on

        t.sleep(50)
            
        if BreakCheck() or house.Demo: # end on breakCheck or if demo'ing
            house.Demo = False
            toggleHouseLights(True)
            break

    house.MkRstate = "INACTIVE"
    log_event("[cargoHold] Exiting.")
