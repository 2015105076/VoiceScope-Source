"""不依赖疾病分类模型的基础声学嗓音指标计算。"""
from __future__ import annotations

from typing import Any

import numpy as np
from scipy.signal import correlate

from .audio_quality import AudioDecodeError, failed_decode_report, inspect_audio
from .voice_inference import AudioInput, InvalidVoiceAudio, VoiceQualityError


MIN_F0_HZ = 70.0
MAX_F0_HZ = 350.0
FRAME_SECONDS = 0.04
HOP_SECONDS = 0.01


def _db(value: float) -> float:
    return 20.0 * float(np.log10(max(value, 1e-12)))


def _decode(audio: AudioInput) -> tuple[np.ndarray, int, dict[str, Any]]:
    try:
        waveform, sample_rate, quality = inspect_audio(audio)
    except AudioDecodeError as error:
        raise VoiceQualityError({"a": failed_decode_report(audio.filename, str(error))}) from error
    if not quality["accepted"]:
        raise VoiceQualityError({"a": quality})
    return waveform, sample_rate, quality


def _frame_signal(waveform: np.ndarray, sample_rate: int) -> tuple[np.ndarray, int, int]:
    frame_size = max(256, round(FRAME_SECONDS * sample_rate))
    hop_size = max(128, round(HOP_SECONDS * sample_rate))
    if waveform.size < frame_size:
        return np.empty((0, frame_size)), frame_size, hop_size
    frame_count = 1 + (waveform.size - frame_size) // hop_size
    starts = np.arange(frame_count) * hop_size
    return np.stack([waveform[start : start + frame_size] for start in starts]), frame_size, hop_size


def _longest_run(mask: np.ndarray) -> tuple[int, int]:
    best_start = best_end = current_start = 0
    active = False
    for index, value in enumerate(mask):
        if value and not active:
            current_start, active = index, True
        if active and (not value or index == len(mask) - 1):
            end = index if not value else index + 1
            if end - current_start > best_end - best_start:
                best_start, best_end = current_start, end
            active = False
    return best_start, best_end


def _estimate_f0(frame: np.ndarray, sample_rate: int) -> float | None:
    centered = frame - np.mean(frame)
    energy = float(np.sqrt(np.mean(centered**2)))
    if energy < 1e-5:
        return None
    windowed = centered * np.hanning(centered.size)
    autocorrelation = correlate(windowed, windowed, mode="full", method="fft")[windowed.size - 1 :]
    min_lag = max(1, int(sample_rate / MAX_F0_HZ))
    max_lag = min(autocorrelation.size - 1, int(sample_rate / MIN_F0_HZ))
    if max_lag <= min_lag:
        return None
    candidates = autocorrelation[min_lag : max_lag + 1]
    lag = min_lag + int(np.argmax(candidates))
    periodicity = float(autocorrelation[lag] / max(autocorrelation[0], 1e-12))
    if periodicity < 0.35:
        return None
    return float(sample_rate / lag)


def analyze_acoustic_voice(audio: AudioInput) -> dict[str, Any]:
    """计算单段持续元音的可解释声学指标，不产生疾病分类结论。"""
    waveform, sample_rate, capture_quality = _decode(audio)
    duration_seconds = waveform.size / sample_rate
    if duration_seconds < 0.5:
        raise InvalidVoiceAudio("用于基础声学分析的录音至少需要 0.5 秒")

    frames, frame_size, hop_size = _frame_signal(waveform, sample_rate)
    if frames.size == 0:
        raise InvalidVoiceAudio("录音过短，无法计算声学指标")
    rms = np.sqrt(np.mean(frames**2, axis=1))
    rms_dbfs = np.array([_db(float(value)) for value in rms])
    # 使用相对于录音峰值的门限，避免安静环境与不同设备的绝对电平差异影响发声段定位。
    voiced_threshold_dbfs = max(float(np.max(rms_dbfs) - 35.0), -55.0)
    voiced_mask = rms_dbfs >= voiced_threshold_dbfs
    run_start, run_end = _longest_run(voiced_mask)
    if run_end <= run_start:
        raise InvalidVoiceAudio("录音接近静音，请在安静环境下重新持续发 /a/")

    selected_frames = frames[run_start:run_end]
    selected_rms = rms[run_start:run_end]
    f0_values = np.array(
        [value for frame in selected_frames if (value := _estimate_f0(frame, sample_rate)) is not None],
        dtype=np.float64,
    )
    f0_hz = float(np.median(f0_values)) if f0_values.size else None
    periods = 1.0 / f0_values if f0_values.size else np.array([], dtype=np.float64)
    jitter_percent = (
        float(np.mean(np.abs(np.diff(periods))) / np.mean(periods) * 100.0)
        if periods.size >= 3 and np.mean(periods) > 0
        else None
    )
    shimmer_percent = (
        float(np.mean(np.abs(np.diff(selected_rms))) / np.mean(selected_rms) * 100.0)
        if selected_rms.size >= 3 and np.mean(selected_rms) > 0
        else None
    )
    mean_intensity_dbfs = _db(float(np.sqrt(np.mean(selected_frames**2))))
    f0_cv_percent = float(np.std(f0_values) / np.mean(f0_values) * 100.0) if f0_values.size >= 2 else None
    intensity_range_db = float(np.percentile(rms_dbfs[run_start:run_end], 95) - np.percentile(rms_dbfs[run_start:run_end], 5))
    voiced_fraction = float(f0_values.size / selected_frames.shape[0])

    if f0_hz is not None and voiced_fraction >= 0.8 and (f0_cv_percent or 0) <= 5 and intensity_range_db <= 6:
        signal_type, signal_description = "I", "音高、音强连续且相对稳定"
    elif f0_hz is not None and voiced_fraction >= 0.45:
        signal_type, signal_description = "II", "存在不连续或中等程度的音高、音强波动"
    else:
        signal_type, signal_description = "III", "周期性不足或波动明显，建议重新采集并由专业人员复核"

    warnings: list[str] = []
    mpt_seconds = (run_end - run_start - 1) * hop_size / sample_rate + frame_size / sample_rate
    if mpt_seconds < 10:
        warnings.append("本次最长连续发声不足 10 秒；请在舒适音高和响度下尽力持续发 /a/ 后复测。")
    if f0_hz is None:
        warnings.append("未能稳定提取基频；请减少背景噪声，并避免耳机回声消除或自动增益。")
    if duration_seconds - mpt_seconds > 1.0:
        warnings.append("录音含较长非发声片段，MPT 按最长连续发声段计算。")

    return {
        "recording_duration_seconds": round(duration_seconds, 2),
        "mpt_seconds": round(max(mpt_seconds, 0.0), 2),
        "f0_hz": round(f0_hz, 2) if f0_hz is not None else None,
        "jitter_percent": round(jitter_percent, 3) if jitter_percent is not None else None,
        "shimmer_percent": round(shimmer_percent, 3) if shimmer_percent is not None else None,
        "mean_intensity_dbfs": round(mean_intensity_dbfs, 2),
        "signal_type": {"grade": signal_type, "description": signal_description},
        "quality": {
            "voiced_fraction": round(voiced_fraction, 3),
            "f0_frame_count": int(f0_values.size),
            "f0_cv_percent": round(f0_cv_percent, 3) if f0_cv_percent is not None else None,
            "intensity_range_db": round(intensity_range_db, 2),
        },
        "capture_quality": capture_quality,
        "references": {
            "mpt_seconds": "10 秒（本系统展示用参考值）",
            "f0_hz": "85–155 Hz（本系统展示用参考区间，需结合性别、年龄与任务解释）",
            "jitter_percent": "0.5%（仪器、算法与采样条件会影响可比性）",
            "shimmer_percent": "3%（仪器、算法与采样条件会影响可比性）",
            "intensity": "82 dB SPL 需要经校准麦克风；本次仅报告相对 dBFS",
        },
        "warnings": warnings,
        "disclaimer": "这是录音条件下的基础声学测量，不是疾病诊断；Jitter 与 Shimmer 为短时估计值，需由专业语音分析设备和临床评估复核。",
    }
