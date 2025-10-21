# control/shutdown.py
import time as t
from utils.tools import log_event
from context import house
from control.houseLights import toggleHouseLights
from control.audio_manager import play_audio, stop_all_audio
from control.arduino import m1Digital_Write

def shutdown():
    log_event("[Shutdown] Executing shutdown routine...")

    house.HouseActive = False
    house.testing = False

    log_event("SHUTDOWN - gangway:")
    m1Digital_Write(54, 0)
    log_event("TR Air Blast 4 OFF")
    m1Digital_Write(27, 0)
    log_event("TR Ambient Lights 1 OFF")
    m1Digital_Write(35, 0)
    log_event("TR Lightning 1 OFF")
    m1Digital_Write(34, 0)
    log_event("TR Lightning 2 OFF")
    m1Digital_Write(36, 0)
    log_event("Can Lights OFF")
    m1Digital_Write(32, 0)
    log_event("TR Strobe 2 OFF")
    m1Digital_Write(30, 0)
    log_event("TR Swamp Monster Light OFF")
    m1Digital_Write(55, 0)
    log_event("TR Swamp Monster Solenoid OFF")

    log_event("SHUTDOWN - TREASURE ROOM:")
    m1Digital_Write(41, 0)
    log_event("TR Ambient Lights 2 OFF")
    m1Digital_Write(31, 0)
    log_event("TR Treasure Room Light OFF")

    log_event("SHUTDOWN - SWAMP ROOM:")
    m1Digital_Write(40, 0)
    log_event("SR Lightning 3 OFF")
    m1Digital_Write(24, 0)
    log_event("SR Lightning 4 OFF")
    m1Digital_Write(28, 0)
    log_event("SR Lightning 5 OFF")
    m1Digital_Write(61, 0)
    log_event("SR Air Explosion 2 OFF")
    log_event("SR Swamp Lasers OFF")
    m1Digital_Write(37, 0)
    log_event("SR Overhang Safety OFF")
    m1Digital_Write(62, 0)
    log_event("Bu Forward OFF")
    m1Digital_Write(63, 0)
    log_event("Bu Up/Down OFF")

    log_event("SHUTDOWN - CARGO HOLD:")
    m1Digital_Write(33, 0)
    log_event("MkR Ambient Light 4 Blacklight OFF")
    m1Digital_Write(23, 0)
    log_event("MkR Strobe 3 OFF")
    m1Digital_Write(59, 0)
    log_event("MkR Air Blast 3 OFF")

    log_event("SHUTDOWN - GRAVEYARD:")
    m1Digital_Write(57, 0)
    log_event("GY Rock Spider Solenoid OFF")

    t.sleep(1)
    toggleHouseLights(True)

def shutdownDetector():
    while house.systemState == "ONLINE":
        t.sleep(1)

    t.sleep(1)

    if house.systemState == "EmergencyShutoff":
        log_event("EMERGENCY SHUTDOWN DETECTED - Please type keyword 'SAFE' into terminal to return to standby mode.")
        stop_all_audio()
        #t.sleep(.1)
        play_audio("emergency shutdown activated", gain=0.1)
        shutdown()
        for _ in range(3):
            toggleHouseLights(True)
            t.sleep(0.25)
            toggleHouseLights(False)
            t.sleep(0.25)
        while True:
            input1 = input().upper()
            if input1 == "SAFE":
                play_audio(f"returning to standby in 5 seconds", gain=0.1)
                for a in range(5, 0, -1):
                    log_event(f"Returning to standby in {a} seconds.")
                    t.sleep(1)
                house.systemState = "REBOOT"
                play_audio("system rebooting", gain=0.1)
                break
            else:
                log_event("Invalid command. Please type keyword 'SAFE' into terminal to return to standby mode.")

    elif house.systemState == "SoftShutdown":
        log_event("SOFT SHUTDOWN DETECTED - Systems will be restarted to standby.")
        stop_all_audio()
        #t.sleep(1)
        play_audio("soft shutdown activated", gain=0.1)
        shutdown()
        for _ in range(3):
            toggleHouseLights(True)
            t.sleep(0.25)
            toggleHouseLights(False)
            t.sleep(0.25)
        play_audio(f"returning to standby in 5 seconds", gain=0.1)
        for a in range(5, 0, -1):
            log_event(f"Returning to standby in {a} seconds.")
            t.sleep(1)
        house.systemState = "REBOOT"
        play_audio("system rebooting", gain=0.1)

    else:
        log_event("Shutdown ID unknown - Please type keyword 'SAFE' into terminal to return to standby mode.")
        stop_all_audio()
        #t.sleep(.2)
        play_audio("unknown shutdown detected", gain=0.1)
        shutdown()
        while True:
            input1 = input().upper()
            if input1 == "SAFE":
                house.systemState = "REBOOT"
                break
            else:
                log_event("Invalid command. Please type keyword 'SAFE' into terminal to return to standby mode.")
