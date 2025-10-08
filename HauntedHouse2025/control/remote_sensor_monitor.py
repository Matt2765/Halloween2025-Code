# remote_sensor_monitor.py
# Single-file module that:
#  - Starts a background process to read NDJSON lines from the ESP32 receiver (USB Serial)
#  - Keeps a shared dictionary of sensor states (via multiprocessing.Manager)
#  - Exposes simple APIs: init(), get_value(), get(), get_latency_ms(), stop()
#
# Usage (in system.py):
#   import remote_sensor_monitor as rsm
#   rsm.init(port=None, baud=921600)  # start once at system boot
#   v = rsm.get_value("TOF1", "dist_mm")  # anywhere else; returns None if missing/stale

from __future__ import annotations
import json, time, sys, atexit
from typing import Dict, Any, Optional
import multiprocessing as mp
from multiprocessing.managers import SyncManager

# ---- Serial deps ----
try:
    import serial, serial.tools.list_ports
except ImportError:
    raise SystemExit("pyserial not installed. Run: pip install pyserial")

# ---- Config ----
DEFAULT_BAUD = 921600                     # matches your ESP32 receiver sketch
PORT_HINTS = ("Silicon Labs", "CP210", "CH340", "USB-SERIAL", "ESP32", "WCH")
STALE_DEFAULT_MS = 250                    # age guard for get_value (ms)

# ---- Module-singleton state (managed in this module) ----
_manager: Optional[SyncManager] = None
_proc: Optional[mp.Process] = None
_shared: Optional[Dict[str, dict]] = None
_started: bool = False

# ---------- Helpers ----------
def _now_ms() -> int:
    return int(time.monotonic() * 1000)

def _autodetect_port() -> Optional[str]:
    for p in serial.tools.list_ports.comports():
        desc = f"{p.device} {p.description} {p.manufacturer or ''}"
        if any(k.lower() in desc.lower() for k in PORT_HINTS):
            return p.device
    ports = list(serial.tools.list_ports.comports())
    return ports[0].device if ports else None

def _open_serial(port: Optional[str], baud: int) -> serial.Serial:
    if not port:
        port = _autodetect_port()
    if not port:
        raise RuntimeError("No serial ports found for ESP32 receiver.")
    return serial.Serial(port=port, baudrate=baud, timeout=0.05)

# ---------- Child process main ----------
def _monitor_main(shared: "dict[str, dict]", port: Optional[str], baud: int) -> None:
    backoff = 0.25
    while True:
        try:
            ser = _open_serial(port, baud)
            sys.stderr.write(f"[RemoteSensorMonitor] Connected {ser.port} @ {baud}\n")
            sys.stderr.flush()
            buff = bytearray()
            last_line_ms = _now_ms()

            while True:
                chunk = ser.read(4096)
                if chunk:
                    buff.extend(chunk)
                    while True:
                        nl = buff.find(b"\n")
                        if nl < 0:
                            break
                        line = buff[:nl].decode("utf-8", "ignore").strip()
                        del buff[:nl+1]
                        if not line:
                            continue
                        last_line_ms = _now_ms()
                        try:
                            obj = json.loads(line)
                            # Expected line from receiver:
                            # {"rx_ms":..., "mac":"AA:BB:..", "data":{ "id":"TOF1","seq":N,"t":ms,"vals":{...}}}
                            rx_ms = int(obj.get("rx_ms", _now_ms()))
                            mac   = obj.get("mac", "")
                            data  = obj.get("data") or {}
                            sid   = data.get("id")
                            if not sid:
                                continue
                            seq   = int(data.get("seq", 0))
                            t_send= int(data.get("t", 0))
                            vals  = data.get("vals") or {}
                            shared[sid] = {
                                "id": sid,
                                "seq": seq,
                                "t_send_ms": t_send,    # sender millis()
                                "t_rx_ms": rx_ms,       # receiver millis()
                                "t_host_ms": _now_ms(), # this process timestamp
                                "mac": mac,
                                "vals": vals
                            }
                        except Exception:
                            # bad line -> ignore, keep streaming
                            pass

                # If silent for 5s, reconnect
                if _now_ms() - last_line_ms > 5000:
                    raise RuntimeError("Serial silent; reconnecting")

        except Exception as e:
            sys.stderr.write(f"[RemoteSensorMonitor] {type(e).__name__}: {e}\n")
            sys.stderr.flush()
            time.sleep(backoff)
            backoff = min(backoff * 2, 5.0)
            continue

# ---------- Public API ----------
def init(port: Optional[str]=None, baud: int=DEFAULT_BAUD) -> None:
    """Start the background monitor process (idempotent). Call once at system boot."""
    global _manager, _proc, _shared, _started
    if _started and _proc and _proc.is_alive():
        return
    if _manager is None:
        _manager = mp.Manager()
    _shared = _manager.dict()  # type: ignore
    _proc = mp.Process(target=_monitor_main, args=(_shared, port, baud), daemon=True)
    _proc.start()
    _started = True
    atexit.register(stop)
    time.sleep(0.2)  # small warmup

def stop() -> None:
    """Stop the background monitor if running."""
    global _proc
    if _proc and _proc.is_alive():
        _proc.terminate()
        _proc.join(timeout=1.0)
    _proc = None

def get(sensor_id: str) -> Optional[dict]:
    """Return full record for a sensor id, or None if missing."""
    if not _shared:
        return None
    return _shared.get(sensor_id)

def get_value(sensor_id: str, key: str, default: Any=None, max_age_ms: Optional[int]=STALE_DEFAULT_MS) -> Any:
    """
    One-liner to read a specific value from a sensor.
    - default: value to return if key missing or record stale (defaults to None).
    - max_age_ms: reject if record older than this many ms (None to disable).
    """
    rec = get(sensor_id)
    if not rec:
        return default
    if max_age_ms is not None:
        t_host_ms = int(rec.get("t_host_ms", 0))
        if (_now_ms() - t_host_ms) > max_age_ms:
            return default
    vals = rec.get("vals") or {}
    return vals.get(key, default)

def get_latency_ms(sensor_id: str) -> Optional[int]:
    """Approx radio latency ~= receiver_rx_ms - sender_t_ms (both are sender/receiver millis)."""
    rec = get(sensor_id)
    if not rec:
        return None
    t_send = int(rec.get("t_send_ms", 0))
    t_rx   = int(rec.get("t_rx_ms", 0))
    if t_send <= 0 or t_rx <= 0:
        return None
    return max(0, t_rx - t_send)

def healthy() -> dict:
    """Return a small status snapshot for dashboards."""
    if not _shared:
        return {"started": _started, "sensors": 0, "since_ms": None}
    return {
        "started": _started,
        "sensors": len(_shared.keys()),
        "since_ms": _now_ms()
    }

# Optional quick test: python remote_sensor_monitor.py
if __name__ == "__main__":
    init()  # auto-detect port
    print("[rsm] running; Ctrl+C to quit")
    try:
        while True:
            v = get_value("TOF1", "dist_mm")
            if v is not None:
                print("TOF1:", v)
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        stop()
