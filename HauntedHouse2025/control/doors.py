# control/doors.py
import time as t
import threading
from control.arduino import m1Digital_Write
from utils.tools import log_event, BreakCheck
from context import house
from control import remote_sensor_monitor as rsm

DOOR_SOLENOID_PINS = {1: 23, 2: 25, 3: 26}
DOOR_SENSOR_IDS = {1: "TOF1", 2: "TOF2", 3: "TOF3"}

OBSTRUCT_RETRY_DELAY_S = 3.0
CLOSE_MONITOR_WINDOW_S = 7.5
SENSOR_POLL_S = 0.1

def setDoorState(id, state):
    if id in (1, 2, 3) and state in ("OPEN", "CLOPEN", "CLOSED"):
        house.DoorState[id] = state
        log_event(f"[Doors] Set Door {id} → {state}")
    else:
        log_event(f"[Doors] Invalid door or state: id={id}, state={state}")

def door_process(id):
    log_event(f"[Doors] Door {id} process created.")
    pin = DOOR_SOLENOID_PINS[id]

    def door_sensor_obstructed():
        return rsm.obstructed(DOOR_SENSOR_IDS[id], block_mm=800, window_ms=250, min_consecutive=2)

    def open():
        m1Digital_Write(pin, 1)
        house.DoorState[id] = "OPEN"
        log_event(f"[Doors] Door {id} opened.")

    def close_attempt_until_clear():
        m1Digital_Write(pin, 0)
        start = t.time()
        while t.time() - start < CLOSE_MONITOR_WINDOW_S and house.systemState == "ONLINE":
            if BreakCheck(): return False
            if door_sensor_obstructed():
                # obstruction → reopen, wait, retry
                house.DoorState[id] = "CLOPEN"
                log_event(f"[Doors] Door {id} obstruction detected. Re-opening, waiting, and retrying.")
                m1Digital_Write(pin, 1)
                t.sleep(OBSTRUCT_RETRY_DELAY_S)
                m1Digital_Write(pin, 0)
                start = t.time()  # restart monitor window after retry
            t.sleep(SENSOR_POLL_S)
        # after window with no obstructions, consider closed
        house.DoorState[id] = "CLOSED"
        log_event(f"[Doors] Door {id} closed successfully.")
        return True

    def handle_change():
        target = house.TargetDoorState[id]
        if target == "OPEN":
            open()
        elif target == "CLOSED":
            # keep retrying until closed or system goes offline/BreakCheck
            while house.systemState == "ONLINE" and house.TargetDoorState[id] == "CLOSED":
                if BreakCheck(): break
                if door_sensor_obstructed():
                    # If already obstructed before moving, open, wait, then try
                    house.DoorState[id] = "CLOPEN"
                    log_event(f"[Doors] Door {id} obstruction present before close. Opening and delaying.")
                    m1Digital_Write(pin, 1)
                    t.sleep(OBSTRUCT_RETRY_DELAY_S)
                if close_attempt_until_clear():
                    break
        elif target == "CLOPEN":
            # Treat as: open now, then caller may set CLOSED later
            open()

    def main():
        house.DoorState[id] = "OPEN"
        while house.systemState == "ONLINE":
            if house.DoorState[id] != house.TargetDoorState[id]:
                handle_change()
            if BreakCheck(): break
            t.sleep(0.1)
        log_event(f"[Doors] System shutdown: Door {id} exiting and opening")
        open()

    open()
    main()

def spawn_doors():
    for door_id in DOOR_SOLENOID_PINS.keys():
        house.DoorState[door_id] = "OPEN"
        threading.Thread(target=door_process, args=(door_id,), daemon=True).start()
    log_event("[Doors] All door threads started.")
