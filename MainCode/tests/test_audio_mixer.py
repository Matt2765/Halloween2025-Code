import sys
import types
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Mixer unit tests do not need PortAudio, files, or GPIO; supply import-time
# stubs so they run on a development machine without haunt dependencies.
if "sounddevice" not in sys.modules:
    sys.modules["sounddevice"] = types.SimpleNamespace(OutputStream=object, WasapiSettings=object)
if "soundfile" not in sys.modules:
    sys.modules["soundfile"] = types.SimpleNamespace()
tools = types.ModuleType("utils.tools")
tools.log_event = lambda *args, **kwargs: None
tools.BreakCheck = lambda: False
sys.modules["utils.tools"] = tools
import control.audio_manager as audio_manager
from control.audio_manager import DeviceMixer, _CachedSource, _Session, _Voice


class MixerTests(unittest.TestCase):
    def mixer(self, channels=8):
        return DeviceMixer("test", 0, "fake", channels, 48000, "test")

    def voice(self, samples, mode, target, gain=1, looping=False):
        return _Voice(_CachedSource(np.asarray(samples, np.float32), looping), mode, target, gain,
                      _Session(1, "test"), True, True)

    def render(self, mixer, frames):
        output = np.empty((frames, mixer.channels), np.float32)
        mixer._callback(output, frames, None, None)
        return output

    def test_mono_routing_and_independent_channels(self):
        m = self.mixer(); m.add(self.voice([[.5], [.25]], "one", 3)); m.add(self.voice([[.2], [.1]], "one", 6))
        got = self.render(m, 2)
        np.testing.assert_allclose(got[:, 3], [.5, .25]); np.testing.assert_allclose(got[:, 6], [.2, .1])
        self.assertEqual(np.count_nonzero(got[:, [0, 1, 2, 4, 5, 7]]), 0)

    def test_stereo_source_is_downmixed_for_a_mono_route(self):
        m = self.mixer()
        m.add(self.voice([[.2, .6], [.4, .8]], "one", 3))
        got = self.render(m, 2)
        np.testing.assert_allclose(got[:, 3], [.4, .6])

    def test_stereo_source_is_downmixed_when_broadcast(self):
        m = self.mixer(2)
        m.add(self.voice([[.2, .6], [.4, .8]], "all", 0))
        got = self.render(m, 2)
        np.testing.assert_allclose(got, [[.4, .4], [.6, .6]])

    def test_stereo_same_channel_mix_all_and_clip(self):
        m = self.mixer(); m.add(self.voice([[.1, .7]], "stereo", [4, 5])); m.add(self.voice([[.6]], "one", 4)); m.add(self.voice([[2]], "all", 0))
        got = self.render(m, 1)
        np.testing.assert_allclose(got, np.ones((1, 8)))

    def test_loop_and_completion(self):
        m = self.mixer(2); loop = self.voice([[.1], [.2]], "one", 0, looping=True); m.add(loop)
        np.testing.assert_allclose(self.render(m, 5)[:, 0], [.1, .2, .1, .2, .1])
        finite = self.voice([[.3]], "one", 1); m.add(finite); self.render(m, 1)
        self.assertTrue(finite.session.done.is_set())

    def test_stop_and_many_voices(self):
        m = self.mixer()
        voices = [self.voice([[.01]], "one", i % 8, looping=True) for i in range(30)]
        for v in voices: m.add(v)
        self.assertEqual(m.active_voice_count, 30); self.assertEqual(m.peak_voices, 30)
        m.stop_matching(lambda v: v.honor_shutdown); self.render(m, 1)
        self.assertEqual(m.active_voice_count, 0)

    def test_invalid_configured_device_falls_back_to_default_output(self):
        class FakeSoundDevice:
            default = types.SimpleNamespace(device=[0, 7])

            @staticmethod
            def query_devices(index):
                return {
                    6: {"name": "Input only", "max_output_channels": 0, "default_samplerate": 48000},
                    7: {"name": "Default output", "max_output_channels": 2, "default_samplerate": 48000},
                }[index]

            @staticmethod
            def query_hostapis():
                return []

        class FakeMixer:
            def __init__(self, *args): self.args = args; self.fallback_to_all = args[-1]
            def start(self): pass

        with patch.object(audio_manager, "sd", FakeSoundDevice), \
             patch.object(audio_manager, "DeviceMixer", FakeMixer), \
             patch.object(audio_manager, "SECONDARY_DEVICE_INDEX", 6), \
             patch.object(audio_manager, "FALLBACK_TO_SYSTEM_DEFAULT", True):
            mixer = audio_manager._make_mixer("secondary")

        self.assertEqual(mixer.device_index if hasattr(mixer, "device_index") else mixer.args[1], 7)
        self.assertTrue(mixer.fallback_to_all)

    def test_fallback_preserves_a_stereo_route_on_stereo_output(self):
        class CaptureMixer:
            fallback_to_all = True
            channels = 2
            samplerate = 48000

            def add(self, voice):
                self.voice = voice

        mixer = CaptureMixer()
        source = _CachedSource(np.zeros((1, 2), np.float32), False)
        with patch.object(audio_manager, "_mixer", return_value=mixer), \
             patch.object(audio_manager, "_source", return_value=source):
            audio_manager._submit(
                Path("clip.wav"), "graveyard", "one", 0, 1.0,
                False, True, True, True,
            )

        self.assertEqual(mixer.voice.mode, "stereo")
        self.assertEqual(mixer.voice.target, [0, 1])
        with audio_manager._active_lock:
            audio_manager._active_sessions.remove(mixer.voice.session)

    def test_configured_stereo_and_runtime_routes_remain_supported(self):
        self.assertEqual(
            audio_manager._resolve_named_target("graveyard"),
            ("secondary", "stereo", [0, 1], 1.0),
        )

        name = "test_runtime_route"
        try:
            audio_manager.register_hdmi_channel(name, 7, gain=0.5)
            self.assertEqual(
                audio_manager._resolve_named_target(name),
                ("primary", "one", 7, 0.5),
            )
            audio_manager.set_channel_gain(name, 0.75)
            self.assertEqual(audio_manager.hdmi_channels[name]["gain"], 0.75)
        finally:
            audio_manager.hdmi_channels.pop(name, None)


if __name__ == "__main__": unittest.main()
