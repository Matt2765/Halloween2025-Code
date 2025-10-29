# control/audio_manager.py
# -------------------------------------------------------------------
# Fixed-index audio manager
# + Two tables: hdmi_channels{} (PRIMARY) and usb7_channels{} (SECONDARY)
# + Manual device indexes (no auto detection)
# + Fallback to system default if stream open fails
# + Threaded, overlapping playback
# + Text-to-speech (offline)
# + Simple unified play_audio() API
# + Stereo support via "stereo_<name>" mapping:
#     - Either a single entry with index=[L,R]
#     - Or two entries: "stereo_<name>_L" and "stereo_<name>_R"
#   play_audio("<name>", file) will auto-use stereo_<name> if present.
# + looping=True/False (loops file audio until BreakCheck() or stop_all_audio())
# + TTS plays through shutdown & BreakCheck (immune), and now plays on ALL channels by default
# + NEW mode "all": duplicate mono to every output channel on the device
# + NEW threaded: bool — choose blocking vs non-blocking playback for file audio (TTS always non-blocking)
# + BreakCheck() returning True stops all audio EXCEPT TTS
# -------------------------------------------------------------------

from __future__ import annotations
import os, threading, tempfile, subprocess, platform
from pathlib import Path
from typing import Dict, Optional, Tuple, List, Union
import numpy as np
import sounddevice as sd
import soundfile as sf
from utils.tools import log_event, BreakCheck

# ==========================================================
# === CONFIGURATION ========================================
# ==========================================================

# Set your exact device indexes (use list_output_devices())
PRIMARY_DEVICE_INDEX: Optional[int] = 3      # HDMI / AVR device
SECONDARY_DEVICE_INDEX: Optional[int] = 5   # USB 7.1 soundcard
FALLBACK_TO_SYSTEM_DEFAULT = True            # fallback if stream open fails

# Primary (HDMI) channels
# NOTE: index can be int (mono target) or [L, R] (stereo target)
hdmi_channels: Dict[str, Dict[str, Union[float, int, List[int]]]] = {
    "treasureRoom": {"index": 0, "gain": 1.0},
    "quarterdeck":  {"index": 1, "gain": 0.6},
    "gangway":      {"index": 2, "gain": 1.4},
    "HDMI_LFE":     {"index": 3, "gain": 1.4},
    "HDMI_SL":      {"index": 4, "gain": 1.6},
    "cargoHold":    {"index": 5, "gain": 1.6},
    "HDMI_BL":      {"index": 6, "gain": 1.8},
    "HDMI_BR":      {"index": 7, "gain": 1.8},
    # "stereo_front": {"index": [0, 1], "gain": 1.0},
    # "stereo_front_L": {"index": 0, "gain": 1.0},
    # "stereo_front_R": {"index": 1, "gain": 1.0},
}

# Secondary (USB 7.1) channels
# NOTE: index can be int (mono target) or [L, R] (stereo target)
usb7_channels: Dict[str, Dict[str, Union[float, int, List[int]]]] = {
    "stereo_graveyard_L": {"index": 0, "gain": 1.0},
    "stereo_graveyard_R": {"index": 1, "gain": 1.0},

    "usb_C":   {"index": 2, "gain": 1.0},
    "usb_LFE": {"index": 3, "gain": 1.0},
    "usb_SL":  {"index": 4, "gain": 1.0},
    "usb_SR":  {"index": 5, "gain": 1.0},
    "usb_BL":  {"index": 6, "gain": 1.0},
    "usb_BR":  {"index": 7, "gain": 1.0},
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

def _read_audio(file_path: Path) -> tuple[np.ndarray, int]:
    """
    Returns (audio, fs) where audio is float32 shape (N, C) with C in {1,2}
    If file has >2 channels, it averages to stereo (2ch). If 2ch+, keeps stereo.
    """
    data, fs = sf.read(str(file_path), dtype="float32", always_2d=True)
    ch = data.shape[1]
    if ch == 1:
        return data.astype("float32"), int(fs)
    if ch == 2:
        return data[:, :2].astype("float32"), int(fs)
    mean_mono = data.mean(axis=1, keepdims=True)  # simple downmix to mono
    stereo = np.repeat(mean_mono, 2, axis=1)
    return stereo.astype("float32"), int(fs)

def _ensure_samplerate(x: np.ndarray, src_fs: int, dst_fs: int) -> tuple[np.ndarray, int]:
    if src_fs == dst_fs:
        return x, src_fs
    n_src = x.shape[0]
    n_dst = int(round(n_src * (dst_fs / src_fs)))
    t_src = np.linspace(0.0, 1.0, n_src, endpoint=False)
    t_dst = np.linspace(0.0, 1.0, n_dst, endpoint=False)
    out = np.empty((n_dst, x.shape[1]), dtype="float32")
    for c in range(x.shape[1]):
        out[:, c] = np.interp(t_dst, t_src, x[:, c])
    return out, dst_fs

def _pack_device(idx: int) -> Tuple[int, int, int, str, str]:
    d = sd.query_devices()[idx]
    max_out = int(d["max_output_channels"])
    default_fs = int(round(float(d.get("default_samplerate", 48000.0))))
    hostapis = sd.query_hostapis()
    hostapi_name = "unknown"
    for h in hostapis:
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

# ---------- Stereo resolver helpers ----------

def _maybe_pair_from_entry(name: str, tbl: Dict[str, Dict[str, Union[float, int, List[int]]]]) -> Optional[List[int]]:
    """
    Find a stereo pair for a given base name by looking for:
      - key "stereo_<name>" with index=[L,R], OR
      - keys "stereo_<name>_L" and "stereo_<name>_R" with int indices.
    Returns [L, R] or None.
    """
    base = f"stereo_{name}"
    if base in tbl:
        idx = tbl[base].get("index")
        if isinstance(idx, (list, tuple)) and len(idx) == 2:
            return [int(idx[0]), int(idx[1])]
    l_key, r_key = f"{base}_L", f"{base}_R"
    if l_key in tbl and r_key in tbl:
        l_idx = tbl[l_key].get("index")
        r_idx = tbl[r_key].get("index")
        if isinstance(l_idx, int) and isinstance(r_idx, int):
            return [l_idx, r_idx]
    return None

def _lookup_in_tables(name: str) -> Tuple[str, Dict[str, Dict[str, Union[float, int, List[int]]]]]:
    if name in hdmi_channels or f"stereo_{name}" in hdmi_channels or f"stereo_{name}_L" in hdmi_channels:
        return "primary", hdmi_channels
    if name in usb7_channels or f"stereo_{name}" in usb7_channels or f"stereo_{name}_L" in usb7_channels:
        return "secondary", usb7_channels
    return "primary", hdmi_channels

def _resolve_named_target(name: str) -> Tuple[str, str, Union[int, List[int]], float]:
    """
    Returns (device_kind, mode, index_or_pair, gain)
      - device_kind: "primary" | "secondary"
      - mode: "one" | "stereo"
      - index_or_pair: int for mono, [L,R] for stereo
      - gain: float
    """
    dev_kind, tbl = _lookup_in_tables(name)

    pair = _maybe_pair_from_entry(name, tbl)
    if pair is not None:
        base = f"stereo_{name}"
        if base in tbl and isinstance(tbl[base].get("gain", 1.0), (int, float)):
            gain = float(tbl[base]["gain"])
        else:
            l_key, r_key = f"{base}_L", f"{base}_R"
            gains = []
            for k in (l_key, r_key):
                if k in tbl and isinstance(tbl[k].get("gain", 1.0), (int, float)):
                    gains.append(float(tbl[k]["gain"]))
            gain = sum(gains)/len(gains) if gains else 1.0
        return dev_kind, "stereo", [int(pair[0]), int(pair[1])], gain

    if name in tbl:
        v = tbl[name]
        idx = v.get("index")
        gain = float(v.get("gain", 1.0))
        if isinstance(idx, (list, tuple)) and len(idx) == 2:
            return dev_kind, "stereo", [int(idx[0]), int(idx[1])], gain
        return dev_kind, "one", int(idx), gain

    other_kind, other_tbl = ("secondary", usb7_channels) if dev_kind == "primary" else ("primary", hdmi_channels)
    pair = _maybe_pair_from_entry(name, other_tbl)
    if pair is not None:
        base = f"stereo_{name}"
        if base in other_tbl and isinstance(other_tbl[base].get("gain", 1.0), (int, float)):
            gain = float(other_tbl[base]["gain"])
        else:
            l_key, r_key = f"{base}_L", f"{base}_R"
            gains = []
            for k in (l_key, r_key):
                if k in other_tbl and isinstance(other_tbl[k].get("gain", 1.0), (int, float)):
                    gains.append(float(other_tbl[k]["gain"]))
            gain = sum(gains)/len(gains) if gains else 1.0
        return other_kind, "stereo", [int(pair[0]), int(pair[1])], gain

    if name in other_tbl:
        v = other_tbl[name]
        idx = v.get("index")
        gain = float(v.get("gain", 1.0))
        if isinstance(idx, (list, tuple)) and len(idx) == 2:
            return other_kind, "stereo", [int(idx[0]), int(idx[1])], gain
        return other_kind, "one", int(idx), gain

    raise ValueError(f"Unknown channel name '{name}'.")

# ==========================================================
# === STREAM OPEN / PLAY ==================================
# ==========================================================

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
      - Otherwise: open generic shared at the DEVICE DEFAULT fs
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

    if "wasapi" in hostapi and have_channels >= MULTICH_MIN_CHANNELS:
        try:
            ex = _mk_wasapi(True)
            if ex:
                return _try(device_index, ex, fs, "WASAPI exclusive"), fs
        except Exception as e:
            log_event(f"[Audio] Fail WASAPI exclusive: {e}")
        try:
            ex = _mk_wasapi(False)
            return _try(device_index, ex, fs, "WASAPI shared"), fs
        except Exception as e:
            log_event(f"[Audio] Fail WASAPI shared: {e}")

    try:
        dev_default_fs = int(round(float(sd.query_devices()[device_index].get("default_samplerate", fs))))
        return _try(device_index, None, dev_default_fs, "generic shared"), dev_default_fs
    except Exception as e:
        log_event(f"[Audio] Fail generic shared: {e}")

    if FALLBACK_TO_SYSTEM_DEFAULT:
        try:
            _, def_out = sd.default.device  # (input, output)
            def_fs = int(round(float(sd.query_devices()[def_out].get("default_samplerate", fs))))
            log_event(f"[Audio] Falling back to system default idx={def_out}")
            return _try(def_out, None, def_fs, "system default fallback"), def_fs
        except Exception as e:
            log_event(f"[Audio] System default fallback failed: {e}")

    raise RuntimeError("Failed to open audio stream: all strategies exhausted.")

def _play_pcm_nonblocking(pcm: np.ndarray, fs: int, dev_idx: int, dev_name: str,
                          have_channels: int, mode: str,
                          idx_or_pair: Union[int, List[int]],
                          gain: float, label: str,
                          *, looping: bool = False,
                          honor_shutdown: bool = True,
                          honor_breakcheck: bool = True):
    """
    pcm: float32 (N, Csrc), Csrc in {1,2}
    mode: "one" | "stereo" | "all"
      - "one" routes mono to a single output index
      - "stereo" routes L/R to two indices
      - "all" duplicates mono to EVERY available output channel
    idx_or_pair: int for mono channel index, [L,R] for stereo indices (ignored for "all")
    looping: when True, repeats until BreakCheck()/stop_all_audio() (ignored for TTS)
    honor_shutdown: when False, ignore stop_all_audio() flags (for TTS)
    honor_breakcheck: when False, ignore BreakCheck() (for TTS)
    """
    epoch = _next_epoch()
    session = _Session(epoch, label)

    def _worker():
        stream = None
        try:
            stream, used_fs = _open_stream_robust(fs, have_channels, dev_idx, dev_name)
            pcm_res, _ = _ensure_samplerate(pcm, fs, used_fs)
            with stream:
                with _active_lock:
                    _active_streams.append(stream)
                    _active_sessions.append(session)
                blocksize = stream.blocksize or max(512, used_fs // 25)
                zero_blk = np.zeros((blocksize, have_channels), np.float32)
                stream.write(zero_blk)
                n = pcm_res.shape[0]
                src_ch = pcm_res.shape[1]

                pos = 0
                while True:
                    if honor_breakcheck and BreakCheck():
                        break
                    if honor_shutdown and (epoch <= _cutoff_epoch or _stop_event.is_set()):
                        break

                    end = min(pos + blocksize, n)
                    block = pcm_res[pos:end] * gain  # (B, Csrc)

                    # Build output frame (B, have_channels)
                    if have_channels <= 1:
                        out = block[:, :1]
                    elif have_channels == 2:
                        if mode == "stereo":
                            out = np.column_stack((block[:, 0], block[:, 0])) if src_ch == 1 else block[:, :2]
                        elif mode == "all":
                            mono = block[:, 0:1]
                            out = np.concatenate([mono, mono], axis=1)
                        else:
                            out = np.column_stack((block[:, 0], block[:, 0]))
                    else:
                        if mode == "all":
                            mono = block[:, 0:1]  # use L/mono
                            out = np.repeat(mono, have_channels, axis=1)
                        else:
                            out = np.zeros((block.shape[0], have_channels), np.float32)
                            if mode == "stereo":
                                L, R = int(idx_or_pair[0]), int(idx_or_pair[1])
                                if src_ch == 1:
                                    out[:, L] = block[:, 0]
                                    out[:, R] = block[:, 0]
                                else:
                                    out[:, L] = block[:, 0]
                                    out[:, R] = block[:, 1]
                            else:
                                idx = min(int(idx_or_pair), have_channels - 1)
                                out[:, idx] = block[:, 0]

                    stream.write(out)
                    pos = end

                    if pos >= n:
                        if looping:
                            pos = 0
                        else:
                            break
        finally:
            with _active_lock:
                if stream in _active_streams: _active_streams.remove(stream)
                if session in _active_sessions: _active_sessions.remove(session)
            session.done.set()

    tname = f"Looping Audio@{epoch}" if looping else f"Audio@{epoch}"
    threading.Thread(target=_worker, daemon=True, name=tname).start()

def _play_pcm_blocking(pcm: np.ndarray, fs: int, dev_idx: int, dev_name: str,
                       have_channels: int, mode: str,
                       idx_or_pair: Union[int, List[int]],
                       gain: float, label: str,
                       *, looping: bool = False,
                       honor_shutdown: bool = True,
                       honor_breakcheck: bool = True):
    """
    Inline (blocking) variant. Still honors BreakCheck() and stop_all_audio()
    according to the flags. TTS should NOT call this (TTS is non-blocking by design).
    """
    stream = None
    try:
        stream, used_fs = _open_stream_robust(fs, have_channels, dev_idx, dev_name)
        pcm_res, _ = _ensure_samplerate(pcm, fs, used_fs)
        with stream:
            n = pcm_res.shape[0]
            src_ch = pcm_res.shape[1]
            pos = 0
            blocksize = stream.blocksize or max(512, used_fs // 25)
            zero_blk = np.zeros((blocksize, have_channels), np.float32)
            stream.write(zero_blk)
            while True:
                if honor_breakcheck and BreakCheck():
                    break
                if honor_shutdown and _stop_event.is_set():
                    break
                end = min(pos + blocksize, n)
                block = pcm_res[pos:end] * gain

                if have_channels <= 1:
                    out = block[:, :1]
                elif have_channels == 2:
                    if mode == "stereo":
                        out = np.column_stack((block[:, 0], block[:, 0])) if src_ch == 1 else block[:, :2]
                    elif mode == "all":
                        mono = block[:, 0:1]
                        out = np.concatenate([mono, mono], axis=1)
                    else:
                        out = np.column_stack((block[:, 0], block[:, 0]))
                else:
                    if mode == "all":
                        mono = block[:, 0:1]
                        out = np.repeat(mono, have_channels, axis=1)
                    else:
                        out = np.zeros((block.shape[0], have_channels), np.float32)
                        if mode == "stereo":
                            L, R = int(idx_or_pair[0]), int(idx_or_pair[1])
                            if src_ch == 1:
                                out[:, L] = block[:, 0]
                                out[:, R] = block[:, 0]
                            else:
                                out[:, L] = block[:, 0]
                                out[:, R] = block[:, 1]
                        else:
                            idx = min(int(idx_or_pair), have_channels - 1)
                            out[:, idx] = block[:, 0]

                stream.write(out)
                pos = end
                if pos >= n:
                    if looping:
                        pos = 0
                    else:
                        break
    finally:
        if stream:
            stream.close()

def _play_pcm(pcm: np.ndarray, fs: int, dev_idx: int, dev_name: str,
              have_channels: int, mode: str,
              idx_or_pair: Union[int, List[int]],
              gain: float, label: str,
              *, looping: bool, honor_shutdown: bool, honor_breakcheck: bool,
              threaded: bool):
    if threaded:
        _play_pcm_nonblocking(
            pcm, fs, dev_idx, dev_name, have_channels, mode, idx_or_pair, gain, label,
            looping=looping, honor_shutdown=honor_shutdown, honor_breakcheck=honor_breakcheck
        )
    else:
        _play_pcm_blocking(
            pcm, fs, dev_idx, dev_name, have_channels, mode, idx_or_pair, gain, label,
            looping=looping, honor_shutdown=honor_shutdown, honor_breakcheck=honor_breakcheck
        )

# ==========================================================
# === PUBLIC PLAYBACK API =================================
# ==========================================================

def play_to_named_channel(wav_file: str, target_name: str, * ,
                          gain_override: float | None = None,
                          base_folder: Path | str | None = None,
                          looping: bool = False,
                          honor_shutdown: bool = True,
                          honor_breakcheck: bool = True,
                          threaded: bool = True):
    """
    honor_shutdown:
      - True (default): stream stops on stop_all_audio()
      - False: ignores stop_all_audio() (used for TTS)
    honor_breakcheck:
      - True (default): stops when BreakCheck() is True
      - False: ignores BreakCheck() (used for TTS)
    threaded:
      - True (default): non-blocking
      - False: blocking until complete or stopped by BreakCheck/stop_all_audio()
    """
    base_path = Path(base_folder) if base_folder else DEFAULT_SOUND_DIR
    file_path = _resolve_sound_path(wav_file, base_folder=base_path)
    if not file_path.exists():
        raise FileNotFoundError(file_path)

    dev_kind, mode, idx_or_pair, default_gain = _resolve_named_target(target_name)
    gain = gain_override if gain_override is not None else default_gain

    pcm, src_fs = _read_audio(file_path)
    pcm, out_fs = _ensure_samplerate(pcm, src_fs, 48000)

    dev = _get_fixed_device(dev_kind)
    if mode == "one":
        ch = min(int(idx_or_pair), dev[1] - 1)
        idx_or_pair = ch
        extra = f"ch={ch}"
    else:
        L = min(int(idx_or_pair[0]), dev[1] - 1)
        R = min(int(idx_or_pair[1]), dev[1] - 1)
        idx_or_pair = [L, R]
        extra = f"L={L},R={R}"

    log_event(f"[Audio] Playing '{file_path.name}' on {dev_kind.upper()} {mode} ({extra}), "
              f"gain={gain}, looping={looping}, threaded={threaded}, "
              #f"honor_shutdown={honor_shutdown},"
              #f"honor_breakcheck={honor_breakcheck},"
              )
    _play_pcm(
        pcm, out_fs, dev[0], dev[4], dev[1], mode, idx_or_pair, gain,
        f"{file_path.name}@{target_name}",
        looping=looping,
        honor_shutdown=honor_shutdown,
        honor_breakcheck=honor_breakcheck,
        threaded=threaded
    )

def play_to_all_channels(wav_or_text: str, *, tts_rate: int = 0,
                         gain_override: float | None = None,
                         base_folder: Path | str | None = None,
                         looping: bool = False,
                         honor_shutdown: bool = True,
                         honor_breakcheck: bool = True,
                         threaded: bool = True):
    """
    Broadcast a clip (file or TTS) to ALL output channels on PRIMARY.
    For TTS (text input), set honor_shutdown=False and honor_breakcheck=False upstream.
    threaded controls blocking for FILE playback; TTS is always threaded (non-blocking).
    """
    base_path = Path(base_folder) if base_folder else DEFAULT_SOUND_DIR
    tmp_path = None
    try:
        treat_as_file = Path(wav_or_text).suffix.lower() == ".wav"
        if not treat_as_file:
            abs_candidate = (base_path / wav_or_text)
            if abs_candidate.exists():
                treat_as_file = True

        if treat_as_file:
            file_path = _resolve_sound_path(wav_or_text, base_folder=base_path)
            pcm, src_fs = _read_audio(file_path)
            # Force mono source for "all" duplication (take L or mono)
            if pcm.ndim == 2 and pcm.shape[1] > 1:
                pcm = pcm[:, :1]
            pcm, out_fs = _ensure_samplerate(pcm, src_fs, 48000)
            gain = gain_override or 1.0
            dev = _get_fixed_device("primary")

            log_event(f"[Audio] Playing '{Path(file_path).name}' to ALL on PRIMARY, gain={gain}, "
                      f"looping={looping}, honor_shutdown={honor_shutdown}, "
                      f"honor_breakcheck={honor_breakcheck}, threaded={threaded}")
            _play_pcm(
                pcm, out_fs, dev[0], dev[4], dev[1], "all", 0, gain,
                Path(file_path).name,
                looping=looping,
                honor_shutdown=honor_shutdown,
                honor_breakcheck=honor_breakcheck,
                threaded=threaded
            )
        else:
            # TTS path is ALWAYS non-blocking and immune to BreakCheck/shutdown
            fd, tmp = tempfile.mkstemp(suffix=".wav"); os.close(fd)
            tmp_path = Path(tmp)
            text_to_wav(wav_or_text, tmp_path, tts_rate)
            pcm, src_fs = _read_audio(tmp_path)
            if pcm.ndim == 2 and pcm.shape[1] > 1:
                pcm = pcm[:, :1]
            pcm, out_fs = _ensure_samplerate(pcm, src_fs, 48000)
            gain = gain_override or 1.0
            dev = _get_fixed_device("primary")

            log_event(f"[Audio] TTS->ALL '{wav_or_text[:48]}...' (len={len(wav_or_text)}) "
                      f"gain={gain}, threaded=True, immune")
            _play_pcm_nonblocking(
                pcm, out_fs, dev[0], dev[4], dev[1], "all", 0, gain,
                "TTS-ALL",
                looping=False,
                honor_shutdown=False,     # immune
                honor_breakcheck=False    # immune
            )
    finally:
        if tmp_path and tmp_path.exists():
            try: tmp_path.unlink()
            except OSError: pass

def play_audio(target_or_text: str, maybe_file: str | None = None, * ,
               gain: float | None = None,
               base_folder: Path | str | None = None,
               tts_rate: int = 0,
               looping: bool = False,
               threaded: bool = True):
    """
    - play_audio("frontLeft", "boom.wav")           # file -> named (respects shutdown & BreakCheck)
    - play_audio("usb_C", "boom.wav")               # file -> named
    - play_audio("all", "boom.wav")                 # file -> broadcast ALL (respects shutdown & BreakCheck)
    - play_audio("The manor is opening...")         # TTS -> broadcast ALL, IGNORES shutdown & BreakCheck (non-blocking)
    - play_audio("frontLeft: The manor is opening...")  # TTS -> named, IGNORES shutdown & BreakCheck (non-blocking)
    - Stereo: if stereo_<name> is configured, play_audio("<name>", file) routes to that L/R pair.
    - looping=True loops file audio only (TTS is not looped).
    - threaded=False blocks for FILE playback (not for TTS).
    """
    # FILED AUDIO paths
    if maybe_file:
        if target_or_text.lower() == "all":
            # broadcast file to ALL; respects shutdown & BreakCheck
            play_to_all_channels(
                maybe_file,
                gain_override=gain,
                base_folder=base_folder,
                looping=looping,
                honor_shutdown=True,
                honor_breakcheck=True,
                threaded=threaded
            )
        else:
            play_to_named_channel(
                maybe_file,
                target_or_text,
                gain_override=gain,
                base_folder=base_folder,
                looping=looping,
                honor_shutdown=True,
                honor_breakcheck=True,
                threaded=threaded
            )
        return

    # "name: text" => TTS on named channel (immune to shutdown & BreakCheck) — always non-blocking
    if ":" in target_or_text:
        name, txt = target_or_text.split(":", 1)
        name, txt = name.strip(), txt.strip()
        if name in hdmi_channels or name in usb7_channels or \
           f"stereo_{name}" in hdmi_channels or f"stereo_{name}" in usb7_channels or \
           f"stereo_{name}_L" in hdmi_channels or f"stereo_{name}_L" in usb7_channels:
            fd, tmp = tempfile.mkstemp(suffix=".wav"); os.close(fd)
            tmp_path = Path(tmp)
            try:
                text_to_wav(txt, tmp_path, tts_rate)
                # immune and non-blocking by design
                play_to_named_channel(
                    str(tmp_path),
                    name,
                    gain_override=gain,
                    base_folder=base_folder,
                    looping=False,
                    honor_shutdown=False,     # immune
                    honor_breakcheck=False,    # immune
                    threaded=True              # TTS non-blocking
                )
            finally:
                try: tmp_path.unlink()
                except OSError: pass
            return

    # Bare TEXT => TTS broadcast to ALL (immune) — always non-blocking
    play_to_all_channels(
        target_or_text,
        tts_rate=tts_rate,
        gain_override=gain,
        base_folder=base_folder,
        looping=False,
        honor_shutdown=False,     # immune
        honor_breakcheck=False,    # immune
        threaded=True              # TTS non-blocking
    )

# ==========================================================
# === CONTROL / UTILITY ===================================
# ==========================================================

def stop_all_audio(timeout: float = 2.0):
    """
    Signals all honor_shutdown=True streams to stop soon.
    Streams started with honor_shutdown=False (TTS) will IGNORE this cutoff and finish naturally.
    Also, any currently-blocking file playback will return quickly if honor_shutdown=True.
    """
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
        if not active:
            break
        time.sleep(0.05)
    _stop_event.clear()
    log_event(f"[Audio] stop_all_audio(): complete")

def list_output_devices() -> list[str]:
    devices = sd.query_devices()
    return [f"[{i}] {d['name']} ({d['max_output_channels']}ch)" for i, d in enumerate(devices)
            if d.get("max_output_channels", 0) > 0]

def list_named_channels() -> Dict[str, Dict[str, Union[float, int, str, List[int]]]]:
    out: Dict[str, Dict[str, Union[float, int, str, List[int]]]] = {}
    for k, v in hdmi_channels.items():
        out[k] = {"index": v["index"], "gain": v["gain"], "device": "primary"}
    for k, v in usb7_channels.items():
        out[k] = {"index": v["index"], "gain": v["gain"], "device": "secondary"}
    return out

def register_hdmi_channel(name: str, index: Union[int, List[int]], gain: float = 1.0):
    if name in usb7_channels: raise ValueError(f"'{name}' exists in usb7_channels")
    hdmi_channels[name] = {"index": index, "gain": gain}

def register_usb7_channel(name: str, index: Union[int, List[int]], gain: float = 1.0):
    if name in hdmi_channels: raise ValueError(f"'{name}' exists in hdmi_channels")
    usb7_channels[name] = {"index": index, "gain": gain}

def set_channel_gain(name: str, gain: float):
    if name in hdmi_channels:
        hdmi_channels[name]["gain"] = gain; return
    if name in usb7_channels:
        usb7_channels[name]["gain"] = gain; return
    raise ValueError(f"Unknown channel '{name}'")
