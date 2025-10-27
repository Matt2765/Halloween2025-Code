# control/shutdown.py
import time as t
from utils.tools import log_event
from context import house
from control.houseLights import toggleHouseLights
from control.audio_manager import play_audio, stop_all_audio
from control.arduino import m1Digital_Write
from utils.thread_diag import dump_threads

def shutdown():
    log_event("[Shutdown] Executing shutdown routine...")

    house.HouseActive = False
    house.testing = False

    def _dim_off(ch: int):
        for fn_name in ("dim_set", "setDimLevel", "set_dimmer_level",
                        "dimmer_set", "set_dimmer", "setDimmer"):
            fn = globals().get(fn_name)
            if callable(fn):
                try:
                    fn(ch, 0)
                    return True
                except Exception:
                    pass
        return False

    # ---------------- Gangway ----------------
    log_event("SHUTDOWN - Gangway:")
    m1Digital_Write(47, 1); log_event("+12v Door, Solenoid A OFF")
    m1Digital_Write(33, 1); log_event("+120v Ambient Lights A OFF")
    m1Digital_Write(35, 1); log_event("+120v Strobe A OFF")

    # ---------------- Treasure Room ----------------
    log_event("SHUTDOWN - Treasure Room:")
    m1Digital_Write(3, 1);  log_event("+120v Ambient Light 4 (G) OFF")   # moved from DIM CH.4 -> D3
    m1Digital_Write(2, 1);  log_event("+120v Strobe 3 (G) OFF")
    m1Digital_Write(26, 1); log_event("+120v Lightning (G) OFF")
    m1Digital_Write(24, 1); log_event("+120v Blacklight (G) OFF")

    # ---------------- Quarterdeck ----------------
    log_event("SHUTDOWN - Quarterdeck:")
    m1Digital_Write(9,  1); log_event("+120v Strobe 2 (F) OFF")           # NEW: D9
    m1Digital_Write(23, 1); log_event("+120v Lightning (B) OFF")
    m1Digital_Write(53, 1); log_event("+12v Prisoner Arms (F) OFF")
    m1Digital_Write(38, 1); log_event("+12v Door 2, Solenoid F OFF")
    m1Digital_Write(4,  1); log_event("+120v Drop Down Light (B) OFF")    # D4

    # ---------------- Graveyard ----------------
    log_event("SHUTDOWN - Graveyard:")
    m1Digital_Write(45, 1); log_event("+12v Enemy Cannon Solenoid (L) OFF")
    m1Digital_Write(58, 1); log_event("+120v Enemy Cannon Smoke Machine (L) OFF")
    m1Digital_Write(31, 1); log_event("+120v Enemy Cannon Muzzle Flash (L) OFF")
    m1Digital_Write(40, 1); log_event("+12v Water Blast (M) OFF")
    m1Digital_Write(6,  1); log_event("+120v Ship Lights 1 (M) OFF")      # D6
    m1Digital_Write(7,  1); log_event("+120v Ship Lights 2 (M) OFF")      # D7

    # ---------------- Cargo Hold ----------------
    log_event("SHUTDOWN - Cargo Hold:")
    m1Digital_Write(49, 1); log_event("+12v Barrel Solenoid (D) OFF")
    m1Digital_Write(30, 1); log_event("+120v Lightning 2 (D) OFF")
    m1Digital_Write(51, 1); log_event("+12v Rowing Skeleton Motor (D) OFF")
    m1Digital_Write(28, 1); log_event("+120v Ambient Light 6 (D) OFF")
    m1Digital_Write(39, 1); log_event("+12v Cannon 1 Solenoid (I) OFF")
    m1Digital_Write(25, 1); log_event("+120v Cannon 1 Muzzle Flash (I) OFF")
    m1Digital_Write(61, 1); log_event("Cannon 1 Smoke Machine (I) OFF")
    m1Digital_Write(41, 1); log_event("+12v Cannon 2 Solenoid (H) OFF")
    m1Digital_Write(27, 1); log_event("+120v Cannon 2 Muzzle Flash (H) OFF")
    m1Digital_Write(60, 1); log_event("Cannon 2 Smoke Machine (H) OFF")

    # ---------------- Brig ----------------
    log_event("SHUTDOWN - Brig:")
    m1Digital_Write(37, 1); log_event("+120v Ambient Lights 2 (C) OFF")
    m1Digital_Write(36, 1); log_event("+120v Strobe 6 (C) OFF")
    m1Digital_Write(34, 1); log_event("+120v Strobe 4 / Blacklight (C) OFF")
    m1Digital_Write(5,  1); log_event("+120v Ambient Light 7 (C) OFF")    # moved from DIM CH.3 -> D5

    # ---------------- Deck ----------------
    log_event("SHUTDOWN - Deck:")
    m1Digital_Write(43, 1); log_event("+12v Falling Mast Solenoid (E) OFF")
    m1Digital_Write(29, 1); log_event("+120v Lightning 4 (E) OFF")
    m1Digital_Write(59, 1); log_event("Fire Lights Smoke Machine (E) OFF")
    m1Digital_Write(32, 1); log_event("+120v Strobes (K) OFF")
    if _dim_off(2): log_event("+120v Fire Lights (K) DIM CH.2 OFF")
    m1Digital_Write(8,  1); log_event("+120v Ambient Lights 5 (K) OFF")   # D8 (no longer DIM CH.7)

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
            toggleHouseLights(False)
            t.sleep(0.25)
            toggleHouseLights(True)
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
            toggleHouseLights(False)
            t.sleep(0.25)
            toggleHouseLights(True)
            t.sleep(0.25)
        play_audio(f"returning to standby in 5 seconds", gain=0.1)
        for a in range(5, 0, -1):
            log_event(f"Returning to standby in {a} seconds.")
            t.sleep(1)
        #dump_threads()
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
