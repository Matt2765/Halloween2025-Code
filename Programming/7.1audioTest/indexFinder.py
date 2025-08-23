import sounddevice as sd

# List all audio devices
devices = sd.query_devices()
for i, device in enumerate(devices):
    print(f"{i}: {device['name']} - {device['max_output_channels']} channels")
