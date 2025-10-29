# rooms/Cargo Hold.py
import time as t
from context import house
from control.audio_manager import play_audio
from utils.tools import BreakCheck, log_event
from control import dimmer_controller as dim
from utils import speakerTest
from control.houseLights import toggleHouseLights
from control.arduino import m1Digital_Write
from control.lightning import bulb_lightning

def run():
    log_event("[cargoHold] Starting...")
    house.cargoHold_state = "ACTIVE"

    while house.HouseActive or house.Demo:
        log_event("[cargoHold] Running loop...")

        '''m1Digital_Write(34,0)  # brig blacklight/strobe on
        m1Digital_Write(37,0)  # brig ambient on
        m1Digital_Write(5,0)    #triangle ambient
        m1Digital_Write(28, 0)  #filipe ambient
        m1Digital_Write(30,0)   # cargo lightning
        m1Digital_Write(36, 0)  # triangle strobe'''

        bulb_lightning(30, flash_ms=100, flashes=(1,3), delay_ms=80, loops=1, threaded=True)

        m1Digital_Write(28, 0)  #filipe ambient

        m1Digital_Write(37, 0)  # brig ambient on

        t.sleep(30)
            
        if BreakCheck() or house.Demo: # end on breakCheck or if demo'ing
            if house.Demo:
                house.Demo = False
                house.HouseActive = False
            toggleHouseLights(True)
            return

    house.cargoHold_state = "INACTIVE"
    log_event("[cargoHold] Exiting.")
