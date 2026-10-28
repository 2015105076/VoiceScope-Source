from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backend.main as main
from backend.screening_report import ScreeningReportConflict, build_screening_report, render_screening_report_html


class ScreeningReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(main, "DATABASE_PATH", Path(self.temp.name) / "report.sqlite3")
        patcher.start()
        self.addCleanup(patcher.stop)
        main.store.initialise()
        main.research_store.initialise()
        self.cid = main.store.create_conversation({
            "name": "<script>alert(1)</script>", "age": 45, "gender": "女", "location": "杭州市", "chief_complaint": "声音嘶哑",
        })

    def _save(self, request_id: str, label: str, probability: float) -> None:
        result = {
            "request_id": request_id,
            "predicted_label": label,
            "predicted_probability": probability,
            "probabilities": [{"label": label, "probability": probability}],
            "audio_quality": {},
            "model": {"architecture": "test", "version": "v2.3"},
            "warnings": [],
            "disclaimer": "不替代医生诊断。",
        }
        saved = main.store.save_detection(self.cid, "voice_analysis", result)
        main.research_store.add_analysis(self.cid, "voice_analysis", saved, [])

    def test_report_locks_requested_analysis_and_escapes_html(self) -> None:
        first_id = "1" * 32
        second_id = "2" * 32
        self._save(first_id, "第一次结果", 0.55)
        self._save(second_id, "第二次结果", 0.88)
        report = build_screening_report(main.store, main.research_store, self.cid, "patient", first_id)
        self.assertEqual(report["analysis"]["request_id"], first_id)
        self.assertEqual(report["analysis"]["result_label"], "第一次结果")
        self.assertEqual(report["analysis"]["result_probability"], 0.55)
        rendered = render_screening_report_html(report, "/download")
        self.assertNotIn("<script>alert(1)</script>", rendered)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)

    def test_report_rejects_history_storage_conflict(self) -> None:
        request_id = "3" * 32
        self._save(request_id, "原始结果", 0.7)
        with main.research_store._connect() as connection:
            row = connection.execute(
                "SELECT id, result_json FROM analysis_records WHERE conversation_id = ?",
                (self.cid,),
            ).fetchone()
            changed = json.loads(row["result_json"])
            changed["predicted_label"] = "被篡改结果"
            connection.execute(
                "UPDATE analysis_records SET result_json = ? WHERE id = ?",
                (json.dumps(changed, ensure_ascii=False), row["id"]),
            )
        with self.assertRaises(ScreeningReportConflict):
            build_screening_report(main.store, main.research_store, self.cid, "professional", request_id)


if __name__ == "__main__":
    unittest.main()
