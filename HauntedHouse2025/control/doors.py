# control/doors.py
import time as t
import threading
from control.arduino import m1Digital_Write
from utils.tools import log_event, BreakCheck
from context import house

DOOR_SOLENOID_PINS = {
    1: 23,
    2: 25,
    3: 26
}

DOOR_SENSOR_IDS = {
    1: "TOF1",
    2: "TOF2",
    3: "TOF3"
}

def setDoorState(id, state):
    if id in (1, 2, 3) and state in ("OPEN", "CLOPEN", "CLOSED"):
        house.DoorState[id] = state
        log_event(f"[Doors] Set Door {id} → {state}")
    else:
        log_event(f"[Doors] Invalid door or state: id={id}, state={state}")

def door_process(id):
    log_event(f"[Doors] Door {id} process created.")

    def open():
        target_state = house.DoorState[id]
        m1Digital_Write(DOOR_SOLENOID_PINS[id], 1)
        log_event(f"[Doors] Door {id} opening...")
        for _ in range(12):
            if house.DoorState[id] != target_state:
                return False
            t.sleep(0.5)
        house.DoorState[id] = "OPEN"
        log_event(f"[Doors] Door {id} OPEN")
        return True

    def open_fast():
        target_state = house.DoorState[id]
        m1Digital_Write(DOOR_SOLENOID_PINS[id], 1)
        log_event(f"[Doors] Door {id} fast opening...")
        for _ in range(4):
            if house.DoorState[id] != target_state:
                return False
            t.sleep(0.5)
        house.DoorState[id] = "OPEN"
        log_event(f"[Doors] Door {id} OPEN")
        return True

    def close():
        target_state = house.DoorState[id]
        m1Digital_Write(DOOR_SOLENOID_PINS[id], 0)
        log_event(f"[Doors] Door {id} closing...")
        for _ in range(5):
            door_sensor_check()
            t.sleep(0.1)
        for _ in range(16):
            if door_sensor_check():
                return False
            if house.DoorState[id] != target_state:
                log_event(f"[Doors] Door {id} interrupted")
                return "ChangeTarget"
            t.sleep(0.3)
        house.DoorState[id] = "CLOSED"
        log_event(f"[Doors] Door {id} CLOSED")
        return True

    def door_sensor_check():
        #replace with remote sensor check
        return 

    def handle_change(last_state):
        if house.DoorState[id] == "OPEN":
            if not open():
                log_event(f"[Doors] Door {id} interrupted during open")
                return handle_change(last_state)
            return "OPEN"

        elif house.DoorState[id] == "CLOPEN":
            open()
            delay = 12 if id in (2, 3) else 6
            for _ in range(delay):
                if BreakCheck():
                    return "CLOSED"
                t.sleep(1)
            while True:
                if BreakCheck():
                    return "CLOSED"
                if not close():
                    log_event(f"[Doors] Door {id} obstructed, reopening")
                    open_fast()
                    t.sleep(1)
                    house.DoorState[id] = "CLOSED"
                    return handle_change(last_state)
                return "CLOSED"

        else:
            result = close()
            if not result:
                log_event(f"[Doors] Door {id} obstructed, reopening")
                open_fast()
                house.DoorState[id] = "CLOSED"
                return handle_change(last_state)
            if result == "ChangeTarget":
                return handle_change(last_state)
            return "CLOSED"

    def main():
        last_state = "OPEN"
        while house.systemState == "ONLINE":
            if last_state != house.DoorState[id]:
                last_state = handle_change(last_state)
                t.sleep(0.5)
            t.sleep(0.5)
        log_event(f"[Doors] System shutdown: Door {id} exiting and opening")
        open()

    open()
    main()

def spawn_doors():
    for door_id in DOOR_SOLENOID_PINS.keys():
        house.DoorState[door_id] = "OPEN"
        threading.Thread(target=door_process,args=(door_id,),daemon=True).start()
    log_event("[Doors] All door threads started.")
