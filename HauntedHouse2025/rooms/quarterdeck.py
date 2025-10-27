# rooms/swamp.py
import time as t
from context import house
from control.audio_manager import play_audio
from utils.tools import BreakCheck, log_event
from control import dimmer_controller as dim
from control.arduino import m1Digital_Write
import threading
import random
from control import remote_sensor_monitor as rsm

def run():
    log_event("[SwampRoom] Starting...")
    house.SRstate = "ACTIVE"

    while house.HouseActive or house.Demo:
        log_event("[SwampRoom] Running loop...")

        #m1Digital_Write(23, 0) #lightning

        #m1Digital_Write(4, 0) #Drop down light

        #m1Digital_Write(9, 0) #strobe

        while not rsm.obstructed("TOF2", block_mm=1000, window_ms=250, min_consecutive=2):
            for i in range(1):
                if BreakCheck():
                    return
                t.sleep(1)

        lightning(
            23, 
            flash_ms=100, 
            flashes=(3,5), 
            delay_ms=80, loops=1, 
            loop_delay_range=(1,3), 
            threaded=False
            )

        if BreakCheck() or house.Demo: # end on breakCheck or if demo'ing
            house.Demo = False
            break

    house.SRstate = "INACTIVE"
    log_event("[SwampRoom] Exiting.")

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
