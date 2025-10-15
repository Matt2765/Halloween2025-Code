# control/audio_manager.py
# -------------------------------------------------------------------
# Generation-based audio stop system (epoch cutoff)
# + Automatic resample to device rate (HDMI-friendly)
# + Per-block channel mapping (low RAM)
# + Robust device selection (prefers HDMI/7.1), broader name match
# + WASAPI Exclusive (when available) with smart fallbacks
# + Latency tuning + prebuffering (stutter fix)
# + Debug device listing
# -------------------------------------------------------------------

from __future__ import annotations

import os
import threading
import tempfile
import subprocess
import platform
from pathlib import Path
from typing import Dict, Optional, Tuple, List

import numpy as np
import sounddevice as sd
import soundfile as sf

from utils.tools import log_event


# ---------- Configuration ----------

named_channels: Dict[str, Dict[str, float | int]] = {
    "frontLeft":     {"index": 0, "gain": 1.0},
    "graveyard":     {"index": 1, "gain": 0.6},   # (your log showed 0.60 earlier)
    "center":        {"index": 2, "gain": 1.4},
    "subwoofer":     {"index": 3, "gain": 1.4},
    "swampRoom":     {"index": 4, "gain": 1.6},
    "atticSpeaker":  {"index": 5, "gain": 1.6},
    "graveyard1":    {"index": 6, "gain": 1.8},
    "closetCreak":   {"index": 7, "gain": 1.8},
}

DEFAULT_SOUND_DIR = (Path(__file__).resolve().parents[3] / "Assets" / "SoundDir").resolve()

# You can still pass a device index, but on Windows we now *prefer by name + channel count*.
DEFAULT_DEVICE_INDEX: Optional[int] = None

DEFAULT_TOTAL_CHANNELS = 8

# Device name matching (case-insensitive). You can override with env var AUDIO_DEVICE_SUBSTR.
DEFAULT_NAME_HINTS: List[str] = [
    "RX-V673", "YAMAHA", "NVIDIA HIGH DEFINITION AUDIO", "NVIDIA", "HDMI", "AVR", "TV"
]

# Minimum channels to consider “multichannel/HDMI-like”. This filters out 2-ch headsets.
MULTICH_MIN_CHANNELS = 6


# ---------- Globals (epochs, sessions, state) ----------

_play_epoch: int = 0
_epoch_lock = threading.Lock()

_cutoff_epoch: int = 0
_stop_event = threading.Event()

class _Session:
    def __init__(self, epoch: int, label: str):
        self.epoch = epoch
        self.done = threading.Event()
        self.label = label

_active_lock = threading.Lock()
_active_sessions: list[_Session] = []
_active_streams: list[sd.OutputStream] = []


# ---------- TTS (offline reliable) ----------

def text_to_wav(text: str, path: Path, rate: int = 0):
    system = platform.system()

    if system in ("Linux", "Darwin"):
        subprocess.run(
            ["espeak", f"-s{150 + rate*10}", "-w", str(path), text],
            check=True
        )
    elif system == "Windows":
        rate = max(-10, min(10, rate))
        ps_script = f'''
        Add-Type -AssemblyName System.Speech
        $speak = New-Object System.Speech.Synthesis.SpeechSynthesizer
        $speak.Rate = {rate}
        $speak.SetOutputToWaveFile("{path}")
        $speak.Speak("{text}")
        $speak.Dispose()
        '''
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], check=True)
    else:
        raise RuntimeError(f"TTS not supported on this platform: {system}")


# ---------- Async wrappers ----------

def play_to_named_channel_async(*args, **kwargs):
    threading.Thread(
        target=play_to_named_channel,
        args=args,
        kwargs=kwargs,
        daemon=True,
        name="AudioWorker-Named"
    ).start()

def play_to_all_channels_async(*args, **kwargs):
    threading.Thread(
        target=play_to_all_channels,
        args=args,
        kwargs=kwargs,
        daemon=True,
        name="AudioWorker-All"
    ).start()


# ---------- Helpers ----------

def _next_epoch() -> int:
    global _play_epoch
    with _epoch_lock:
        _play_epoch += 1
        return _play_epoch

def _resolve_sound_path(wav_or_path: str, base_folder: Optional[Path] = None) -> Path:
    p = Path(wav_or_path)
    if p.is_absolute():
        return p
    base = Path(base_folder) if base_folder is not None else DEFAULT_SOUND_DIR
    return (base / p).resolve()

def _device_label(idx: Optional[int]) -> str:
    try:
        if idx is None:
            return "[default] (None)"
        name = sd.query_devices()[idx].get("name", "Unknown")
    except Exception:
        name = "Unknown"
    return f"[{idx}] {name}"

def _read_audio_mono(file_path: Path) -> tuple[np.ndarray, int]:
    data, fs = sf.read(str(file_path), dtype="float32", always_2d=True)
    mono = data[:, 0] if data.shape[1] == 1 else data.mean(axis=1)
    return mono.astype("float32", copy=False), int(fs)

def _get_hostapi_index(name_contains: str) -> Optional[int]:
    try:
        for i, h in enumerate(sd.query_hostapis()):
            if name_contains.lower() in (h.get("name", "").lower()):
                return i
    except Exception:
        pass
    return None

def _list_devices_debug():
    try:
        hostapis = sd.query_hostapis()
        devices = sd.query_devices()
        log_event("[Audio] ==== Device Inventory (by host API) ====")
        for i, h in enumerate(hostapis):
            log_event(f"[Audio] HostAPI[{i}] {h.get('name','?')}:")
            for dev_idx in h.get("devices", []):
                if dev_idx < 0 or dev_idx >= len(devices):
                    continue
                d = devices[dev_idx]
                out_ch = d.get("max_output_channels", 0)
                in_ch = d.get("max_input_channels", 0)
                fs = d.get("default_samplerate", 0)
                log_event(f"[Audio]   dev {dev_idx:>3} | out={out_ch:<2} in={in_ch:<2} "
                          f"| fs={int(round(float(fs or 0)))} | name='{d.get('name','?')}'")
        log_event("[Audio] =======================================")
    except Exception as e:
        log_event(f"[Audio] Device inventory failed: {e}")

def _pack_device(idx: int) -> Tuple[int, int, int, str]:
    d = sd.query_devices()[idx]
    max_out = int(d["max_output_channels"])
    default_fs = int(round(float(d.get("default_samplerate", 48000.0))))
    hostapis = sd.query_hostapis()
    hostapi_name = "unknown"
    for i, h in enumerate(hostapis):
        if idx in h.get("devices", []):
            hostapi_name = h.get("name", "unknown")
            break
    log_event(f"[Audio] Using device {_device_label(idx)} (hostapi={hostapi_name}) "
              f"with {max_out} out @ {default_fs} Hz default")
    return idx, max_out, default_fs, hostapi_name

def _choose_output_device(preferred_index: int | None,
                          name_hints: Optional[List[str]] = None
                         ) -> Tuple[Optional[int], int, int, str]:
    """
    Robust device selection:
      - On Windows: prefer WASAPI devices with >= MULTICH_MIN_CHANNELS and names matching hints;
                    if none, prefer any device with >= MULTICH_MIN_CHANNELS;
                    else fall back to defaults.
      - On other OS: preferred index -> default -> first available.
    Returns (device_index_or_None, max_output_channels, default_samplerate, hostapi_name)
    """
    _list_devices_debug()  # helpful each run
    devices = sd.query_devices()
    hints = name_hints or []
    env_hint = os.getenv("AUDIO_DEVICE_SUBSTR")
    if env_hint:
        hints = [env_hint] + hints
    hints = [h.lower() for h in hints]

    def valid_out(idx: int) -> bool:
        return 0 <= idx < len(devices) and devices[idx].get("max_output_channels", 0) > 0

    def pick_best(candidates: List[int]) -> Optional[int]:
        """Pick the highest-channel device among candidates."""
        if not candidates:
            return None
        return max(candidates, key=lambda i: int(devices[i].get("max_output_channels", 0)))

    # Windows: search WASAPI first
    if platform.system() == "Windows":
        wasapi_idx = _get_hostapi_index("wasapi")
        if wasapi_idx is not None:
            h = sd.query_hostapis()[wasapi_idx]
            wasapi_devs = [i for i in h.get("devices", []) if valid_out(i)]

            # 1) Name match + multichannel first
            name_match_multi = [
                i for i in wasapi_devs
                if any(s in devices[i].get("name","").lower() for s in hints)
                and int(devices[i].get("max_output_channels",0)) >= MULTICH_MIN_CHANNELS
            ]
            choice = pick_best(name_match_multi)
            if choice is not None:
                return _pack_device(choice)

            # 2) Any name match
            name_match_any = [
                i for i in wasapi_devs
                if any(s in devices[i].get("name","").lower() for s in hints)
            ]
            choice = pick_best(name_match_any)
            if choice is not None:
                return _pack_device(choice)

            # 3) Any multichannel (>=6)
            any_multi = [
                i for i in wasapi_devs
                if int(devices[i].get("max_output_channels",0)) >= MULTICH_MIN_CHANNELS
            ]
            choice = pick_best(any_multi)
            if choice is not None:
                return _pack_device(choice)

            # 4) WASAPI default output if valid
            def_out = h.get("default_output_device", -1)
            if valid_out(def_out):
                return _pack_device(def_out)

        # If WASAPI lookup failed, fall through to generic path

    # Generic path
    if isinstance(preferred_index, int) and valid_out(preferred_index):
        return _pack_device(preferred_index)

    # Prefer any device with MANY channels globally (HDMI/AVR tend to expose 8)
    many_channels = [i for i in range(len(devices)) if valid_out(i)]
    many_channels.sort(key=lambda i: int(devices[i].get("max_output_channels", 0)), reverse=True)
    if many_channels:
        return _pack_device(many_channels[0])

    # System default
    try:
        _, default_out = sd.default.device
    except Exception:
        default_out = None
    if isinstance(default_out, int) and valid_out(default_out):
        return _pack_device(default_out)

    # First available
    for idx, d in enumerate(devices):
        if d.get("max_output_channels", 0) > 0:
            return _pack_device(idx)

    raise RuntimeError("No output audio devices with output channels found.")

def _ensure_samplerate(x: np.ndarray, src_fs: int, dst_fs: int) -> tuple[np.ndarray, int]:
    if src_fs == dst_fs:
        return x, src_fs
    n_src = x.shape[0]
    n_dst = int(round(n_src * (dst_fs / src_fs)))
    t_src = np.linspace(0.0, 1.0, num=n_src, endpoint=False, dtype=np.float64)
    t_dst = np.linspace(0.0, 1.0, num=n_dst, endpoint=False, dtype=np.float64)
    y = np.interp(t_dst, t_src, x.astype(np.float64, copy=False)).astype(np.float32, copy=False)
    return y, dst_fs


# ---------- Stream open (robust) ----------

def _open_stream_robust(fs: int, device_index: Optional[int], have_channels: int):
    """
    Attempt order:
      1) If multichannel (>=6) and Windows: WASAPI Exclusive with requested device.
      2) Same but default device (None).
      3) Requested device, shared mode.
      4) Default device, shared mode.
    """
    blocksize = max(512, fs // 25)  # ~40 ms @ 48k

    def _mk_extra(exclusive: bool):
        if platform.system() == "Windows":
            try:
                return sd.WasapiSettings(exclusive=exclusive)
            except Exception:
                return None
        return None

    def _try_open(dev, ex, note):
        log_event(f"[Audio] Opening stream dev={_device_label(dev)}, fs={fs}, ch={max(1, have_channels)}, "
                  f"exclusive={'yes' if (ex and platform.system()=='Windows') else 'no'} ({note})")
        return sd.OutputStream(
            samplerate=fs,
            device=dev,
            channels=max(1, have_channels),
            dtype="float32",
            blocksize=blocksize,
            latency=0.06,
            extra_settings=ex
        )

    wants_exclusive = (platform.system() == "Windows" and have_channels >= MULTICH_MIN_CHANNELS)

    # Try 1: requested dev + exclusive
    if wants_exclusive:
        try:
            return _try_open(device_index, _mk_extra(True), "try1"), _mk_extra(True), device_index
        except sd.PortAudioError as e1:
            log_event(f"[Audio] Open fail try1: {e1}")

        # Try 2: default dev + exclusive
        try:
            return _try_open(None, _mk_extra(True), "try2"), _mk_extra(True), None
        except sd.PortAudioError as e2:
            log_event(f"[Audio] Open fail try2: {e2}")

    # Try 3: requested dev, shared
    try:
        return _try_open(device_index, None, "try3"), None, device_index
    except sd.PortAudioError as e3:
        log_event(f"[Audio] Open fail try3: {e3}")

    # Try 4: default dev, shared
    return _try_open(None, None, "try4"), None, None


# ---------- Core playback (epoch cutoff, per-block mapping) ----------

def _play_mono_nonblocking(
    mono: np.ndarray,
    fs: int,
    device_index: Optional[int],
    have_channels: int,
    mode: str,
    ch_index: int | None,
    gain: float,
    label: str,
) -> None:
    """
    mode == "all": duplicate mono to all channels
    mode == "one": route mono only to ch_index (others zero)
    """
    epoch = _next_epoch()
    session = _Session(epoch=epoch, label=label)

    def _worker():
        stream = None
        try:
            stream, used_extra, used_dev = _open_stream_robust(fs, device_index, have_channels)
            with stream:
                with _active_lock:
                    _active_streams.append(stream)
                    _active_sessions.append(session)

                # Prebuffer zeros to fill device FIFO and avoid first-block hiccup
                blocksize = stream.blocksize or max(512, fs // 25)
                zero_blk = np.zeros((blocksize, max(1, have_channels)), dtype=np.float32)
                stream.write(zero_blk)
                stream.write(zero_blk)

                n = mono.shape[0]
                pos = 0
                while pos < n:
                    if epoch <= _cutoff_epoch or _stop_event.is_set():
                        break
                    end = min(pos + blocksize, n)
                    block = mono[pos:end] * gain  # (b,)

                    if have_channels <= 1:
                        out = block[:, None]
                    elif have_channels == 2:
                        out = np.column_stack((block, block))
                    else:
                        if mode == "all":
                            out = np.repeat(block[:, None], have_channels, axis=1)
                        else:
                            use_idx = min(int(ch_index or 0), have_channels - 1)
                            out = np.zeros((block.shape[0], have_channels), dtype=np.float32)
                            out[:, use_idx] = block

                    stream.write(out)
                    pos = end

        finally:
            with _active_lock:
                if stream and stream in _active_streams:
                    _active_streams.remove(stream)
                if session in _active_sessions:
                    _active_sessions.remove(session)
            session.done.set()

    threading.Thread(target=_worker, daemon=True, name=f"AudioWorker@{epoch}").start()


# ---------- Shutdown control (epoch cutoff) ----------

def stop_all_audio(timeout: float = 2.0):
    with _epoch_lock:
        snapshot = _play_epoch
        global _cutoff_epoch
        _cutoff_epoch = snapshot

    _stop_event.set()
    log_event(f"[Audio] stop_all_audio(): cutoff_epoch set to {snapshot}")

    deadline = None
    if timeout and timeout > 0:
        import time as _t
        deadline = _t.time() + timeout

    while True:
        with _active_lock:
            pending = [s for s in _active_sessions if s.epoch <= snapshot and not s.done.is_set()]
        if not pending:
            break
        if deadline is not None:
            import time as _t
            if _t.time() >= deadline:
                break
        for s in pending:
            if s.done.wait(timeout=0.05):
                break

    _stop_event.clear()

    with _active_lock:
        still_alive = [s for s in _active_sessions if s.epoch <= snapshot and not s.done.is_set()]
        total_post = [s for s in _active_sessions if s.epoch > snapshot]
    if still_alive:
        log_event(f"[Audio] stop_all_audio(): {len(still_alive)} pre-cutoff session(s) did not exit before timeout")
    log_event(f"[Audio] stop_all_audio(): complete. Post-cutoff active sessions: {len(total_post)}")


# ---------- Public API ----------

def play_to_named_channel(
    wav_file: str,
    target_name: str,
    device_index: int | None = DEFAULT_DEVICE_INDEX,
    total_channels: int = DEFAULT_TOTAL_CHANNELS,  # legacy param
    gain_override: float | None = None,
    base_folder: Path | str | None = None,
) -> None:
    if target_name not in named_channels:
        raise ValueError(f"Channel name '{target_name}' not found. Valid: {list(named_channels.keys())}")

    base_path = Path(base_folder) if base_folder is not None else DEFAULT_SOUND_DIR
    file_path = _resolve_sound_path(wav_file, base_folder=base_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    # Prefer HDMI / multichannel device; avoid headphones
    dev_idx, have_channels, dev_fs, hostapi_name = _choose_output_device(
        device_index,
        name_hints=DEFAULT_NAME_HINTS,
    )

    target = named_channels[target_name]
    ch_index = int(target["index"])
    if gain_override is not None:
        gain = float(gain_override)
    else:
        gain = float(target["gain"])
        if device_index is not None and dev_idx != device_index:
            log_event("[Audio] Fallback device in use — resetting gain to 1.0 for safety")
            gain = 1.0

    mono, src_fs = _read_audio_mono(file_path)
    mono, out_fs = _ensure_samplerate(mono, src_fs, dev_fs)

    use_idx = 0 if have_channels <= 1 else min(ch_index, have_channels - 1)

    log_event(
        f"[Audio] Playing '{file_path.name}' -> channel '{target_name}' (idx {use_idx}), "
        f"gain={gain:.2f}, device={_device_label(dev_idx)}, hostapi={hostapi_name}, "
        f"channels={have_channels}, fs_in={src_fs} -> fs_out={out_fs}"
    )

    _play_mono_nonblocking(
        mono=mono,
        fs=out_fs,
        device_index=dev_idx,     # may be None if we selected hostapi default
        have_channels=have_channels,
        mode="one",
        ch_index=use_idx,
        gain=gain,
        label=f"{file_path.name}@{target_name}"
    )

def play_to_all_channels(
    wav_or_text: str,
    device_index: int | None = DEFAULT_DEVICE_INDEX,
    base_folder: Path | str | None = None,
    tts_rate: int = 0,
    gain_override: float | None = None,
) -> None:
    base_path = Path(base_folder) if base_folder is not None else DEFAULT_SOUND_DIR

    tmp_path: Path | None = None
    try:
        treat_as_file = False
        candidate = Path(wav_or_text)

        if candidate.suffix.lower() == ".wav":
            treat_as_file = True
            file_path = _resolve_sound_path(wav_or_text, base_folder=base_path)
        else:
            abs_candidate = candidate if candidate.is_absolute() else (base_path / candidate)
            if abs_candidate.exists():
                treat_as_file = True
                file_path = abs_candidate.resolve()

        if not treat_as_file:
            fd, path_str = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            tmp_path = Path(path_str)
            text_to_wav(wav_or_text, tmp_path, tts_rate)
            file_path = tmp_path

        if not Path(file_path).exists():
            raise FileNotFoundError(f"Audio file not found: {file_path}")

        dev_idx, have_channels, dev_fs, hostapi_name = _choose_output_device(
            device_index,
            name_hints=DEFAULT_NAME_HINTS,
        )

        if gain_override is not None:
            gain = float(gain_override)
        else:
            gain = 1.0
            if device_index is not None and dev_idx != device_index:
                log_event("[Audio] Fallback device in use — resetting gain to 1.0 for safety")

        mono, src_fs = _read_audio_mono(Path(file_path))
        mono, out_fs = _ensure_samplerate(mono, src_fs, dev_fs)

        label = f"file:{Path(file_path).name}" if treat_as_file else f"tts:{str(wav_or_text)[:40]}"
        log_event(
            f"[Audio] Playing {label} -> ALL channels ({have_channels}), "
            f"gain={gain:.2f}, device={_device_label(dev_idx)}, hostapi={hostapi_name}, "
            f"fs_in={src_fs} -> fs_out={out_fs}"
        )

        _play_mono_nonblocking(
            mono=mono,
            fs=out_fs,
            device_index=dev_idx,
            have_channels=have_channels,
            mode="all",
            ch_index=None,
            gain=gain,
            label=label
        )

    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


# ---------- Utilities ----------

def sound_dir() -> Path:
    return DEFAULT_SOUND_DIR

def list_output_devices() -> list[str]:
    devices = sd.query_devices()
    out = []
    for idx, d in enumerate(devices):
        if d.get("max_output_channels", 0) > 0:
            out.append(f"[{idx}] {d.get('name', 'Unknown')}")
    return out
