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
    house.TRstate = "ACTIVE"
    deadMenTellNoTalesLoop(threaded=True)

    while house.HouseActive or house.Demo:
        t.sleep(1)

        if house.Demo:
            break

        if BreakCheck() or house.Demo: # end on breakCheck or if demo'ing
            house.Demo = False
            toggleHouseLights(True)
            break

        t.sleep(0.1)

    house.TRstate = "INACTIVE"
    log_event("[gangway] Exiting.")

def deadMenTellNoTalesLoop(threaded=True):
    def main():
        while house.HouseActive or house.Demo:
            play_audio("gangway", "deadMenTellNoTales.wav", gain=1)
            t.sleep(10)
    if threaded:
        threading.Thread(target=main, daemon=True, name="DMTNT audio loop").start()