# control/audio_manager.py
import numpy as np
import sounddevice as sd
import soundfile as sf
import os
import tempfile
import pyttsx3

named_channels = {
    "frontLeft":     {"index": 0, "gain": 1.0},
    "frontRight":    {"index": 1, "gain": 1.0},
    "center":        {"index": 2, "gain": 1.4},
    "subwoofer":     {"index": 3, "gain": 1.4},
    "swampRoom":     {"index": 4, "gain": 1.6},
    "atticSpeaker":  {"index": 5, "gain": 1.6},
    "dungeon":       {"index": 6, "gain": 1.8},
    "closetCreak":   {"index": 7, "gain": 1.8},
}

default_audio_folder = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "SoundDir")
)

def play_to_named_channel(
    wav_file,
    target_name,
    device_index=38,
    total_channels=8,
    gain_override=None,
    base_folder=default_audio_folder
):
    if target_name not in named_channels:
        raise ValueError(f"Channel name '{target_name}' not found.")

    file_path = os.path.join(base_folder, wav_file) if not os.path.isabs(wav_file) else wav_file
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    target = named_channels[target_name]
    ch_index = target["index"]
    gain = gain_override if gain_override is not None else target["gain"]

    data, fs = sf.read(file_path, dtype='float32')
    if data.ndim == 1:
        data = np.expand_dims(data, axis=1)

    output = np.zeros((len(data), total_channels), dtype='float32')
    output[:, ch_index] = data[:, 0] * gain

    sd.play(output, samplerate=fs, device=device_index)
    sd.wait()

def play_to_all_channels(
    wav_or_text,
    device_index=38,
    total_channels=8,
    base_folder=default_audio_folder
):
    # Check if this is a filename or plain text
    if wav_or_text.lower().endswith(".wav") or os.path.exists(os.path.join(base_folder, wav_or_text)):
        file_path = os.path.join(base_folder, wav_or_text) if not os.path.isabs(wav_or_text) else wav_or_text
    else:
        # Generate TTS from string
        engine = pyttsx3.init()
        engine.setProperty('rate', 150)
        fd, file_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        engine.save_to_file(wav_or_text, file_path)
        engine.runAndWait()

    data, fs = sf.read(file_path, dtype='float32')
    if data.ndim == 1:
        data = np.expand_dims(data, axis=1)

    output = np.tile(data[:, 0:1], (1, total_channels))
    sd.play(output, samplerate=fs, device=device_index)
    sd.wait()

    if not wav_or_text.lower().endswith(".wav"):
        os.remove(file_path)
