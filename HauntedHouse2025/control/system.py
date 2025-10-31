# control/system.py
import time as t
import threading
import multiprocessing

from rooms import quarterdeck
from rooms import cargoHold, gangway, treasureRoom
from context import house
from control.audio_manager import play_audio
from control.arduino import connectArduino
from control.shutdown import shutdownDetector
from control.doors import setDoorState
from control.houseLights import toggleHouseLights
from control import remote_sensor_monitor
from ui.gui import MainGUI
from ui.http_server import HTTP_SERVER
from utils.tools import log_event, BreakCheck
from rooms import graveyard
from control.doors import spawn_doors
import control.dimmer_controller as dim
from control.shutdown import shutdown
import random

def initialize_system():
    while True:
        if house.Boot:
            log_event("[System] Initializing persistent services...")

            # Initialize hardware
            connectArduino()
            dim.init()
            
            t.sleep(1)

            shutdown()

            # Launch core services
            threading.Thread(target=HTTP_SERVER, daemon=True, name="HTTP SERVER").start()
            threading.Thread(target=MainGUI, daemon=True, name="GUI").start()
            
            remote_sensor_monitor.init(port="COM6", baud=921600)

            house.Boot = False
            
        log_event("[System] Initializing non-persistent services...")

        spawn_doors()

        t.sleep(0.2)
        house.systemState = "ONLINE"

        toggleHouseLights(True)
        
        log_event("[System] Initialization complete. System is ONLINE.")
            
        threading.Thread(target=shutdownDetector, daemon=True, name="Shutdown Detector").start()
        
        t.sleep(1)
        
        while house.systemState == "ONLINE":
            t.sleep(1)
        
        log_event("[System] All non-persistent services stopped. Most likely due to shutdown.")
            
        while house.systemState != "REBOOT":
            t.sleep(1) 

def StartHouse():
    if not house.HouseActive and house.systemState == "ONLINE":
        play_audio("starting house", gain=0.1)
        log_event("[System] Launching main sequence...")
        house.HouseActive = True

        setDoorState(1, "CLOSED")
        setDoorState(2, "CLOSED")
        toggleHouseLights(False)

        threading.Thread(
            target=graveyard.run, 
            args=(), 
            daemon=True,
            name=graveyard.__name__.split('.')[-1]
        ).start()

        threading.Thread(
            target=gangway.run, 
            args=(), 
            daemon=True, 
            name=gangway.__name__.split('.')[-1]
        ).start()
        
        threading.Thread(
            target=treasureRoom.run, 
            args=(), 
            daemon=True, 
            name=treasureRoom.__name__.split('.')[-1]
        ).start()
        
        threading.Thread(
            target=quarterdeck.run, 
            args=(), 
            daemon=True, 
            name=quarterdeck.__name__.split('.')[-1]
        ).start()
        
        threading.Thread(
            target=cargoHold.run, 
            args=(), 
            daemon=True, 
            name=cargoHold.__name__.split('.')[-1]
        ).start()

        noScareDetector(threaded=True)

        shipAmbience()

        while house.HouseActive:
            if BreakCheck():
                break
            t.sleep(.1)

        log_event("[System] Main sequence ended.")
    else:
        if not house.HouseActive and house.systemState != "ONLINE":
            log_event("[System] Cannot start house while it is in a shutdown state.")
        else:
            log_event("[System] House is already active. Please stop the house before attemping to re-start it.")

def shipAmbience():
    log_event("Playing ship ambience in cargoHold, gangway, and quarterdeck.")
    play_audio("cargoHold", "shipAmbienceCUT.wav", gain=1, looping=True)
    play_audio("gangway", "shipAmbienceCUT.wav", gain=1, looping=True)
    play_audio("quarterdeck", "shipAmbienceCUT.wav", gain=1, looping=True)

def noScareDetector(threaded=False):
    no_scare_files = [
    "noScare1.wav",
    "noScare2.wav",
    "noScare3.wav",
    "noScare4.wav",
    "noScare5.wav",
    "noScare6.wav",
    "noScare7.wav",
    "noScare8.wav",
    "noScare9.wav",
    "noScare10.wav",
    "noScare11.wav"
    ]

    def main():
        while not remote_sensor_monitor.get_button_value("BTN3"):
            t.sleep(.05)
            if BreakCheck():
                return

        t.sleep(1)

        while not remote_sensor_monitor.get_button_value("BTN3"):
            audio = random.choice(no_scare_files)
            play_audio(audio, threaded=True)

            for i in range(300): #15 secs
                if remote_sensor_monitor.get_button_value("BTN3"):
                    break
                t.sleep(.05)
                if BreakCheck():
                    return
                
        for i in range(5):
            t.sleep(1)
            if BreakCheck():
                return
            
    if threaded:
        threading.Thread(target=main, daemon=True, name="no scare detector").start()
    else:
        main()
        
