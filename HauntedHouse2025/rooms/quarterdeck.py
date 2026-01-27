# rooms/quarterdeck.py
import time as t
from context import house
from control.audio_manager import play_audio
from utils.tools import BreakCheck, log_event
from control import dimmer_controller as dim
from control.arduino import m1Digital_Write
import threading
import random
from control import remote_sensor_monitor as rsm
from control.doors import setDoorState
from control.houseLights import toggleHouseLights
from control.lightning import bulb_lightning

def run():
    log_event("[Quaterdeck] Starting...")
    house.quarterdeck_state = "ACTIVE"

    play_audio("quarterdeck", "quarterdeckAmbient.wav", gain=.5, looping=True)

    prisonerArms(threaded=True)

    while house.HouseActive or house.Demo:
        log_event("[Quaterdeck] Running loop...")

        #m1Digital_Write(23, 0) #lightning

        #m1Digital_Write(4, 0) #Drop down light

        #m1Digital_Write(9, 0) #strobe

        #m1Digital_Write(53, 0) #prisoner arms

        count = 0
        while not rsm.obstructed("TOF2", block_mm=2500, window_ms=250, min_consecutive=2):
            #print("looping")
            #print(count)
            if count > 2:
                count = 0
                bulb_lightning(
                    23, 
                    flash_ms=100, 
                    flashes=(3,5), 
                    delay_ms=80, loops=1, 
                    loop_delay_range=(1,3), 
                    threaded=True
                )
            if BreakCheck():
                return
            t.sleep(.05)
            count += 0.05

        #t.sleep(1)

        play_audio("quarterdeck", "quarterdeckTease1.wav", gain=.7)
        dropDownFlash(loops=15, threaded=True)

        for i in range(7):
            bulb_lightning(
                    23, 
                    flash_ms=100, 
                    flashes=(1,3), 
                    delay_ms=80, loops=1, 
                    loop_delay_range=(1,3), 
                    threaded=True
                )
            if BreakCheck():
                break
            t.sleep(1)  

        #t.sleep(1)
        if BreakCheck():
            break

        play_audio("quarterdeck", "quarterdeckHitLayer1.wav", gain=1)

        for i in range(1):
            if BreakCheck():
                break
            t.sleep(1)

        play_audio("quarterdeck", "Digital_Scream_3.wav", gain=.7)

        t.sleep(.5)

        m1Digital_Write(9,0)  # strobe on
        log_event("[quarterdeck] Strobe ON")

        t.sleep(.5)

        play_audio("quarterdeck", "Horrific_1.wav", gain=1)

        for i in range(4):
            if BreakCheck():
                break
            t.sleep(1)

        #setDoorState(2, "OPEN")  # open door to next room

        for i in range(20):  
            bulb_lightning(
                    23, 
                    flash_ms=100, 
                    flashes=(1,3), 
                    delay_ms=80, loops=1, 
                    loop_delay_range=(1,3), 
                    threaded=True
                )    
            if BreakCheck():
                break
            t.sleep(1)

        #setDoorState(2, "CLOSED")  # close door to next room

        m1Digital_Write(9,1)  # strobe off
        log_event("[quarterdeck] Strobe OFF")

        if BreakCheck() or house.Demo: # end on breakCheck or if demo'ing
            if house.Demo:
                house.Demo = False
                house.HouseActive = False
            toggleHouseLights(True)
            return

    house.quarterdeck_state = "INACTIVE"
    log_event("[Quaterdeck] Exiting.")

def prisonerArms(threaded=False):
    log_event("[quarterdeck] Starting prisoner arms...")
    def main():
        while house.HouseActive or house.Demo:
            m1Digital_Write(53, 0) #prisoner arms
            t.sleep(random.uniform(.1, 1))
            m1Digital_Write(53, 1) #prisoner arms
            t.sleep(random.uniform(.1, 1))
            if BreakCheck():
                return

    if threaded:
        threading.Thread(target=main, daemon=True, name="prisoner arms").start()
    else:
        main()


def dropDownFlash(loops, threaded=True):
    def main():
        log_event(f"[DropDown] Starting drop-down flash sequence ({loops} flashes)")
        for i in range(loops):
            if BreakCheck():
                log_event(f"[DropDown] Interrupted")
                return
            m1Digital_Write(4, 0)  # ON
            #print("0")
            t.sleep(.15)
            m1Digital_Write(4, 1)  # OFF
            #print("1")
            t.sleep(.15)

        log_event(f"[DropDown] Drop-down flash sequence complete")

    if threaded:
        threading.Thread(target=main, daemon=True, name="QD drop-down flash").start()
    else:
        main()