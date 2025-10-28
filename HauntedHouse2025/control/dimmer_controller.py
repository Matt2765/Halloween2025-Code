# File: dimmer.py
# pip install pyserial

import serial, time, random, threading
from utils.tools import BreakCheck

# ---- Hardcode UNO port here ----
PORT = "COM7"      # change if needed
BAUD = 115200
TIMEOUT = 0.25

_ser = None
_lock = threading.Lock()
_current_pct = 0.0
_stop_event = threading.Event()
_flicker_thread: threading.Thread | None = None
_last_sent_int: int | None = None  # avoid redundant writes

# ----------------------------
# Internal serial helpers
# ----------------------------
def _writeln(line: str):
    """Safe write with lock. Dim writes should NOT be gated by BreakCheck()."""
    if _ser is None:
        raise RuntimeError("dimmer.init() must be called first.")
    data = (line + "\n").encode("ascii")
    with _lock:
        try:
            _ser.write(data)
            # Avoid flush() stalls under shutdown; OS buffering is fine.
        except Exception:
            # Swallow write errors during teardown
            pass

def _readline_nonblock():
    try:
        return _ser.readline().decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""

# ----------------------------
# Public control
# ----------------------------
def init(port: str | None = None):
    """Open serial to UNO. If port is given, overrides the hardcoded PORT."""
    global _ser, PORT, _last_sent_int
    if port:
        PORT = port
    _ser = serial.Serial(PORT, BAUD, timeout=TIMEOUT)
    _last_sent_int = None
    _writeln("PING"); _readline_nonblock()
    _writeln("INFO"); _readline_nonblock()
    dim(15)  # safe start

def close():
    """Close serial cleanly."""
    global _ser
    try:
        if _ser:
            _ser.close()
    finally:
        _ser = None

def request_stop():
    """Signal all dimmer activity (ramps/flicker) to stop ASAP."""
    _stop_event.set()

def clear_stop():
    """Clear the global stop flag (only call when safe)."""
    _stop_event.clear()

def dim(value: float):
    """
    Logical 0..100 (UNO maps to its own raw range).
    Intentionally NOT interrupted by BreakCheck() or stop flag.
    """
    global _current_pct, _last_sent_int
    pct = max(0.0, min(100.0, float(value)))
    _current_pct = pct
    iv = int(round(pct))
    if _last_sent_int is not None and iv == _last_sent_int:
        return  # avoid redundant serial traffic
    _last_sent_int = iv
    _writeln(f"SET {iv}")

def dimmer_flicker(duration: float,
                   min_intensity: float,
                   max_intensity: float,
                   flicker_length_min: float,
                   flicker_length_max: float,
                   threaded: bool = False):
    """
    Basic flicker effect:
    Randomly jumps between values in [min_intensity, max_intensity],
    holding each for a random time in [flicker_length_min, flicker_length_max].
    Runs for 'duration' seconds.

    Only this effect is interruptible by BreakCheck() or request_stop().
    """
    def _run():
        start = time.monotonic()
        while (time.monotonic() - start) < duration:
            if _stop_event.is_set() or BreakCheck():
                break
            value = random.uniform(min_intensity, max_intensity)
            dim(value)
            wait = random.uniform(flicker_length_min, flicker_length_max)
            # Sleep in small slices so we can react quickly to stop/break
            end_wait = time.monotonic() + wait
            while time.monotonic() < end_wait:
                if _stop_event.is_set() or BreakCheck():
                    return
                time.sleep(0.02)

    if threaded:
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return t
    else:
        _run()
