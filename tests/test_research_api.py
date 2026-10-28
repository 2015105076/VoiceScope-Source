from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.main as main
from auth_test_utils import auth_client


class ResearchApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(main, "DATABASE_PATH", Path(self.temp.name) / "research-api.sqlite3")
        patcher.start(); self.addCleanup(patcher.stop)
        main.store.initialise(); main.research_store.initialise()
        self.cid = main.store.create_conversation({"name": "研究测试患者", "age": 30, "gender": "女", "location": "上海市", "chief_complaint": "声音嘶哑"})

    def test_local_research_chain_export_and_model_run(self) -> None:
        audio = main.research_store.save_audio(self.cid, "a.wav", b"test-audio", "a")
        main.research_store.add_analysis(self.cid, "voice_analysis", {
            "request_id": "a" * 32, "predicted_label": "测试风险", "predicted_probability": 0.61,
            "probabilities": [{"label": "测试风险", "probability": 0.61}, {"label": "其他", "probability": 0.39}],
            "audio_quality": {
                key: {
                    "filename": f"{key}.wav", "status": "pass", "accepted": True,
                    "metrics": {"duration_seconds": 2.1, "active_voice_seconds": 1.8},
                    "issues": [], "scope_note": "仅检查录音技术可用性",
                }
                for key in ("a", "o", "e", "i", "u", "v")
            },
            "model": {"architecture": "AST + WavLM", "version": "test-v1"},
            "warnings": [],
            "disclaimer": "仅供筛查，不替代医生诊断。",
        }, [audio["id"]])
        main.store.save_detection(self.cid, "voice_analysis", {
            "request_id": "a" * 32, "predicted_label": "测试风险", "predicted_probability": 0.61,
            "probabilities": [{"label": "测试风险", "probability": 0.61}, {"label": "其他", "probability": 0.39}],
            "audio_quality": {
                key: {
                    "filename": f"{key}.wav", "status": "pass", "accepted": True,
                    "metrics": {"duration_seconds": 2.1, "active_voice_seconds": 1.8},
                    "issues": [], "scope_note": "仅检查录音技术可用性",
                }
                for key in ("a", "o", "e", "i", "u", "v")
            },
            "model": {"architecture": "AST + WavLM", "version": "test-v1"},
            "warnings": [],
            "disclaimer": "仅供筛查，不替代医生诊断。",
        })
        with auth_client(main, "admin") as client:
            cases = client.get("/api/research/cases")
            self.assertEqual(cases.status_code, 200)
            item = next(case for case in cases.json()["cases"] if case["conversation_id"] == self.cid)
            self.assertEqual(item["quality"]["status"], "incomplete")
            self.assertEqual(item["review"]["review_status"], "pending")
            self.assertTrue(item["review_eligible"])
            self.assertGreaterEqual(cases.json()["counts"]["pending"], 1)
            queued = client.get("/api/research/cases?review_queue=true&review_status=pending").json()
            self.assertIn(self.cid, [case["conversation_id"] for case in queued["cases"]])
            detail_before = client.get(f"/api/research/cases/{self.cid}").json()
            self.assertNotIn("storage_path", detail_before["audio_assets"][0])
            audio_response = client.get(detail_before["audio_assets"][0]["playback_url"])
            self.assertEqual(audio_response.status_code, 200)
            self.assertEqual(audio_response.content, b"test-audio")
            self.assertEqual(client.get(f"/api/research/cases/{self.cid}/audio/not-this-case").status_code, 404)
            self.assertEqual(client.post(f"/api/research/cases/{self.cid}/labels", json={"label_type": "喉镜诊断", "label_value": "测试标签", "label_status": "confirmed", "annotator": "研究者", "source": "复核", "notes": ""}).status_code, 200)
            self.assertEqual(client.post(f"/api/research/cases/{self.cid}/examinations", json={"examination_type": "喉镜", "examination_date": "2026-09-05", "findings": "测试检查", "clinician": "医生", "structured": {}}).status_code, 200)
            self.assertEqual(client.post(f"/api/research/cases/{self.cid}/follow-ups", json={"follow_up_date": "2026-10-05", "outcome_status": "completed", "outcome_notes": "测试随访", "recorder": "研究者"}).status_code, 200)
            detail = client.get(f"/api/research/cases/{self.cid}")
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.json()["quality"]["status"], "ready")
            review = client.post(f"/api/research/cases/{self.cid}/reviews", json={"review_status": "completed", "reviewer": "王医生", "opinion": "录音及检查资料已复核。", "recommendation": "建议门诊随访"})
            self.assertEqual(review.status_code, 200)
            self.assertEqual(review.json()["version"], 1)
            patient_report = client.get(
                f"/api/conversations/{self.cid}/screening-report?analysis_request_id={'a' * 32}&format=json"
            )
            self.assertEqual(patient_report.status_code, 200)
            self.assertEqual(patient_report.json()["audience"], "patient")
            self.assertEqual(patient_report.json()["analysis"]["request_id"], "a" * 32)
            self.assertEqual(patient_report.json()["analysis"]["model"]["version"], "test-v1")
            self.assertEqual(patient_report.json()["capture_quality"]["status"], "pass")
            self.assertNotIn("probabilities", patient_report.json()["analysis"])
            professional_report = client.get(
                f"/api/research/cases/{self.cid}/screening-report?analysis_request_id={'a' * 32}&format=json"
            )
            self.assertEqual(professional_report.status_code, 200)
            self.assertEqual(professional_report.json()["audience"], "professional")
            self.assertEqual(len(professional_report.json()["analysis"]["probabilities"]), 2)
            self.assertEqual(professional_report.json()["review"]["status"], "completed")
            self.assertEqual(professional_report.json()["review"]["recommendation"], "建议门诊随访")
            report_html = client.get(
                f"/api/research/cases/{self.cid}/screening-report?analysis_request_id={'a' * 32}&format=html"
            )
            self.assertEqual(report_html.status_code, 200)
            self.assertIn("嗓音筛查专业报告", report_html.text)
            self.assertIn("建议门诊随访", report_html.text)
            self.assertIn("Content-Security-Policy", report_html.headers)
            self.assertTrue(report_html.headers["Content-Disposition"].startswith("inline"))
            report_download = client.get(
                f"/api/conversations/{self.cid}/screening-report?analysis_request_id={'a' * 32}&format=html&download=true"
            )
            self.assertTrue(report_download.headers["Content-Disposition"].startswith("attachment"))
            missing_report = client.get(
                f"/api/conversations/{self.cid}/screening-report?analysis_request_id={'0' * 32}&format=json"
            )
            self.assertEqual(missing_report.status_code, 404)
            invalid_review = client.post(f"/api/research/cases/{self.cid}/reviews", json={"review_status": "completed", "reviewer": "王医生", "opinion": "   ", "recommendation": ""})
            self.assertEqual(invalid_review.status_code, 422)
            completed = client.get("/api/research/cases?review_status=completed").json()
            self.assertIn(self.cid, [case["conversation_id"] for case in completed["cases"]])
            pending = client.get("/api/research/cases?review_status=pending").json()
            self.assertNotIn(self.cid, [case["conversation_id"] for case in pending["cases"]])
            exported = client.get("/api/research/export?format=csv")
            self.assertEqual(exported.status_code, 200)
            self.assertNotIn("研究测试患者", exported.text)
            model = client.post("/api/research/models", json={"model_name": "test", "version": "v1", "model_type": "baseline", "artifact_reference": "model.pt", "status": "validated", "metrics": {}})
            self.assertEqual(model.status_code, 200)
            experiment = client.post("/api/research/experiments", json={"model_version_id": model.json()["id"], "dataset_version": "d1", "split_strategy": "patient-level", "random_seed": 7, "run_status": "completed", "metrics": {}, "notes": ""})
            self.assertEqual(experiment.status_code, 200)
        self.assertGreater(len(main.research_store.experiments()), 0)


if __name__ == "__main__":
    unittest.main()
