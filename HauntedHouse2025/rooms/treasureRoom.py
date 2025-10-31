# rooms/Treasure Room.py
import time as t
from context import house
from control.audio_manager import play_audio
from utils.tools import BreakCheck, log_event
from control import dimmer_controller as dim
from control.arduino import m1Digital_Write
import random, threading
from control.houseLights import toggleHouseLights

def run():
    log_event("[treasureRoom] Starting...")
    house.treasureRoom_state = "ACTIVE"

    m1Digital_Write(3,0) #ambient light
    log_event("[treasureRoom] Ambient light ON")
    play_audio("treasureRoom", "treasureRoomAmbience.wav", gain=.7, looping=True)

    while house.HouseActive or house.Demo:
        log_event("[treasureRoom] Running loop...")

        play_audio("treasureRoom", "treasureRoomVoices.wav", gain=.7)

        for i in range(10):
            t.sleep(1)
            if BreakCheck():
                return
        
        m1Digital_Write(3,1) #ambient light
        log_event("[treasureRoom] Ambient light OFF")
        play_audio("treasureRoom", "treasureRoomHit1.wav", gain=1)

        t.sleep(1.8)

        m1Digital_Write(2, 0);  log_event("+120v Strobe 3 (G) ON")

        for i in range(10):
            t.sleep(1)
            if BreakCheck():
                return

        m1Digital_Write(3,0) #ambient light
        log_event("+120v Ambient Light 4 (G) ON")
        m1Digital_Write(2, 1);  log_event("+120v Strobe 3 (G) OFF")

        for i in range(10):
            t.sleep(1)
            if BreakCheck():
                return
            
        if BreakCheck() or house.Demo: # end on breakCheck or if demo'ing
            if house.Demo:
                house.Demo = False
                house.HouseActive = False
            toggleHouseLights(True)
            return

    house.treasureRoom_state = "INACTIVE"
    log_event("[treasureRoom] Exiting.")
