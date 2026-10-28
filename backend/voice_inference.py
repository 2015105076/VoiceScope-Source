"""AST + WavLM 六元音 checkpoint 的生产侧推理封装。"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .audio_quality import AudioDecodeError, failed_decode_report, inspect_audio


ROOT = Path(__file__).resolve().parent
VOICE_CONFIG_PATH = ROOT / "config" / "voice_model.json"
VOWEL_KEYS = ("a", "o", "e", "i", "u", "v")
VOWEL_DISPLAY_NAMES = ("/a/", "/o/", "/e/", "/i/", "/u/", "/ü/")
REQUIRED_PACKAGES = ("torch", "transformers", "numpy", "soundfile", "scipy")


class VoiceModelError(RuntimeError):
    """可安全返回给接口层的嗓音模型错误基类。"""


class VoiceModelUnavailable(VoiceModelError):
    """模型配置、依赖或 checkpoint 尚不可用。"""


class InvalidVoiceAudio(VoiceModelError):
    """上传的音频不能满足推理输入要求。"""


class VoiceQualityError(InvalidVoiceAudio):
    """一个或多个元音未通过采集技术质量检查。"""

    def __init__(self, reports: dict[str, dict[str, Any]]) -> None:
        self.reports = reports
        failed = [VOWEL_DISPLAY_NAMES[VOWEL_KEYS.index(key)] if key in VOWEL_KEYS else key
                  for key, report in reports.items() if not report.get("accepted")]
        super().__init__(f"{('、'.join(failed) or '录音')} 未通过采集质量检查，请按提示重录。")

    def as_detail(self) -> dict[str, Any]:
        return {"code": "AUDIO_QUALITY_FAILED", "message": str(self), "quality_control": self.reports}


@dataclass(frozen=True)
class AudioInput:
    filename: str
    content: bytes


@dataclass(frozen=True)
class VoiceModelSettings:
    checkpoint_path: Path | None
    device: str
    local_files_only: bool
    max_upload_mb: int
    min_audio_seconds: float
    model_version: str = "unregistered"


def load_voice_settings() -> VoiceModelSettings:
    raw: dict[str, Any] = {}
    if VOICE_CONFIG_PATH.is_file():
        try:
            raw = json.loads(VOICE_CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise VoiceModelUnavailable("嗓音模型配置文件不是有效 JSON") from error

    configured_path = os.environ.get("VOICESCOPE_VOICE_CHECKPOINT") or raw.get("checkpoint_path")
    checkpoint_path = Path(configured_path).expanduser() if configured_path else None
    return VoiceModelSettings(
        checkpoint_path=checkpoint_path,
        device=str(os.environ.get("VOICESCOPE_VOICE_DEVICE") or raw.get("device", "auto")),
        local_files_only=bool(raw.get("local_files_only", False)),
        max_upload_mb=int(raw.get("max_upload_mb", 25)),
        min_audio_seconds=float(raw.get("min_audio_seconds", 0.5)),
        model_version=str(os.environ.get("VOICESCOPE_VOICE_MODEL_VERSION") or raw.get("model_version", "unregistered")),
    )


def dependency_status() -> dict[str, bool]:
    return {name: importlib.util.find_spec(name) is not None for name in REQUIRED_PACKAGES}


class VoiceInferenceService:
    """线程安全的懒加载推理服务；每个进程只保留一个模型实例。"""

    def __init__(self, settings: VoiceModelSettings | None = None) -> None:
        self.settings = settings or load_voice_settings()
        self._load_lock = threading.Lock()
        self._inference_lock = threading.Lock()
        self._model: Any = None
        self._ast_extractor: Any = None
        self._ssl_extractor: Any = None
        self._device: Any = None
        self._checkpoint_metadata: dict[str, Any] = {}
        self._load_error: str | None = None

    @property
    def max_upload_bytes(self) -> int:
        return self.settings.max_upload_mb * 1024 * 1024

    def status(self) -> dict[str, Any]:
        checkpoint = self.settings.checkpoint_path
        dependencies = dependency_status()
        return {
            "configured": checkpoint is not None,
            "checkpoint_found": bool(checkpoint and checkpoint.is_file()),
            "dependencies_ready": all(dependencies.values()),
            "dependencies": dependencies,
            "loaded": self._model is not None,
            "device": str(self._device or self.settings.device),
            "load_error": self._load_error,
        }

    @staticmethod
    def _build_model(torch: Any, transformers: Any, config: dict[str, Any], num_classes: int) -> Any:
        nn = torch.nn

        class VowelAttention(nn.Module):
            def __init__(self, dim: int, dropout: float) -> None:
                super().__init__()
                hidden = max(dim // 2, 64)
                self.score = nn.Sequential(
                    nn.Linear(dim, hidden),
                    nn.Tanh(),
                    nn.Dropout(dropout),
                    nn.Linear(hidden, 1),
                )

            def forward(self, values: Any) -> tuple[Any, Any]:
                scores = self.score(values).squeeze(-1)
                weights = torch.softmax(scores, dim=1)
                return torch.sum(values * weights.unsqueeze(-1), dim=1), weights

        class ASTWavLMSixVowel(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                model_config = config["model"]
                ast_config = transformers.ASTConfig.from_pretrained(
                    model_config["ast_name"],
                    local_files_only=config["local_files_only"],
                )
                wavlm_config = transformers.WavLMConfig.from_pretrained(
                    model_config["ssl_name"],
                    local_files_only=config["local_files_only"],
                )
                self.ast = transformers.ASTModel(ast_config)
                self.wavlm = transformers.WavLMModel(wavlm_config)
                self.num_vowels = len(config["vowel_keys"])
                self.vowel_pooling = model_config["vowel_pooling"]
                fusion_dim = int(model_config["fusion_dim"])
                dropout = float(model_config["dropout"])
                self.vowel_fusion = nn.Sequential(
                    nn.Linear(self.ast.config.hidden_size + self.wavlm.config.hidden_size, fusion_dim),
                    nn.LayerNorm(fusion_dim),
                    nn.GELU(),
                    nn.Dropout(dropout),
                )
                self.vowel_attention = VowelAttention(fusion_dim, dropout) if self.vowel_pooling == "attention" else None
                self.classifier = nn.Sequential(
                    nn.Linear(fusion_dim, fusion_dim // 2),
                    nn.GELU(),
                    nn.Dropout(dropout),
                    nn.Linear(fusion_dim // 2, num_classes),
                )

            @staticmethod
            def _pool_ast(hidden: Any) -> Any:
                return hidden[:, :2].mean(dim=1) if hidden.size(1) >= 2 else hidden[:, 0]

            def _pool_wavlm(self, hidden: Any, attention_mask: Any) -> Any:
                if attention_mask is None:
                    return hidden.mean(dim=1)
                feature_mask = self.wavlm._get_feature_vector_attention_mask(hidden.shape[1], attention_mask)
                feature_mask = feature_mask.to(hidden.dtype).unsqueeze(-1)
                return torch.sum(hidden * feature_mask, dim=1) / feature_mask.sum(dim=1).clamp_min(1.0)

            def forward(self, ast_input: Any, wav_input: Any, wav_attention_mask: Any = None) -> dict[str, Any]:
                ast_feature = self._pool_ast(self.ast(input_values=ast_input).last_hidden_state)
                wav_hidden = self.wavlm(input_values=wav_input, attention_mask=wav_attention_mask).last_hidden_state
                wav_feature = self._pool_wavlm(wav_hidden, wav_attention_mask)
                fused = self.vowel_fusion(torch.cat([ast_feature, wav_feature], dim=-1))
                fused = fused.reshape(1, self.num_vowels, -1)
                if self.vowel_attention is None:
                    patient_feature = fused.mean(dim=1)
                    weights = torch.full((1, self.num_vowels), 1.0 / self.num_vowels, device=fused.device)
                else:
                    patient_feature, weights = self.vowel_attention(fused)
                return {"logits": self.classifier(patient_feature), "vowel_weights": weights}

        # checkpoint 已含完整 backbone 权重；在 meta device 构造可避免随机参数与
        # checkpoint 同时占用两份约 693 MB 内存。load_state_dict(assign=True) 会直接挂载参数。
        try:
            with torch.device("meta"):
                return ASTWavLMSixVowel()
        except Exception:
            return ASTWavLMSixVowel()

    def _resolve_device(self, torch: Any) -> Any:
        requested = self.settings.device.lower()
        if requested == "auto":
            return torch.device("cuda" if torch.cuda.is_available() else "cpu")
        if requested == "cuda" and not torch.cuda.is_available():
            raise VoiceModelUnavailable("配置要求 CUDA，但当前运行环境没有可用 CUDA 设备")
        return torch.device(requested)

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            try:
                missing = [name for name, available in dependency_status().items() if not available]
                if missing:
                    raise VoiceModelUnavailable(f"缺少嗓音推理依赖：{', '.join(missing)}")
                checkpoint_path = self.settings.checkpoint_path
                if checkpoint_path is None:
                    raise VoiceModelUnavailable("尚未配置嗓音模型 checkpoint 路径")
                if not checkpoint_path.is_file():
                    raise VoiceModelUnavailable("配置的嗓音模型 checkpoint 不存在")

                import torch
                import transformers

                checkpoint = torch.load(
                    checkpoint_path,
                    map_location="cpu",
                    weights_only=True,
                    mmap=True,
                )
                if not {"model_state", "config", "label_names"}.issubset(checkpoint):
                    raise VoiceModelUnavailable("checkpoint 缺少 model_state、config 或 label_names")
                training_config = checkpoint["config"]
                data_config = training_config["data"]
                model_config = training_config["model"]
                runtime_config = {
                    "model": model_config,
                    "vowel_keys": VOWEL_KEYS,
                    "local_files_only": self.settings.local_files_only,
                }
                label_names = list(checkpoint["label_names"])
                model = self._build_model(torch, transformers, runtime_config, len(label_names))
                try:
                    model.load_state_dict(checkpoint["model_state"], assign=True)
                except TypeError:
                    model.load_state_dict(checkpoint["model_state"])

                device = self._resolve_device(torch)
                model.to(device)
                model.eval()
                self._ast_extractor = transformers.AutoFeatureExtractor.from_pretrained(
                    model_config["ast_name"],
                    local_files_only=self.settings.local_files_only,
                )
                self._ssl_extractor = transformers.AutoFeatureExtractor.from_pretrained(
                    model_config["ssl_name"],
                    local_files_only=self.settings.local_files_only,
                )
                self._model = model
                self._device = device
                self._checkpoint_metadata = {
                    "label_names": label_names,
                    "sample_rate": int(data_config["sample_rate"]),
                    "max_audio_sec": float(data_config.get("max_audio_sec") or 10.0),
                    "best_val_macro_f1": float(checkpoint.get("best_val_macro_f1", 0.0)),
                    "model_version": str(checkpoint.get("model_version") or training_config.get("version") or self.settings.model_version),
                }
                self._load_error = None
            except VoiceModelError as error:
                self._load_error = str(error)
                raise
            except Exception as error:
                self._load_error = f"{type(error).__name__}: {error}"
                raise VoiceModelUnavailable("嗓音模型加载失败，请检查依赖、Hugging Face 配置资源和 checkpoint 兼容性") from error

    @staticmethod
    def _prepare_audio(values: Any, original_rate: int, sample_rate: int, max_seconds: float) -> tuple[Any, bool]:
        import numpy as np
        from scipy.signal import resample_poly

        if original_rate != sample_rate:
            common = math.gcd(int(original_rate), sample_rate)
            values = resample_poly(values, sample_rate // common, int(original_rate) // common).astype("float32")
        max_samples = int(sample_rate * max_seconds)
        was_truncated = values.shape[0] > max_samples
        values = values[:max_samples]
        peak = float(np.max(np.abs(values)))
        values = (values / peak).astype("float32")
        return values, was_truncated

    def analyze(self, audio_inputs: Sequence[AudioInput]) -> dict[str, Any]:
        if len(audio_inputs) != len(VOWEL_KEYS):
            raise InvalidVoiceAudio("必须按顺序提供六个元音音频")
        inspected: list[tuple[Any, int] | None] = []
        quality: dict[str, dict[str, Any]] = {}
        for key, audio in zip(VOWEL_KEYS, audio_inputs, strict=True):
            try:
                waveform, original_rate, report = inspect_audio(audio)
                inspected.append((waveform, original_rate))
            except AudioDecodeError as error:
                report = failed_decode_report(audio.filename, str(error))
                inspected.append(None)
            quality[key] = report
        if any(not report["accepted"] for report in quality.values()):
            raise VoiceQualityError(quality)

        # 先完成轻量质量检查，再加载大模型，质量不合格时无需占用模型资源。
        self._ensure_loaded()

        import torch

        sample_rate = self._checkpoint_metadata["sample_rate"]
        max_seconds = self._checkpoint_metadata["max_audio_sec"]
        prepared = [self._prepare_audio(item[0], item[1], sample_rate, max_seconds)
                    for item in inspected if item is not None]
        waveforms = [item[0] for item in prepared]
        warnings: list[str] = []
        for index, item in enumerate(prepared):
            quality[VOWEL_KEYS[index]]["metrics"]["truncated"] = item[1]
            if item[1]:
                warnings.append(f"{VOWEL_DISPLAY_NAMES[index]} 超过 {max_seconds:g} 秒，模型仅分析前 {max_seconds:g} 秒")

        with self._inference_lock, torch.inference_mode():
            ast_batch = self._ast_extractor(waveforms, sampling_rate=sample_rate, return_tensors="pt")
            ssl_batch = self._ssl_extractor(
                waveforms,
                sampling_rate=sample_rate,
                padding=True,
                return_attention_mask=True,
                return_tensors="pt",
            )
            output = self._model(
                ast_input=ast_batch["input_values"].to(self._device),
                wav_input=ssl_batch["input_values"].to(self._device),
                wav_attention_mask=ssl_batch.get("attention_mask").to(self._device) if ssl_batch.get("attention_mask") is not None else None,
            )
            probabilities = torch.softmax(output["logits"], dim=-1)[0].detach().cpu().tolist()
            vowel_weights = output["vowel_weights"][0].detach().cpu().tolist()

        labels = self._checkpoint_metadata["label_names"]
        predicted_index = max(range(len(probabilities)), key=probabilities.__getitem__)
        return {
            "predicted_label": labels[predicted_index],
            "predicted_probability": round(float(probabilities[predicted_index]), 6),
            "probabilities": [
                {"label": label, "probability": round(float(probability), 6)}
                for label, probability in zip(labels, probabilities, strict=True)
            ],
            "vowel_attention": {
                display: round(float(weight), 6)
                for display, weight in zip(VOWEL_DISPLAY_NAMES, vowel_weights, strict=True)
            },
            "audio_quality": quality,
            "warnings": warnings,
            "model": {
                "architecture": "AST + WavLM Base+ six-vowel attention",
                "version": self._checkpoint_metadata["model_version"],
                "best_validation_macro_f1": self._checkpoint_metadata["best_val_macro_f1"],
            },
            "disclaimer": "该结果是研究模型的筛查输出，不能替代耳鼻喉科医生诊断或喉镜检查。",
        }


_service: VoiceInferenceService | None = None
_service_lock = threading.Lock()


def get_voice_service() -> VoiceInferenceService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = VoiceInferenceService()
    return _service
