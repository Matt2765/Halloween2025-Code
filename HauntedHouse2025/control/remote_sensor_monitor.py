# remote_sensor_monitor.py
# =============================================================================
# REMOTE SENSOR MONITOR — OPERATION MANUAL
# =============================================================================
# OVERVIEW
# This module launches a background *process* that talks to your ESP32 receiver
# over USB serial. The receiver streams newline-delimited JSON (NDJSON). Each
# line contains a normalized "data" object representing one sensor update
# (e.g., TOF distance) or a button edge (for multi-button boards).
#
# The child process parses each line and writes the *latest* record per sensor
# into a cross-process shared dict, keyed by sensor id (e.g., "TOF1", "TOF2",
# "BTN3", "Multi_BTN1"). In the main process, you read from this shared table
# with get()/get_value(), or use higher-level helpers like obstructed().
#
# Additionally, the module exposes a PC→receiver transmit path (TX) for sending
# JSON commands back out over ESP-NOW: broadcast, by device ID, or by MAC.
#
# -----------------------------------------------------------------------------
# QUICK START
# -----------------------------------------------------------------------------
# 1) Start it once during program boot:
#
#     import remote_sensor_monitor as rsm
#     rsm.init(port=None, baud=921600)   # port=None => auto-detect
#     # (Optional) small pause to allow first packets to land:
#     import time; time.sleep(0.2)
#
# 2) Read a TOF value (stateless sampling):
#
#     d = rsm.get_value("TOF2", "dist_mm")   # returns int distance or None
#     if d is not None and d < 1000:
#         print("Blocked!")
#
# 3) Use a *stateful* predicate with windowing (debounced “blocked”):
#
#     # Important timing note: obstructed() needs ≥ min_consecutive samples
#     # inside window_ms. Poll fast enough or make window_ms large enough
#     # to contain those samples. See “TIMING & WINDOWING” below.
#     while not rsm.obstructed("TOF2", block_mm=1000, window_ms=600, min_consecutive=2):
#         if BreakCheck(): break
#         time.sleep(0.05)   # ~20 Hz polling (typical)
#     print("TOF2 obstructed")
#
# 4) Print a live table for debugging:
#
#     print(rsm.format_table())
#
# 5) Send commands out via the receiver (ESP-NOW):
#
#     rsm.tx_broadcast({"to":"Skull1","cmd":"start"})
#     rsm.tx_to_id("ServoBox1", {"cmd":"goto","deg":75})
#     rsm.tx_to_mac("24:6F:28:AA:BB:CC", {"cmd":"stop"})
#
# 6) Read button edges (optional event FIFO):
#
#     evt = rsm.button_pop(timeout=0.0)
#     if evt:
#         # evt = {"id": "...", "btn": int, "pressed": bool, "seq": int, "t_host_ms": int, "mac": str}
#         print("Button:", evt)
#
# 7) Move a servo (convenience helper):
#
#     rsm.servo("SERVO1", 120)                # immediate move
#     rsm.servo("SERVO1", 45, ramp_ms=800)    # smooth ramp over 800 ms
#
# The helper emits (via receiver -> ESP-NOW):
#     {"id":"<SERVO_ID>","angle":<deg>[,"ramp_ms":<ms>]}
#
# Persistent default on the node (see servo firmware):
#     rsm.tx_to_id("SERVO1", {"id":"SERVO1","set_default":135})
#
# -----------------------------------------------------------------------------
# DATA MODEL (SHARED TABLE)
# -----------------------------------------------------------------------------
# Each sensor id maps to the *latest* record:
#
#   {
#     "id": "TOF2",              # sensor/device id string
#     "seq": 1234,               # sender sequence number (if provided)
#     "t_send_ms": 12345678,     # device timestamp (if provided)
#     "t_rx_ms":   12345890,     # receiver-side timestamp (from the ESP32)
#     "t_host_ms": 12345900,     # host-side receipt timestamp (monotonic ms)
#     "mac": "AA:BB:..",         # sender MAC (if known)
#     "vals": {
#       # For TOF:
#       "dist_mm": 742,          # distance in millimeters (int-coerced; see FAR mapping)
#       "status": 0              # optional sensor status, if your firmware sends it
#       # For buttons:
#       "btn": 1,                # button index (1-based)
#       "pressed": true          # True on press edge, False on release edge
#     }
#   }
#
# Use get_value(sid, "dist_mm") for distances; for buttons use "pressed" or
# consume edges via button_pop() (preferred for edge-triggered logic).
#
# -----------------------------------------------------------------------------
# API REFERENCE (MOST USED)
# -----------------------------------------------------------------------------
# init(port: Optional[str]=None, baud: int=DEFAULT_BAUD) -> None
#     Starts the background process, the shared table, and TX queues. Idempotent.
#     - port=None: attempt auto-detection (uses common USB-UART descriptors).
#
# get(sensor_id: str) -> Optional[dict]
#     Returns the latest raw record (or None if unknown).
#
# get_value(sensor_id: str, key: str, default: Any=None, max_age_ms: Optional[int]=STALE_DEFAULT_MS) -> Any
#     Returns vals[key] from the latest record, enforcing staleness via max_age_ms.
#     If data is too old (or missing), returns default. Special case for "dist_mm":
#       negatives (e.g., -1) are *coerced to FAR_DISTANCE_MM* to represent “very far”.
#
# get_latency_ms(sensor_id: str) -> Optional[int]
#     If the sender provides t_send_ms and the receiver provides t_rx_ms, returns
#     (t_rx_ms - t_send_ms); otherwise None. This is *device→receiver* latency.
#
# format_table() / print_table()
#     Pretty table for current snapshot (id, value, age_ms, lat_ms, MAC, seq).
#
# snapshot() -> Dict[str, dict]
#     Shallow copy of the shared table (safe to iterate in UI/loggers).
#
# set_far_distance_mm(value: int) -> None
#     Sets the synthetic distance used when a TOF reports a negative number (e.g., -1).
#     Default is 10000 mm (treated as “clear/very far”).
#
# button_pop(timeout: float=0.0) -> Optional[dict]
#     Pops the next *edge* event from a small in-memory FIFO (max ~256). Returns
#     None on timeout/empty. Best for reacting to press/release transitions.
#
# obstructed(
#     sid: str,
#     block_mm: int,
#     clear_mm: Optional[int]=None,
#     window_ms: int=250,
#     min_consecutive: int=2,
#     ignore_neg1: bool=True,
#     require_status_zero: bool=False
# ) -> bool
#     Debounced obstruction detector. Returns True iff the most recent history
#     contains ≥ min_consecutive samples < block_mm. Once True, it *latches*
#     until a sample > clear_mm (default = block_mm + 50). Uses an internal
#     per-sensor deque populated by calls into rsm (see TIMING below).
#
# get_distance_filtered(
#     sid: str,
#     window_ms: int=250,
#     min_samples: int=3,
#     ignore_neg1: bool=True,
#     require_status_zero: bool=False,
#     method: str="median"
# ) -> Optional[float]
#     Rolling window filter (median/mean) over the recent sample deque. Returns
#     None until enough samples are accumulated.
#
# TX HELPERS (PC → receiver → ESP-NOW)
#   tx_broadcast(payload: Union[str, dict]) -> None         #  emits: "TXB <JSON>\n"
#   tx_to_id(device_id: str, payload: Union[str, dict]) -> None   # "TX <ID> <JSON>\n"
#   tx_to_mac(mac: str, payload: Union[str, dict]) -> None        # "TXMAC <mac> <JSON>\n"
# Payloads may be dicts (JSON encoded) or pre-encoded JSON strings.
#
# servo(device_id: str, angle: int, ramp_ms: Optional[int]=None) -> None
#     Convenience wrapper around tx_to_id() for ESP-NOW servo nodes.
#     - angle is clamped to 0..180
#     - when ramp_ms is provided (>=0), it’s included as "ramp_ms" in the payload
#
# -----------------------------------------------------------------------------
# TIMING & WINDOWING (IMPORTANT)
# -----------------------------------------------------------------------------
# • The internal history deque that powers obstructed() and get_distance_filtered()
#   is populated each time you *call* into the module (e.g., obstructed(), get_*()).
#   Therefore, your polling cadence controls how many samples land inside window_ms.
#
# • To reliably trigger obstructed(sid, block_mm, window_ms, min_consecutive):
#     1) Ensure the sender publishes frequently enough (e.g., ≥ 10 Hz).
#     2) Poll rsm.obstructed() fast enough OR make window_ms large enough so that
#        ≥ min_consecutive fresh samples fall into the window.
#
#   Example good loop for 10 Hz sender:
#       while not rsm.obstructed("TOF2", block_mm=1000, window_ms=600, min_consecutive=2):
#           if BreakCheck(): break
#           time.sleep(0.05)  # 20 Hz polling (window contains ≥ 2 updates)
#
#   Example with slow polling (1 second):
#       # Either relax min_consecutive OR enlarge window to exceed your sleep:
#       while not rsm.obstructed("TOF2", block_mm=1000, window_ms=2500, min_consecutive=2):
#           time.sleep(1)
#
# • If you only need a simple threshold without debounce/latching, prefer stateless:
#       d = rsm.get_value("TOF2","dist_mm")
#       if d is not None and d < 1000: break
#
# -----------------------------------------------------------------------------
# NEGATIVE DISTANCES & FAR MAPPING
# -----------------------------------------------------------------------------
# • By default, any negative "dist_mm" from the device (e.g., -1 meaning “no target”)
#   is *coerced* to FAR_DISTANCE_MM (default 10000 mm). This prevents spurious
#   obstruction triggers when the sensor has no valid reading.
#
# • If your hardware uses -1 to indicate *blocked/too close*, you can either:
#     - Set FAR distance to 0 globally (blunt, not recommended):
#           rsm.set_far_distance_mm(0)
#     - Or modify _get_dist_sample() to treat negatives as 0 when ignore_neg1=False,
#       then call obstructed(..., ignore_neg1=False). (This manual keeps the code as-is.)
#
# -----------------------------------------------------------------------------
# BUTTONS
# -----------------------------------------------------------------------------
# • The receiver should normalize button events to:
#     {"type":"button","id":"Multi_BTN1","btn":1,"pressed":true/false,"seq":N}
#
# • Latest state is in the shared table under that id; for edge-driven logic use:
#     evt = rsm.button_pop(timeout=0.0)
#     if evt and evt["pressed"]:
#         # handle press edge
#
# -----------------------------------------------------------------------------
# CLI / MANUAL DIAGNOSTICS
# -----------------------------------------------------------------------------
# Run the file directly:
#     python remote_sensor_monitor.py --watch 2 --baud 921600 --port COM5
# You’ll see a live table updating ~2 Hz. Press Ctrl+C to quit.
#
# -----------------------------------------------------------------------------
# THREADING / MULTIPROCESS NOTES
# -----------------------------------------------------------------------------
# • The reader runs in a separate *process* (multiprocessing.Process, daemon=True).
# • Cross-process state:
#     - _shared: Manager().dict() for latest records.
#     - _btnq:   mp.Queue() for button edges.
#     - _txq:    mp.Queue() for PC→receiver commands.
# • Call stop() on shutdown if you need a clean exit (init() auto-registers atexit).
#
# -----------------------------------------------------------------------------
# PERFORMANCE TIPS
# -----------------------------------------------------------------------------
# • Leave baud at 921600 for high-rate multi-sensor rigs.
# • On Windows, USB serial drivers sometimes “pause”; the child auto-reconnects
#   after SILENCE_RECONNECT_MS (default 2000 ms) of no bytes.
# • Avoid heavy prints inside tight loops; use format_table() intermittently.
#
# -----------------------------------------------------------------------------
# COMMON PITFALLS (CHECKLIST)
# -----------------------------------------------------------------------------
# [ ] Forgot rsm.init() before using the APIs.
# [ ] Sensor id typo ("TOF2" vs "TOF02").
# [ ] Polling too slowly for obstructed(min_consecutive>1, small window_ms).
# [ ] block_mm doesn’t match the physical geometry (target never < threshold).
# [ ] Expecting -1 to mean “blocked” even though it's mapped to FAR by default.
# [ ] max_age_ms in get_value() filters out stale values (returns default).
# [ ] Receiver not detected; pass an explicit --port or init(port="COMx").
#
# -----------------------------------------------------------------------------
# COPYRIGHT / LICENSE
# -----------------------------------------------------------------------------
# You own your project; keep/adjust this header as needed for your docs.
# =============================================================================


from __future__ import annotations
import json, time, sys, atexit, argparse, os, queue
from typing import Dict, Any, Optional, List, Tuple, Union
import multiprocessing as mp
from multiprocessing.managers import SyncManager
from collections import deque

# ---- Serial deps ----
try:
    import serial, serial.tools.list_ports
except ImportError:
    raise SystemExit("pyserial not installed. Run: pip install pyserial")

# ---- Config ----
DEFAULT_BAUD = 921600
STALE_DEFAULT_MS = 250
PORT_HINTS = ("Silicon Labs", "CP210", "CH340", "USB-SERIAL", "ESP32", "WCH")
SILENCE_RECONNECT_MS = 2000
BACKOFF_START_S = 0.25
BACKOFF_MAX_S = 3.0
SHOW_LINES_PER_SEC = False

# Treat any negative TOF distance (e.g., -1) as "very far/clear".
# This avoids false positives in obstructed() and other predicates.
FAR_DISTANCE_MM = 10000  # configurable upper bound to represent "no reading / max distance"

# ---- Module-singleton state ----
_manager: Optional[SyncManager] = None
_proc: Optional[mp.Process] = None
_shared: Optional[Dict[str, dict]] = None
_started: bool = False

# TX queue to child (for PC -> receiver writes)
_txq: Optional[mp.Queue] = None

# ---- In-process history for filtering (main process only) ----
_hist: Dict[str, dict] = {}  # sid -> {'q': deque[(t_ms, dist_mm)], 'last': bool, 'last_ts': int}

# ---- Optional button event FIFO (cross-process) ----
_btnq: Optional[mp.Queue] = None

# ---------- Time helpers ----------
def _now_ms() -> int:
    return int(time.monotonic() * 1000)

def _now_ms_local() -> int:
    return int(time.monotonic() * 1000)

# ---------- Serial helpers ----------
def _autodetect_port() -> Optional[str]:
    candidates = list(serial.tools.list_ports.comports())
    for p in candidates:
        desc = f"{p.device} {p.description} {p.manufacturer or ''}"
        if any(k.lower() in desc.lower() for k in PORT_HINTS):
            return p.device
    return candidates[0].device if candidates else None

def _open_serial(port: Optional[str], baud: int) -> serial.Serial:
    if not port:
        port = _autodetect_port()
    if not port:
        raise RuntimeError("No serial ports found for ESP32 receiver.")
    ser = serial.Serial(
        port=port,
        baudrate=baud,
        timeout=0.05,
        rtscts=False,
        dsrdtr=False,
        write_timeout=0.5,
    )
    try:
        ser.setDTR(False); ser.setRTS(False)
    except Exception:
        pass
    return ser

# ---------- Child process main ----------
def _monitor_main(shared: "dict[str, dict]", port: Optional[str], baud: int,
                  txq: mp.Queue, btnq: mp.Queue) -> None:
    """
    Background loop:
      - reads lines from receiver (NDJSON per your receiver)
      - updates shared table
      - writes outbound TX commands whenever txq has items
    """
    backoff = BACKOFF_START_S
    lines_seen = 0
    last_rate_t = _now_ms()
    while True:
        try:
            ser = _open_serial(port, baud)
            sys.stderr.write(f"[RemoteSensorMonitor] Connected {ser.port} @ {baud}\n")
            sys.stderr.flush()
            buff = bytearray()
            last_byte_ms = _now_ms()
            while True:
                # --- 1) handle outbound TX commands from Python ---
                try:
                    # non-blocking drain
                    for _ in range(8):  # send up to 8 per tick
                        cmd = txq.get_nowait()  # ('TXB'|'TX'|'TXMAC', arg1, json_str)
                        kind = cmd[0]
                        if kind == 'TXB':
                            line = f"TXB {cmd[2]}\n"
                            ser.write(line.encode('utf-8', 'ignore'))
                        elif kind == 'TX':
                            line = f"TX {cmd[1]} {cmd[2]}\n"
                            ser.write(line.encode('utf-8', 'ignore'))
                        elif kind == 'TXMAC':
                            line = f"TXMAC {cmd[1]} {cmd[2]}\n"
                            ser.write(line.encode('utf-8', 'ignore'))
                        ser.flush()
                except queue.Empty:
                    pass

                # --- 2) read inbound from receiver ---
                try:
                    chunk = ser.read(4096)
                except (serial.SerialException, OSError) as e:
                    raise RuntimeError(f"Serial read failed: {e}")
                if chunk:
                    buff.extend(chunk)
                    last_byte_ms = _now_ms()
                    while True:
                        nl = buff.find(b"\n")
                        if nl < 0:
                            break
                        line = buff[:nl].decode("utf-8", "ignore").strip()
                        del buff[:nl+1]
                        if not line:
                            continue
                        lines_seen += 1
                        now = _now_ms()
                        if SHOW_LINES_PER_SEC and (now - last_rate_t >= 1000):
                            sys.stderr.write(f"[RemoteSensorMonitor] {lines_seen} lines/s\n")
                            sys.stderr.flush()
                            lines_seen = 0
                            last_rate_t = now
                        try:
                            obj = json.loads(line)
                            # Receiver format: {"rx_ms":..., "mac":"AA:BB:..", "data":{...}}
                            rx_ms = int(obj.get("rx_ms", _now_ms()))
                            mac   = obj.get("mac", "")
                            data  = obj.get("data") or {}
                            # Handle BUTTONS
                            if isinstance(data, dict) and data.get("type") == "button":
                                sid   = str(data.get("id") or "")
                                if not sid:
                                    continue
                                btn_n = int(data.get("btn", 1))
                                pressed = bool(data.get("pressed", False))
                                seq   = int(data.get("seq", 0))
                                rec = {
                                    "id": sid,
                                    "seq": seq,
                                    "t_send_ms": 0,
                                    "t_rx_ms": rx_ms,
                                    "t_host_ms": now,
                                    "mac": mac,
                                    "vals": {"btn": btn_n, "pressed": pressed},
                                }
                                shared[sid] = rec
                                # push event (best-effort, non-blocking)
                                try:
                                    btnq.put_nowait({
                                        "id": sid, "btn": btn_n, "pressed": pressed,
                                        "seq": seq, "t_host_ms": now, "mac": mac
                                    })
                                except Exception:
                                    pass
                                continue
                            # Handle TOF / other JSON sensors (unchanged)
                            sid   = data.get("id")
                            if not sid:
                                continue
                            seq   = int(data.get("seq", 0))
                            t_send= int(data.get("t", 0))
                            vals = data.get("vals") or {}
                            # ---- NEW: accept flat JSON (no "vals" wrapper) ----
                            if not vals and isinstance(data, dict):
                                for k in ("dist_mm", "status", "angle", "target", "default", "ramp_ms", "pos", "pos_deg"):
                                    if k in data:
                                        vals[k] = data[k]
                            # Optional: normalize common synonyms to "angle"
                            if "angle" not in vals:
                                if "pos_deg" in vals: vals["angle"] = vals["pos_deg"]
                                elif "pos" in vals:   vals["angle"] = vals["pos"]

                            shared[sid] = {
                                "id": sid,
                                "seq": seq,
                                "t_send_ms": t_send,
                                "t_rx_ms": rx_ms,
                                "t_host_ms": now,
                                "mac": mac,
                                "vals": vals,
                            }

                        except Exception:
                            pass
                if (_now_ms() - last_byte_ms) > SILENCE_RECONNECT_MS:
                    raise RuntimeError("Serial silent; reconnecting")
        except Exception as e:
            sys.stderr.write(f"[RemoteSensorMonitor] {type(e).__name__}: {e}\n")
            sys.stderr.flush()
            try:
                ser.close()  # type: ignore[name-defined]
            except Exception:
                pass
            time.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_S)
            continue
        else:
            backoff = BACKOFF_START_S

# ---------- Public API ----------
def init(port: Optional[str]=None, baud: int=DEFAULT_BAUD) -> None:
    """Start the background monitor (idempotent) and TX channel."""
    global _manager, _proc, _shared, _started, _txq, _btnq
    if _started and _proc and _proc.is_alive():
        return
    if _manager is None:
        _manager = mp.Manager()
    _shared = _manager.dict()  # shared latest records per id
    _txq = mp.Queue()          # outbound commands to child
    _btnq = mp.Queue(maxsize=256)  # recent button edges
    _proc = mp.Process(target=_monitor_main, args=(_shared, port, baud, _txq, _btnq), daemon=True)
    _proc.start()
    _started = True
    atexit.register(stop)
    time.sleep(0.2)

def stop() -> None:
    """Stop the background monitor if running."""
    global _proc
    if _proc and _proc.is_alive():
        _proc.terminate()
        _proc.join(timeout=1.0)
    _proc = None

def get(sensor_id: str) -> Optional[dict]:
    if not _shared:
        return None
    return _shared.get(sensor_id)

def get_value(sensor_id: str, key: str, default: Any=None, max_age_ms: Optional[int]=STALE_DEFAULT_MS) -> Any:
    rec = get(sensor_id)
    if not rec:
        return default
    if max_age_ms is not None:
        t_host_ms = int(rec.get("t_host_ms", 0))
        if (_now_ms() - t_host_ms) > max_age_ms:
            return default
    vals = rec.get("vals") or {}

    # NEW: if caller asks for 'dist_mm', coerce negatives/strings to FAR_DISTANCE_MM
    if key == "dist_mm":
        return _coerce_dist(vals.get("dist_mm", None))

    return vals.get(key, default)

def get_latency_ms(sensor_id: str) -> Optional[int]:
    rec = get(sensor_id)
    if not rec:
        return None
    t_send = int(rec.get("t_send_ms", 0))
    t_rx   = int(rec.get("t_rx_ms", 0))
    if t_send <= 0 or t_rx <= 0:
        return None
    return max(0, t_rx - t_send)

def healthy() -> dict:
    if not _shared:
        return {"started": _started, "sensors": 0, "since_ms": None}
    return {"started": _started, "sensors": len(_shared.keys()), "since_ms": _now_ms()}

# ---------- TX helpers (PC -> receiver -> ESP-NOW) ----------
def _json_str(payload: Union[str, dict]) -> str:
    if isinstance(payload, str):
        return payload.strip()
    return json.dumps(payload, separators=(",", ":"))

def tx_broadcast(payload: Union[str, dict]) -> None:
    """Broadcast a JSON payload: receiver sends 'TXB <JSON>\\n'."""
    if not _txq: raise RuntimeError("call init() first")
    _txq.put(('TXB', None, _json_str(payload)))

def tx_to_id(device_id: str, payload: Union[str, dict]) -> None:
    """Unicast by ID (receiver resolves ID->MAC): 'TX <ID> <JSON>\\n'."""
    if not _txq: raise RuntimeError("call init() first")
    _txq.put(('TX', device_id, _json_str(payload)))

def tx_to_mac(mac: str, payload: Union[str, dict]) -> None:
    """Unicast to a MAC: 'TXMAC <mac> <JSON>\\n'."""
    if not _txq: raise RuntimeError("call init() first")
    _txq.put(('TXMAC', mac, _json_str(payload)))

# ---------- High-level convenience: servo control ----------
def servo(device_id: str, angle: int, ramp_ms: Optional[int] = None) -> None:
    """
    Move an ESP-NOW servo node.

    Example:
        servo("SERVO1", 120)              # immediate
        servo("SERVO1", 45, ramp_ms=800)  # smooth 800 ms ramp

    Payload format (what the receiver sends over ESP-NOW):
        {"id":"<device_id>","angle":<deg>[,"ramp_ms":<ms>]}
    """
    if not isinstance(device_id, str) or not device_id:
        raise ValueError("servo(): 'device_id' must be a non-empty string")
    try:
        deg = int(angle)
    except Exception:
        raise ValueError("servo(): 'angle' must be an integer (degrees 0..180)")
    # Clamp to valid range expected by the node
    if   deg < 0:   deg = 0
    elif deg > 180: deg = 180

    payload = {"id": device_id, "angle": deg}
    if ramp_ms is not None:
        try:
            r = int(ramp_ms)
        except Exception:
            raise ValueError("servo(): 'ramp_ms' must be an integer (milliseconds)")
        if r < 0:
            r = 0
        payload["ramp_ms"] = r

    tx_to_id(device_id, payload)

# ---------- Sprite trigger helpers ----------
def sprite_next(device_id: str, pulse_ms: int = 150) -> None:
    """
    Momentarily "press" the Sprite's NEXT/trigger input via its ESP-NOW node.
    The node should act on: {"id":"<ID>","cmd":"next","pulse_ms":<ms>}.
    """
    if not isinstance(device_id, str) or not device_id:
        raise ValueError("sprite_next(): 'device_id' must be a non-empty string")
    try:
        ms = int(pulse_ms)
    except Exception:
        raise ValueError("sprite_next(): 'pulse_ms' must be an integer (milliseconds)")
    if ms < 20:
        ms = 20  # ensure a minimal visible press
    tx_to_id(device_id, {"id": device_id, "cmd": "next", "pulse_ms": ms})

def activateSprite(device_id: str, pulse_ms: int = 150) -> None:
    """
    Alias for sprite_next(); provided for convenience.
    """
    sprite_next(device_id, pulse_ms)

# ---------- Snapshot / formatting / watch ----------
def snapshot() -> Dict[str, dict]:
    return dict(_shared) if _shared else {}

def _format_row(sid: str, rec: dict, now_ms: int) -> Tuple:
    vals = rec.get("vals") or {}
    if "pressed" in vals:
        display = f"btn{vals.get('btn', '')}:{'T' if vals['pressed'] else 'F'}"
    else:
        # NEW: coerce negatives/strings to FAR for display
        display = _coerce_dist(vals.get("dist_mm", None))
    age  = now_ms - int(rec.get("t_host_ms", 0))
    lat  = get_latency_ms(sid)
    mac  = rec.get("mac", "")
    seq  = rec.get("seq", 0)
    return (sid, display, age, (lat if lat is not None else ""), mac, seq)

def format_table() -> str:
    rows = []
    now = _now_ms()
    for sid, rec in snapshot().items():
        rows.append(_format_row(sid, rec, now))
    rows.sort(key=lambda r: r[0])
    headers = ("ID", "value/dist_or_btn", "age_ms", "lat_ms", "MAC", "seq")
    all_rows: List[Tuple] = [headers] + rows
    widths = [max(len(str(row[i])) for row in all_rows) for i in range(len(headers))]
    def fmt(row: Tuple) -> str:
        return "  ".join(str(row[i]).rjust(widths[i]) for i in range(len(headers)))
    lines = [fmt(headers), "-" * (sum(widths) + 2 * (len(widths)-1))]
    for r in rows:
        lines.append(fmt(r))
    return "\n".join(lines)

def print_table() -> None:
    print(format_table())

def _clear_screen():
    if os.name == "nt":
        os.system("cls")
    else:
        sys.stdout.write("\033[2J\033[H"); sys.stdout.flush()

# ---------- CLI ----------
def _main_cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", nargs="?", const="2", help="Refresh rate in Hz for live table (default 2 Hz).")
    ap.add_argument("--port", help="COM port (e.g., COM5). Omit for auto-detect.", default=None)
    ap.add_argument("--baud", help="Serial baud for receiver (default 921600).", type=int, default=DEFAULT_BAUD)
    args = ap.parse_args()
    init(port=args.port, baud=args.baud)
    if args.watch is not None:
        try: hz = float(args.watch)
        except ValueError: hz = 2.0
        period = max(0.1, 1.0 / hz)
        print(f"[rsm] Watching at {hz:.2f} Hz. Ctrl+C to quit.")
        try:
            while True:
                _clear_screen()
                print(time.strftime("%H:%M:%S"), "— live sensor table")
                print(format_table())
                time.sleep(period)
        except KeyboardInterrupt:
            pass
        finally:
            stop()
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

if __name__ == "__main__":
    _main_cli()

# ---------- FAR distance config ----------
def set_far_distance_mm(value: int) -> None:
    """
    Set the synthetic distance used when a TOF sensor reports a negative value (e.g., -1).
    This is treated as "very far/clear".
    """
    global FAR_DISTANCE_MM
    FAR_DISTANCE_MM = max(1, int(value))

# ---------- Filtering & predicates (unchanged for TOF except -1 mapping) ----------
def _coerce_dist(val) -> int:
    """
    Convert mixed-type distance readings to an int.
    - Any negative or unparseable value -> FAR_DISTANCE_MM (treat as 'no target / very far')
    - None -> FAR_DISTANCE_MM
    """
    if val is None:
        return FAR_DISTANCE_MM
    try:
        # handles str like "-1", "123.0", floats, ints
        v = int(round(float(val)))
    except Exception:
        return FAR_DISTANCE_MM
    return FAR_DISTANCE_MM if v < 0 else v

def _get_dist_sample(sid: str, ignore_neg1: bool=True, require_status_zero: bool=False):
    rec = get(sid)
    if not rec:
        return None
    vals = rec.get("vals") or {}
    status = vals.get("status", None)

    # Map any negative / bad reading to FAR, even if it arrived as a string
    dist = _coerce_dist(vals.get("dist_mm", None))

    # Keep optional status gate exactly as before
    if require_status_zero and status not in (None, 0):
        return None

    return (int(rec.get("t_host_ms", _now_ms_local())), int(dist))


def _hist_update(sid: str, window_ms: int, **kw):
    now = _now_ms_local()
    sample = _get_dist_sample(sid, **kw)

    # keep a per-sensor deque of (timestamp_ms, distance_mm)
    h = _hist.setdefault(sid, {'q': deque(maxlen=256), 'last': False, 'last_ts': -1})
    if sample:
        t_ms, d = sample
        # avoid duplicating the same timestamp
        if t_ms != h['last_ts']:
            h['q'].append((t_ms, d))
            h['last_ts'] = t_ms

    q = h['q']
    # evict old samples from the *left* (oldest first)
    while q and (now - q[0][0]) > window_ms:
        q.popleft()

    return h

def get_distance_filtered(sid: str, window_ms: int=250, min_samples: int=3,
                          ignore_neg1: bool=True, require_status_zero: bool=False,
                          method: str="median"):
    h = _hist_update(sid, window_ms, ignore_neg1=ignore_neg1, require_status_zero=require_status_zero)
    q = list(h['q'])
    if len(q) < max(1, min_samples):
        return None
    vals = [d for (_, d) in q]
    if not vals:
        return None
    if method == "mean":
        return sum(vals) / len(vals)
    vals.sort()
    n = len(vals); mid = n // 2
    return vals[mid] if n % 2 else (vals[mid-1] + vals[mid]) / 2

def obstructed(sid: str, block_mm: int,
               clear_mm: int|None=None,
               window_ms: int=250,
               min_consecutive: int=2,
               ignore_neg1: bool=True,
               require_status_zero: bool=False) -> bool:
    h = _hist_update(sid, window_ms, ignore_neg1=ignore_neg1, require_status_zero=require_status_zero)
    q = h['q']
    if clear_mm is None:
        clear_mm = block_mm + 50
    consec = 0
    for (_, d) in reversed(q):
        if d < block_mm:
            consec += 1
            if consec >= max(1, min_consecutive):
                h['last'] = True
                return True
        else:
            break
    if h['last']:
        if q:
            _, latest = q[-1]
            if latest > clear_mm:
                h['last'] = False
    return h['last']

# ---------- Button event FIFO ----------
def button_pop(timeout: float=0.0) -> Optional[dict]:
    """Pop the next button edge event, or None if empty (timeout seconds)."""
    if not _btnq:
        return None
    try:
        return _btnq.get(timeout=timeout)
    except queue.Empty:
        return None
    
# ---------- Button value helper ----------
def get_button_value(device_id: str, btn_num: int | None = None) -> Optional[bool]:
    """
    Returns the last known pressed state of a button.
      - For single-button devices (e.g., 'BTN1'), call:  get_button_value('BTN1')
      - For multi-button devices (e.g., 'Multi_BTN1'), call:  get_button_value('Multi_BTN1', 3)
    Returns:
        True  -> button currently pressed
        False -> button currently released
        None  -> no data yet or button never seen
    """
    rec = get(device_id)
    if not rec:
        return None

    vals = rec.get("vals", {})
    rec_btn = vals.get("btn")
    pressed = vals.get("pressed")

    # Single-button device (no btn_num needed)
    if btn_num is None:
        return bool(pressed) if pressed is not None else None

    # Multi-button device (only return if it matches desired button number)
    if rec_btn == btn_num:
        return bool(pressed)
    return None
