import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from engine import GROUPS, analyse, compare, audio_files


def generate_test_audio(path: Path, bpm: float, freq: float) -> None:
    sr, seconds = 22050, 8
    t = np.arange(sr * seconds) / sr
    audio = 0.03*np.sin(2*np.pi*freq*t)
    for beat in np.arange(0, seconds, 60/bpm):
        start = int(beat*sr)
        end = min(start + 2500, len(t))
        burst = np.arange(end-start) / sr
        audio[start:end] += 0.6*np.sin(2*np.pi*(85-35*burst)*burst)*np.exp(-23*burst)
    sf.write(path, audio.astype(np.float32), sr)


class EngineTests(unittest.TestCase):
    def test_analyse_and_compare(self):
        with tempfile.TemporaryDirectory() as td:
            a, b = Path(td)/"a.wav", Path(td)/"b.wav"
            generate_test_audio(a, 145, 70)
            generate_test_audio(b, 88, 220)
            same = analyse(a)
            different = analyse(b)
            for group in GROUPS:
                self.assertGreater(len(same[group]), 0)
                self.assertTrue(all(np.isfinite(same[group])))
            match = compare(same, same)
            mismatch = compare(same, different)
            self.assertAlmostEqual(match["score"], 100.0, places=3)
            self.assertLess(mismatch["score"], match["score"])
            self.assertEqual(len(audio_files(td)), 2)

    def test_weights_and_rejection(self):
        sample = {"bpm": 120, **{name: [0.3, 0.1] for name in GROUPS}}
        result = compare(sample, sample, {"Rhythm": 5})
        self.assertEqual(result["score"], 100)
        with self.assertRaises(ValueError):
            compare(sample, sample, {"Rhythm": 0})


if __name__ == "__main__":
    unittest.main()
