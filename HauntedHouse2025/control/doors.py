# control/doors.py
import time as t
import threading
from control.arduino import m1Digital_Write
from utils.tools import log_event
from context import house
from control import remote_sensor_monitor as rsm

# -----------------------------
# Configuration
# -----------------------------
DOOR_SOLENOID_PINS = {1: 47, 2: 38}
DOOR_SENSOR_IDS    = {1: "TOF3", 2: "TOF5"}

OBSTRUCT_RETRY_DELAY_S   = 3.0
CLOSE_MONITOR_WINDOW_S   = 5
SENSOR_POLL_S            = 0.05

# Time to ignore the TOF after commanding a close (door/frame self-pass)
# Tune per-door: typical 0.4–0.8s
DOOR_SELF_PASS_IGNORE_S = {1: 0.01, 2: 0.01}

# Obstruction thresholds + detection profiles
BLOCK_MM_ENTER   = {1: 1500, 2: 1050}   # threshold to consider "blocked"
BLOCK_MM_CLEAR   = 900   # reserved if you later implement explicit hysteresis in rsm

IDLE_WINDOW_MS   = 250   # sensitive when idle
IDLE_MIN_CONSEC  = 2

MOVING_WINDOW_MS  = 250  # stricter while closing (filters the door edge)
MOVING_MIN_CONSEC = 2

CLEAR_HOLD_S      = 4  # require brief clear before declaring closed


def setDoorState(id, state):
    if id in (1, 2) and state in ("OPEN", "CLOPEN", "CLOSED"):
        house.TargetDoorState[id] = state
        #print(house.TargetDoorState[id])
        #print(house.DoorState[id])
        log_event(f"[Doors] Set Door {id} → {state}")
    else:
        log_event(f"[Doors] Invalid door or state: id={id}, state={state}")


def door_process(id: int):
    log_event(f"[Doors] Door {id} process created.")
    pin = DOOR_SOLENOID_PINS[id]

    # Mode-aware obstruction check
    def door_sensor_obstructed(moving: bool) -> bool:
        if moving:
            return rsm.obstructed(
                DOOR_SENSOR_IDS[id],
                block_mm=BLOCK_MM_ENTER[id],
                window_ms=MOVING_WINDOW_MS,
                min_consecutive=MOVING_MIN_CONSEC
            )
        else:
            return rsm.obstructed(
                DOOR_SENSOR_IDS[id],
                block_mm=BLOCK_MM_ENTER[id],
                window_ms=IDLE_WINDOW_MS,
                min_consecutive=IDLE_MIN_CONSEC
            )

    def open():
        m1Digital_Write(pin, 1)
        house.DoorState[id] = "OPEN"
        log_event(f"[Doors] Door {id} opened.")

    def close_attempt_until_clear():
        # Command close
        m1Digital_Write(pin, 0)

        # Ignore the door’s own pass across the TOF
        t.sleep(DOOR_SELF_PASS_IGNORE_S[id])

        start = t.time()
        last_clear_ts = t.time()

        while (t.time() - start) < CLOSE_MONITOR_WINDOW_S and house.systemState == "ONLINE":
            if not house.systemState == "ONLINE":
                return False

            if door_sensor_obstructed(moving=True):
                # obstruction → reopen, wait, retry
                log_event(f"[Doors] Door {id} obstruction detected while closing. Re-opening, waiting, retrying.")
                m1Digital_Write(pin, 1)                 # reopen
                t.sleep(OBSTRUCT_RETRY_DELAY_S)
                m1Digital_Write(pin, 0)                 # try to close again
                t.sleep(DOOR_SELF_PASS_IGNORE_S[id])    # ignore self-pass again
                start = t.time()                        # restart monitor window
                last_clear_ts = t.time()
            else:
                # currently clear; track how long it stays clear
                if (t.time() - last_clear_ts) >= CLEAR_HOLD_S:
                    house.DoorState[id] = "CLOSED"
                    log_event(f"[Doors] Door {id} closed successfully.")
                    return True

            t.sleep(SENSOR_POLL_S)

        # Timeout reached without a recent obstruction; consider closed
        house.DoorState[id] = "CLOSED"
        log_event(f"[Doors] Door {id} closed (timeout reached, no obstruction).")
        return True

    def handle_change():
        target = house.TargetDoorState[id]
        if target == "OPEN":
            open()

        elif target == "CLOSED":
            # keep retrying until closed or system goes offline/BreakCheck
            while house.systemState == "ONLINE" and house.TargetDoorState[id] == "CLOSED":
                if not house.systemState == "ONLINE":
                    break

                # If obstructed before moving, be sensitive (idle profile)
                if door_sensor_obstructed(moving=False):
                    log_event(f"[Doors] Door {id} obstruction present before close. Opening and delaying.")
                    m1Digital_Write(pin, 1)
                    t.sleep(OBSTRUCT_RETRY_DELAY_S)

                if close_attempt_until_clear():
                    break

        elif target == "CLOPEN":
            open()
            for i in range(12):
                if not house.systemState == "ONLINE":
                    break
                t.sleep(1)
            setDoorState(id, "CLOSED")


    def main():
        house.DoorState[id] = "OPEN"

        t.sleep(1)  # allow system to startup
        
        while house.systemState == "ONLINE":
            #print("Door running:", id)
            if house.DoorState[id] != house.TargetDoorState[id]:
                handle_change()
            t.sleep(0.05)

        log_event(f"[Doors] System shutdown: Door {id} opening")
        open()

    open()
    main()


def spawn_doors():
    for door_id in DOOR_SOLENOID_PINS.keys():
        house.DoorState[door_id] = "OPEN"
        house.TargetDoorState[door_id] = "OPEN"
        threading.Thread(target=door_process, args=(door_id,), daemon=True, name=f"Door {door_id} Process").start()
    log_event("[Doors] All door threads started.")
