# control/audio_manager.py
# -------------------------------------------------------------------
# Generation-based audio stop system (epoch cutoff)
# + Automatic resample to device rate (HDMI/USB-friendly)
# + Per-block channel mapping (low RAM)
# + Robust device selection (prefers WASAPI multichannel), resolves duplicate names
# + Open streams BY INDEX to avoid "Multiple devices found for 'NAME'" error
# + Smart fallbacks (WASAPI exclusive -> shared -> DS/MME w/ default fs)
# + Latency tuning + prebuffering (stutter fix)
# + Optional simultaneous playback to a SECOND device (e.g., HDMI + USB 7.1)
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
    "graveyard":     {"index": 1, "gain": 0.6},
    "center":        {"index": 2, "gain": 1.4},
    "subwoofer":     {"index": 3, "gain": 1.4},
    "swampRoom":     {"index": 4, "gain": 1.6},
    "atticSpeaker":  {"index": 5, "gain": 1.6},
    "graveyard1":    {"index": 6, "gain": 1.8},
    "closetCreak":   {"index": 7, "gain": 1.8},
}

DEFAULT_SOUND_DIR = (Path(__file__).resolve().parents[3] / "Assets" / "SoundDir").resolve()

# Primary output preference (index is optional; automatic selection prefers WASAPI multichannel)
DEFAULT_DEVICE_INDEX: Optional[int] = 3
DEFAULT_TOTAL_CHANNELS = 8

# Secondary output (optional simultaneous playback). Set via env:
#   SECONDARY_AUDIO_DEVICE_SUBSTR="USB"  (name contains)
# or SECONDARY_AUDIO_DEVICE_INDEX="12"
SECONDARY_ENV_SUBSTR = "SECONDARY_AUDIO_DEVICE_SUBSTR"
SECONDARY_ENV_INDEX = "SECONDARY_AUDIO_DEVICE_INDEX"

# Device name matching (case-insensitive). You can override with env var AUDIO_DEVICE_SUBSTR.
DEFAULT_NAME_HINTS: List[str] = [
    "RX-V673", "YAMAHA", "NVIDIA HIGH DEFINITION AUDIO", "NVIDIA", "HDMI", "AVR", "TV"
]

# Minimum channels to consider “multichannel/HDMI-like”.
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
        raise RuntimeError(f"TTS not supported on this platform: {system}")


# ---------- Async wrappers ----------

def play_to_named_channel_async(*args, **kwargs):
    threading.Thread(target=play_to_named_channel, args=args, kwargs=kwargs, daemon=True, name="AudioWorker-Named").start()

def play_to_all_channels_async(*args, **kwargs):
    threading.Thread(target=play_to_all_channels, args=args, kwargs=kwargs, daemon=True, name="AudioWorker-All").start()


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

def _read_audio_mono(file_path: Path) -> tuple[np.ndarray, int]:
    data, fs = sf.read(str(file_path), dtype="float32", always_2d=True)
    mono = data[:, 0] if data.shape[1] == 1 else data.mean(axis=1)
    return mono.astype("float32", copy=False), int(fs)

def _ensure_samplerate(x: np.ndarray, src_fs: int, dst_fs: int) -> tuple[np.ndarray, int]:
    if src_fs == dst_fs:
        return x, src_fs
    n_src = x.shape[0]
    n_dst = int(round(n_src * (dst_fs / src_fs)))
    t_src = np.linspace(0.0, 1.0, num=n_src, endpoint=False, dtype=np.float64)
    t_dst = np.linspace(0.0, 1.0, num=n_dst, endpoint=False, dtype=np.float64)
    y = np.interp(t_dst, t_src, x.astype(np.float64, copy=False)).astype(np.float32, copy=False)
    return y, dst_fs

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
                if 0 <= dev_idx < len(devices):
                    d = devices[dev_idx]
                    out_ch = d.get("max_output_channels", 0)
                    in_ch = d.get("max_input_channels", 0)
                    fs = d.get("default_samplerate", 0)
                    log_event(f"[Audio]   dev {dev_idx:>3} | out={out_ch:<2} in={in_ch:<2} "
                              f"| fs={int(round(float(fs or 0)))} | name='{d.get('name','?')}'")
        log_event("[Audio] =======================================")
    except Exception as e:
        log_event(f"[Audio] Device inventory failed: {e}")

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
    log_event(f"[Audio] Using device [{idx}] {name_str} (hostapi={hostapi_name}) "
              f"with {max_out} out @ {default_fs} Hz default")
    return idx, max_out, default_fs, hostapi_name, name_str

def _choose_output_device(preferred_index: int | None,
                          name_hints: Optional[List[str]] = None
                         ) -> Tuple[int, int, int, str, str]:
    """
    Returns (device_index, max_output_channels, default_samplerate, hostapi_name, device_name)
    Chooses a concrete device INDEX (avoids duplicate-name ambiguity).
    """
    _list_devices_debug()
    devices = sd.query_devices()
    hints = name_hints or []
    env_hint = os.getenv("AUDIO_DEVICE_SUBSTR")
    if env_hint:
        hints = [env_hint] + hints
    hints = [h.lower() for h in hints]

    def valid_out(idx: int) -> bool:
        return 0 <= idx < len(devices) and devices[idx].get("max_output_channels", 0) > 0

    # Prefer explicitly given index
    if isinstance(preferred_index, int) and valid_out(preferred_index):
        return _pack_device(preferred_index)

    # Prefer WASAPI with >=6ch and name match
    if platform.system() == "Windows":
        wasapi_idx = _get_hostapi_index("wasapi")
        if wasapi_idx is not None:
            h = sd.query_hostapis()[wasapi_idx]
            wasapi_devs = [i for i in h.get("devices", []) if valid_out(i)]
            name_match_multi = [i for i in wasapi_devs
                                if any(s in devices[i].get("name","").lower() for s in hints)
                                and int(devices[i].get("max_output_channels",0)) >= MULTICH_MIN_CHANNELS]
            if name_match_multi:
                # choose highest channel count
                choice = max(name_match_multi, key=lambda i: int(devices[i]["max_output_channels"]))
                return _pack_device(choice)

            any_multi = [i for i in wasapi_devs if int(devices[i].get("max_output_channels",0)) >= MULTICH_MIN_CHANNELS]
            if any_multi:
                choice = max(any_multi, key=lambda i: int(devices[i]["max_output_channels"]))
                return _pack_device(choice)

            def_out = h.get("default_output_device", -1)
            if valid_out(def_out):
                return _pack_device(def_out)

    # Generic: highest channel count overall
    candidates = [i for i in range(len(devices)) if valid_out(i)]
    if candidates:
        choice = max(candidates, key=lambda i: int(devices[i]["max_output_channels"]))
        return _pack_device(choice)

    raise RuntimeError("No output audio devices with output channels found.")

def _choose_secondary_device() -> Optional[Tuple[int, int, int, str, str]]:
    """
    Optional secondary output. Looks for env SECONDARY_AUDIO_DEVICE_INDEX or *_SUBSTR.
    Returns same tuple as _choose_output_device or None if not configured/found.
    """
    idx_str = os.getenv(SECONDARY_ENV_INDEX, "").strip()
    substr = os.getenv(SECONDARY_ENV_SUBSTR, "").strip().lower()
    if not idx_str and not substr:
        return None

    devices = sd.query_devices()
    def valid_out(i: int) -> bool:
        return 0 <= i < len(devices) and devices[i].get("max_output_channels", 0) > 0

    # Index wins if provided and valid
    if idx_str.isdigit():
        idx = int(idx_str)
        if valid_out(idx):
            return _pack_device(idx)

    # Otherwise search by substring, prefer WASAPI then highest channels
    candidates = []
    for i, d in enumerate(devices):
        if valid_out(i) and substr and substr in d.get("name","").lower():
            candidates.append(i)
    if not candidates:
        log_event(f"[Audio] Secondary device not found for substr '{substr}'")
        return None

    # Prefer WASAPI among matches
    wasapi_idx = _get_hostapi_index("wasapi")
    if wasapi_idx is not None:
        wasapi_devs = set(sd.query_hostapis()[wasapi_idx].get("devices", []))
        wasapi_matches = [i for i in candidates if i in wasapi_devs]
        if wasapi_matches:
            choice = max(wasapi_matches, key=lambda i: int(devices[i]["max_output_channels"]))
            return _pack_device(choice)

    choice = max(candidates, key=lambda i: int(devices[i]["max_output_channels"]))
    return _pack_device(choice)


# ---------- Stream open (BY INDEX, robust) ----------

def _open_stream_robust(fs: int, have_channels: int, device_index: int, device_name: str):
    """
    Try to open with WASAPI exclusive → WASAPI shared → same index shared → DS/MME alt index (same name).
    Returns (stream, used_fs). Uses INDEX to avoid duplicate-name ambiguity.
    """
    blocksize = max(512, fs // 25)  # ~40 ms @ 48k

    def _mk_wasapi(exclusive: bool):
        if platform.system() == "Windows":
            try:
                return sd.WasapiSettings(exclusive=exclusive)
            except Exception:
                return None
        return None

    def _try(idx, ex, use_fs, note):
        log_event(f"[Audio] Opening stream idx={idx} '{device_name}', fs={use_fs}, ch={max(1, have_channels)}, "
                  f"exclusive={'yes' if (ex and platform.system()=='Windows') else 'no'} ({note})")
        return sd.OutputStream(
            samplerate=use_fs,
            device=idx,
            channels=max(1, have_channels),
            dtype="float32",
            blocksize=blocksize,
            latency=0.06,
            extra_settings=ex
        )

    # Attempt WASAPI exclusive w/ given index
    if platform.system() == "Windows" and have_channels >= MULTICH_MIN_CHANNELS:
        try:
            ex = _mk_wasapi(True)
            if ex is not None:
                return _try(device_index, ex, fs, "WASAPI exclusive"), fs
        except sd.PortAudioError as e1:
            log_event(f"[Audio] Open fail WASAPI exclusive: {e1}")

    # WASAPI shared (or generic shared) with same index
    try:
        ex = _mk_wasapi(False)
        return _try(device_index, ex, fs, "shared"), fs
    except sd.PortAudioError as e2:
        log_event(f"[Audio] Open fail shared: {e2}")

    # Fallback: search DS/MME index by SAME NAME, use their default fs
    try:
        devices = sd.query_devices()
        ds_idx = _get_hostapi_index("directsound")
        mme_idx = _get_hostapi_index("mme")
        target_fs = fs
        for host_idx in [ds_idx, mme_idx]:
            if host_idx is None:
                continue
            h = sd.query_hostapis()[host_idx]
            for di in h.get("devices", []):
                if 0 <= di < len(devices):
                    if devices[di].get("name") == device_name and devices[di].get("max_output_channels", 0) >= 2:
                        target_fs = int(round(float(devices[di].get("default_samplerate", fs))))
                        try:
                            return _try(di, None, target_fs, "DS/MME fallback by name"), target_fs
                        except sd.PortAudioError as e3:
                            log_event(f"[Audio] Open fail DS/MME by name: {e3}")
                        break
    except Exception as e:
        log_event(f"[Audio] DS/MME fallback scan failed: {e}")

    # Last resort: system default output
    try:
        _, def_out = sd.default.device
        return _try(def_out, None, fs, "system default"), fs
    except Exception as e:
        log_event(f"[Audio] Default output open failed: {e}")
        raise


# ---------- Core playback (epoch cutoff, per-block mapping) ----------

def _play_mono_nonblocking(
    mono: np.ndarray,
    fs: int,
    dev_idx: int,
    dev_name: str,
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
            stream, used_fs = _open_stream_robust(fs, have_channels, dev_idx, dev_name)
            # Resample if fallback changed fs
            mono_res, _ = _ensure_samplerate(mono, fs, used_fs)
            with stream:
                with _active_lock:
                    _active_streams.append(stream)
                    _active_sessions.append(session)

                blocksize = stream.blocksize or max(512, used_fs // 25)
                zero_blk = np.zeros((blocksize, max(1, have_channels)), dtype=np.float32)
                stream.write(zero_blk)
                stream.write(zero_blk)

                n = mono_res.shape[0]
                pos = 0
                while pos < n:
                    if epoch <= _cutoff_epoch or _stop_event.is_set():
                        break
                    end = min(pos + blocksize, n)
                    block = mono_res[pos:end] * gain  # (b,)

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


def _play_to_device_both_modes(mono: np.ndarray, out_fs: int,
                               primary: Tuple[int, int, int, str, str],
                               mode: str, ch_index: Optional[int],
                               gain: float, label: str):
    dev_idx, have_channels, dev_fs, hostapi_name, device_name = primary
    # If device default fs differs from computed out_fs, follow device
    mono_adj, use_fs = _ensure_samplerate(mono, out_fs, dev_fs)
    _play_mono_nonblocking(
        mono=mono_adj,
        fs=use_fs,
        dev_idx=dev_idx,
        dev_name=device_name,
        have_channels=have_channels,
        mode=mode,
        ch_index=ch_index,
        gain=gain,
        label=label
    )


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
    total_channels: int = DEFAULT_TOTAL_CHANNELS,  # legacy param (unused, kept for signature stability)
    gain_override: float | None = None,
    base_folder: Path | str | None = None,
) -> None:
    if target_name not in named_channels:
        raise ValueError(f"Channel name '{target_name}' not found. Valid: {list(named_channels.keys())}")

    base_path = Path(base_folder) if base_folder is not None else DEFAULT_SOUND_DIR
    file_path = _resolve_sound_path(wav_file, base_folder=base_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    primary = _choose_output_device(device_index, name_hints=DEFAULT_NAME_HINTS)

    target = named_channels[target_name]
    ch_index = int(target["index"])
    gain = float(gain_override) if gain_override is not None else float(target["gain"])

    mono, src_fs = _read_audio_mono(file_path)
    # We prepare at 48k; per-device adjustment happens inside _play_to_device_both_modes
    mono, out_fs = _ensure_samplerate(mono, src_fs, 48000)

    use_idx = 0 if primary[1] <= 1 else min(ch_index, primary[1] - 1)
    log_event(
        f"[Audio] Playing '{file_path.name}' -> channel '{target_name}' (idx {use_idx}), "
        f"gain={gain:.2f}, primary=[{primary[0]}] {primary[4]}, hostapi={primary[3]}, "
        f"channels={primary[1]}, fs_in={src_fs} -> fs_out~{out_fs}"
    )
    _play_to_device_both_modes(mono, out_fs, primary, mode="one", ch_index=use_idx, gain=gain,
                               label=f"{file_path.name}@{target_name}")

    # Optional simultaneous playback to SECONDARY device
    secondary = _choose_secondary_device()
    if secondary:
        use_idx2 = 0 if secondary[1] <= 1 else min(ch_index, secondary[1] - 1)
        log_event(
            f"[Audio] (Secondary) '{file_path.name}' -> channel '{target_name}' (idx {use_idx2}), "
            f"gain={gain:.2f}, secondary=[{secondary[0]}] {secondary[4]}, hostapi={secondary[3]}, "
            f"channels={secondary[1]}"
        )
        _play_to_device_both_modes(mono, out_fs, secondary, mode="one", ch_index=use_idx2, gain=gain,
                                   label=f"{file_path.name}@{target_name}/sec")


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

        primary = _choose_output_device(device_index, name_hints=DEFAULT_NAME_HINTS)

        gain = float(gain_override) if gain_override is not None else 1.0

        mono, src_fs = _read_audio_mono(Path(file_path))
        mono, out_fs = _ensure_samplerate(mono, src_fs, 48000)

        label = f"file:{Path(file_path).name}" if treat_as_file else f"tts:{str(wav_or_text)[:40]}"
        log_event(
            f"[Audio] Playing {label} -> ALL channels ({primary[1]}), "
            f"gain={gain:.2f}, primary=[{primary[0]}] {primary[4]}, hostapi={primary[3]}, "
            f"fs_in={src_fs} -> fs_out~{out_fs}"
        )
        _play_to_device_both_modes(mono, out_fs, primary, mode="all", ch_index=None, gain=gain, label=label)

        # Optional simultaneous playback to SECONDARY device
        secondary = _choose_secondary_device()
        if secondary:
            log_event(
                f"[Audio] (Secondary) {label} -> ALL channels ({secondary[1]}), "
                f"gain={gain:.2f}, secondary=[{secondary[0]}] {secondary[4]}, hostapi={secondary[3]}"
            )
            _play_to_device_both_modes(mono, out_fs, secondary, mode="all", ch_index=None, gain=gain,
                                       label=f"{label}/sec")

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
