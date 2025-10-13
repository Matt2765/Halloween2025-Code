# control/audio_manager.py
# -------------------------------------------------------------------
# Generation-based audio stop system (epoch cutoff)
# -------------------------------------------------------------------
# What’s new:
# - Every playback is tagged with a monotonically increasing epoch ID.
# - Calling stop_all_audio() snapshots the current epoch into _cutoff_epoch
#   and signals a wake event. Any playback whose epoch <= _cutoff_epoch exits
#   at the next buffer boundary (near-instant on small blocks).
# - New audio (e.g., TTS generated WAV) started AFTER stop_all_audio()
#   gets a higher epoch, so it keeps playing normally.
#
# Notes:
# - We only use sounddevice + soundfile.
# - We do NOT “sleep and poll”; we check the cutoff on each block write and
#   also wake blocked writers via _stop_event so they re-check immediately.
# - We keep a tiny per-playback _Session(done Event, epoch) so stop can wait
#   briefly for all pre-cutoff sessions to terminate (deterministic).
# - Blocksize ~= 20 ms for responsive stops without excessive CPU use.
# -------------------------------------------------------------------

from __future__ import annotations

import os
import threading
import tempfile
import subprocess
import platform
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import sounddevice as sd
import soundfile as sf

from utils.tools import log_event


# ---------- Configuration ----------

named_channels: Dict[str, Dict[str, float | int]] = {
    "frontLeft":     {"index": 0, "gain": 1.0},
    "frontRight":    {"index": 1, "gain": 1.0},
    "center":        {"index": 2, "gain": 1.4},
    "subwoofer":     {"index": 3, "gain": 1.4},
    "swampRoom":     {"index": 4, "gain": 1.6},
    "atticSpeaker":  {"index": 5, "gain": 1.6},
    "graveyard":     {"index": 6, "gain": 1.8},
    "closetCreak":   {"index": 7, "gain": 1.8},
}

DEFAULT_SOUND_DIR = (Path(__file__).resolve().parents[3] / "Assets" / "SoundDir").resolve()
DEFAULT_DEVICE_INDEX: Optional[int] = 50  # 38 IS THE 7.1 CARD INDEX
DEFAULT_TOTAL_CHANNELS = 8


# ---------- Globals (epochs, sessions, state) ----------

# Epoch counter for playbacks; increment for every new playback
_play_epoch: int = 0
_epoch_lock = threading.Lock()

# Any playback with epoch <= _cutoff_epoch must stop
_cutoff_epoch: int = 0

# Wake signal so any blocked writer re-checks cutoff immediately
_stop_event = threading.Event()

# Track active audio sessions so we can deterministically wait in stop_all_audio
class _Session:
    def __init__(self, epoch: int, label: str):
        self.epoch = epoch
        self.done = threading.Event()
        self.label = label  # for logging/debug only

_active_lock = threading.Lock()
_active_sessions: list[_Session] = []

# (Optional) Keep list of live streams for visibility (not strictly required)
_active_streams: list[sd.OutputStream] = []


# ---------- TTS (offline reliable) ----------

def text_to_wav(text: str, path: Path, rate: int = 0):
    system = platform.system()

    if system in ("Linux", "Darwin"):  # macOS = Darwin
        subprocess.run(
            ["espeak", f"-s{150 + rate*10}", "-w", str(path), text],
            check=True
        )
    elif system == "Windows":
        rate = max(-10, min(10, rate))  # clamp to -10..10
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

def _device_label(idx: int) -> str:
    try:
        name = sd.query_devices()[idx].get("name", "Unknown")
    except Exception:
        name = "Unknown"
    return f"[{idx}] {name}"

def _read_audio_mono(file_path: Path) -> tuple[np.ndarray, int]:
    data, fs = sf.read(str(file_path), dtype="float32", always_2d=True)
    if data.shape[1] == 1:
        mono = data[:, 0]
    else:
        mono = data.mean(axis=1)
    return mono.astype("float32", copy=False), int(fs)

def _play_array_nonblocking(arr: np.ndarray, fs: int, device_index: Optional[int], label: str) -> None:
    """
    Play array on a background thread with its own OutputStream.
    Uses epoch cutoff for deterministic global stops.

    - This playback's epoch is captured at start.
    - On each block, if epoch <= _cutoff_epoch OR _stop_event.is_set(), the worker exits.
    - Blocksize ~ 20 ms for responsive stopping.
    """
    epoch = _next_epoch()
    session = _Session(epoch=epoch, label=label)

    def _worker():
        stream = None
        try:
            # Use a short blocksize for responsive stop behavior (~20 ms)
            blocksize = max(256, fs // 50)

            with sd.OutputStream(
                samplerate=fs,
                device=device_index,
                channels=arr.shape[1],
                dtype="float32"
            ) as stream:
                with _active_lock:
                    _active_streams.append(stream)
                    _active_sessions.append(session)

                # Iterate in blocks; bail immediately if this is a pre-cutoff playback
                for start in range(0, len(arr), blocksize):
                    if epoch <= _cutoff_epoch or _stop_event.is_set():
                        break
                    end = start + blocksize
                    stream.write(arr[start:end])
        finally:
            with _active_lock:
                if stream and stream in _active_streams:
                    _active_streams.remove(stream)
                if session in _active_sessions:
                    _active_sessions.remove(session)
            session.done.set()

    t = threading.Thread(target=_worker, daemon=True, name=f"AudioWorker@{epoch}")
    t.start()

def _choose_output_device(preferred_index: int | None) -> Tuple[int | None, int]:
    devices = sd.query_devices()

    def valid_out(idx: int) -> bool:
        return 0 <= idx < len(devices) and devices[idx].get("max_output_channels", 0) > 0

    if isinstance(preferred_index, int) and valid_out(preferred_index):
        max_out = int(devices[preferred_index]["max_output_channels"])
        log_event(f"[Audio] Using preferred device {_device_label(preferred_index)} with {max_out} output channels")
        return preferred_index, max_out

    try:
        default_in, default_out = sd.default.device
    except Exception:
        default_out = None

    if isinstance(default_out, int) and valid_out(default_out):
        max_out = int(devices[default_out]["max_output_channels"])
        log_event(f"[Audio] Using system default device {_device_label(default_out)} with {max_out} output channels")
        return default_out, max_out

    for idx, d in enumerate(devices):
        if d.get("max_output_channels", 0) > 0:
            max_out = int(d["max_output_channels"])
            log_event(f"[Audio] Using first available device {_device_label(idx)} with {max_out} output channels")
            return idx, max_out

    raise RuntimeError("No output audio devices with output channels found.")


# ---------- Shutdown control (epoch cutoff) ----------

def stop_all_audio(timeout: float = 2.0):
    """
    Cut off ALL audio that started at or before the current epoch.
    New audio started AFTER this call (e.g., immediate TTS) will continue.

    Steps:
      1) Snapshot the current play epoch into _cutoff_epoch.
      2) Signal _stop_event so any sleepers wake and re-check cutoff.
      3) Wait briefly for all pre-cutoff sessions to report done.
    """
    global _cutoff_epoch
    with _epoch_lock:
        snapshot = _play_epoch
        _cutoff_epoch = snapshot

    _stop_event.set()
    log_event(f"[Audio] stop_all_audio(): cutoff_epoch set to {snapshot}")

    # Wait for all sessions with epoch <= cutoff to finish (drain up to 'timeout')
    end_time = sd.get_stream_write_available.__self__ if False else None  # (doc hint: ignore)
    deadline = None
    if timeout is not None and timeout > 0:
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
        # Wait a short slice or until one completes
        for s in pending:
            if s.done.wait(timeout=0.05):
                break

    # Clear the event so subsequent, post-cutoff playbacks don't get a spurious wake
    _stop_event.clear()

    # Final status log
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
    total_channels: int = DEFAULT_TOTAL_CHANNELS,
    gain_override: float | None = None,
    base_folder: Path | str | None = None,
) -> None:
    if target_name not in named_channels:
        raise ValueError(f"Channel name '{target_name}' not found. Valid: {list(named_channels.keys())}")

    base_path = Path(base_folder) if base_folder is not None else DEFAULT_SOUND_DIR
    file_path = _resolve_sound_path(wav_file, base_folder=base_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    target = named_channels[target_name]
    ch_index = int(target["index"])

    # Gain logic
    if gain_override is not None:
        gain = float(gain_override)
    else:
        gain = float(target["gain"])
        if device_index is not None:
            dev_idx_probe, _ = _choose_output_device(device_index)
            if dev_idx_probe != device_index:
                log_event("[Audio] Fallback device in use — resetting gain to 1.0 for safety")
                gain = 1.0

    mono, fs = _read_audio_mono(file_path)
    dev_idx, have_channels = _choose_output_device(device_index)

    if have_channels <= 1:
        out = mono[:, None] * gain
        use_idx = 0
    elif have_channels == 2:
        out = np.concatenate([mono[:, None], mono[:, None]], axis=1) * gain
        use_idx = 0
    else:
        use_idx = min(ch_index, have_channels - 1)
        out = np.zeros((mono.shape[0], have_channels), dtype="float32")
        out[:, use_idx] = mono * gain

    log_event(
        f"[Audio] Playing '{file_path.name}' -> channel '{target_name}' (idx {use_idx}), "
        f"gain={gain:.2f}, device={_device_label(dev_idx)}, channels={have_channels}, fs={fs}"
    )
    _play_array_nonblocking(out, fs=fs, device_index=dev_idx, label=f"{file_path.name}@{target_name}")

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

        mono, fs = _read_audio_mono(Path(file_path))
        dev_idx, have_channels = _choose_output_device(device_index)

        # Gain logic
        if gain_override is not None:
            gain = float(gain_override)
        else:
            gain = 1.0
            if device_index is not None and dev_idx != device_index:
                log_event("[Audio] Fallback device in use — resetting gain to 1.0 for safety")

        if have_channels <= 1:
            out = mono[:, None] * gain
        else:
            out = np.repeat(mono[:, None], have_channels, axis=1) * gain

        label = f"file:{Path(file_path).name}" if treat_as_file else f"tts:{str(wav_or_text)[:40]}"
        log_event(
            f"[Audio] Playing {label} -> ALL channels ({have_channels}), "
            f"gain={gain:.2f}, device={_device_label(dev_idx)}, fs={fs}"
        )
        _play_array_nonblocking(out, fs=fs, device_index=dev_idx, label=label)

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
