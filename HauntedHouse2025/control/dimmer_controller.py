# File: dimmer.py
# pip install pyserial

import serial, time, random, threading

# ---- Hardcode UNO port here ----
PORT = "COM7"      # change if needed
BAUD = 115200
TIMEOUT = 0.25

_ser = None
_lock = threading.Lock()
_current_pct = 0.0
_stop_event = threading.Event()

def _writeln(line: str):
    if _ser is None:
        raise RuntimeError("dimmer.init() must be called first.")
    with _lock:
        _ser.write((line + "\n").encode("ascii"))
        _ser.flush()

def _readline_nonblock():
    try:
        return _ser.readline().decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""

def init(port: str = None):
    """Open serial to UNO. If port is given, overrides the hardcoded PORT."""
    global _ser, PORT
    if port: PORT = port
    _ser = serial.Serial(PORT, BAUD, timeout=TIMEOUT)
    _writeln("PING"); _readline_nonblock()
    _writeln("INFO"); _readline_nonblock()
    dim(15)  # safe start

def close():
    global _ser
    if _ser:
        try: _ser.close()
        finally: _ser = None

def dim(value: float):
    """Logical 0..100 (UNO maps to 0..93 and enforces floor)."""
    global _current_pct
    pct = max(0.0, min(100.0, float(value)))
    _current_pct = pct
    _writeln(f"SET {int(round(pct))}")

def _ramp(from_pct: float, to_pct: float, duration: float, step_s: float = 0.02):
    if duration <= 0:
        dim(to_pct); return
    steps = max(1, int(duration / step_s))
    for i in range(1, steps + 1):
        if _stop_event.is_set(): return
        t = i / float(steps)
        v = from_pct + (to_pct - from_pct) * t
        dim(v)
        time.sleep(step_s)

def dimmer_flicker(duration: float,
                   min_intensity: float,
                   max_intensity: float,
                   flicker_length_min: float,
                   flicker_length_max: float,
                   in_thread: bool = False):
    """
    Smoothly flicker by ramping between random targets in [min_intensity, max_intensity].
    Each ramp duration is uniform in [flicker_length_min, flicker_length_max].
    """
    assert duration > 0
    assert 0 <= min_intensity <= max_intensity <= 100
    assert 0 < flicker_length_min <= flicker_length_max

    def _run():
        start = time.time()
        last = _current_pct
        while not _stop_event.is_set() and (time.time() - start) < duration:
            seg = random.uniform(flicker_length_min, flicker_length_max)
            target = random.uniform(min_intensity, max_intensity)
            target = max(0.0, min(100.0, target))
            _ramp(last, target, seg, step_s=0.02)
            last = target

    if in_thread:
        th = threading.Thread(target=_run, daemon=True, name="Dimmer")
        th.start()
        return th
    else:
        _run()
        return None
