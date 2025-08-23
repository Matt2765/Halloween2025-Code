# control/audio_manager.py (rewritten)
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import sounddevice as sd
import soundfile as sf
import pyttsx3

from utils.tools import log_event


# ---------- Configuration ----------

# Map of logical speaker names to (channel index, default gain)
named_channels: Dict[str, Dict[str, float | int]] = {
    "frontLeft":     {"index": 0, "gain": 1.0},
    "frontRight":    {"index": 1, "gain": 1.0},
    "center":        {"index": 2, "gain": 1.4},
    "subwoofer":     {"index": 3, "gain": 1.4},
    "swampRoom":     {"index": 4, "gain": 1.6},
    "atticSpeaker":  {"index": 5, "gain": 1.6},
    "dungeon":       {"index": 6, "gain": 1.8},
    "closetCreak":   {"index": 7, "gain": 1.8},
}

# Resolve: .../Halloween2025/Assets/SoundDir
DEFAULT_SOUND_DIR = (Path(__file__).resolve().parents[3] / "Assets" / "SoundDir").resolve()

# Keep your previous default to avoid breaking callers; you can change to None to use system default
DEFAULT_DEVICE_INDEX: Optional[int] = 38

# Total output channels on your interface (7.1 = 8)
DEFAULT_TOTAL_CHANNELS = 8


# ---------- Helpers ----------

def _resolve_sound_path(wav_or_path: str, base_folder: Optional[Path] = None) -> Path:
    """
    Turn a filename or absolute path into a concrete file Path.
    If `wav_or_path` is not an absolute path, resolve it relative to `base_folder` (or DEFAULT_SOUND_DIR).
    """
    p = Path(wav_or_path)
    if p.is_absolute():
        return p
    base = Path(base_folder) if base_folder is not None else DEFAULT_SOUND_DIR
    return (base / p).resolve()

def _device_label(idx: int) -> str:
    """Return a short label like '[12] Speakers (Realtek...)' for logging."""
    try:
        name = sd.query_devices()[idx].get("name", "Unknown")
    except Exception:
        name = "Unknown"
    return f"[{idx}] {name}"

def _read_audio_mono(file_path: Path) -> tuple[np.ndarray, int]:
    """
    Read a WAV/FLAC/etc and return (mono_float32_array, samplerate).
    If the file is multi-channel, downmix to mono by averaging channels.
    """
    data, fs = sf.read(str(file_path), dtype="float32", always_2d=True)
    # data shape: (samples, channels)
    if data.shape[1] == 1:
        mono = data[:, 0]
    else:
        mono = data.mean(axis=1)  # downmix to mono
    return mono.astype("float32", copy=False), int(fs)


def _ensure_channel_buffer(mono: np.ndarray, total_channels: int, target_index: int, gain: float) -> np.ndarray:
    """
    Create an output buffer with `total_channels` where only `target_index` is filled with mono * gain.
    """
    if not (0 <= target_index < total_channels):
        raise ValueError(f"Target channel index {target_index} out of range 0..{total_channels-1}")
    out = np.zeros((mono.shape[0], total_channels), dtype="float32")
    out[:, target_index] = mono * gain
    return out


def _play_array(arr: np.ndarray, fs: int, device_index: Optional[int]) -> None:
    """
    Play an (N, channels) float32 array via sounddevice and block until completion.
    """
    sd.play(arr, samplerate=fs, device=device_index)
    sd.wait()

def _choose_output_device(preferred_index: int | None) -> Tuple[int | None, int]:
    """
    Return (device_index, max_output_channels) for an output device.
    Logs which device will be used.
    """
    devices = sd.query_devices()

    def valid_out(idx: int) -> bool:
        return 0 <= idx < len(devices) and devices[idx].get("max_output_channels", 0) > 0

    # 1) Try preferred
    if isinstance(preferred_index, int) and valid_out(preferred_index):
        max_out = int(devices[preferred_index]["max_output_channels"])
        log_event(f"[Audio] Using preferred device {_device_label(preferred_index)} with {max_out} output channels")
        return preferred_index, max_out

    # 2) Try system default output
    try:
        default_in, default_out = sd.default.device  # may be (None, None)
    except Exception:
        default_out = None

    if isinstance(default_out, int) and valid_out(default_out):
        max_out = int(devices[default_out]["max_output_channels"])
        log_event(f"[Audio] Using system default device {_device_label(default_out)} with {max_out} output channels")
        return default_out, max_out

    # 3) First available output device
    for idx, d in enumerate(devices):
        if d.get("max_output_channels", 0) > 0:
            max_out = int(d["max_output_channels"])
            log_event(f"[Audio] Using first available device {_device_label(idx)} with {max_out} output channels")
            return idx, max_out

    raise RuntimeError("No output audio devices with output channels found.")

def _play_array_with_fallback(
    arr: np.ndarray,
    fs: int,
    preferred_device_index: int | None,
) -> None:
    """
    Play an (N, channels) float32 array, adapt to the actual output device.
    """
    if arr.ndim == 1:
        arr = arr[:, None]
    arr = arr.astype("float32", copy=False)

    dev_idx, max_out = _choose_output_device(preferred_device_index)

    want = arr.shape[1]
    have = max_out
    if have <= 0:
        raise RuntimeError("Selected output device reports 0 output channels.")

    if want == have:
        play_buf = arr
    elif want < have:
        pad = np.repeat(arr[:, -1:], have - want, axis=1)
        play_buf = np.concatenate([arr, pad], axis=1)
    else:
        if have == 1:
            mono = arr.mean(axis=1, keepdims=True)
            play_buf = mono
        else:
            mono = arr.mean(axis=1, keepdims=True)
            base = np.concatenate([mono, mono], axis=1)
            if have > 2:
                pad = np.repeat(base[:, -1:], have - 2, axis=1)
                play_buf = np.concatenate([base, pad], axis=1)
            else:
                play_buf = base

    try:
        sd.play(play_buf, samplerate=fs, device=dev_idx)
        sd.wait()
    except sd.PortAudioError:
        log_event(f"[Audio] PortAudioError on {_device_label(dev_idx)}; retrying with system default (device=None)")
        sd.play(play_buf, samplerate=fs, device=None)
        sd.wait()


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
        raise FileNotFoundError(
            f"Audio file not found: {file_path}\n"
            f"— Searched base folder: {base_path}\n"
            f"— Current working dir:  {Path.cwd()}\n"
            f"— Default sound dir:    {DEFAULT_SOUND_DIR}"
        )

    target = named_channels[target_name]
    ch_index = int(target["index"])
    gain = float(gain_override) if gain_override is not None else float(target["gain"])

    mono, fs = _read_audio_mono(file_path)

    # Determine actual device/channel capacity (and log device choice inside)
    dev_idx, have_channels = _choose_output_device(device_index)

    if have_channels <= 1:
        out = mono[:, None] * gain
        use_idx = 0
    elif have_channels == 2:
        out = np.concatenate([mono[:, None], mono[:, None]], axis=1) * gain
        use_idx = 0  # conceptually centered
    else:
        use_idx = min(ch_index, have_channels - 1)
        out = np.zeros((mono.shape[0], have_channels), dtype="float32")
        out[:, use_idx] = mono * gain

    # Log the playback action
    log_event(
        f"[Audio] Playing '{file_path.name}' -> channel '{target_name}' (idx {use_idx}), "
        f"gain={gain:.2f}, device={_device_label(dev_idx)}, channels={have_channels}, fs={fs}"
    )

    _play_array_with_fallback(out, fs=fs, preferred_device_index=device_index)

def play_to_all_channels(
    wav_or_text: str,
    device_index: int | None = DEFAULT_DEVICE_INDEX,
    base_folder: Path | str | None = None,
    tts_rate: int = 150,
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
            engine = pyttsx3.init()
            engine.setProperty("rate", tts_rate)
            fd, path_str = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            tmp_path = Path(path_str)
            engine.save_to_file(wav_or_text, str(tmp_path))
            engine.runAndWait()
            file_path = tmp_path

        if not Path(file_path).exists():
            raise FileNotFoundError(f"Audio file not found: {file_path}")

        mono, fs = _read_audio_mono(Path(file_path))

        # Decide device/capacity now (and log inside chooser)
        dev_idx, have_channels = _choose_output_device(device_index)

        if have_channels <= 1:
            out = mono[:, None]
        else:
            out = np.repeat(mono[:, None], have_channels, axis=1)

        # Log playback
        label = f"file '{file_path.name}'" if treat_as_file else f"TTS '{str(wav_or_text)[:60]}'"
        log_event(
            f"[Audio] Playing {label} -> ALL channels ({have_channels}), "
            f"device={_device_label(dev_idx)}, fs={fs}"
        )

        _play_array_with_fallback(out, fs=fs, preferred_device_index=device_index)

    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

# ---------- Optional utilities ----------

def sound_dir() -> Path:
    """Return the resolved default sound directory (Assets/SoundDir)."""
    return DEFAULT_SOUND_DIR


def list_output_devices() -> list[str]:
    """Return a simple list of output device names with their indices for quick selection."""
    devices = sd.query_devices()
    out = []
    for idx, d in enumerate(devices):
        if d.get("max_output_channels", 0) > 0:
            out.append(f"[{idx}] {d.get('name', 'Unknown')}")
    return out
