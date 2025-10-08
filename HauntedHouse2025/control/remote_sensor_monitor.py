# remote_sensor_monitor.py
# =============================================================================
# QUICK CHEAT SHEET (read this first)
#
# WHAT THIS MODULE DOES
# - Starts a background process that reads NDJSON lines from your receiver ESP32
#   over USB serial and maintains a live, shared table of latest sensor values
#   keyed by sensor id (e.g., "TOF1").
# - Gives you clean one-liners for raw values, robust filtered distance, and a
#   debounced/hysteretic "obstructed" boolean for door interlocks.
# - Has a watch mode to print a live table for debugging.
#
# TYPICAL USE IN system.py
#   import remote_sensor_monitor as rsm
#   rsm.init(port=None, baud=921600)   # start once at boot
#   if rsm.obstructed("TOF1", block_mm=500, window_ms=200, min_consecutive=2):
#       keep_door_open()
#
# CORE FUNCTIONS
# - init(port=None, baud=921600)
#     Start the background reader (idempotent). Call once in system.py.
#     port: "COM5" on Windows (None = auto-detect).  baud: 115200 or 921600.
#
# - get_value(sensor_id, key, default=None, max_age_ms=250)
#     Return sensors[sensor_id]["vals"][key] if fresh; else default.
#     max_age_ms=None disables freshness guard.  Example: get_value("TOF1","dist_mm")
#
# - get(sensor_id) → full latest record or None:
#     {
#       "id": "TOF1", "seq": 1234,
#       "t_send_ms": <sender millis>, "t_rx_ms": <receiver millis>, "t_host_ms": <PC time>,
#       "mac": "AA:BB:..", "vals": {"dist_mm": 823, "status": 0}
#     }
#
# - get_latency_ms(sensor_id)
#     Approx one-way radio latency ~= t_rx_ms - t_send_ms (device clocks).
#
# - healthy()
#     {"started": bool, "sensors": int, "since_ms": now}
#
# - print_table()
#     One-shot table: ID, dist_mm, age_ms, lat_ms, MAC, seq
#
# ROBUST DISTANCE + DOOR LOGIC HELPERS
# - get_distance_filtered(
#       sid,
#       window_ms=250,          # look back without waiting (uses already-buffered samples)
#       min_samples=3,          # need ≥ this many samples in the window
#       ignore_neg1=True,       # ignore -1 (out-of-range)
#       require_status_zero=False,  # only accept samples with status==0
#       method="median"         # "median" (default) or "mean"
#   ) → float|None
#
# - obstructed(
#       sid,
#       block_mm,               # trip threshold: sample < block_mm counts
#       clear_mm=None,          # release threshold; defaults to block_mm+50 (hysteresis)
#       window_ms=250,          # lookback window (no waiting)
#       min_consecutive=2,      # require this many distinct packets under threshold
#       ignore_neg1=True,
#       require_status_zero=False
#   ) → bool
#   Debounced + hysteretic; fail-safe: if previously TRUE and samples stop, remains TRUE.
#
# DEBUG / CLI
#   python remote_sensor_monitor.py --watch [Hz] --baud 921600 --port COM5
#   (Live table; stderr "lines/s" is disabled by default in this build.)
# =============================================================================

from __future__ import annotations
import json, time, sys, atexit, argparse, os
from typing import Dict, Any, Optional, List, Tuple
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
STALE_DEFAULT_MS = 250              # reject values older than this (ms); None = disable
PORT_HINTS = ("Silicon Labs", "CP210", "CH340", "USB-SERIAL", "ESP32", "WCH")
SILENCE_RECONNECT_MS = 2000         # if no bytes for this long, drop & reconnect
BACKOFF_START_S = 0.25
BACKOFF_MAX_S = 3.0
SHOW_LINES_PER_SEC = False          # set True to re-enable "[RemoteSensorMonitor] N lines/s"

# ---- Module-singleton state ----
_manager: Optional[SyncManager] = None
_proc: Optional[mp.Process] = None
_shared: Optional[Dict[str, dict]] = None
_started: bool = False

# ---- In-process history for filtering (main process only) ----
# We store only DISTINCT PACKETS (by t_host_ms) to avoid tight-loop duplicates.
_hist: Dict[str, dict] = {}  # sid -> {'q': deque[(t_ms, dist_mm)], 'last': bool, 'last_ts': int}

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
        ser.setDTR(False)
        ser.setRTS(False)
    except Exception:
        pass
    return ser

# ---------- Child process main ----------
def _monitor_main(shared: "dict[str, dict]", port: Optional[str], baud: int) -> None:
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
                            # {"rx_ms":..., "mac":"AA:BB:..", "data":{"id":"TOF1","seq":N,"t":ms,"vals":{...}}}
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

# ---------- Snapshot / formatting / watch ----------
def snapshot() -> Dict[str, dict]:
    return dict(_shared) if _shared else {}

def _format_row(sid: str, rec: dict, now_ms: int) -> Tuple:
    vals = rec.get("vals") or {}
    dist = vals.get("dist_mm", "")
    age  = now_ms - int(rec.get("t_host_ms", 0))
    lat  = get_latency_ms(sid)
    mac  = rec.get("mac", "")
    seq  = rec.get("seq", 0)
    return (sid, dist, age, (lat if lat is not None else ""), mac, seq)

def format_table() -> str:
    rows = []
    now = _now_ms()
    for sid, rec in snapshot().items():
        rows.append(_format_row(sid, rec, now))
    rows.sort(key=lambda r: r[0])
    headers = ("ID", "dist_mm", "age_ms", "lat_ms", "MAC", "seq")
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

def _main_cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", nargs="?", const="2", help="Refresh rate in Hz for live table (default 2 Hz).")
    ap.add_argument("--port", help="COM port (e.g., COM5). Omit for auto-detect.", default=None)
    ap.add_argument("--baud", help="Serial baud for receiver (default 921600).", type=int, default=DEFAULT_BAUD)
    args = ap.parse_args()
    init(port=args.port, baud=args.baud)
    if args.watch is not None:
        try:
            hz = float(args.watch)
        except ValueError:
            hz = 2.0
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
            return
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

# ---------- Filtering & predicates ----------
def _get_dist_sample(sid: str, ignore_neg1: bool=True, require_status_zero: bool=False):
    """Fetch latest raw sample; returns (t_ms, dist_mm) or None (no blocking)."""
    rec = get(sid)
    if not rec:
        return None
    vals = rec.get("vals") or {}
    dist = vals.get("dist_mm", None)
    status = vals.get("status", None)
    if dist is None:
        return None
    if ignore_neg1 and isinstance(dist, (int, float)) and dist < 0:
        return None
    if require_status_zero and status not in (None, 0):
        return None
    return (int(rec.get("t_host_ms", _now_ms_local())), int(dist))

def _hist_update(sid: str, window_ms: int, **kw):
    """
    Update per-sensor history with current sample; trim by time window.
    IMPORTANT: only append when the packet timestamp (t_host_ms) CHANGES,
    so tight loops don't add duplicates between packets.
    """
    now = _now_ms_local()
    sample = _get_dist_sample(sid, **kw)
    h = _hist.setdefault(sid, {'q': deque(maxlen=256), 'last': False, 'last_ts': -1})
    if sample:
        t_ms, d = sample
        if t_ms != h['last_ts']:
            h['q'].append((t_ms, d))
            h['last_ts'] = t_ms
    q = h['q']
    while q and (now - q[0][0]) > window_ms:
        q.popleft()
    return h

def get_distance_filtered(sid: str, window_ms: int=250, min_samples: int=3,
                          ignore_neg1: bool=True, require_status_zero: bool=False,
                          method: str="median"):
    """
    Robust distance from recent buffered samples (no waiting).
    Returns None if not enough distinct samples in window.
    """
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
    """
    Debounced + hysteretic obstruction check (no blocking).
    - TRUE if there are >= min_consecutive DISTINCT packets < block_mm in the last window_ms.
    - Once TRUE, remains TRUE until a DISTINCT packet > clear_mm (default block_mm+50).
    - Fail-safe: if previously TRUE and no new packets arrive, remains TRUE.
    """
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
