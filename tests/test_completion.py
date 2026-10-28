from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.main as main
from auth_test_utils import auth_client


class CompletionFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(main, "DATABASE_PATH", Path(self.temp.name) / "completion.sqlite3")
        patcher.start(); self.addCleanup(patcher.stop)
        main.store.initialise(); main.research_store.initialise()
        self.cid = main.store.create_conversation({"name": "测试", "age": 30, "gender": "女", "location": "上海", "chief_complaint": "声音嘶哑"})

    def test_only_voice_completed_case_can_be_closed(self) -> None:
        with auth_client(main, "patient", [self.cid]) as client:
            self.assertEqual(client.post(f"/api/conversations/{self.cid}/complete").status_code, 422)
            main.store.save_detection(self.cid, "voice_analysis", {"predicted_label": "NV", "predicted_probability": 0.8})
            response = client.post(f"/api/conversations/{self.cid}/complete")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "completed")
            record = client.get(f"/api/conversations/{self.cid}")
            self.assertEqual(record.json()["status"], "completed")
            self.assertEqual(client.post("/api/chat/stream", json={"conversation_id": self.cid, "message": "继续咨询"}).status_code, 409)
            self.assertEqual(client.post(f"/api/conversations/{self.cid}/complete").json()["already_completed"], True)


if __name__ == "__main__":
    unittest.main()
