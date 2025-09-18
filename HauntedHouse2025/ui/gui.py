# ui/gui.py
import tkinter as tk
import threading
from context import house
from control.shutdown import shutdown
from control.doors import setDoorState
from control.houseLights import toggleHouseLights
from utils.tools import log_event

def demoEvent(room):
    house.Demo = True
    house.HouseActive = True
    toggleHouseLights(False)
    log_event(f"[GUI] Starting demo of {room}")

    from rooms import cave, mirror, swamp, mask, graveyard
    if room == 'CR':
        threading.Thread(target=cave.run, args=()).start()
    elif room == 'MR':
        threading.Thread(target=mirror.run, args=()).start()
    elif room == 'SR':
        threading.Thread(target=swamp.run, args=()).start()
    elif room == 'MkR':
        threading.Thread(target=mask.run, args=()).start()
    elif room == 'GY':
        threading.Thread(target=graveyard.run, args=()).start()

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

    tk.Button(root, text="START HAUNTED HOUSE", height=3, width=25, bg="turquoise1", command=lambda: threading.Thread(target=StartHouse, daemon=True).start()).place(x=250, y=50)
    tk.Button(root, text="EMERGENCY SHUTOFF", height=3, width=25, bg="red", command=lambda: change_system_state("EmergencyShutoff")).place(x=25, y=50)
    tk.Button(root, text="SOFT SHUTDOWN", height=3, width=25, bg="yellow", command=lambda: change_system_state("SoftShutdown")).place(x=25, y=125)

    tk.Button(root, text="Open Door 1", height=2, width=15, command=lambda: setDoorState(1, "OPEN")).place(x=25, y=235)
    tk.Button(root, text="Close Door 1", height=2, width=15, command=lambda: setDoorState(1, "CLOSED")).place(x=150, y=235)
    tk.Button(root, text="Open Door 2", height=2, width=15, command=lambda: setDoorState(2, "OPEN")).place(x=25, y=285)
    tk.Button(root, text="Close Door 2", height=2, width=15, command=lambda: setDoorState(2, "CLOSED")).place(x=150, y=285)
    tk.Button(root, text="Open Door 3", height=2, width=15, command=lambda: setDoorState(3, "OPEN")).place(x=25, y=335)
    tk.Button(root, text="Close Door 3", height=2, width=15, command=lambda: setDoorState(3, "CLOSED")).place(x=150, y=335)

    tk.Button(root, text="Demo Cave Room", height=2, width=15, command=lambda: demoEvent('CR')).place(x=150, y=430)
    tk.Button(root, text="Demo Swamp Room", height=2, width=15, command=lambda: demoEvent('SR')).place(x=25, y=430)
    tk.Button(root, text="Demo Graveyard", height=2, width=15, command=lambda: demoEvent('GY')).place(x=275, y=430)
    tk.Button(root, text="Demo Mirror Room", height=2, width=15, command=lambda: demoEvent("MR")).place(x=25, y=480)
    tk.Button(root, text="Demo Mask Room", height=2, width=15, command=lambda: demoEvent("MkR")).place(x=150, y=480)

    tk.Button(root, text="Start Testing", height=2, width=15, command=None).place(x=25, y=570)
    tk.Button(root, text="Toggle House Lights", height=3, width=25, bg="chartreuse2", command=toggleHouseLights).place(x=250, y=125)

    root.mainloop()