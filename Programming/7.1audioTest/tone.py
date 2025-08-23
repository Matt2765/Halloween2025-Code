import numpy as np
import sounddevice as sd
import pyttsx3
import time
import os
import soundfile as sf
from scipy.signal import resample

#Your named output channels
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

device_index = 38
total_channels = 8
sample_rate = 44100
delay_between = 1.0  # Seconds between speakers
temp_wav_path = "tts_temp.wav"

#Convert a phrase to a temporary WAV file
def speak_to_wav(text, filename):
    engine = pyttsx3.init()
    engine.save_to_file(text, filename)
    engine.runAndWait()

def play_label_on_channel(label_name):
    if label_name not in named_channels:
        print(f"Unknown channel: {label_name}")
        return

    ch_index = named_channels[label_name]["index"]
    gain = named_channels[label_name]["gain"]

    # Generate spoken label
    speak_to_wav(label_name.replace('_', ' '), temp_wav_path)

    # Load and force correct format
    data, fs = sf.read(temp_wav_path, dtype='float32')
    if data.ndim == 1:
        data = np.expand_dims(data, axis=1)

    if fs != sample_rate:
        print(f"Sample rate mismatch: {fs} != {sample_rate}")
        # Manually resample to 44.1kHz
        duration = len(data) / fs
        new_length = int(duration * sample_rate)
        from scipy.signal import resample
        data = resample(data, new_length)
        fs = sample_rate

    # Pad to 8-channel output
    output = np.zeros((len(data), total_channels), dtype='float32')
    output[:, ch_index] = data[:, 0] * gain

    print(f"🔈 Playing '{label_name}' on channel {ch_index} (gain={gain}) | shape: {output.shape}, fs: {fs}")
    sd.play(output, samplerate=fs, device=device_index)
    sd.wait()

#Loop through each channel with ID announcement
try:
    while True:
        for label in named_channels:
            play_label_on_channel(label)
            time.sleep(delay_between)

except KeyboardInterrupt:
    print("Speaker test stopped.")
finally:
    if os.path.exists(temp_wav_path):
        os.remove(temp_wav_path)
