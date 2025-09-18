# control/audio_manager.py (gain_override fixed for fallback)
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
    "graveyard":       {"index": 6, "gain": 1.8},
    "closetCreak":   {"index": 7, "gain": 1.8},
}

DEFAULT_SOUND_DIR = (Path(__file__).resolve().parents[3] / "Assets" / "SoundDir").resolve()
DEFAULT_DEVICE_INDEX: Optional[int] = 50  # 38 IS THE 7.1 CARD INDEX
DEFAULT_TOTAL_CHANNELS = 8


# ---------- Globals for active streams ----------

_active_streams: list[sd.OutputStream] = []
_active_lock = threading.Lock()
_audio_shutdown = False  # flag checked by all audio threads


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

def _play_array_nonblocking(arr: np.ndarray, fs: int, device_index: Optional[int]) -> None:
    """
    Play array in a background thread using its own OutputStream.
    Respects _audio_shutdown flag for graceful global stop.
    """
    def _worker():
        global _audio_shutdown
        stream = None
        try:
            with sd.OutputStream(
                samplerate=fs,
                device=device_index,
                channels=arr.shape[1],
                dtype="float32"
            ) as stream:
                with _active_lock:
                    _active_streams.append(stream)

                blocksize = fs  # ~1 second blocks
                for start in range(0, len(arr), blocksize):
                    if _audio_shutdown:
                        break
                    end = start + blocksize
                    stream.write(arr[start:end])
        finally:
            with _active_lock:
                if stream and stream in _active_streams:
                    _active_streams.remove(stream)

    # Reset shutdown flag when new audio starts (allow playback after shutdown)
    global _audio_shutdown
    _audio_shutdown = False
    threading.Thread(target=_worker, daemon=True, name="AudioWorker").start()

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


# ---------- Shutdown control ----------

def stop_all_audio():
    """Signal all audio threads to stop gracefully."""
    global _audio_shutdown
    _audio_shutdown = True
    log_event("[Audio] Shutdown signal sent to all audio")


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
            dev_idx, _ = _choose_output_device(device_index)
            if dev_idx != device_index:
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
    _play_array_nonblocking(out, fs=fs, device_index=dev_idx)

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

        label = f"file '{file_path.name}'" if treat_as_file else f"TTS '{str(wav_or_text)[:60]}'"
        log_event(
            f"[Audio] Playing {label} -> ALL channels ({have_channels}), "
            f"gain={gain:.2f}, device={_device_label(dev_idx)}, fs={fs}"
        )
        _play_array_nonblocking(out, fs=fs, device_index=dev_idx)

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
