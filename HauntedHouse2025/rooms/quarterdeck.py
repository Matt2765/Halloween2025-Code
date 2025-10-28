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

def run():
    log_event("[Quaterdeck] Starting...")
    house.SRstate = "ACTIVE"

    play_audio("quarterdeck", "quarterdeckAmbient.wav", gain=.5, looping=True)

    while house.HouseActive or house.Demo:
        log_event("[Quaterdeck] Running loop...")

        #m1Digital_Write(23, 0) #lightning

        #m1Digital_Write(4, 0) #Drop down light

        #m1Digital_Write(9, 0) #strobe

        count = 0
        while not rsm.obstructed("TOF2", block_mm=2500, window_ms=250, min_consecutive=2):
            #print("looping")
            #print(count)
            if count > 2:
                count = 0
                lightning(
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

        for i in range(6):
            lightning(
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

        t.sleep(3)
        if BreakCheck():
            break

        m1Digital_Write(9,0)  # strobe on

        play_audio("quarterdeck", "quarterdeckHit1.wav", gain=2)

        setDoorState(2, "OPEN")  # open door to next room

        for i in range(4):
            if BreakCheck():
                break
            t.sleep(1)

        for i in range(6):  
            lightning(
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

        setDoorState(2, "CLOSED")  # close door to next room

        m1Digital_Write(9,1)  # strobe off

        if BreakCheck() or house.Demo: # end on breakCheck or if demo'ing
            house.Demo = False
            toggleHouseLights(True)
            break

    house.SRstate = "INACTIVE"
    log_event("[Quaterdeck] Exiting.")

def dropDownFlash(loops, threaded=True):
    def main():
        log_event(f"[DropDown] Starting drop-down flash sequence ({loops} flashes)")
        for i in range(loops):
            if BreakCheck():
                log_event(f"[DropDown] Interrupted")
                return
            m1Digital_Write(4, 0)  # ON
            print("0")
            t.sleep(.15)
            m1Digital_Write(4, 1)  # OFF
            print("1")
            t.sleep(.15)

        log_event(f"[DropDown] Drop-down flash sequence complete")

    if threaded:
        threading.Thread(target=main, daemon=True, name="QD drop-down flash").start()
    else:
        main()

def lightning(pin: int, flash_ms: int = 100, flashes: range = (1, 3), delay_ms: int = 80, loops: int = 1, loop_delay_range: range = (1, 3), threaded: bool = True):
    """
    Simulate lightning by flashing a relay output rapidly (threaded, interruptible, naturalized).

    Args:
        pin (int): Digital pin to control (e.g., 23)
        flash_ms (int): Base ON duration (ms)
        flashes (int): Number of flashes
        delay_ms (int): Base delay between flashes (ms)
    """
    def _run():
        log_event(f"[Lightning] Starting lightning sequence on D{pin} ({flashes} flashes)")
        for i in range(loops):
            for i in range(random.randint(flashes[0], flashes[1])):
                if BreakCheck():
                    log_event(f"[Lightning] Interrupted on D{pin}")
                    return

                # Randomize flash and delay slightly for realism
                on_time = flash_ms / 1000 * random.uniform(0.7, 1.3)
                off_time = delay_ms / 1000 * random.uniform(0.5, 1.5)

                m1Digital_Write(pin, 0)  # ON
                t.sleep(on_time)

                if BreakCheck():
                    log_event(f"[Lightning] Interrupted on D{pin}")
                    return

                m1Digital_Write(pin, 1)  # OFF
                t.sleep(off_time)

            if BreakCheck():
                log_event(f"[Lightning] Interrupted on D{pin}")
                return
            t.sleep(random.uniform(loop_delay_range[0], loop_delay_range[1]))

        log_event(f"[Lightning] Lightning sequence complete on D{pin}")

    if threaded:
        threading.Thread(target=_run, daemon=True, name="QD lightning").start()
    else:
        _run()
