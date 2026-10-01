import sys
import types
import unittest
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


if __name__ == "__main__": unittest.main()
