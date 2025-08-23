import numpy as np
import sounddevice as sd
import soundfile as sf

# 🧭 Define named channels with their default channel index and gain
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

def play_to_named_channel(wav_file, target_name, device_index=35, total_channels=8, gain_override=None):
    if target_name not in named_channels:
        raise ValueError(f"Channel name '{target_name}' not found in mapping.")

    target = named_channels[target_name]
    ch_index = target["index"]
    default_gain = target["gain"]
    gain = gain_override if gain_override is not None else default_gain

    data, fs = sf.read(wav_file, dtype='float32')
    if data.ndim == 1:
        data = np.expand_dims(data, axis=1)

    output = np.zeros((len(data), total_channels), dtype='float32')
    output[:, ch_index] = data[:, 0] * gain

    sd.play(output, samplerate=fs, device=device_index)
    sd.wait()
