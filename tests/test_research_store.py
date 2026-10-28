from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.research_store import ResearchStore


class ResearchStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "research.sqlite3"
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TABLE conversations (id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT)")
            connection.execute("INSERT INTO conversations VALUES ('case-1', '2026-09-04T00:00:00+00:00', '2026-09-04T00:00:00+00:00')")
        self.store = ResearchStore(lambda: self.path)
        self.store.initialise()

    def test_research_chain_quality_and_deidentified_export(self) -> None:
        audio = self.store.save_audio("case-1", "a.wav", b"research-wave", "a")
        self.assertTrue(Path(audio["storage_path"]).is_file())
        self.store.add_label("case-1", {"label_type": "laryngoscopy", "label_value": "测试标签", "label_status": "confirmed", "annotator": "研究者A", "source": "临床复核"})
        self.store.add_examination("case-1", {"examination_type": "喉镜", "examination_date": "2026-09-04", "findings": "测试检查结果", "clinician": "医生A", "structured": {"nbi": "negative"}})
        self.store.add_follow_up("case-1", {"follow_up_date": "2026-10-04", "outcome_status": "completed", "outcome_notes": "测试随访", "recorder": "研究者A"})
        quality = self.store.refresh_quality("case-1")
        self.assertEqual(quality["status"], "ready")
        details = self.store.case_detail("case-1")
        self.assertEqual(len(details["audio_assets"]), 1)
        self.assertEqual(details["analyses"], [])
        self.assertEqual(details["reviews"], [])
        rows = self.store.export_rows([{"conversation_id": "case-1", "created_at": "2026-09-04"}], lambda _: {"patient_info": {"name": "隐私姓名", "gender": "女", "age": 30}})
        self.assertIsNone(rows[0]["name"])
        self.assertEqual(rows[0]["audio_count"], 1)
        self.assertEqual(rows[0]["quality_status"], "ready")

    def test_model_versions_experiments_and_audit(self) -> None:
        model = self.store.add_model({"model_name": "VoiceScope", "version": "v1", "model_type": "AST-WavLM", "artifact_reference": "checkpoint.pt", "status": "validated", "metrics": {"macro_f1": 0.81}})
        experiment = self.store.add_experiment({"model_version_id": model["id"], "dataset_version": "dataset-v1", "split_strategy": "patient-level 7:1:2", "random_seed": 42, "run_status": "completed", "metrics": {"auroc": 0.9}})
        self.assertEqual(self.store.models()[0]["version"], "v1")
        self.assertEqual(self.store.experiments()[0]["id"], experiment["id"])
        self.store.audit("export_deidentified", "case-1", "local")
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM research_audit").fetchone()[0], 1)

    def test_clinical_review_is_append_only_and_drives_queue_status(self) -> None:
        first = self.store.add_review("case-1", {
            "review_status": "in_review", "reviewer": "王医生",
            "opinion": "已听取录音，等待喉镜资料。", "recommendation": "补充电子喉镜",
        })
        second = self.store.add_review("case-1", {
            "review_status": "completed", "reviewer": "王医生",
            "opinion": "结合喉镜完成复核。", "recommendation": "按期嗓音门诊随访",
        })
        self.assertEqual((first["version"], second["version"]), (1, 2))
        details = self.store.case_detail("case-1")
        self.assertEqual([item["review_status"] for item in details["reviews"]], ["in_review", "completed"])
        cases = self.store.cases(
            [{"conversation_id": "case-1", "created_at": "2026-09-04"}],
            lambda _: {"patient_info": {"name": "测试患者", "gender": "女", "age": 30}},
        )
        self.assertEqual(cases[0]["review"]["review_status"], "completed")
        self.assertEqual(cases[0]["review"]["version"], 2)
        self.assertTrue(cases[0]["review_eligible"])


if __name__ == "__main__":
    unittest.main()
