from __future__ import annotations

from io import BytesIO
from pathlib import Path
import unittest

import numpy as np
import soundfile as sf

from backend.audio_quality import evaluate_audio
from backend.voice_inference import AudioInput, VoiceInferenceService, VoiceModelSettings, VoiceQualityError


SAMPLE_RATE = 16000


def wav_bytes(samples: np.ndarray) -> bytes:
    buffer = BytesIO()
    sf.write(buffer, samples, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def sine(seconds: float = 1.0, amplitude: float = 0.2, frequency: float = 130.0) -> np.ndarray:
    time = np.arange(round(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return amplitude * np.sin(2 * np.pi * frequency * time)


class AudioQualityTest(unittest.TestCase):
    def report(self, samples: np.ndarray, filename: str = "a.wav") -> dict:
        return evaluate_audio(AudioInput(filename=filename, content=wav_bytes(samples)))

    def test_stable_vowel_passes(self) -> None:
        report = self.report(sine())
        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["accepted"])
        self.assertGreaterEqual(report["metrics"]["active_voice_seconds"], 0.9)

    def test_short_effective_voice_requires_rerecord(self) -> None:
        samples = np.zeros(SAMPLE_RATE)
        samples[4000:7200] = sine(seconds=0.2)
        report = self.report(samples)
        self.assertFalse(report["accepted"])
        self.assertIn("ACTIVE_VOICE_TOO_SHORT", {issue["code"] for issue in report["issues"]})

    def test_obvious_clipping_requires_rerecord(self) -> None:
        report = self.report(np.sign(sine(amplitude=1.0)))
        self.assertFalse(report["accepted"])
        self.assertIn("CLIPPING", {issue["code"] for issue in report["issues"]})

    def test_severe_edge_background_noise_requires_rerecord(self) -> None:
        rng = np.random.default_rng(20260907)
        samples = sine(amplitude=0.1)
        edge = round(0.3 * SAMPLE_RATE)
        samples[:edge] = rng.normal(0.0, 0.035, edge)
        samples[-edge:] = rng.normal(0.0, 0.035, edge)
        report = self.report(samples)
        self.assertFalse(report["accepted"])
        self.assertIn("SEVERE_BACKGROUND_NOISE", {issue["code"] for issue in report["issues"]})

    def test_aperiodic_voice_like_signal_is_not_rejected_as_pathology(self) -> None:
        rng = np.random.default_rng(7)
        # 故意使用无稳定基频的连续信号；采集质控不应把周期性不足当作技术失败。
        samples = rng.normal(0.0, 0.08, SAMPLE_RATE)
        report = self.report(samples, "rough_voice.wav")
        self.assertTrue(report["accepted"])
        self.assertFalse(any(issue["severity"] == "error" for issue in report["issues"]))

    def test_six_vowel_validation_aggregates_every_failed_vowel_before_model_load(self) -> None:
        service = VoiceInferenceService(VoiceModelSettings(
            checkpoint_path=Path("/definitely/missing/checkpoint.pt"),
            device="cpu",
            local_files_only=True,
            max_upload_mb=1,
            min_audio_seconds=0.5,
        ))
        inputs = [AudioInput(filename=f"{key}.wav", content=wav_bytes(sine()))
                  for key in ("a", "o", "e", "i", "u", "v")]
        inputs[1] = AudioInput(filename="o.wav", content=wav_bytes(np.sign(sine(amplitude=1.0))))
        inputs[2] = AudioInput(filename="e.wav", content=wav_bytes(sine(seconds=0.2)))
        with self.assertRaises(VoiceQualityError) as caught:
            service.analyze(inputs)
        failed = {key for key, report in caught.exception.reports.items() if not report["accepted"]}
        self.assertEqual(failed, {"o", "e"})


if __name__ == "__main__":
    unittest.main()
