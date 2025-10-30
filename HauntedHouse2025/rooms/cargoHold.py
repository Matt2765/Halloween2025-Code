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
from control import remote_sensor_monitor as rsm
import threading

def run():
    log_event("[cargoHold] Starting...")
    house.cargoHold_state = "ACTIVE"
    threading.Thread(target=brig, daemon=True, name="brig").start()

    m1Digital_Write(51, 0)  # ROWING SKELETON

    while house.HouseActive or house.Demo:
        log_event("[cargoHold] Running loop...")

        '''m1Digital_Write(34,0)  # brig blacklight/strobe on
        m1Digital_Write(37,0)  # brig ambient on
        m1Digital_Write(5,0)    #triangle ambient
        m1Digital_Write(28, 0)  #filipe ambient
        m1Digital_Write(30,0)   # cargo lightning
        m1Digital_Write(36, 0)  # triangle strobe'''

        m1Digital_Write(28, 0)  #filipe ambient
        m1Digital_Write(36, 1)  #triangle strobe

        count = 0
        while not rsm.get_button_value("BTN1"):
            if count > 3:
                count = 0
                bulb_lightning(30, flash_ms=100, flashes=(1,3), delay_ms=80, loops=1, threaded=True)
            count += 0.05
            t.sleep(.05)
            if BreakCheck():
                return
        
        play_audio("cargoHold", "triangleHit.wav", gain=1)
        m1Digital_Write(36, 0)  #triangle strobe
        for i in range(8):
            m1Digital_Write(28, 1)  #filipe ambient
            t.sleep(.1)
            m1Digital_Write(28, 0)  #filipe ambient
            t.sleep(.1)

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

    house.cargoHold_state = "INACTIVE"
    log_event("[cargoHold] Exiting.")

def brig():
    while house.HouseActive or house.Demo:
        m1Digital_Write(37, 0)  # brig ambient on
        m1Digital_Write(34, 1)  # brig strobe/blacklight
        m1Digital_Write(28, 0)  #filipe ambient

        while not rsm.get_button_value("BTN4"):
            t.sleep(.05)
            if BreakCheck():
                return
        
        m1Digital_Write(28, 1)  #filipe ambient
        m1Digital_Write(37, 1)  # brig ambient

        m1Digital_Write(34, 0)  # brig strobe/blacklight

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