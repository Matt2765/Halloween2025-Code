# control/audio_manager.py
# -------------------------------------------------------------------
# Fixed-index audio manager
# + Two tables: hdmi_channels{} (PRIMARY) and usb7_channels{} (SECONDARY)
# + Manual device indexes (no auto detection)
# + Fallback to system default if stream open fails
# + Threaded, overlapping playback
# + Text-to-speech (offline)
# + Simple unified play_audio() API
# -------------------------------------------------------------------

from __future__ import annotations
import os, threading, tempfile, subprocess, platform
from pathlib import Path
from typing import Dict, Optional, Tuple, List
import numpy as np
import sounddevice as sd
import soundfile as sf
from utils.tools import log_event

# ==========================================================
# === CONFIGURATION ========================================
# ==========================================================

# Set your exact device indexes (use list_output_devices())
PRIMARY_DEVICE_INDEX: Optional[int] = 3      # HDMI / AVR device
SECONDARY_DEVICE_INDEX: Optional[int] = 26   # USB 7.1 soundcard
FALLBACK_TO_SYSTEM_DEFAULT = True            # fallback if stream open fails

# Primary (HDMI) channels
hdmi_channels: Dict[str, Dict[str, float | int]] = {
    "graveyard":     {"index": 0, "gain": 1.0},
    "frontRight":    {"index": 1, "gain": 0.6},
    "center":        {"index": 2, "gain": 1.4},
    "subwoofer":     {"index": 3, "gain": 1.4},
    "swampRoom":     {"index": 4, "gain": 1.6},
    "atticSpeaker":  {"index": 5, "gain": 1.6},
    "graveyard1":    {"index": 6, "gain": 1.8},
    "closetCreak":   {"index": 7, "gain": 1.8},
}

# Secondary (USB 7.1) channels
usb7_channels: Dict[str, Dict[str, float | int]] = {
    "usb_FL": {"index": 0, "gain": 1.0},
    "usb_FR": {"index": 1, "gain": 1.0},
    "usb_C":  {"index": 2, "gain": 1.0},
    "usb_LFE":{"index": 3, "gain": 1.0},
    "usb_SL": {"index": 4, "gain": 1.0},
    "usb_SR": {"index": 5, "gain": 1.0},
    "usb_BL": {"index": 6, "gain": 1.0},
    "usb_BR": {"index": 7, "gain": 1.0},
}

# ==========================================================

DEFAULT_SOUND_DIR = (Path(__file__).resolve().parents[3] / "Assets" / "SoundDir").resolve()
MULTICH_MIN_CHANNELS = 6

_play_epoch = 0
_epoch_lock = threading.Lock()
_cutoff_epoch = 0
_stop_event = threading.Event()

class _Session:
    def __init__(self, epoch: int, label: str):
        self.epoch = epoch
        self.done = threading.Event()
        self.label = label

_active_lock = threading.Lock()
_active_sessions: list[_Session] = []
_active_streams: list[sd.OutputStream] = []

# ==========================================================
# === UTILITY FUNCTIONS ====================================
# ==========================================================

def text_to_wav(text: str, path: Path, rate: int = 0):
    system = platform.system()
    if system in ("Linux", "Darwin"):
        subprocess.run(["espeak", f"-s{150 + rate*10}", "-w", str(path), text], check=True)
    elif system == "Windows":
        rate = max(-10, min(10, rate))
        ps_script = f'''
        Add-Type -AssemblyName System.Speech
        $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
        $s.Rate = {rate}
        $s.SetOutputToWaveFile("{path}")
        $s.Speak("{text}")
        $s.Dispose()
        '''
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], check=True)
    else:
        raise RuntimeError(f"TTS not supported on {system}")

def _next_epoch() -> int:
    global _play_epoch
    with _epoch_lock:
        _play_epoch += 1
        return _play_epoch

def _resolve_sound_path(wav_or_path: str, base_folder: Optional[Path] = None) -> Path:
    p = Path(wav_or_path)
    if p.is_absolute():
        return p
    base = Path(base_folder) if base_folder else DEFAULT_SOUND_DIR
    return (base / p).resolve()

def _read_audio_mono(file_path: Path) -> tuple[np.ndarray, int]:
    data, fs = sf.read(str(file_path), dtype="float32", always_2d=True)
    mono = data[:, 0] if data.shape[1] == 1 else data.mean(axis=1)
    return mono.astype("float32"), int(fs)

def _ensure_samplerate(x: np.ndarray, src_fs: int, dst_fs: int) -> tuple[np.ndarray, int]:
    if src_fs == dst_fs:
        return x, src_fs
    n_src = len(x)
    n_dst = int(round(n_src * (dst_fs / src_fs)))
    t_src = np.linspace(0.0, 1.0, n_src, endpoint=False)
    t_dst = np.linspace(0.0, 1.0, n_dst, endpoint=False)
    return np.interp(t_dst, t_src, x).astype("float32"), dst_fs

def _pack_device(idx: int) -> Tuple[int, int, int, str, str]:
    d = sd.query_devices()[idx]
    max_out = int(d["max_output_channels"])
    default_fs = int(round(float(d.get("default_samplerate", 48000.0))))
    hostapis = sd.query_hostapis()
    hostapi_name = "unknown"
    for i, h in enumerate(hostapis):
        if idx in h.get("devices", []):
            hostapi_name = h.get("name", "unknown")
            break
    name_str = d.get("name", "Unknown")
    log_event(f"[Audio] Using device [{idx}] {name_str} ({hostapi_name}) out={max_out} fs={default_fs}")
    return idx, max_out, default_fs, hostapi_name, name_str

def _get_fixed_device(which: str) -> Tuple[int, int, int, str, str]:
    if which == "primary":
        if PRIMARY_DEVICE_INDEX is None:
            raise RuntimeError("PRIMARY_DEVICE_INDEX not set.")
        return _pack_device(PRIMARY_DEVICE_INDEX)
    if which == "secondary":
        if SECONDARY_DEVICE_INDEX is None:
            raise RuntimeError("SECONDARY_DEVICE_INDEX not set or disabled.")
        return _pack_device(SECONDARY_DEVICE_INDEX)
    raise ValueError("which must be 'primary' or 'secondary'")

def _resolve_named_target(name: str) -> Tuple[str, int, float]:
    if name in hdmi_channels and name in usb7_channels:
        raise ValueError(f"Name '{name}' exists in both tables.")
    if name in hdmi_channels:
        v = hdmi_channels[name]; return "primary", int(v["index"]), float(v["gain"])
    if name in usb7_channels:
        v = usb7_channels[name]; return "secondary", int(v["index"]), float(v["gain"])
    raise ValueError(f"Unknown channel name '{name}'.")

# ==========================================================
# === STREAM OPEN / PLAY ==================================
# ==========================================================

# --- replace ONLY this helper + function in control/audio_manager.py ---

def _device_hostapi_name(idx: int) -> str:
    try:
        hostapis = sd.query_hostapis()
        for h in hostapis:
            if idx in h.get("devices", []):
                return h.get("name", "unknown")
    except Exception:
        pass
    return "unknown"

def _open_stream_robust(fs: int, have_channels: int, device_index: int, device_name: str):
    """
    Open by fixed device INDEX. Host-API aware:
      - If the device is WASAPI: try exclusive -> shared at requested fs
      - Otherwise (MME/DirectSound/WDM-KS/etc): open generic shared at the DEVICE DEFAULT fs
      - Optional fallback to system default
    Returns (stream, used_fs)
    """
    hostapi = _device_hostapi_name(device_index).lower()
    blocksize = max(512, fs // 25)

    def _mk_wasapi(exclusive: bool):
        if "wasapi" in hostapi:
            try:
                return sd.WasapiSettings(exclusive=exclusive)
            except Exception:
                return None
        return None

    def _try(idx, ex, use_fs, note):
        log_event(f"[Audio] Opening idx={idx} '{device_name}', fs={use_fs}, ch={have_channels}, note={note}")
        return sd.OutputStream(
            samplerate=use_fs,
            device=idx,
            channels=have_channels,
            dtype="float32",
            blocksize=blocksize,
            latency=0.06,
            extra_settings=ex
        )

    # WASAPI path only if this index belongs to the WASAPI host API
    if "wasapi" in hostapi and have_channels >= MULTICH_MIN_CHANNELS:
        # 1) WASAPI exclusive at requested fs
        try:
            ex = _mk_wasapi(True)
            if ex:
                return _try(device_index, ex, fs, "WASAPI exclusive"), fs
        except Exception as e:
            log_event(f"[Audio] Fail WASAPI exclusive: {e}")

        # 2) WASAPI shared at requested fs
        try:
            ex = _mk_wasapi(False)
            return _try(device_index, ex, fs, "WASAPI shared"), fs
        except Exception as e:
            log_event(f"[Audio] Fail WASAPI shared: {e}")

    # Non-WASAPI (e.g., MME/DirectSound) → open at the device's default fs with no extra settings
    try:
        dev_default_fs = int(round(float(sd.query_devices()[device_index].get("default_samplerate", fs))))
        return _try(device_index, None, dev_default_fs, "generic shared (hostapi!=WASAPI)"), dev_default_fs
    except Exception as e:
        log_event(f"[Audio] Fail generic shared on hostapi '{hostapi}': {e}")

    # Optional fallback to system default output
    if FALLBACK_TO_SYSTEM_DEFAULT:
        try:
            _, def_out = sd.default.device  # (input, output)
            def_fs = int(round(float(sd.query_devices()[def_out].get("default_samplerate", fs))))
            log_event(f"[Audio] Falling back to system default idx={def_out}")
            return _try(def_out, None, def_fs, "system default fallback"), def_fs
        except Exception as e:
            log_event(f"[Audio] System default fallback failed: {e}")

    raise RuntimeError("Failed to open audio stream: all strategies exhausted.")
# --- end replace ---

def _play_mono_nonblocking(mono: np.ndarray, fs: int, dev_idx: int, dev_name: str,
                           have_channels: int, mode: str, ch_index: int | None,
                           gain: float, label: str):
    """Non-blocking threaded stream."""
    epoch = _next_epoch()
    session = _Session(epoch, label)

    def _worker():
        stream = None
        try:
            stream, used_fs = _open_stream_robust(fs, have_channels, dev_idx, dev_name)
            mono_res, _ = _ensure_samplerate(mono, fs, used_fs)
            with stream:
                with _active_lock:
                    _active_streams.append(stream)
                    _active_sessions.append(session)
                blocksize = stream.blocksize or max(512, used_fs // 25)
                zero_blk = np.zeros((blocksize, have_channels), np.float32)
                stream.write(zero_blk)
                n = mono_res.shape[0]; pos = 0
                while pos < n:
                    if epoch <= _cutoff_epoch or _stop_event.is_set(): break
                    end = min(pos + blocksize, n)
                    block = mono_res[pos:end] * gain
                    if have_channels <= 1:
                        out = block[:, None]
                    elif have_channels == 2:
                        out = np.column_stack((block, block))
                    else:
                        if mode == "all":
                            out = np.repeat(block[:, None], have_channels, axis=1)
                        else:
                            idx = min(int(ch_index or 0), have_channels - 1)
                            out = np.zeros((block.shape[0], have_channels), np.float32)
                            out[:, idx] = block
                    stream.write(out)
                    pos = end
        finally:
            with _active_lock:
                if stream in _active_streams: _active_streams.remove(stream)
                if session in _active_sessions: _active_sessions.remove(session)
            session.done.set()

    threading.Thread(target=_worker, daemon=True, name=f"Audio@{epoch}").start()

# ==========================================================
# === PUBLIC PLAYBACK API =================================
# ==========================================================

def play_to_named_channel(wav_file: str, target_name: str, *,
                          gain_override: float | None = None,
                          base_folder: Path | str | None = None):
    base_path = Path(base_folder) if base_folder else DEFAULT_SOUND_DIR
    file_path = _resolve_sound_path(wav_file, base_folder=base_path)
    if not file_path.exists():
        raise FileNotFoundError(file_path)
    kind, ch_index, default_gain = _resolve_named_target(target_name)
    gain = gain_override if gain_override is not None else default_gain
    mono, src_fs = _read_audio_mono(file_path)
    mono, out_fs = _ensure_samplerate(mono, src_fs, 48000)
    dev = _get_fixed_device(kind)
    ch = min(ch_index, dev[1] - 1)
    log_event(f"[Audio] Playing '{file_path.name}' on {kind.upper()} ch={ch}, gain={gain}")
    _play_mono_nonblocking(mono, out_fs, dev[0], dev[4], dev[1], "one", ch, gain, f"{file_path.name}@{target_name}")

def play_to_all_channels(wav_or_text: str, *, tts_rate: int = 0,
                         gain_override: float | None = None,
                         base_folder: Path | str | None = None):
    base_path = Path(base_folder) if base_folder else DEFAULT_SOUND_DIR
    tmp_path = None
    try:
        treat_as_file = Path(wav_or_text).suffix.lower() == ".wav"
        if not treat_as_file:
            abs_candidate = (base_path / wav_or_text)
            if abs_candidate.exists(): treat_as_file = True
        if treat_as_file:
            file_path = _resolve_sound_path(wav_or_text, base_folder=base_path)
        else:
            fd, tmp = tempfile.mkstemp(suffix=".wav"); os.close(fd)
            tmp_path = Path(tmp)
            text_to_wav(wav_or_text, tmp_path, tts_rate)
            file_path = tmp_path
        mono, src_fs = _read_audio_mono(file_path)
        mono, out_fs = _ensure_samplerate(mono, src_fs, 48000)
        gain = gain_override or 1.0
        dev = _get_fixed_device("primary")
        _play_mono_nonblocking(mono, out_fs, dev[0], dev[4], dev[1], "all", None, gain, file_path.name)
    finally:
        if tmp_path and tmp_path.exists():
            try: tmp_path.unlink()
            except OSError: pass

def play_audio(target_or_text: str, maybe_file: str | None = None, *,
               gain: float | None = None,
               base_folder: Path | str | None = None,
               tts_rate: int = 0):
    """
    - play_audio("frontLeft", "boom.wav")
    - play_audio("usb_C", "boom.wav")
    - play_audio("all", "boom.wav")
    - play_audio("The manor is opening...")
    - play_audio("frontLeft: The manor is opening...")
    """
    if maybe_file:
        if target_or_text.lower() == "all":
            play_to_all_channels(maybe_file, gain_override=gain, base_folder=base_folder)
        else:
            play_to_named_channel(maybe_file, target_or_text, gain_override=gain, base_folder=base_folder)
        return
    if ":" in target_or_text:
        name, txt = target_or_text.split(":", 1)
        name, txt = name.strip(), txt.strip()
        if name in hdmi_channels or name in usb7_channels:
            fd, tmp = tempfile.mkstemp(suffix=".wav"); os.close(fd)
            tmp_path = Path(tmp)
            try:
                text_to_wav(txt, tmp_path, tts_rate)
                play_to_named_channel(str(tmp_path), name, gain_override=gain, base_folder=base_folder)
            finally:
                try: tmp_path.unlink()
                except OSError: pass
            return
    play_to_all_channels(target_or_text, tts_rate=tts_rate, gain_override=gain, base_folder=base_folder)

# ==========================================================
# === CONTROL / UTILITY ===================================
# ==========================================================

def stop_all_audio(timeout: float = 2.0):
    global _cutoff_epoch
    with _epoch_lock:
        snapshot = _play_epoch
        _cutoff_epoch = snapshot
    _stop_event.set()
    import time
    log_event(f"[Audio] stop_all_audio(): cutoff={snapshot}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _active_lock:
            active = [s for s in _active_sessions if not s.done.is_set()]
        if not active: break
        time.sleep(0.05)
    _stop_event.clear()
    log_event(f"[Audio] stop_all_audio(): complete")

def list_output_devices() -> list[str]:
    devices = sd.query_devices()
    return [f"[{i}] {d['name']} ({d['max_output_channels']}ch)" for i, d in enumerate(devices)
            if d.get("max_output_channels", 0) > 0]

def list_named_channels() -> Dict[str, Dict[str, float | int | str]]:
    out: Dict[str, Dict[str, float | int | str]] = {}
    for k, v in hdmi_channels.items():
        out[k] = {"index": v["index"], "gain": v["gain"], "device": "primary"}
    for k, v in usb7_channels.items():
        out[k] = {"index": v["index"], "gain": v["gain"], "device": "secondary"}
    return out

def register_hdmi_channel(name: str, index: int, gain: float = 1.0):
    if name in usb7_channels: raise ValueError(f"'{name}' exists in usb7_channels")
    hdmi_channels[name] = {"index": index, "gain": gain}

def register_usb7_channel(name: str, index: int, gain: float = 1.0):
    if name in hdmi_channels: raise ValueError(f"'{name}' exists in hdmi_channels")
    usb7_channels[name] = {"index": index, "gain": gain}

def set_channel_gain(name: str, gain: float):
    if name in hdmi_channels:
        hdmi_channels[name]["gain"] = gain; return
    if name in usb7_channels:
        usb7_channels[name]["gain"] = gain; return
    raise ValueError(f"Unknown channel '{name}'")
