# rooms/gangway.py
import time as t
from context import house
from control.audio_manager import play_audio
from control.arduino import m1Digital_Write
from control.doors import setDoorState
from utils.tools import BreakCheck, log_event
from control import remote_sensor_monitor as rsm
from control.houseLights import toggleHouseLights
import threading


def run():
    log_event("[gangway] Starting...")
    house.gangway_state = "ACTIVE"
    deadMenTellNoTalesLoop(threaded=True)

    #m1Digital_Write(33, 0) #torch lights

    #m1Digital_Write(35,0) #strobe/blacklight

    while house.HouseActive or house.Demo:

        #m1Digital_Write(33, 0) #torch lights

        setDoorState(1, "CLOPEN")

        count = 0
        while not rsm.obstructed("TOF1", block_mm=800, window_ms=250, min_consecutive=2):
            if BreakCheck():
                return
            if count > 30:
                log_event("No guests detected in gangway for 30 seconds, opening front door.")
                setDoorState(1, "CLOPEN")
                count = 0
            t.sleep(.05)
            count += 0.05

        play_audio("gangway", "gangwayHit1.wav", gain=1)

        m1Digital_Write(35,0) #strobe/blacklight
        log_event("[gangway] Strobe/Blacklight ON")

        for i in range(20):
            t.sleep(1)
            if BreakCheck():
                return
                    
        m1Digital_Write(35,1) #strobe/blacklight
        log_event("[gangway] Strobe/Blacklight OFF")

        if BreakCheck() or house.Demo: # end on breakCheck or if demo'ing
            if house.Demo:
                house.Demo = False
                house.HouseActive = False
            toggleHouseLights(True)
            return

        t.sleep(0.1)

    house.gangway_state = "INACTIVE"
    log_event("[gangway] Exiting.")

def deadMenTellNoTalesLoop(threaded=True):
    def main():
        while house.HouseActive or house.Demo:
            play_audio("gangway", "deadMenTellNoTales.wav", gain=1)
            t.sleep(10)
            if BreakCheck():
                return
    if threaded:
        threading.Thread(target=main, daemon=True, name="DMTNT audio loop").start()