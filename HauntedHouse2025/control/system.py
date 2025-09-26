# control/system.py
import time as t
import threading
import multiprocessing

from HauntedHouse2025.rooms import cargoHold, gangway, treasureRoom
from context import house
from control.audio_manager import play_to_named_channel_async, play_to_all_channels_async
from control.arduino import connectArduino, m2Digital_Write
from control.shutdown import shutdownDetector
from control.doors import setDoorState
from control.houseLights import toggleHouseLights
from control.sensor_monitor import analog_update_loop
from control import remote_sensor_monitor
from ui.gui import MainGUI
from ui.http_server import HTTP_SERVER
from utils.tools import log_event, BreakCheck
from rooms import swamp, graveyard
from control.doors import spawn_doors

def initialize_system():
    while True:
        if house.Boot:
            log_event("[System] Initializing persistent services...")

            # Initialize hardware
            connectArduino()
            
            t.sleep(1)

            # Launch core services
            threading.Thread(target=HTTP_SERVER, daemon=True).start()
            threading.Thread(target=MainGUI, daemon=True).start()
            
            house.Boot = False
            
        log_event("[System] Initializing non-persistent services...")

        # threading.Thread(target=analog_update_loop, daemon=True).start() # Commented out because hoping to use only remote sensors
        _, house.remote_sensor_value = remote_sensor_monitor.start_sensor_listener()
                
        spawn_doors()

        t.sleep(0.2)
        house.systemState = "ONLINE"
        log_event("[System] Initialization complete. System is ONLINE.")
            
        threading.Thread(target=shutdownDetector, daemon=True).start()
        
        t.sleep(1)
        
        while house.systemState == "ONLINE":
            t.sleep(1)
        
        log_event("[System] All non-persistent services stopped. Most likely due to shutdown.")
            
        while house.systemState != "REBOOT":
            t.sleep(1) 

def StartHouse():
    if not house.HouseActive and house.systemState == "ONLINE":
        play_to_all_channels_async("starting house")
        log_event("[System] Launching main sequence...")
        house.HouseActive = True

        setDoorState(1, "CLOSED")
        setDoorState(2, "CLOSED")
        toggleHouseLights(False)

        threading.Thread(
            target=graveyard.run, 
            args=(), 
            daemon=True,
            name="graveyard"
        ).start()
        
        t.sleep(5)

        threading.Thread(
            target=gangway.run, 
            args=(), 
            daemon=True, 
            name="gangway"
        ).start()
        
        threading.Thread(
            target=treasureRoom.run, 
            args=(), 
            daemon=True, 
            name="treasureRoom"
        ).start()
        
        threading.Thread(
            target=swamp.run, 
            args=(), 
            daemon=True, 
            name="swampRoom"
        ).start()
        
        threading.Thread(
            target=cargoHold.run, 
            args=(), 
            daemon=True, 
            name="cargoHold"
        ).start()

        while house.HouseActive:
            if BreakCheck():
                t.sleep(2)
                break
            t.sleep(5)

        log_event("[System] Main sequence ended.")
    else:
        if not house.HouseActive and house.systemState != "ONLINE":
            log_event("[System] Cannot start house while it is in a shutdown state.")
        else:
            log_event("[System] House is already active. Please stop the house before attemping to re-start it.")