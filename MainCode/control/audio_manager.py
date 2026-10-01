"""Persistent, multi-device software mixer used by the haunt rooms.

The public playback API is compatible with the previous audio manager.  Edit
the device indexes and channel maps in the configuration section below when
the audio hardware changes; the mixer and room code should not need edits.
"""

from __future__ import annotations

import os
import platform
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union

import numpy as np
import sounddevice as sd
import soundfile as sf

from context import house
from utils.tools import log_event

# =============================================================================
# Audio hardware and room-routing configuration
# =============================================================================
#
# Run `python utils/audio_mixer_diagnostic.py` from MainCode to list the output
# devices.  Set these to the corresponding *output* device indexes.  Do not use
# a microphone/input index: it has zero output channels.
PRIMARY_DEVICE_INDEX: Optional[int] = 3  # HDMI / AVR (normally 8 channels)
SECONDARY_DEVICE_INDEX: Optional[int] = 6  # USB 7.1 interface (normally 8 channels)

# If a configured device is unavailable, continue through Windows' default
# output.  That avoids a show-stopping exception, but routes audio to every
# default-device channel rather than its configured discrete room channel.
FALLBACK_TO_SYSTEM_DEFAULT = True

MULTICH_MIN_CHANNELS = 6
SHORT_CLIP_MAX_SECONDS = 30.0
SHORT_CLIP_CACHE_MAX_BYTES = 256 * 1024 * 1024
STREAM_READ_FRAMES = 16_384
STREAM_BUFFER_SECONDS = 3.0
MIX_BLOCKSIZE = 1_024

# A channel map entry has an output-channel `index` (zero based) and `gain`.
# For a stereo room, either use `"stereo_<room>": {"index": [L, R], ...}` or
# the equivalent two entries, `stereo_<room>_L` and `stereo_<room>_R`.  Calling
# `play_audio("<room>", "clip.wav")` automatically selects that stereo pair.
ChannelIndex = Union[int, List[int]]
ChannelMap = Dict[str, Dict[str, Union[float, ChannelIndex]]]

# Primary: HDMI / AVR output channels.
hdmi_channels: ChannelMap = {
    "treasureRoom": {"index": 0, "gain": 1.0},
    "quarterdeck": {"index": 1, "gain": 0.6},
    "gangway": {"index": 2, "gain": 1.4},
    "HDMI_LFE": {"index": 3, "gain": 1.4},
    "HDMI_SL": {"index": 4, "gain": 1.6},
    "cargoHold": {"index": 5, "gain": 1.6},
    "HDMI_BL": {"index": 6, "gain": 1.8},
    "HDMI_BR": {"index": 7, "gain": 1.8},
}

# Secondary: USB 7.1 output channels.
usb7_channels: ChannelMap = {
    "stereo_graveyard_L": {"index": 0, "gain": 1.0},
    "stereo_graveyard_R": {"index": 1, "gain": 1.0},
    "usb_C": {"index": 2, "gain": 1.0},
    "usb_LFE": {"index": 3, "gain": 1.0},
    "stereo_beckettPA_L": {"index": 4, "gain": 1.0},
    "stereo_beckettPA_R": {"index": 5, "gain": 1.0},
    "usb_BL": {"index": 6, "gain": 1.0},
    "usb_BR": {"index": 7, "gain": 1.0},
}

DEFAULT_SOUND_DIR = (Path(__file__).resolve().parents[3] / "Assets" / "SoundDir").resolve()
_play_epoch = 0
_cutoff_epoch = 0
_epoch_lock = threading.Lock()
_active_lock = threading.Lock()
_mixer_lock = threading.Lock()
_cache_lock = threading.Lock()
_stop_event = threading.Event()


class _Session:
    def __init__(self, epoch: int, label: str):
        self.epoch = epoch
        self.label = label
        self.done = threading.Event()


_active_sessions: list[_Session] = []

def text_to_wav(text: str, path: Path, rate: int = 0):
    """Generate an offline speech WAV file."""
    system = platform.system()
    if system in ("Linux", "Darwin"):
        subprocess.run(["espeak", f"-s{150 + rate * 10}", "-w", str(path), text], check=True)
    elif system == "Windows":
        rate = max(-10, min(10, rate))
        script = f'''Add-Type -AssemblyName System.Speech
$s=New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Rate={rate};$s.SetOutputToWaveFile("{path}");$s.Speak("{text}");$s.Dispose()'''
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)
    else:
        raise RuntimeError(f"TTS not supported on {system}")


def _next_epoch():
    global _play_epoch
    with _epoch_lock:
        _play_epoch += 1
        return _play_epoch


def _resolve_sound_path(value, base_folder=None):
    path = Path(value)
    base = Path(base_folder) if base_folder else DEFAULT_SOUND_DIR
    return path if path.is_absolute() else (base / path).resolve()


def _normalise(x):
    x = np.asarray(x, np.float32)
    x = x[:, None] if x.ndim == 1 else x
    if x.shape[1] <= 2:
        return x
    return np.repeat(x.mean(axis=1, keepdims=True, dtype=np.float32), 2, axis=1)


def _resample(x, src, dst):
    if src == dst or not len(x):
        return np.asarray(x, np.float32)
    frames = max(1, round(len(x) * dst / src))
    positions = np.minimum(np.arange(frames, dtype=float) * src / dst, len(x) - 1)
    original = np.arange(len(x), dtype=float)
    return np.column_stack([np.interp(positions, original, x[:, i]) for i in range(x.shape[1])]).astype(np.float32)

class _CachedSource:
    def __init__(self, data, looping):
        self.data = data
        self.looping = looping
        self.position = 0
        self.channels = data.shape[1]

    def read(self, frames):
        if not len(self.data):
            return np.zeros((frames, self.channels), np.float32), True
        if not self.looping:
            end = min(len(self.data), self.position + frames)
            block = self.data[self.position:end]
            self.position = end
            return block, end >= len(self.data)

        output = np.empty((frames, self.channels), np.float32)
        at = 0
        while at < frames:
            take = min(frames - at, len(self.data) - self.position)
            output[at : at + take] = self.data[self.position : self.position + take]
            at += take
            self.position = (self.position + take) % len(self.data)
        return output, False

    def close(self):
        pass

class _StreamedSource:
    """Reader thread owns SoundFile; callback sees only queued PCM."""
    def __init__(self, path, src_fs, dst_fs, channels, looping):
        self.path = path
        self.src_fs = src_fs
        self.dst_fs = dst_fs
        self.channels = channels
        self.looping = looping
        self.blocks = deque()
        self.offset = 0
        self.queued = 0
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.eof = threading.Event()
        self.ready = threading.Event()
        self.underruns = 0
        self.maximum = max(dst_fs, int(dst_fs * STREAM_BUFFER_SECONDS))
        threading.Thread(target=self._reader, name=f"AudioReader:{path.name}", daemon=True).start()

    def _reader(self):
        try:
            with sf.SoundFile(str(self.path)) as audio_file:
                while not self.stop.is_set():
                    with self.lock:
                        full = self.queued >= self.maximum
                    if full:
                        time.sleep(0.01)
                        continue
                    raw = audio_file.read(STREAM_READ_FRAMES, dtype="float32", always_2d=True)
                    if not len(raw):
                        if self.looping:
                            audio_file.seek(0)
                            continue
                        self.eof.set()
                        self.ready.set()
                        return
                    block = _resample(_normalise(raw), self.src_fs, self.dst_fs)
                    with self.lock:
                        self.blocks.append(block)
                        self.queued += len(block)
                        if self.queued >= min(self.dst_fs // 4, self.maximum):
                            self.ready.set()
        except Exception as error:
            self.eof.set()
            self.ready.set()
            log_event(f"[Audio] stream reader failed for '{self.path}': {error}")

    def read(self, frames):
        output = np.zeros((frames, self.channels), np.float32)
        at = 0
        with self.lock:
            while at < frames and self.blocks:
                block = self.blocks[0]
                take = min(frames - at, len(block) - self.offset)
                output[at : at + take] = block[self.offset : self.offset + take]
                self.offset += take
                at += take
                self.queued -= take
                if self.offset == len(block):
                    self.blocks.popleft()
                    self.offset = 0
        if at < frames and not self.eof.is_set():
            self.underruns += 1
        return (output[:at] if self.eof.is_set() else output), self.eof.is_set() and at < frames

    def close(self):
        self.stop.set()

@dataclass
class _Voice:
    source: object
    mode: str
    target: object
    gain: float
    session: _Session
    honor_shutdown: bool
    honor_breakcheck: bool
    stopped: bool = False

class DeviceMixer:
    def __init__(self, kind, index, name, channels, samplerate, hostapi, fallback=False):
        self.kind = kind
        self.device_index = index
        self.device_name = name
        self.channels = channels
        self.samplerate = samplerate
        self.hostapi = hostapi
        self.fallback_to_all = fallback
        self.voices = []
        self.lock = threading.Lock()
        self.stream = None
        self.callback_status_count = 0
        self.stream_underruns = 0
        self.peak_voices = 0

    def start(self):
        def open_stream(extra_settings):
            return sd.OutputStream(
                device=self.device_index,
                samplerate=self.samplerate,
                channels=self.channels,
                dtype="float32",
                blocksize=MIX_BLOCKSIZE,
                latency=0.06,
                callback=self._callback,
                extra_settings=extra_settings,
            )

        extra = None
        if "wasapi" in self.hostapi.lower() and self.channels >= MULTICH_MIN_CHANNELS:
            try:
                extra = sd.WasapiSettings(exclusive=True)
            except Exception:
                pass
        try:
            self.stream = open_stream(extra)
            self.stream.start()
        except Exception as error:
            if extra is None:
                raise
            log_event(f"[Audio] {self.kind} WASAPI exclusive failed; shared fallback: {error}")
            self.stream = open_stream(sd.WasapiSettings(exclusive=False))
            self.stream.start()
        log_event(
            f"[Audio] {self.kind.upper()} persistent mixer opened "
            f"idx={self.device_index} '{self.device_name}', "
            f"fs={self.samplerate}, ch={self.channels}"
        )

    def add(self, voice):
        with self.lock:
            self.voices.append(voice)
            self.peak_voices = max(self.peak_voices, len(self.voices))
        log_event(
            f"[Audio] playback started '{voice.session.label}' on {self.kind.upper()}, "
            f"active={self.active_voice_count}"
        )

    @property
    def active_voice_count(self):
        with self.lock:
            return len(self.voices)

    def stop_matching(self, predicate):
        with self.lock:
            for voice in self.voices:
                if predicate(voice):
                    voice.stopped = True

    def _route(self, output, block, voice):
        frames = len(block)
        if not frames:
            return
        block = block * voice.gain
        if self.channels == 1:
            output[:frames, 0] += block[:, 0]
        elif voice.mode == "all":
            # Broadcasting a stereo source should include both channels rather
            # than silently discarding the right side.
            mono = block[:, :1] if block.shape[1] == 1 else block.mean(axis=1, keepdims=True)
            output[:frames] += mono
        elif voice.mode == "stereo":
            output[:frames, int(voice.target[0])] += block[:, 0]
            right = 0 if block.shape[1] == 1 else 1
            output[:frames, int(voice.target[1])] += block[:, right]
        else:
            # A single physical speaker should receive both halves of a stereo
            # source, not just its left channel.
            mono = block[:, 0] if block.shape[1] == 1 else block.mean(axis=1)
            output[:frames, min(int(voice.target), self.channels - 1)] += mono

    def _callback(self, output, frames, time_info, status):
        output.fill(0)
        if status:
            self.callback_status_count += 1
            self.stream_underruns += int(bool(getattr(status, "output_underflow", False)))
        with self.lock:
            voices = tuple(self.voices)

        done = []
        for voice in voices:
            if voice.stopped:
                done.append(voice)
                continue
            block, finished = voice.source.read(frames)
            self._route(output, block, voice)
            if finished:
                done.append(voice)

        np.clip(output, -1, 1, out=output)  # Per-block hard safety; no AGC/pumping.
        if done:
            with self.lock:
                for voice in done:
                    if voice in self.voices:
                        self.voices.remove(voice)
                        voice.source.close()
                        voice.session.done.set()
                        with _active_lock:
                            if voice.session in _active_sessions:
                                _active_sessions.remove(voice.session)

    def diagnostics(self):
        with self.lock:
            voices = tuple(self.voices)
        return {
            "device": self.device_index,
            "channels": self.channels,
            "samplerate": self.samplerate,
            "active_voices": len(voices),
            "peak_voices": self.peak_voices,
            "callback_statuses": self.callback_status_count,
            "callback_underruns": self.stream_underruns,
            "stream_buffer_underruns": sum(getattr(v.source, "underruns", 0) for v in voices),
        }

def _host(index):
    try:
        for hostapi in sd.query_hostapis():
            if index in hostapi.get("devices", []):
                return hostapi.get("name", "unknown")
    except Exception:
        pass
    return "unknown"


def _fixed(kind):
    index = PRIMARY_DEVICE_INDEX if kind == "primary" else SECONDARY_DEVICE_INDEX
    if index is None:
        raise RuntimeError(f"{kind.upper()}_DEVICE_INDEX not set or disabled.")
    device = sd.query_devices(index)
    channels = int(device["max_output_channels"])
    if channels <= 0:
        raise RuntimeError(f"Configured {kind} device {index} has no output channels")
    samplerate = int(round(float(device.get("default_samplerate", 48000))))
    return index, channels, samplerate, _host(index), str(device.get("name", "Unknown"))


_mixers = {}


def _make_mixer(kind):
    try:
        index, channels, default_rate, host, name = _fixed(kind)
        samplerate = 48000 if "wasapi" in host.lower() and channels >= MULTICH_MIN_CHANNELS else default_rate
        mixer = DeviceMixer(kind, index, name, channels, samplerate, host)
        mixer.start()
        return mixer
    except Exception as error:
        if not FALLBACK_TO_SYSTEM_DEFAULT:
            raise RuntimeError(f"Failed to open configured {kind} mixer: {error}") from error

        _, default_index = sd.default.device
        device = sd.query_devices(default_index)
        channels = int(device["max_output_channels"])
        if channels <= 0:
            raise RuntimeError(f"Configured mixer failed ({error}); system default has no output") from error
        log_event(
            f"[Audio] {kind.upper()} mixer failed ({error}); "
            f"fallback idx={default_index}, all {channels} channel(s)"
        )
        mixer = DeviceMixer(
            kind,
            int(default_index),
            str(device.get("name", "System Default")),
            channels,
            int(round(float(device.get("default_samplerate", 48000)))),
            _host(int(default_index)),
            True,
        )
        mixer.start()
        return mixer


def _mixer(kind):
    with _mixer_lock:
        if kind not in _mixers:
            _mixers[kind] = _make_mixer(kind)
        return _mixers[kind]


_cache = OrderedDict()
_cache_bytes = 0


def _cached(path, rate):
    global _cache_bytes
    key = (str(path.resolve()), rate)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]

    raw, samplerate = sf.read(str(path), dtype="float32", always_2d=True)
    data = _resample(_normalise(raw), int(samplerate), rate)
    with _cache_lock:
        _cache[key] = data
        _cache_bytes += data.nbytes
        while _cache and _cache_bytes > SHORT_CLIP_CACHE_MAX_BYTES:
            _, old = _cache.popitem(last=False)
            _cache_bytes -= old.nbytes
    return data


def _source(path, rate, looping, force_cached=False):
    info = sf.info(str(path))
    duration = info.frames / info.samplerate
    if force_cached or duration <= SHORT_CLIP_MAX_SECONDS:
        return _CachedSource(_cached(path, rate), looping)
    source = _StreamedSource(path, int(info.samplerate), rate, 1 if info.channels == 1 else 2, looping)
    source.ready.wait(2)
    return source

def _pair(name,t):
    base = f"stereo_{name}"
    if base in t and isinstance(t[base].get("index"), (list, tuple)) and len(t[base]["index"]) == 2:
        return [int(channel) for channel in t[base]["index"]]
    left, right = f"{base}_L", f"{base}_R"
    if left in t and right in t and isinstance(t[left].get("index"), int) and isinstance(t[right].get("index"), int):
        return [int(t[left]["index"]), int(t[right]["index"])]


def _resolve_named_target(name):
    for kind, channel_map in (("primary", hdmi_channels), ("secondary", usb7_channels)):
        pair = _pair(name, channel_map)
        if pair:
            base = f"stereo_{name}"
            if base in channel_map:
                gain = float(channel_map[base].get("gain", 1))
            else:
                gain = (
                    float(channel_map[f"{base}_L"].get("gain", 1))
                    + float(channel_map[f"{base}_R"].get("gain", 1))
                ) / 2
            return kind, "stereo", pair, gain
        if name in channel_map:
            entry = channel_map[name]
            index = entry["index"]
            if isinstance(index, (list, tuple)) and len(index) == 2:
                return kind, "stereo", list(index), float(entry.get("gain", 1))
            return kind, "one", int(index), float(entry.get("gain", 1))
    raise ValueError(f"Unknown channel name '{name}'.")


def _submit(path,name,mode,target,gain,looping,honor_shutdown,honor_breakcheck,threaded,force_cached=False):
    kind = "primary" if mode == "all" else _resolve_named_target(name)[0]
    if mode != "all":
        kind, mode, target, _ = _resolve_named_target(name)
    mixer = _mixer(kind)
    if mixer.fallback_to_all:
        # Preserve stereo when the fallback device can reproduce it. Discrete
        # mono routes still broadcast because their original channel number
        # has no meaning on a different device.
        if mode == "stereo" and mixer.channels >= 2:
            target = [0, 1]
        else:
            mode, target = "all", 0
    elif mode == "stereo":
        target = [min(int(channel), mixer.channels - 1) for channel in target]
    else:
        target = min(int(target), mixer.channels - 1)

    session = _Session(_next_epoch(), f"{path.name}@{name or 'all'}")
    voice = _Voice(
        _source(path, mixer.samplerate, looping, force_cached),
        mode,
        target,
        float(gain),
        session,
        honor_shutdown,
        honor_breakcheck,
    )
    with _active_lock:
        _active_sessions.append(session)
    mixer.add(voice)
    if not threaded:
        session.done.wait()

def play_to_named_channel(
    wav_file: str,
    target_name: str,
    *,
    gain_override: float | None = None,
    base_folder: Path | str | None = None,
    looping: bool = False,
    honor_shutdown: bool = True,
    honor_breakcheck: bool = True,
    threaded: bool = True,
):
    """Play a WAV on one configured mono or stereo named route."""
    path = _resolve_sound_path(wav_file, base_folder or DEFAULT_SOUND_DIR)
    if not path.exists():
        raise FileNotFoundError(path)
    gain = _resolve_named_target(target_name)[3] if gain_override is None else gain_override
    log_event(
        f"[Audio] Playing '{path.name}' -> {target_name}, gain={gain}, "
        f"looping={looping}, threaded={threaded}"
    )
    _submit(path, target_name, "one", 0, gain, looping, honor_shutdown, honor_breakcheck, threaded)


def play_to_all_channels(
    wav_or_text: str,
    *,
    tts_rate: int = 0,
    gain_override: float | None = None,
    base_folder: Path | str | None = None,
    looping: bool = False,
    honor_shutdown: bool = True,
    honor_breakcheck: bool = True,
    threaded: bool = True,
):
    """Play a WAV, or speak text, on every channel of the primary output."""
    base = Path(base_folder) if base_folder else DEFAULT_SOUND_DIR
    path = _resolve_sound_path(wav_or_text, base)
    gain = 1 if gain_override is None else gain_override
    if Path(wav_or_text).suffix.lower() == ".wav" or path.exists():
        if not path.exists():
            raise FileNotFoundError(path)
        return _submit(path, None, "all", 0, gain, looping, honor_shutdown, honor_breakcheck, threaded)

    descriptor, temporary_path = tempfile.mkstemp(suffix=".wav")
    os.close(descriptor)
    path = Path(temporary_path)
    try:
        text_to_wav(wav_or_text, path, tts_rate)
        # TTS is intentionally asynchronous and does not stop during shutdown.
        _submit(path, None, "all", 0, gain, False, False, False, True, True)
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def play_audio(
    target_or_text: str,
    maybe_file: str | None = None,
    *,
    gain: float | None = None,
    base_folder: Path | str | None = None,
    tts_rate: int = 0,
    looping: bool = False,
    threaded: bool = True,
):
    """Convenience API for room audio and text-to-speech.

    ``play_audio("graveyard", "hit.wav")`` routes a WAV to the named room.
    ``play_audio("all", "hit.wav")`` broadcasts a WAV on the primary output.
    ``play_audio("graveyard: Welcome")`` speaks on a named route, while bare
    text is spoken on every primary channel.  TTS is always non-blocking and
    intentionally ignores break/shutdown requests.
    """
    if maybe_file:
        if target_or_text.lower() == "all":
            return play_to_all_channels(
                maybe_file,
                gain_override=gain,
                base_folder=base_folder,
                looping=looping,
                threaded=threaded,
            )
        return play_to_named_channel(
            maybe_file,
            target_or_text,
            gain_override=gain,
            base_folder=base_folder,
            looping=looping,
            threaded=threaded,
        )

    if ":" in target_or_text:
        name, text = (value.strip() for value in target_or_text.split(":", 1))
        try:
            _resolve_named_target(name)
        except ValueError:
            pass
        else:
            descriptor, temporary_path = tempfile.mkstemp(suffix=".wav")
            os.close(descriptor)
            path = Path(temporary_path)
            try:
                text_to_wav(text, path, tts_rate)
                return _submit(path, name, "one", 0, 1 if gain is None else gain, False, False, False, True, True)
            finally:
                try:
                    path.unlink()
                except OSError:
                    pass

    return play_to_all_channels(
        target_or_text,
        tts_rate=tts_rate,
        gain_override=gain,
        base_folder=base_folder,
        honor_shutdown=False,
        honor_breakcheck=False,
        threaded=True,
    )
def _break_monitor():
    while True:
        try:
            # BreakCheck() logs every failed check. This daemon has the same
            # condition but avoids flooding the log while the house is offline.
            if not house.HouseActive or house.systemState != "ONLINE":
                for mixer in tuple(_mixers.values()):
                    mixer.stop_matching(lambda voice: voice.honor_breakcheck)
        except Exception as error:
            log_event(f"[Audio] BreakCheck monitor error: {error}")
        time.sleep(0.05)


threading.Thread(target=_break_monitor, name="AudioBreakCheck", daemon=True).start()


def stop_all_audio(timeout: float = 2.0):
    """Stop active file audio while allowing TTS to finish naturally."""
    global _cutoff_epoch
    with _epoch_lock:
        _cutoff_epoch = _play_epoch
    _stop_event.set()
    for mixer in tuple(_mixers.values()):
        mixer.stop_matching(lambda voice: voice.honor_shutdown)
    log_event(f"[Audio] stop_all_audio(): cutoff={_cutoff_epoch}")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _active_lock:
            _active_sessions[:] = [session for session in _active_sessions if not session.done.is_set()]
            pending = bool(_active_sessions)
        if not pending:
            break
        time.sleep(0.01)
    _stop_event.clear()
    log_event("[Audio] stop_all_audio(): complete")


def list_output_devices():
    """Return the selectable PortAudio output devices for configuration."""
    return [
        f"[{index}] {device['name']} ({device['max_output_channels']}ch)"
        for index, device in enumerate(sd.query_devices())
        if device.get("max_output_channels", 0) > 0
    ]


def list_named_channels():
    """Return configured channel names, output indexes, gains, and device kind."""
    result = {}
    for key, value in hdmi_channels.items():
        result[key] = {"index": value["index"], "gain": value["gain"], "device": "primary"}
    for key, value in usb7_channels.items():
        result[key] = {"index": value["index"], "gain": value["gain"], "device": "secondary"}
    return result


def register_hdmi_channel(name, index, gain=1.0):
    """Add or replace a primary-device route at runtime."""
    if name in usb7_channels:
        raise ValueError(f"'{name}' exists in usb7_channels")
    hdmi_channels[name] = {"index": index, "gain": gain}


def register_usb7_channel(name, index, gain=1.0):
    """Add or replace a secondary-device route at runtime."""
    if name in hdmi_channels:
        raise ValueError(f"'{name}' exists in hdmi_channels")
    usb7_channels[name] = {"index": index, "gain": gain}


def set_channel_gain(name, gain):
    """Change a configured route's gain at runtime."""
    if name in hdmi_channels:
        hdmi_channels[name]["gain"] = gain
        return
    if name in usb7_channels:
        usb7_channels[name]["gain"] = gain
        return
    raise ValueError(f"Unknown channel '{name}'.")


def audio_diagnostics():
    """Return health and voice-count data for each opened persistent mixer."""
    return {kind: mixer.diagnostics() for kind, mixer in _mixers.items()}
