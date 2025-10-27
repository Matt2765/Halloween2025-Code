# ui/gui.py
import tkinter as tk
import threading
from rooms import cargoHold, gangway, treasureRoom, graveyard, quarterdeck
from context import house
from control.shutdown import shutdown
from control.doors import setDoorState
from control.houseLights import toggleHouseLights
from utils.tools import log_event

# NEW: read-only sensor values
from control import remote_sensor_monitor as rsm  # minimal addition


def demoEvent(room):
    house.Demo = True
    house.HouseActive = True
    toggleHouseLights(False)
    log_event(f"[GUI] Starting demo of {room}")

    if room == gangway.__name__.split('.')[-1]:
        threading.Thread(target=gangway.run, args=(), name=f"{room} demo").start()
    elif room == treasureRoom.__name__.split('.')[-1]:
        threading.Thread(target=treasureRoom.run, args=(), name=f"{room} demo").start()
    elif room == quarterdeck.__name__.split('.')[-1]:
        threading.Thread(target=quarterdeck.run, args=(), name=f"{room} demo").start()
    elif room == cargoHold.__name__.split('.')[-1]:
        threading.Thread(target=cargoHold.run, args=(), name=f"{room} demo").start()
    elif room == graveyard.__name__.split('.')[-1]:
        threading.Thread(target=graveyard.run, args=(), name=f"{room} demo").start()


def change_system_state(new_state):
    house.systemState = new_state


def MainGUI():
    from control.system import StartHouse
    log_event(f"[GUI] Booting main GUI...")

    root = tk.Tk()
    root.configure(background="orange")
    root.title("Halloween 2025 Control Panel")
    root.geometry("465x1080")

    tk.Label(root, text="MAINS", font=("Helvetica bold", 15), bg="orange").place(x=25, y=15)
    tk.Label(root, text="DOOR CONTROLS", font=("Helvetica bold", 15), bg="orange").place(x=25, y=200)
    tk.Label(root, text="DEMO CONTROLS", font=("Helvetica bold", 15), bg="orange").place(x=25, y=395)
    tk.Label(root, text="ADVANCED CONTROLS", font=("Helvetica bold", 15), bg="orange").place(x=25, y=535)

    tk.Button(root, text="START HAUNTED HOUSE", height=3, width=25, bg="turquoise1",
              command=lambda: threading.Thread(target=StartHouse, daemon=True).start()).place(x=250, y=50)
    tk.Button(root, text="EMERGENCY SHUTOFF", height=3, width=25, bg="red",
              command=lambda: change_system_state("EmergencyShutoff")).place(x=25, y=50)
    tk.Button(root, text="SOFT SHUTDOWN", height=3, width=25, bg="yellow",
              command=lambda: change_system_state("SoftShutdown")).place(x=25, y=125)

    tk.Button(root, text="Open Door 1", height=2, width=15,
              command=lambda: setDoorState(1, "OPEN")).place(x=25, y=235)
    tk.Button(root, text="Close Door 1", height=2, width=15,
              command=lambda: setDoorState(1, "CLOSED")).place(x=150, y=235)
    tk.Button(root, text="Open Door 2", height=2, width=15,
              command=lambda: setDoorState(2, "OPEN")).place(x=25, y=285)
    tk.Button(root, text="Close Door 2", height=2, width=15,
              command=lambda: setDoorState(2, "CLOSED")).place(x=150, y=285)

    # DEMO CONTROLS (strip "rooms." prefix)
    tk.Button(root, text=f"Demo {gangway.__name__.split('.')[-1]}", height=2, width=15,
              command=lambda: demoEvent(gangway.__name__.split('.')[-1])).place(x=150, y=430)
    tk.Button(root, text=f"Demo {quarterdeck.__name__.split('.')[-1]}", height=2, width=15,
              command=lambda: demoEvent(quarterdeck.__name__.split('.')[-1])).place(x=25, y=430)
    tk.Button(root, text=f"Demo {graveyard.__name__.split('.')[-1]}", height=2, width=15,
              command=lambda: demoEvent(graveyard.__name__.split('.')[-1])).place(x=275, y=430)
    tk.Button(root, text=f"Demo {treasureRoom.__name__.split('.')[-1]}", height=2, width=15,
              command=lambda: demoEvent(treasureRoom.__name__.split('.')[-1])).place(x=25, y=480)
    tk.Button(root, text=f"Demo {cargoHold.__name__.split('.')[-1]}", height=2, width=15,
              command=lambda: demoEvent(cargoHold.__name__.split('.')[-1])).place(x=150, y=480)

    tk.Button(root, text="Start Testing", height=2, width=15, command=None).place(x=25, y=570)
    tk.Button(root, text="Toggle House Lights", height=3, width=25, bg="chartreuse2",
              command=toggleHouseLights).place(x=250, y=125)

    # -------------------------------------------------------------------------
    # NEW: SENSOR + BUTTON STATUS PANEL (bottom section)
    # -------------------------------------------------------------------------
    SECTION_Y = 640
    tk.Label(root, text="SENSORS & BUTTONS", font=("Helvetica bold", 15), bg="orange").place(x=25, y=SECTION_Y)

    # ----- TOF sensors (TOF1..TOF5)
    tof_ids = ["TOF1", "TOF2", "TOF3", "TOF4", "TOF5"]
    tof_labels = {}
    base_y = SECTION_Y + 35
    for i, sid in enumerate(tof_ids):
        row_y = base_y + i * 32
        tk.Label(root, text=sid, font=("Helvetica", 12), bg="orange").place(x=25, y=row_y)
        lbl = tk.Label(root, text="0 mm", font=("Helvetica", 12), bg="orange")
        lbl.place(x=100, y=row_y)
        tof_labels[sid] = lbl

    # ----- Single-button boxes (BTN1..BTN4)
    btn_ids = ["BTN1", "BTN2", "BTN3", "BTN4"]
    btn_labels = {}
    btn_states = {sid: False for sid in btn_ids}
    btn_y = base_y
    for i, sid in enumerate(btn_ids):
        row_y = btn_y + i * 32
        tk.Label(root, text=sid, font=("Helvetica", 12), bg="orange").place(x=220, y=row_y)
        lbl = tk.Label(root, text="False", font=("Helvetica", 12), bg="orange")
        lbl.place(x=290, y=row_y)
        btn_labels[sid] = lbl

    # ----- Multi-button panel (shows 4 buttons True/False)
    multi_id = "Multi_BTN1"
    tk.Label(root, text="Multi Panel", font=("Helvetica", 12), bg="orange").place(x=25, y=base_y + 5*32 + 12)

    multi_state = {"states": [False, False, False, False], "last_seq": -1}
    multi_labels = []
    mp_row_y = base_y + 5*32 + 12 + 26

    for i in range(4):
        tk.Label(root, text=f"Btn{i+1}", font=("Helvetica", 12), bg="orange").place(x=25, y=mp_row_y + i*26)
        lbl = tk.Label(root, text="False", font=("Helvetica", 12), bg="orange")
        lbl.place(x=80, y=mp_row_y + i*26)
        multi_labels.append(lbl)

    # Live updater (every 100 ms)
    def _update_status():
        # TOF distances
        for sid, lbl in tof_labels.items():
            v = rsm.get_value(sid, "dist_mm", default=None)
            if v is not None:
                try:
                    lbl.config(text=f"{int(v)} mm")
                except Exception:
                    lbl.config(text=f"{v} mm")

        # Single-button states
        for sid, lbl in btn_labels.items():
            pressed = rsm.get_value(sid, "pressed", default=None)
            if pressed is not None:
                btn_states[sid] = bool(pressed)
            lbl.config(text=str(btn_states[sid]))

        # Multi-button (update only on new seq)
        rec = rsm.get(multi_id)
        if rec:
            try:
                seq = int(rec.get("seq", -1))
            except Exception:
                seq = -1
            if seq != multi_state["last_seq"]:
                vals = rec.get("vals") or {}
                try:
                    btn_n = int(vals.get("btn", 0) or 0)
                except Exception:
                    btn_n = 0
                pressed = bool(vals.get("pressed", False))
                if 1 <= btn_n <= 4:
                    multi_state["states"][btn_n - 1] = pressed
                multi_state["last_seq"] = seq

        for i, lbl in enumerate(multi_labels):
            lbl.config(text=str(multi_state["states"][i]))

        root.after(100, _update_status)

    # kick off updater
    root.after(200, _update_status)

    root.mainloop()
