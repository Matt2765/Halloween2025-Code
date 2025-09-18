# utils/tools.py
import time
import os
from context import house

def wait_until(condition_func, timeout=10, interval=0.1):
    """Wait until a condition becomes True or timeout is reached."""
    start = time.time()
    while time.time() - start < timeout:
        if condition_func():
            return True
        time.sleep(interval)
    return False

def log_event(message, logfile="logs/haunt_log.txt"):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    log_event(line)
    os.makedirs(os.path.dirname(logfile), exist_ok=True)
    with open(logfile, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def toggle_demo_mode(state, enable=True):
    state.Demo = enable
    state.systemState = "ONLINE" if enable else "OFFLINE"
    log_event(f"Demo mode {'ENABLED' if enable else 'DISABLED'}")

def BreakCheck():
    if not house.HouseActive or house.systemState != "ONLINE":
        log_event("BreakCheck triggered: System no longer active.")
        return True
    return False