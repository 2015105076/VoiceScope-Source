from __future__ import annotations

from io import BytesIO
import uuid
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.main as main_module
from backend.voice_inference import VoiceModelUnavailable, VoiceQualityError
from auth_test_utils import auth_client


class FakeVoiceService:
    max_upload_bytes = 1024

    def status(self) -> dict:
        return {
            "configured": True,
            "checkpoint_found": True,
            "dependencies_ready": True,
            "dependencies": {},
            "loaded": True,
            "device": "cpu",
            "load_error": None,
        }

    def analyze(self, audio_inputs):
        assert [item.filename for item in audio_inputs] == ["a.wav", "o.wav", "e.wav", "i.wav", "u.wav", "v.wav"]
        return {
            "predicted_label": "NV",
            "predicted_probability": 0.8,
            "probabilities": [
                {"label": label, "probability": probability}
                for label, probability in zip(
                    ["NV", "SD", "UVFP", "VFS", "BPLVF", "MVFT"],
                    [0.8, 0.05, 0.04, 0.04, 0.04, 0.03],
                    strict=True,
                )
            ],
            "vowel_attention": {"/a/": 0.2, "/o/": 0.2, "/e/": 0.15, "/i/": 0.15, "/u/": 0.15, "/ü/": 0.15},
            "audio_quality": {},
            "warnings": [],
            "model": {"architecture": "test", "best_validation_macro_f1": 0.78},
            "disclaimer": "test disclaimer",
        }


def _files() -> dict[str, tuple[str, bytes, str]]:
    return {key: (f"{key}.wav", b"fake-wave", "audio/wav") for key in ("a", "o", "e", "i", "u", "v")}


def _quality_wav(seconds: float = 1.0) -> bytes:
    sample_rate = 16000
    time = np.arange(round(seconds * sample_rate)) / sample_rate
    buffer = BytesIO()
    sf.write(buffer, 0.2 * np.sin(2 * np.pi * 130 * time), sample_rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


class VoiceApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(main_module, "DATABASE_PATH", Path(self.temp.name) / "test.sqlite3")
        patcher.start()
        self.addCleanup(patcher.stop)
        main_module.store.initialise()
        self.conversation_id = main_module.store.create_conversation({"chief_complaint": "声音嘶哑"})
        self.form = {"conversation_id": self.conversation_id}

    def test_voice_analyze_contract(self) -> None:
        with patch.object(main_module, "get_voice_service", return_value=FakeVoiceService()):
            with auth_client(main_module, "admin") as client:
                response = client.post("/api/voice/analyze", files=_files(), data=self.form)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["predicted_label"], "NV")
        self.assertEqual(payload["input_vowels"]["v"], "/ü/")
        self.assertEqual(len(payload["request_id"]), 32)

    def test_single_vowel_quality_check_does_not_persist_audio(self) -> None:
        service = FakeVoiceService()
        service.max_upload_bytes = 100_000
        with patch.object(main_module, "get_voice_service", return_value=service), patch.object(
            main_module.research_store, "save_audio"
        ) as save_audio:
            with auth_client(main_module, "admin") as client:
                response = client.post(
                    "/api/voice/quality-check",
                    files={"audio": ("a.wav", _quality_wav(), "audio/wav")},
                    data={"vowel_key": "a"},
                )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["quality"]["accepted"])
        save_audio.assert_not_called()

    def test_voice_quality_failure_returns_per_vowel_details(self) -> None:
        service = FakeVoiceService()
        report = {
            "filename": "o.wav", "status": "re_record", "accepted": False, "metrics": None,
            "issues": [{"code": "CLIPPING", "severity": "error", "message": "存在削波", "guidance": "移远后重录"}],
            "scope_note": "仅检查录音技术可用性",
        }
        service.analyze = lambda _: (_ for _ in ()).throw(VoiceQualityError({"o": report}))
        with patch.object(main_module, "get_voice_service", return_value=service):
            with auth_client(main_module, "admin") as client:
                response = client.post("/api/voice/analyze", files=_files(), data=self.form)
        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "AUDIO_QUALITY_FAILED")
        self.assertEqual(detail["quality_control"]["o"]["issues"][0]["code"], "CLIPPING")

    def test_voice_analyze_requires_all_six_vowels(self) -> None:
        files = _files()
        files.pop("v")
        with patch.object(main_module, "get_voice_service", return_value=FakeVoiceService()):
            with auth_client(main_module, "admin") as client:
                response = client.post("/api/voice/analyze", files=files, data=self.form)
        self.assertEqual(response.status_code, 422)

    def test_voice_analyze_reports_unavailable_model(self) -> None:
        service = FakeVoiceService()

        def unavailable(_):
            raise VoiceModelUnavailable("测试模型不可用")

        service.analyze = unavailable
        with patch.object(main_module, "get_voice_service", return_value=service):
            with auth_client(main_module, "admin") as client:
                response = client.post("/api/voice/analyze", files=_files(), data=self.form)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "测试模型不可用")

    def test_voice_upload_size_limit(self) -> None:
        service = FakeVoiceService()
        service.max_upload_bytes = 4
        with patch.object(main_module, "get_voice_service", return_value=service):
            with auth_client(main_module, "admin") as client:
                response = client.post("/api/voice/analyze", files=_files(), data=self.form)
        self.assertEqual(response.status_code, 413)

    def test_voice_diagnosis_correction_preserves_model_output_and_appends_feedback(self) -> None:
        with patch.object(main_module, "get_voice_service", return_value=FakeVoiceService()):
            with auth_client(main_module, "admin") as client:
                analysis = client.post("/api/voice/analyze", files=_files(), data=self.form).json()
                first = client.post(
                    f"/api/conversations/{self.conversation_id}/voice-diagnosis-corrections",
                    json={"model_request_id": analysis["request_id"], "corrected_label": "SD", "note": "结合喉镜判断"},
                )
                second = client.post(
                    f"/api/conversations/{self.conversation_id}/voice-diagnosis-corrections",
                    json={"model_request_id": analysis["request_id"], "corrected_label": "UVFP", "note": "二次复核"},
                )
                history = client.get(f"/api/conversations/{self.conversation_id}").json()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["original_predicted_label"], "NV")
        self.assertEqual(first.json()["corrected_label"], "SD")
        self.assertEqual(first.json()["correction_number"], 1)
        self.assertEqual(second.json()["correction_number"], 2)
        voice_events = [event for event in history["events"] if event["kind"] == "voice_analysis"]
        correction_events = [event for event in history["events"] if event["kind"] == "voice_diagnosis_correction"]
        self.assertEqual(len(voice_events), 1)
        self.assertEqual(voice_events[0]["payload"]["predicted_label"], "NV")
        self.assertEqual([event["payload"]["corrected_label"] for event in correction_events], ["SD", "UVFP"])
        labels = main_module.research_store.case_detail(self.conversation_id)["labels"]
        correction_labels = [label for label in labels if label["label_type"] == "voice_diagnosis_correction"]
        self.assertEqual([label["label_value"] for label in correction_labels], ["SD", "UVFP"])
        self.assertTrue(all(label["source"] == f"voice_analysis:{analysis['request_id']}" for label in correction_labels))
        exported = main_module.research_store.export_rows(
            [{"conversation_id": self.conversation_id, "created_at": history["events"][0]["created_at"]}],
            main_module.store.facts,
        )[0]
        self.assertEqual(exported["voice_model_request_id"], analysis["request_id"])
        self.assertEqual(exported["voice_model_label"], "NV")
        self.assertEqual(exported["voice_model_probability"], 0.8)
        self.assertEqual(exported["voice_corrected_label"], "UVFP")
        self.assertEqual(exported["voice_correction_count"], 2)

    def test_voice_diagnosis_correction_rejects_untraceable_or_invalid_feedback(self) -> None:
        with auth_client(main_module, "admin") as client:
            missing = client.post(
                f"/api/conversations/{self.conversation_id}/voice-diagnosis-corrections",
                json={"model_request_id": "0" * 32, "corrected_label": "SD", "note": ""},
            )
        self.assertEqual(missing.status_code, 422)
        with patch.object(main_module, "get_voice_service", return_value=FakeVoiceService()):
            with auth_client(main_module, "admin") as client:
                analysis = client.post("/api/voice/analyze", files=_files(), data=self.form).json()
                unchanged = client.post(
                    f"/api/conversations/{self.conversation_id}/voice-diagnosis-corrections",
                    json={"model_request_id": analysis["request_id"], "corrected_label": "NV", "note": ""},
                )
                fabricated = client.post(
                    f"/api/conversations/{self.conversation_id}/voice-diagnosis-corrections",
                    json={"model_request_id": analysis["request_id"], "corrected_label": "UNKNOWN", "note": ""},
                )
        self.assertEqual(unchanged.status_code, 422)
        self.assertEqual(fabricated.status_code, 422)
        self.assertEqual(
            [event for event in main_module.store.events(self.conversation_id) if event["kind"] == "voice_diagnosis_correction"],
            [],
        )

    def test_acoustic_analyze_accepts_single_a_without_loading_disease_model(self) -> None:
        service = FakeVoiceService()
        with patch.object(main_module, "get_voice_service", return_value=service), patch.object(
            main_module, "calculate_acoustic_metrics", return_value={"mpt_seconds": 11.2, "signal_type": {"grade": "I"}}
        ) as calculate:
            with auth_client(main_module, "admin") as client:
                response = client.post("/api/voice/acoustic-analyze", files={"a": ("a.wav", b"wave", "audio/wav")}, data=self.form)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mpt_seconds"], 11.2)
        calculate.assert_called_once()


    def test_saved_smoking_history_is_available_for_follow_up(self) -> None:
        conversation_id = uuid.uuid4().hex
        main_module.store.ensure_conversation(conversation_id)
        main_module.store.add_message(conversation_id, "user", "我最近声音嘶哑")
        try:
            with auth_client(main_module, "admin") as client:
                save_response = client.put(
                    f"/api/conversations/{conversation_id}/smoking-history",
                    json={"smoking_status": "长期吸烟", "smoking_years": 12, "cigarettes_per_day": 20},
                )
                history_response = client.get(f"/api/conversations/{conversation_id}")
                list_response = client.get("/api/conversations")
            self.assertEqual(save_response.status_code, 200)
            self.assertEqual(history_response.json()["facts"]["smoking_history"]["cigarettes_per_day"], 20)
            self.assertTrue(any(item["conversation_id"] == conversation_id for item in list_response.json()["conversations"]))
        finally:
            main_module.store.delete(conversation_id)


    def test_smoking_and_alcohol_history_coexist_for_follow_up(self) -> None:
        conversation_id = uuid.uuid4().hex
        main_module.store.ensure_conversation(conversation_id)
        main_module.store.add_message(conversation_id, "user", "我想做嗓音随访")
        try:
            with auth_client(main_module, "admin") as client:
                smoking_response = client.put(
                    f"/api/conversations/{conversation_id}/smoking-history",
                    json={"smoking_status": "从不吸烟", "smoking_years": None, "cigarettes_per_day": None},
                )
                alcohol_response = client.put(
                    f"/api/conversations/{conversation_id}/alcohol-history",
                    json={"drinking_status": "经常饮酒", "drinking_years": 8, "standard_drinks_per_day": 2},
                )
                history_response = client.get(f"/api/conversations/{conversation_id}")
                preflight_response = client.options(
                    f"/api/conversations/{conversation_id}/alcohol-history",
                    headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "PUT"},
                )
            facts = history_response.json()["facts"]
            self.assertEqual(smoking_response.status_code, 200)
            self.assertEqual(alcohol_response.status_code, 200)
            self.assertEqual(facts["smoking_history"]["smoking_status"], "从不吸烟")
            self.assertEqual(facts["alcohol_history"]["standard_drinks_per_day"], 2)
            self.assertEqual(preflight_response.status_code, 200)
            self.assertIn("PUT", preflight_response.headers["access-control-allow-methods"])
        finally:
            main_module.store.delete(conversation_id)


    def test_patient_intake_creates_named_conversation_and_first_question(self) -> None:
        with auth_client(main_module, "patient") as client:
            create_response = client.post(
                "/api/conversations",
                json={
                    "name": "张三",
                    "age": 42,
                    "gender": "男",
                    "location": "浙江省杭州市",
                    "chief_complaint": "声音嘶哑",
                },
            )
            self.assertEqual(create_response.status_code, 200)
            conversation_id = create_response.json()["conversation_id"]
            try:
                history_response = client.get(f"/api/conversations/{conversation_id}")
                list_response = client.get("/api/conversations")
                invalid_response = client.post(
                    "/api/conversations",
                    json={"name": "   ", "age": 42, "gender": "男", "location": "杭州", "chief_complaint": "声音嘶哑"},
                )
                self.assertEqual(history_response.status_code, 200)
                self.assertEqual(len(history_response.json()["messages"]), 1)
                self.assertEqual(history_response.json()["messages"][0]["source"], "system_response")
                self.assertIn("持续多久", history_response.json()["messages"][0]["content"])
                self.assertEqual(history_response.json()["case_points"]["collected_count"], 1)
                self.assertEqual(history_response.json()["facts"]["patient_info"]["name"], "张三")
                self.assertTrue(any(item["conversation_id"] == conversation_id and item["title"] == "张三 · 声音嘶哑" for item in list_response.json()["conversations"]))
                self.assertEqual(invalid_response.status_code, 422)
            finally:
                main_module.store.delete(conversation_id)


if __name__ == "__main__":
    unittest.main()
