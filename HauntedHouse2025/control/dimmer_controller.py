# dimmer_service.py
# Background service for an Arduino Nano 8-ch triac dimmer.

import threading
import queue
import time
from typing import Iterable, Optional, Tuple

import serial

# ---------- Config ----------
DEFAULT_PORT: Optional[str] = "COM5"   # <-- your Nano port
DEFAULT_BAUD = 115200
DEFAULT_TIMEOUT = 0.25

# ---------- Internals ----------
_cmd_q: "queue.Queue[Tuple[str, Tuple]]" = queue.Queue()
_worker_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_ready = threading.Event()
_state_lock = threading.Lock()
_serial: Optional[serial.Serial] = None
_current_port: Optional[str] = None
_pong_event = threading.Event()


def _open_serial(port: str, baud: int) -> Optional[serial.Serial]:
    try:
        ser = serial.Serial(port, baudrate=baud, timeout=DEFAULT_TIMEOUT)
        # Nano auto-resets on open; give it a moment
        time.sleep(0.6)
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        return ser
    except Exception:
        return None


def _send_line(ser: serial.Serial, line: str):
    ser.write((line.strip() + "\n").encode("ascii"))


def _readline(ser: serial.Serial) -> Optional[str]:
    try:
        s = ser.readline().decode("ascii", errors="ignore").strip()
        return s or None
    except Exception:
        return None


def _worker(port: Optional[str], baud: int):
    global _serial, _current_port
    backoff = 0.5

    while not _stop.is_set():
        # Ensure connected
        if _serial is None or not _serial.is_open:
            _ready.clear()
            if port is None:
                time.sleep(backoff)
                backoff = min(backoff * 1.7, 5.0)
                continue
            _serial = _open_serial(port, baud)
            if _serial:
                _current_port = _serial.port
                backoff = 0.5
                _ready.set()
                print(f"[Dimmer] Connected successfully on {_current_port} at {baud} baud.")
            else:
                time.sleep(backoff)
                backoff = min(backoff * 1.7, 5.0)
                continue

        # Process next command
        try:
            cmd, args = _cmd_q.get(timeout=0.05)
        except queue.Empty:
            continue

        try:
            if cmd == "PING":
                _pong_event.clear()
                _send_line(_serial, "PING")
                # wait for PONG without swallowing unrelated messages forever
                t0 = time.time()
                got = False
                while time.time() - t0 < 0.8:
                    resp = _readline(_serial)
                    if resp == "PONG":
                        got = True
                        break
                    # allow READY/OK/INFO lines to pass silently
                    if resp is None:
                        time.sleep(0.01)
                if got:
                    _pong_event.set()

            elif cmd == "D":
                ch, lvl = args
                _send_line(_serial, f"D,{ch},{lvl}")
                # optional: read the immediate "OK" without blocking ping
                _ = _readline(_serial)

            elif cmd == "A":
                levels = args[0]
                _send_line(_serial, "A," + ",".join(str(v) for v in levels))
                _ = _readline(_serial)

            elif cmd == "CLOSE":
                try:
                    _serial.close()
                except Exception:
                    pass
                _serial = None
                _ready.clear()

        except Exception:
            try:
                if _serial:
                    _serial.close()
            except Exception:
                pass
            _serial = None
            _ready.clear()


# ---------- Public API ----------
def start(port: Optional[str] = DEFAULT_PORT, baud: int = DEFAULT_BAUD) -> None:
    """Start the background dimmer service thread (idempotent)."""
    global _worker_thread
    with _state_lock:
        if _worker_thread and _worker_thread.is_alive():
            return
        _stop.clear()
        _worker_thread = threading.Thread(target=_worker, args=(port, baud), daemon=True)
        _worker_thread.start()


def stop() -> None:
    """Stop the service and close the serial port."""
    global _worker_thread, _serial, _current_port
    with _state_lock:
        if _worker_thread and _worker_thread.is_alive():
            _stop.set()
            _cmd_q.put(("CLOSE", ()))
            _worker_thread.join(timeout=2.0)
    _worker_thread = None
    _serial = None
    _current_port = None
    _stop.clear()
    _ready.clear()


def wait_ready(timeout: float = 5.0) -> bool:
    """Block until the service is connected to the Nano (or timeout)."""
    return _ready.wait(timeout=timeout)


def get_port() -> Optional[str]:
    """Return the current connected serial port, if any."""
    return _current_port


def dimmer(channel: int, intensity: int) -> None:
    """Set one channel (1–8) to 0–100%."""
    ch = max(1, min(8, int(channel)))
    lvl = max(0, min(100, int(intensity)))
    _cmd_q.put(("D", (ch, lvl)))


def dimmer_all(levels: Iterable[int]) -> None:
    """Set all 8 channels at once (iterable of 8 ints 0–100)."""
    vals = list(int(v) for v in levels)
    if len(vals) != 8:
        raise ValueError("dimmer_all() needs exactly 8 values")
    vals = [max(0, min(100, v)) for v in vals]
    _cmd_q.put(("A", (vals,)))


def ping(timeout: float = 1.0) -> bool:
    """Queue a PING and wait for a PONG from the worker."""
    _pong_event.clear()
    _cmd_q.put(("PING", ()))
    return _pong_event.wait(timeout=timeout)


# ---------- Self-test ----------
if __name__ == "__main__":
    start()  # uses COM5 by default
    if wait_ready(5):
        ok = ping()
        print("Ping:", ok)
        if ok:
            dimmer_all([0, 20, 40, 60, 80, 100, 60, 40])
    else:
        print("Dimmer not ready on COM5")
