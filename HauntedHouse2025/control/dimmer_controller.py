# dimmer_service.py
# Single high-rate mixer: one thread owns serial and sends full 8-ch frames at fixed cadence.
# Effects only set desired targets; mixer slews -> atomic A,<8> writes. Nano firmware unchanged.

import threading, time, random
from typing import Iterable, Optional, Tuple, List, Dict, Any
import serial
from utils.tools import BreakCheck

# ---------- Config ----------
DEFAULT_PORT: Optional[str] = "COM5"
DEFAULT_BAUD = 115200
DEFAULT_TIMEOUT = 0.02

MIX_HZ = 240.0
DT = 1.0 / MIX_HZ
KEEPALIVE_MS = 100
DEFAULT_SLEW_STEP = 3  # % per tick (x MIX_HZ ≈ %/s). Effects can override per-channel while active.

# ---------- Internals ----------
_ser: Optional[serial.Serial] = None
_mix_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_ready = threading.Event()
_state_lock = threading.RLock()

# Levels are 1..8 (index 0 unused)
_desired: List[int] = [0]*9
_active:  List[int] = [0]*9
_last_sent: List[int] = [-1]*9

# Per-channel slew step (% per mixer tick)
_slew_step: List[int] = [DEFAULT_SLEW_STEP]*9

# INFO / PING
_pong_event = threading.Event()
_info_event = threading.Event()
_info_latest: Dict[str, Any] = {}

# ---------- Serial ----------
def _open_serial(port: str, baud: int) -> Optional[serial.Serial]:
    try:
        s = serial.Serial(port, baudrate=baud, timeout=DEFAULT_TIMEOUT)
        time.sleep(0.6)
        s.reset_input_buffer(); s.reset_output_buffer()
        return s
    except Exception:
        return None

def _send_line(line: str):
    if _ser and _ser.is_open:
        try:
            _ser.write((line.strip() + "\n").encode("ascii"))
        except Exception:
            pass

def _readline() -> Optional[str]:
    try:
        if not _ser or not _ser.is_open: return None
        s = _ser.readline().decode("ascii", errors="ignore").strip()
        return s or None
    except Exception:
        return None

def _parse_info(line: str) -> Optional[Dict[str, Any]]:
    try:
        if not line.startswith("HALF_US="): return None
        out: Dict[str, Any] = {}
        parts = line.split()
        for p in parts:
            if p.startswith("HALF_US="):
                out["half_us"] = int(p.split("=", 1)[1])
            elif p.startswith("LEVELS="):
                out["levels"] = [int(x) for x in p.split("=", 1)[1].split(",")]
            elif p.startswith("ACTIVE_HIGH="):
                out["active_high"] = (p.split("=", 1)[1] == "1")
        if "half_us" in out and "levels" in out and len(out["levels"]) == 8:
            return out
    except Exception:
        pass
    return None

# ---------- Utils ----------
def _norm_channel(ch: int) -> int:
    return 1 if ch < 1 else 8 if ch > 8 else int(ch)

def _norm_level(v: int) -> int:
    return 0 if v < 0 else 100 if v > 100 else int(v)

def _clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v

# ---------- Mixer Thread ----------
def _mixer(port: Optional[str], baud: int):
    global _ser, _info_latest
    backoff = 0.5
    last_keepalive = time.monotonic()
    next_tick = time.perf_counter() + DT

    while not _stop.is_set():
        # Ensure serial
        if _ser is None or not _ser.is_open:
            _ready.clear()
            if port is None:
                time.sleep(backoff); backoff = min(backoff*1.7, 5.0); continue
            _ser = _open_serial(port, baud)
            if _ser:
                backoff = 0.5
                _ready.set()
                _ = _readline()  # drain banner
            else:
                time.sleep(backoff); backoff = min(backoff*1.7, 5.0); continue

        # Drain responses quick
        for _ in range(4):
            line = _readline()
            if not line: break
            if line == "PONG":
                _pong_event.set()
            else:
                info = _parse_info(line)
                if info:
                    _info_latest = info
                    _info_event.set()

        # Build next frame
        with _state_lock:
            changed = False
            for ch in range(1,9):
                d = _desired[ch]
                a = _active[ch]
                if a != d:
                    step = _slew_step[ch]
                    if step <= 0: step = 1
                    if d > a:
                        a2 = min(d, a + step)
                    else:
                        a2 = max(d, a - step)
                    if a2 != a:
                        _active[ch] = a2
                        changed = True
                # if identical, keep a
            # Decide to send
            now = time.monotonic()
            due_keepalive = (now - last_keepalive) * 1000.0 >= KEEPALIVE_MS
            need_send = changed or due_keepalive
            if need_send:
                frame = [_active[i] for i in range(1,9)]
                _send_line("A," + ",".join(str(v) for v in frame))
                _last_sent[1:9] = frame
                if due_keepalive: last_keepalive = now

        # Fixed-rate tick
        next_tick += DT
        sleep_for = next_tick - time.perf_counter()
        if sleep_for > 0:
            time.sleep(sleep_for)
        else:
            # if we fell behind, catch up but don't spiral
            next_tick = time.perf_counter() + DT

    # Shutdown
    try:
        if _ser: _ser.close()
    except Exception:
        pass
    _ser = None
    _ready.clear()

# ---------- Public API ----------
def start(port: Optional[str] = DEFAULT_PORT, baud: int = DEFAULT_BAUD) -> None:
    global _mix_thread
    with _state_lock:
        if _mix_thread and _mix_thread.is_alive():
            return
        _stop.clear()
        _mix_thread = threading.Thread(target=_mixer, args=(port, baud), daemon=True)
        _mix_thread.start()

def stop() -> None:
    global _mix_thread
    _stop.set()
    if _mix_thread and _mix_thread.is_alive():
        _mix_thread.join(timeout=2.0)
    _mix_thread = None
    _stop.clear()
    _ready.clear()

def wait_ready(timeout: float = 5.0) -> bool:
    return _ready.wait(timeout=timeout)

def get_port() -> Optional[str]:
    return None if not _ser else _ser.port

def get_levels_snapshot() -> List[int]:
    with _state_lock:
        return _active[1:9].copy()

# Immediate set: updates desired; mixer sends next tick.
def dimmer(channel: int, intensity: int) -> None:
    ch = _norm_channel(channel)
    val = _norm_level(intensity)
    with _state_lock:
        _desired[ch] = val

def dimmer_all(levels: Iterable[int]) -> None:
    vals = list(int(v) for v in levels)
    if len(vals) != 8:
        raise ValueError("dimmer_all() needs exactly 8 values")
    vals = [_norm_level(v) for v in vals]
    with _state_lock:
        for i, v in enumerate(vals, start=1):
            _desired[i] = v

# Ping/Info piggyback on mixer thread IO
def ping(timeout: float = 1.0) -> bool:
    if not _ready.is_set(): return False
    _pong_event.clear()
    _send_line("PING")
    return _pong_event.wait(timeout=timeout)

def info(timeout: float = 1.0) -> Optional[Dict[str, Any]]:
    if not _ready.is_set(): return None
    _info_event.clear()
    _send_line("INFO")
    if _info_event.wait(timeout=timeout):
        return dict(_info_latest)
    return None

# ---------- Effects ----------
def dimmer_flicker(channel: int,
                   duration_s: float,
                   intensity_min: int,
                   intensity_max: int,
                   flicker_length_min: float,
                   flicker_length_max: float) -> threading.Thread:
    """
    Smooth flicker: selects random targets; mixer handles ramp via per-channel slew.
    Computes a temporary per-channel slew so each ramp approximately lasts flicker_length.
    """
    chan = _norm_channel(channel)
    imin = _norm_level(min(intensity_min, intensity_max))
    imax = _norm_level(max(intensity_min, intensity_max))
    fl_min = max(float(flicker_length_min), DT)
    fl_max = max(float(flicker_length_max), fl_min)

    def _run():
        end = time.time() + float(duration_s)
        # initial current
        with _state_lock:
            cur = _active[chan]
            _desired[chan] = _clamp(cur, imin, imax)
        while time.time() < end:
            if BreakCheck(): return
            target = random.randint(imin, imax)
            ramp_time = random.uniform(fl_min, fl_max)
            # choose slew step so ramp completes ~ in ramp_time
            steps = max(1, int(ramp_time * MIX_HZ))
            with _state_lock:
                cur = _active[chan]
                dist = abs(target - cur)
                step = max(1, int(round(dist / steps))) if dist > 0 else DEFAULT_SLEW_STEP
                old_step = _slew_step[chan]
                _slew_step[chan] = step
                _desired[chan] = target
            # wait roughly until ramp completes or duration ends
            t_end = time.time() + ramp_time
            while time.time() < t_end:
                if BreakCheck(): 
                    with _state_lock: _slew_step[chan] = old_step
                    return
                time.sleep(min(0.02, t_end - time.time()))
            with _state_lock:
                _slew_step[chan] = old_step
        with _state_lock:
            _desired[chan] = 0

    th = threading.Thread(target=_run, daemon=True, name=f"dimmer_flicker_ch{chan}")
    th.start()
    return th

# ---------- Self-test ----------
if __name__ == "__main__":
    start()
    if wait_ready(5):
        print("Ping:", ping())
        print("Info:", info() or "n/a")
        dimmer_all([0]*8)
        dimmer_flicker(2, 6.0, 10, 60, 0.08, 0.25)
        dimmer_flicker(5, 6.0, 20, 80, 0.05, 0.18)
        dimmer_flicker(7, 6.0, 5, 30, 0.12, 0.35)
        time.sleep(7.0)
    else:
        print("Dimmer not ready on", DEFAULT_PORT)
