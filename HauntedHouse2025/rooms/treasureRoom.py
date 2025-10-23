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

        dimmer4_fire(10)

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

        dimmer4_fire(500)

        for i in range(500):
            t.sleep(1)
            if BreakCheck():
                return
            
        if BreakCheck() or house.Demo: # end on breakCheck or if demo'ing
            house.Demo = False
            break

    house.TRstate = "INACTIVE"
    log_event("[treasureRoom] Exiting.")

def dimmer4_fire(duration_s: float) -> threading.Thread:
    """
    Flicker DIM CH.4 between 10–50 (fire effect) for duration_s seconds.
    Stops early if BreakCheck() returns True.
    """
    def _run():
        end = t.time() + float(duration_s)
        while t.time() < end:
            if BreakCheck():
                return
            dim.dimmer(4, random.randint(20, 60))  # intensity must be a number, not a list
            t.sleep(random.uniform(0.05, 0.18))
        dim.dimmer(4, 0)

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    return th

