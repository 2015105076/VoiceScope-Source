"""与疾病表现解耦的录音采集技术质量检查。"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any, Protocol

import numpy as np
import soundfile as sf


class AudioLike(Protocol):
    filename: str
    content: bytes


class AudioDecodeError(ValueError):
    """音频容器或采样数据无法读取。"""


@dataclass(frozen=True)
class CaptureQualityThresholds:
    min_recording_seconds: float = 0.5
    min_active_seconds: float = 0.5
    silence_peak: float = 1e-6
    silence_rms: float = 1e-5
    clipping_level: float = 0.995
    max_clipped_fraction: float = 0.01
    severe_noise_floor_dbfs: float = -32.0
    severe_snr_db: float = 8.0
    warning_noise_floor_dbfs: float = -45.0
    warning_snr_db: float = 15.0


DEFAULT_THRESHOLDS = CaptureQualityThresholds()
FRAME_SECONDS = 0.02
HOP_SECONDS = 0.01


def _db(value: float) -> float:
    return 20.0 * float(np.log10(max(value, 1e-12)))


def decode_audio(audio: AudioLike) -> tuple[np.ndarray, int]:
    try:
        values, sample_rate = sf.read(BytesIO(audio.content), dtype="float32", always_2d=True)
    except (RuntimeError, ValueError) as error:
        raise AudioDecodeError(f"{audio.filename} 无法解码；请上传 WAV、FLAC、AIFF 或 OGG 音频") from error
    if sample_rate <= 0 or values.shape[0] == 0:
        raise AudioDecodeError(f"{audio.filename} 是空音频")
    waveform = np.asarray(values.mean(axis=1), dtype=np.float64)
    if not np.isfinite(waveform).all():
        raise AudioDecodeError(f"{audio.filename} 含有无效采样值")
    return waveform, int(sample_rate)


def _frames(waveform: np.ndarray, sample_rate: int) -> tuple[np.ndarray, int, int]:
    frame_size = max(64, round(FRAME_SECONDS * sample_rate))
    hop_size = max(32, round(HOP_SECONDS * sample_rate))
    if waveform.size < frame_size:
        return np.empty((0, frame_size)), frame_size, hop_size
    frame_count = 1 + (waveform.size - frame_size) // hop_size
    starts = np.arange(frame_count) * hop_size
    return np.stack([waveform[start : start + frame_size] for start in starts]), frame_size, hop_size


def _covered_seconds(mask: np.ndarray, frame_size: int, hop_size: int, sample_rate: int) -> float:
    """按活动帧覆盖区间计时，避免重叠窗口重复累计。"""
    total_samples = 0
    run_length = 0
    for active in np.append(mask, False):
        if active:
            run_length += 1
        elif run_length:
            total_samples += frame_size + (run_length - 1) * hop_size
            run_length = 0
    return float(total_samples / sample_rate)


def _spectral_flatness(frames: np.ndarray) -> np.ndarray:
    if frames.size == 0:
        return np.array([], dtype=np.float64)
    window = np.hanning(frames.shape[1])
    power = np.abs(np.fft.rfft((frames - frames.mean(axis=1, keepdims=True)) * window, axis=1)) ** 2
    power = np.maximum(power[:, 1:], 1e-12)
    geometric = np.exp(np.mean(np.log(power), axis=1))
    arithmetic = np.mean(power, axis=1)
    return geometric / np.maximum(arithmetic, 1e-12)


def _issue(code: str, severity: str, message: str, guidance: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "message": message, "guidance": guidance}


def assess_waveform(
    waveform: np.ndarray,
    sample_rate: int,
    *,
    filename: str,
    thresholds: CaptureQualityThresholds = DEFAULT_THRESHOLDS,
) -> dict[str, Any]:
    duration = float(waveform.size / sample_rate)
    peak = float(np.max(np.abs(waveform))) if waveform.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(waveform), dtype=np.float64))) if waveform.size else 0.0
    clipped_fraction = float(np.mean(np.abs(waveform) >= thresholds.clipping_level)) if waveform.size else 0.0
    issues: list[dict[str, str]] = []

    if duration < thresholds.min_recording_seconds:
        issues.append(_issue(
            "RECORDING_TOO_SHORT", "error",
            f"录音总时长仅 {duration:.2f} 秒，少于 {thresholds.min_recording_seconds:g} 秒。",
            "靠近麦克风重新录制，并持续发完当前元音后再结束。",
        ))
    if peak <= thresholds.silence_peak or rms <= thresholds.silence_rms:
        issues.append(_issue(
            "NEAR_SILENCE", "error", "录音接近静音，未采集到可用声音。",
            "检查麦克风权限和输入设备，距麦克风约 15–20 厘米后重新录制。",
        ))

    frames, frame_size, hop_size = _frames(waveform, sample_rate)
    rms_by_frame = np.sqrt(np.mean(frames**2, axis=1)) if frames.size else np.array([], dtype=np.float64)
    db_by_frame = np.array([_db(float(value)) for value in rms_by_frame])
    active_seconds = 0.0
    active_fraction = 0.0
    active_level_dbfs: float | None = None
    noise_floor_dbfs: float | None = None
    estimated_snr_db: float | None = None
    noise_assessment = "not_assessed"

    if db_by_frame.size and peak > thresholds.silence_peak and rms > thresholds.silence_rms:
        low_level = float(np.percentile(db_by_frame, 20))
        high_level = float(np.percentile(db_by_frame, 90))
        # 只有能量动态范围足够时才把低能量帧当作噪声候选；稳定或病理性持续元音
        # 不因缺少“安静段”而被误判。
        relative_floor = low_level + 8.0 if high_level - low_level >= 12.0 else high_level - 35.0
        active_threshold = max(-50.0, relative_floor, high_level - 35.0)
        active_mask = db_by_frame >= active_threshold
        active_seconds = min(duration, _covered_seconds(active_mask, frame_size, hop_size, sample_rate))
        active_fraction = min(1.0, active_seconds / max(duration, 1e-12))
        if np.any(active_mask):
            active_level_dbfs = float(np.percentile(db_by_frame[active_mask], 75))

        edge_frame_count = max(1, round(0.3 / HOP_SECONDS))
        edge_indices = np.unique(np.r_[np.arange(min(edge_frame_count, db_by_frame.size)),
                                       np.arange(max(0, db_by_frame.size - edge_frame_count), db_by_frame.size)])
        edge_db = db_by_frame[edge_indices]
        quiet_edge = edge_db <= high_level - 6.0
        if np.count_nonzero(quiet_edge) >= max(5, round(0.08 / HOP_SECONDS)):
            quiet_indices = edge_indices[quiet_edge]
            flatness = _spectral_flatness(frames[quiet_indices])
            noise_floor_dbfs = float(np.median(db_by_frame[quiet_indices]))
            estimated_snr_db = float((active_level_dbfs or high_level) - noise_floor_dbfs)
            noise_assessment = "measured"
            # 只有首尾低能量片段呈宽带噪声、底噪很高且信噪比极低时才阻断。
            # 这一保守组合条件避免用周期性或嗓音规则度拒绝病理嗓音。
            noise_is_broadband = bool(flatness.size and float(np.median(flatness)) >= 0.45)
            if (noise_is_broadband
                    and noise_floor_dbfs >= thresholds.severe_noise_floor_dbfs
                    and estimated_snr_db < thresholds.severe_snr_db):
                issues.append(_issue(
                    "SEVERE_BACKGROUND_NOISE", "error",
                    f"首尾环境声较强，估计信噪比仅 {estimated_snr_db:.1f} dB。",
                    "关闭电视、风扇和音乐，移到安静房间，麦克风距口部约 15–20 厘米后重录。",
                ))
            elif (noise_floor_dbfs >= thresholds.warning_noise_floor_dbfs
                  and estimated_snr_db < thresholds.warning_snr_db):
                issues.append(_issue(
                    "BACKGROUND_NOISE_WARNING", "warning",
                    f"检测到一定背景声，估计信噪比约 {estimated_snr_db:.1f} dB。",
                    "当前录音仍可分析；若条件允许，建议关闭附近声源后重录以提高稳定性。",
                ))

    if peak > thresholds.silence_peak and active_seconds < thresholds.min_active_seconds:
        issues.append(_issue(
            "ACTIVE_VOICE_TOO_SHORT", "error",
            f"有效发声约 {active_seconds:.2f} 秒，少于 {thresholds.min_active_seconds:g} 秒。",
            "重新录制当前元音：开始后立即发声，并持续至少 1 秒再结束。",
        ))
    if clipped_fraction >= thresholds.max_clipped_fraction:
        issues.append(_issue(
            "CLIPPING", "error",
            f"约 {clipped_fraction * 100:.1f}% 的采样达到削波上限，声音可能失真。",
            "将手机或麦克风移远一些，避免大声喊叫，并重新录制当前元音。",
        ))
    elif peak > thresholds.silence_peak and _db(peak) < -25.0:
        issues.append(_issue(
            "LOW_RECORDING_LEVEL", "warning",
            f"录音峰值为 {_db(peak):.1f} dBFS，音量偏低。",
            "当前录音仍可分析；建议靠近麦克风至约 15–20 厘米后重录。",
        ))

    accepted = not any(item["severity"] == "error" for item in issues)
    status = "re_record" if not accepted else ("warning" if issues else "pass")
    return {
        "filename": filename,
        "status": status,
        "accepted": accepted,
        "metrics": {
            "duration_seconds": round(duration, 3),
            "active_voice_seconds": round(active_seconds, 3),
            "active_fraction": round(active_fraction, 3),
            "original_sample_rate": int(sample_rate),
            "peak_dbfs": round(_db(peak), 2) if peak > 0 else None,
            "rms_dbfs": round(_db(rms), 2) if rms > 0 else None,
            "clipped_fraction": round(clipped_fraction, 6),
            "noise_assessment": noise_assessment,
            "estimated_noise_dbfs": round(noise_floor_dbfs, 2) if noise_floor_dbfs is not None else None,
            "estimated_snr_db": round(estimated_snr_db, 2) if estimated_snr_db is not None else None,
        },
        "issues": issues,
        "scope_note": "仅检查录音技术可用性；不以基频、周期性、Jitter、Shimmer、嘶哑或气息声作为拒绝条件。",
    }


def inspect_audio(
    audio: AudioLike,
    thresholds: CaptureQualityThresholds = DEFAULT_THRESHOLDS,
) -> tuple[np.ndarray, int, dict[str, Any]]:
    waveform, sample_rate = decode_audio(audio)
    return waveform, sample_rate, assess_waveform(
        waveform, sample_rate, filename=audio.filename, thresholds=thresholds,
    )


def failed_decode_report(filename: str, message: str) -> dict[str, Any]:
    return {
        "filename": filename,
        "status": "re_record",
        "accepted": False,
        "metrics": None,
        "issues": [_issue(
            "DECODE_FAILED", "error", message,
            "请重新录制，或导出为 WAV、FLAC、AIFF、OGG；不能只修改文件扩展名。",
        )],
        "scope_note": "仅检查录音技术可用性；不以病理嗓音特征作为拒绝条件。",
    }


def evaluate_audio(audio: AudioLike) -> dict[str, Any]:
    try:
        return inspect_audio(audio)[2]
    except AudioDecodeError as error:
        return failed_decode_report(audio.filename, str(error))
