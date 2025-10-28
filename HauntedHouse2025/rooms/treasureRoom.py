# rooms/Treasure Room.py
import time as t
from context import house
from control.audio_manager import play_audio
from utils.tools import BreakCheck, log_event
from control import dimmer_controller as dim
from control.arduino import m1Digital_Write
import random, threading

def run():
    log_event("[treasureRoom] Starting...")
    house.TRstate = "ACTIVE"

    while house.HouseActive or house.Demo:
        log_event("[treasureRoom] Running loop...")

        for i in range(6):
            t.sleep(1)
            if BreakCheck():
                return

        for i in range(10):
            t.sleep(1)
            if BreakCheck():
                return

        for i in range(2):
            t.sleep(1)
            if BreakCheck():
                return

        m1Digital_Write(2, 0);  log_event("+120v Strobe 3 (G) ON")

        for i in range(10):
            t.sleep(1)
            if BreakCheck():
                return

        m1Digital_Write(2, 1);  log_event("+120v Strobe 3 (G) OFF")

        for i in range(500):
            t.sleep(1)
            if BreakCheck():
                return
            
        if BreakCheck() or house.Demo: # end on breakCheck or if demo'ing
            house.Demo = False
            break

    house.TRstate = "INACTIVE"
    log_event("[treasureRoom] Exiting.")

