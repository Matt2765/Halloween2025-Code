# control/system.py
import time as t
import threading
import multiprocessing

from context import house
from control.audio_manager import play_to_named_channel
from control.arduino import connectArduino, m2Digital_Write
from control.shutdown import shutdownDetector
from control.doors import setDoorState
from control.houseLights import toggleHouseLights
from control.sensor_monitor import analog_update_loop
from control import remote_sensor_monitor
from ui.gui import MainGUI
from ui.http_server import HTTP_SERVER
from utils.tools import log_event, BreakCheck
from rooms import cave, mirror, swamp, mask, graveyard
from control.doors import spawn_doors

def initialize_system():
    log_event("[System] Initializing haunted house...")

    # Initialize hardware
    connectArduino()
    
    t.sleep(1)

    # Launch core services
    threading.Thread(target=HTTP_SERVER, daemon=True).start()
    threading.Thread(target=MainGUI, daemon=True).start()
    # threading.Thread(target=analog_update_loop, daemon=True).start() # Commented out because hoping to use only remote sensors
    _, house.remote_sensor_value = remote_sensor_monitor.start_sensor_listener()
    spawn_doors()

    t.sleep(0.2)
    log_event("[System] Initialization complete. System is ONLINE.")
    house.systemState = "ONLINE"
    
    threading.Thread(target=shutdownDetector, daemon=True).start()
    
    while True:
        if house.systemState != "ONLINE":
            #while house.systemState != "ONLINE":    # wait until system is back online, so program doesnt completely stop
            #    t.sleep(1)
            break
        else:
            t.sleep(1)
            
    log_event("[System] All services stopped. Most likely due to shutdown.")


def StartHouse():
    if not house.HouseActive and house.systemState == "ONLINE":
        log_event("[System] Launching main sequence...")
        house.HouseActive = True

        setDoorState(1, "CLOSED")
        setDoorState(2, "CLOSED")
        toggleHouseLights(False)

        threading.Thread(target=graveyard.run, args=(), daemon=True).start()
        t.sleep(5)

        threading.Thread(target=cave.run, args=(), daemon=True).start()
        threading.Thread(target=mirror.run, args=(), daemon=True).start()
        threading.Thread(target=swamp.run, args=(), daemon=True).start()
        threading.Thread(target=mask.run, args=(), daemon=True).start()

        while house.HouseActive:
            if BreakCheck():
                t.sleep(2)
                break
            t.sleep(5)

        log_event("[System] Main sequence ended.")
    else:
        if not house.HouseActive and house.systemState != "ONLINE":
            log_event("Cannot start house while it is in a shutdown state.")
        else:
            log_event("House is already active. Please stop the house before attemping to re-start it.")