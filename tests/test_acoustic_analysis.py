from __future__ import annotations

from io import BytesIO
import unittest

import numpy as np
import soundfile as sf

from backend.acoustic_analysis import analyze_acoustic_voice
from backend.voice_inference import AudioInput


def sine_wav(frequency_hz: float = 120.0, seconds: float = 12.0, sample_rate: int = 16000) -> bytes:
    time = np.arange(round(seconds * sample_rate)) / sample_rate
    samples = 0.2 * np.sin(2 * np.pi * frequency_hz * time)
    buffer = BytesIO()
    sf.write(buffer, samples, sample_rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


class AcousticAnalysisTest(unittest.TestCase):
    def test_stable_vowel_returns_expected_core_metrics(self) -> None:
        result = analyze_acoustic_voice(AudioInput(filename="a.wav", content=sine_wav()))
        self.assertAlmostEqual(result["mpt_seconds"], 12.0, delta=0.1)
        self.assertAlmostEqual(result["f0_hz"], 120.0, delta=4.0)
        self.assertEqual(result["signal_type"]["grade"], "I")
        self.assertIsNotNone(result["jitter_percent"])
        self.assertIsNotNone(result["shimmer_percent"])

    def test_short_recording_is_rejected(self) -> None:
        with self.assertRaisesRegex(Exception, "未通过采集质量检查"):
            analyze_acoustic_voice(AudioInput(filename="short.wav", content=sine_wav(seconds=0.25)))


if __name__ == "__main__":
    unittest.main()
