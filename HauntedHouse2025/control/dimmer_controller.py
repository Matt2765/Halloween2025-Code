# File: dimmer.py
# Purpose: Robust dimmer driver with smooth ramping + continuous RX drain
# - ALWAYS drains UNO serial output in a background thread (prevents UNO TX stall)
# - Smooth cosine-eased ramps between random targets
# - ACK-pacing default ON (with proper ACK wait via RX thread)
# - Rate-limited writes (30 Hz by default)
# - Keepalive resends, single flicker worker, wire debug, ACK RTT test

from __future__ import annotations
import time, random, threading, re, math
from collections import deque
from typing import Optional
import serial
from utils.tools import BreakCheck

# ----------------------------
# Hardcoded defaults (requested)
# ----------------------------
PORT = "COM7"
BAUD = 115200
TIMEOUT = 0.10                # shorter read timeout helps RX thread be snappy

_WRITE_RATE_HZ = 30.0         # write cap & ramp step rate
_MIN_WRITE_INTERVAL = 1.0 / _WRITE_RATE_HZ

_KEEPALIVE_S = 0.5            # resend same value if quiet for this long

_ACK_TIMEOUT_S = 0.25
_ACK_PACING = True            # default ON

_RAMP_HZ = 30.0               # smoothness of ramp
_RAMP_DT = 1.0 / _RAMP_HZ

# ----------------------------
# Serial + state
# ----------------------------
_ser: Optional[serial.Serial] = None
_lock = threading.Lock()
_last_send_ts: float = 0.0
_last_send_ok_ts: float = 0.0

_current_pct: float = 0.0
_last_sent_int: Optional[int] = None

# Flicker worker (single)
_flicker_thread: Optional[threading.Thread] = None
_flicker_stop_evt: Optional[threading.Event] = None

# Global stop for effects
_global_stop_evt = threading.Event()

# ----------------------------
# RX reader thread (prevents UNO from blocking on Serial.print)
# ----------------------------
_rx_thread: Optional[threading.Thread] = None
_rx_stop_evt = threading.Event()
_ACK_RE = re.compile(r"^ACK SET\b", re.IGNORECASE)

# ACK coordination
_ack_cv = threading.Condition()
_ack_counter = 0              # monotonically increases when ACKs seen
_ack_lines = deque(maxlen=256)  # optional capture of recent lines for debugging

# ----------------------------
# Wire debug counters (per 1s)
# ----------------------------
_wire_debug = False
_send_count = 0
_timeout_count = 0
_ack_seen_count = 0
_last_debug_print = 0.0

# ----------------------------
# Public tuning helpers
# ----------------------------
def set_write_rate_hz(hz: float):
    global _WRITE_RATE_HZ, _MIN_WRITE_INTERVAL
    hz = max(1.0, float(hz))
    _WRITE_RATE_HZ = hz
    _MIN_WRITE_INTERVAL = 1.0 / hz

def set_keepalive(seconds: float):
    global _KEEPALIVE_S
    _KEEPALIVE_S = max(0.05, float(seconds))

def set_ack_timeout(seconds: float):
    global _ACK_TIMEOUT_S
    _ACK_TIMEOUT_S = max(0.05, float(seconds))

def set_ack_pacing(on: bool = True):
    global _ACK_PACING
    _ACK_PACING = bool(on)

def set_ramp_hz(hz: float):
    global _RAMP_HZ, _RAMP_DT
    _RAMP_HZ = max(5.0, float(hz))
    _RAMP_DT = 1.0 / _RAMP_HZ

def enable_wire_debug(on: bool = True):
    global _wire_debug, _send_count, _timeout_count, _ack_seen_count, _last_debug_print
    _wire_debug = bool(on)
    _send_count = 0
    _timeout_count = 0
    _ack_seen_count = 0
    _last_debug_print = 0.0

# ----------------------------
# Internal: debug tick
# ----------------------------
def _debug_tick(wrote_ok: bool):
    global _send_count, _timeout_count, _ack_seen_count, _last_debug_print
    _send_count += 1
    if not wrote_ok:
        _timeout_count += 1
    if not _wire_debug:
        return
    now = time.monotonic()
    if now - _last_debug_print >= 1.0:
        outq = None
        try:
            outq = _ser.out_waiting if _ser else None
        except Exception:
            pass
        print(f"[dimmer] 1s stats: sends={_send_count} timeouts={_timeout_count} "
              f"acks={_ack_seen_count} out_waiting={outq}")
        _send_count = 0
        _timeout_count = 0
        _ack_seen_count = 0
        _last_debug_print = now

def _note_ack_seen():
    global _ack_seen_count
    if _wire_debug:
        _ack_seen_count += 1

# ----------------------------
# RX thread helpers
# ----------------------------
def _rx_loop():
    """Continuously read lines to prevent UNO Serial buffer from filling."""
    global _ack_counter
    while not _rx_stop_evt.is_set():
        line = ""
        try:
            # TIMEOUT controls how long we block; kept short for responsiveness
            line = _ser.readline().decode("utf-8", "ignore").strip() if _ser else ""
        except Exception:
            line = ""
        if not line:
            # tiny breath to avoid hot-spin if port closed
            time.sleep(0.002)
            continue

        # keep a small history for debugging
        _ack_lines.append(line)

        # if it's an ACK, bump the counter and notify any waiter
        if _ACK_RE.search(line):
            with _ack_cv:
                _ack_counter += 1
                _ack_cv.notify_all()
            _note_ack_seen()
        # else: ignore (INFO/ERR/etc.) — but still important that we DRAIN it!

def _start_rx_thread():
    global _rx_thread
    if _rx_thread and _rx_thread.is_alive():
        return
    _rx_stop_evt.clear()
    _rx_thread = threading.Thread(target=_rx_loop, name="dimmer-rx", daemon=True)
    _rx_thread.start()

def _stop_rx_thread(join: bool = True):
    _rx_stop_evt.set()
    if join and _rx_thread and _rx_thread.is_alive():
        _rx_thread.join(timeout=0.5)

def _wait_for_ack(prev_count: int, timeout_s: float) -> bool:
    """Wait until _ack_counter increases beyond prev_count."""
    deadline = time.monotonic() + timeout_s
    with _ack_cv:
        while time.monotonic() < deadline:
            if _ack_counter > prev_count:
                return True
            remaining = deadline - time.monotonic()
            _ack_cv.wait(timeout=max(0.001, min(0.05, remaining)))
    return False

# ----------------------------
# Serial write helper
# ----------------------------
def _writeln(line: str) -> bool:
    """Safe, rate-limited, non-blocking write. Returns True on success."""
    global _last_send_ts, _last_send_ok_ts
    if _ser is None:
        raise RuntimeError("dimmer.init() must be called first.")

    # Rate limit
    now = time.monotonic()
    dt = now - _last_send_ts
    if dt < _MIN_WRITE_INTERVAL:
        time.sleep(_MIN_WRITE_INTERVAL - dt)
    _last_send_ts = time.monotonic()

    data = (line + "\n").encode("ascii")
    with _lock:
        try:
            _ser.write(data)  # write_timeout=0 set in init
            _last_send_ok_ts = time.monotonic()
            _debug_tick(True)
            return True
        except Exception:
            _debug_tick(False)
            return False

# ----------------------------
# Control & lifecycle
# ----------------------------
def init(port: str | None = None):
    """Open serial to UNO and start RX drain thread."""
    global _ser, PORT, _last_sent_int, _last_send_ts, _last_send_ok_ts
    if port:
        PORT = port
    _ser = serial.Serial(
        PORT,
        BAUD,
        timeout=TIMEOUT,
        write_timeout=0,      # non-blocking writes
        inter_byte_timeout=0
    )
    _last_sent_int = None
    _last_send_ts = 0.0
    _last_send_ok_ts = 0.0

    # start reader BEFORE we provoke INFO text, so we don't block the UNO
    _start_rx_thread()

    # friendly handshake
    try:
        _writeln("PING")
        _writeln("INFO")
    except Exception:
        pass

    dim(15)  # safe start

def close():
    """Stop effects and close serial cleanly."""
    stop_flicker(join=True)
    _stop_rx_thread(join=True)
    global _ser
    try:
        if _ser:
            _ser.close()
    finally:
        _ser = None

def request_stop():
    _global_stop_evt.set()

def clear_stop():
    _global_stop_evt.clear()

# ----------------------------
# High-level SET
# ----------------------------
def dim(value: float, *, force: bool = False) -> bool:
    """
    Set brightness 0..100 (UNO maps to RAW).
    Not gated by BreakCheck/stop. Throttled by _writeln().
    """
    global _current_pct, _last_sent_int
    pct = max(0.0, min(100.0, float(value)))
    iv = int(round(pct))

    stale = (time.monotonic() - _last_send_ok_ts) > _KEEPALIVE_S

    if not force and not stale and _last_sent_int is not None and iv == _last_sent_int:
        _current_pct = pct
        return False

    if _writeln(f"SET {iv}"):
        _last_sent_int = iv
        _current_pct = pct
        return True
    else:
        return False

def get_current_pct() -> float:
    return _current_pct

# ----------------------------
# Flicker management + SMOOTH RAMP
# ----------------------------
def stop_flicker(join: bool = False, timeout: float = 0.5):
    """Signal the flicker thread (if any) to stop."""
    global _flicker_thread, _flicker_stop_evt
    if _flicker_stop_evt:
        _flicker_stop_evt.set()
    if join and _flicker_thread and _flicker_thread.is_alive():
        _flicker_thread.join(timeout=timeout)
    _flicker_thread = None
    _flicker_stop_evt = None

def _should_stop_effect(local_stop_evt: Optional[threading.Event]) -> bool:
    if local_stop_evt is not None and local_stop_evt.is_set():
        return True
    if _global_stop_evt.is_set():
        return True
    if BreakCheck():
        return True
    return False

def _ramp(from_val: float, to_val: float, seg_duration: float,
          local_stop: threading.Event, ease: bool = True):
    """
    Smoothly ramp from 'from_val' to 'to_val' over 'seg_duration' seconds.
    Sends at _RAMP_HZ using dim(). ACKs are consumed by RX thread so UNO never stalls.
    If ACK pacing is enabled, we finish the segment with a small ACK sync.
    """
    if seg_duration <= 0:
        dim(to_val)
        return

    steps = max(1, int(seg_duration / _RAMP_DT))
    start = time.monotonic()
    a = from_val
    b = to_val
    last_iv = None

    for i in range(steps):
        if _should_stop_effect(local_stop):
            return
        t01 = i / (steps - 1) if steps > 1 else 1.0
        if ease:
            # cosine ease-in-out
            t01 = 0.5 - 0.5 * math.cos(math.pi * t01)
        val = a + (b - a) * t01
        iv = int(round(max(0.0, min(100.0, val))))
        last_iv = iv
        dim(iv)

        # maintain cadence
        target = start + (i + 1) * _RAMP_DT
        sleep_left = target - time.monotonic()
        if sleep_left > 0:
            time.sleep(min(sleep_left, _RAMP_DT))

    # Final quick ACK sync (only if pacing enabled)
    if last_iv is not None and _ACK_PACING:
        prev = None
        with _ack_cv:
            prev = _ack_counter
        _writeln(f"SET {last_iv}")  # send once more to ensure a fresh ACK marker
        _wait_for_ack(prev, _ACK_TIMEOUT_S)

def dimmer_flicker(duration: float,
                   min_intensity: float,
                   max_intensity: float,
                   flicker_length_min: float,
                   flicker_length_max: float,
                   threaded: bool = False,
                   ease: bool = True):
    """
    SMOOTH FLICKER:
      - Random target in [min_intensity, max_intensity]
      - Ramp smoothly from current to target over random segment in [min,max]
      - Repeat until total duration elapses
    Continuous RX thread prevents UNO serial-blocking even with verbose ACKs.
    """
    # auto-swap times if reversed
    if flicker_length_max < flicker_length_min:
        flicker_length_min, flicker_length_max = flicker_length_max, flicker_length_min

    # bounds
    min_intensity = max(0.0, min(100.0, min_intensity))
    max_intensity = max(min_intensity, min(100.0, max_intensity))
    flicker_length_min = max(0.03, flicker_length_min)
    flicker_length_max = max(flicker_length_min, flicker_length_max)

    # Stop any existing flicker worker first
    stop_flicker(join=True)

    local_stop = threading.Event()

    def _run():
        start_all = time.monotonic()
        cur = get_current_pct()
        if _last_sent_int is None:
            cur = (min_intensity + max_intensity) * 0.5
            dim(cur)

        while (time.monotonic() - start_all) < duration:
            if _should_stop_effect(local_stop):
                break

            target = random.uniform(min_intensity, max_intensity)
            seg = random.uniform(flicker_length_min, flicker_length_max)
            _ramp(cur, target, seg, local_stop, ease=ease)
            cur = target

            # small breath
            time.sleep(0.005)

    if threaded:
        global _flicker_thread, _flicker_stop_evt
        _flicker_stop_evt = local_stop
        t = threading.Thread(target=_run, daemon=True, name="dimmer-flicker")
        _flicker_thread = t
        t.start()
        return t
    else:
        _run()

# ----------------------------
# Diagnostics
# ----------------------------
def ack_latency_test(set_value: int = 50, n: int = 30, ack_timeout_s: float = None):
    """
    Send SET <value>, wait for 'ACK SET' each time via RX thread, measure RTT.
    """
    assert _ser is not None, "init() first"
    to = _ACK_TIMEOUT_S if ack_timeout_s is None else float(ack_timeout_s)
    rtts = []
    lost = 0
    for _ in range(int(n)):
        with _ack_cv:
            prev = _ack_counter
        t0 = time.monotonic()
        if not _writeln(f"SET {int(set_value)}"):
            lost += 1
            continue
        if _wait_for_ack(prev, to):
            rtts.append(time.monotonic() - t0)
        else:
            lost += 1
    if rtts:
        import statistics as stats
        mean = stats.mean(rtts)
        p95 = stats.quantiles(rtts, n=20)[-1] if len(rtts) >= 20 else max(rtts)
        print(f"[ACKTEST] n={n} ok={len(rtts)} lost={lost} "
              f"mean={mean*1000:.1f}ms p95={p95*1000:.1f}ms max={max(rtts)*1000:.1f}ms")
    else:
        print(f"[ACKTEST] no ACKs, lost={lost}/{n}")
