"""Opt-in real-hardware smoke test; never invoked by haunt startup."""
import argparse
import time

from control.audio_manager import (audio_diagnostics, list_output_devices, list_named_channels,
                                   play_audio, stop_all_audio)


def main():
    parser = argparse.ArgumentParser(description="Haunt persistent-audio mixer diagnostic")
    parser.add_argument("--wav", help="WAV to use for explicit playback tests")
    parser.add_argument("--exercise", action="store_true", help="Play WAV through mappings and overlap voices")
    parser.add_argument("--seconds", type=float, default=5, help="How long to observe overlapping playback")
    args = parser.parse_args()
    print("Output devices:")
    print("\n".join(list_output_devices()))
    print("Named channels:", list_named_channels())
    if not args.exercise:
        print("No sound played. Add --exercise --wav PATH to run the audible check.")
        return
    if not args.wav:
        parser.error("--exercise requires --wav")
    # Sequential identification is intentionally opt-in; this validates every
    # configured route, then verifies simultaneous logical voices reuse mixers.
    for name in list_named_channels():
        print("Playing", name); play_audio(name, args.wav, gain=.2, threaded=False)
    print("Starting overlap test")
    for name in list(list_named_channels()) * 3:
        play_audio(name, args.wav, gain=.1, looping=True)
    play_audio("all", args.wav, gain=.05)
    time.sleep(args.seconds)
    print("Diagnostics:", audio_diagnostics())
    stop_all_audio()


if __name__ == "__main__":
    main()
