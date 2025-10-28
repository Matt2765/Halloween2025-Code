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
              command=lambda: threading.Thread(target=StartHouse, daemon=True, name="HOUSE").start()).place(x=250, y=50)
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
    # NEW: SENSOR + BUTTON STATUS PANEL (fits 465px width)
    # -------------------------------------------------------------------------
    SECTION_Y = 640
    tk.Label(root, text="SENSORS & BUTTONS", font=("Helvetica bold", 15), bg="orange").place(x=25, y=SECTION_Y)

    panel = tk.Frame(root, bg="orange")
    panel.place(x=25, y=SECTION_Y + 30, width=415)  # <= keep within window

    small = ("Helvetica", 11)

    # ===== Row group 1: TOF (left) + Buttons (right) =====
    row1 = tk.Frame(panel, bg="orange")
    row1.grid(row=0, column=0, sticky="nw")

    # TOF table
    tof_frame = tk.Frame(row1, bg="orange")
    tof_frame.grid(row=0, column=0, sticky="nw", padx=(0, 14))
    tk.Label(tof_frame, text="TOF", font=small, bg="orange").grid(row=0, column=0, sticky="w", padx=(0, 10))
    tk.Label(tof_frame, text="Dist", font=small, bg="orange").grid(row=0, column=1, sticky="w")

    tof_ids = ["TOF1", "TOF2", "TOF3", "TOF4", "TOF5"]
    tof_labels = {}
    for i, sid in enumerate(tof_ids, start=1):
        tk.Label(tof_frame, text=sid, font=small, bg="orange").grid(row=i, column=0, sticky="w", padx=(0, 10))
        lbl = tk.Label(tof_frame, text="0 mm", font=small, bg="orange")
        lbl.grid(row=i, column=1, sticky="w")
        tof_labels[sid] = lbl

    # Buttons table
    btn_frame = tk.Frame(row1, bg="orange")
    btn_frame.grid(row=0, column=1, sticky="nw")
    tk.Label(btn_frame, text="Buttons", font=small, bg="orange").grid(row=0, column=0, columnspan=2, sticky="w")

    btn_ids = ["BTN1", "BTN2", "BTN3", "BTN4"]
    btn_labels = {}
    btn_states = {sid: False for sid in btn_ids}
    for i, sid in enumerate(btn_ids, start=1):
        tk.Label(btn_frame, text=sid, font=small, bg="orange").grid(row=i, column=0, sticky="w", padx=(0, 6))
        lbl = tk.Label(btn_frame, text="False", font=small, bg="orange")
        lbl.grid(row=i, column=1, sticky="w")
        btn_labels[sid] = lbl

    # ===== Row group 2: Multi Panel (single compact row) =====
    mid = tk.Frame(panel, bg="orange")
    mid.grid(row=1, column=0, sticky="nw", pady=(4, 0))

    tk.Label(mid, text="Multi Panel", font=small, bg="orange").grid(row=0, column=0, columnspan=8, sticky="w")
    multi_id = "Multi_BTN1"
    multi_state = {"states": [False, False, False, False], "last_seq": -1}
    multi_labels = []
    for i in range(4):
        tk.Label(mid, text=f"B{i+1}", font=small, bg="orange").grid(row=1, column=i*2, sticky="w", padx=(0, 4))
        v = tk.Label(mid, text="False", font=small, bg="orange")
        v.grid(row=1, column=i*2+1, sticky="w", padx=(0, 10))
        multi_labels.append(v)

    # ===== Row group 3: SERVOS (full-width, under Multi) =====
    right = tk.Frame(panel, bg="orange")
    right.grid(row=2, column=0, sticky="nw", pady=(6, 0))

    tk.Label(right, text="SERVOS", font=("Helvetica bold", 13), bg="orange").grid(row=0, column=0, columnspan=2, sticky="w")

    servo_ids = ["SERVO1", "SERVO2"]  # edit as needed
    servo_labels = {}
    for i, sid in enumerate(servo_ids, start=1):
        tk.Label(right, text=sid, font=small, bg="orange").grid(row=i, column=0, sticky="w", padx=(0, 10))
        lbl = tk.Label(right, text="--°", font=small, bg="orange")
        lbl.grid(row=i, column=1, sticky="w")
        servo_labels[sid] = lbl

    # ===== Live updater (every 100 ms) =====
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
            lbl.config(text="True" if btn_states[sid] else "False")

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
            lbl.config(text=("True" if multi_state["states"][i] else "False"))

        # Servo angles
        for sid, lbl in servo_labels.items():
            ang = rsm.get_value(sid, "angle", default=None, max_age_ms=1000)
            lbl.config(text="--°" if ang is None else f"{int(ang)}°")

        root.after(100, _update_status)

    root.after(200, _update_status)

    root.mainloop()
