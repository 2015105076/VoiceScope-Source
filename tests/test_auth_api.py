from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.main as main
from auth_test_utils import auth_client


class AuthApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(main, "DATABASE_PATH", Path(self.temp.name) / "auth.sqlite3")
        patcher.start(); self.addCleanup(patcher.stop)
        main.store.initialise(); main.research_store.initialise(); main.auth_store.initialise()

    def register_patient(self, username: str) -> tuple[TestClient, str, str]:
        client = TestClient(main.app)
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        response = client.post("/api/auth/register", json={
            "username": username,
            "password": "PatientPass12345",
            "display_name": username,
            "consent_accepted": True,
            "consent_version": "2026-09-14-v1",
        })
        self.assertEqual(response.status_code, 200, response.text)
        return client, response.json()["csrf_token"], response.json()["user"]["id"]

    def create_case(self, client: TestClient, csrf: str, name: str) -> str:
        response = client.post("/api/conversations", headers={"X-CSRF-Token": csrf}, json={
            "name": name, "age": 35, "gender": "女", "location": "杭州市", "chief_complaint": "声音嘶哑",
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["conversation_id"]

    def test_patient_isolation_cookie_csrf_and_consent_withdrawal(self) -> None:
        self.assertEqual(TestClient(main.app).get("/api/conversations").status_code, 401)
        first, first_csrf, _ = self.register_patient("patient_one")
        second, _, _ = self.register_patient("patient_two")
        case_id = self.create_case(first, first_csrf, "甲患者")

        self.assertEqual(first.post("/api/conversations", json={}).status_code, 403)
        self.assertEqual(second.get(f"/api/conversations/{case_id}").status_code, 404)
        self.assertEqual(second.get("/api/conversations").json()["conversations"], [])
        self.assertEqual(first.get(f"/api/conversations/{case_id}").status_code, 200)

        revoked = first.post("/api/auth/consent", headers={"X-CSRF-Token": first_csrf}, json={"accepted": False, "version": "2026-09-14-v1"})
        self.assertEqual(revoked.status_code, 200)
        blocked = first.post("/api/chat/stream", headers={"X-CSRF-Token": first_csrf}, json={"conversation_id": case_id, "message": "声音嘶哑"})
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(first.get(f"/api/conversations/{case_id}").status_code, 200)

    def test_doctor_needs_case_grant_and_actor_cannot_be_spoofed(self) -> None:
        patient, csrf, _ = self.register_patient("patient_case")
        case_id = self.create_case(patient, csrf, "授权病例")
        main.research_store.add_analysis(case_id, "voice_analysis", {"request_id": "a" * 32}, [])

        admin_client = auth_client(main, "admin")
        doctor = main.auth_store.create_user("doctor_one", "DoctorPass12345", "王医生", "doctor")
        doctor_token, _ = main.auth_store.create_session(doctor["id"])
        doctor_client = TestClient(main.app, headers={"Authorization": f"Bearer {doctor_token}"})
        self.assertEqual(doctor_client.get(f"/api/research/cases/{case_id}").status_code, 404)
        self.assertEqual(doctor_client.get("/api/research/export").status_code, 403)

        grant = admin_client.post(f"/api/admin/cases/{case_id}/grants", json={"doctor_user_id": doctor["id"], "expires_at": None})
        self.assertEqual(grant.status_code, 200, grant.text)
        self.assertEqual(doctor_client.get(f"/api/research/cases/{case_id}").status_code, 200)
        review = doctor_client.post(f"/api/research/cases/{case_id}/reviews", json={
            "review_status": "completed", "reviewer": "伪造姓名", "opinion": "已复核录音和问诊资料。", "recommendation": "门诊随访",
        })
        self.assertEqual(review.status_code, 200, review.text)
        self.assertEqual(review.json()["reviewer"], "王医生")

        self.assertEqual(admin_client.delete(f"/api/admin/case-grants/{grant.json()['id']}").status_code, 200)
        self.assertEqual(doctor_client.get(f"/api/research/cases/{case_id}").status_code, 404)
        audit = admin_client.get(f"/api/admin/audit?target_id={case_id}").json()["entries"]
        self.assertTrue(any(item["actor_username"] == "doctor_one" and item["request_path"].endswith("/reviews") for item in audit))
        self.assertTrue(any(item["outcome"] == "denied" for item in audit))

    def test_disabling_account_revokes_existing_session(self) -> None:
        admin_client = auth_client(main, "admin")
        doctor = main.auth_store.create_user("doctor_disable", "DoctorPass12345", "停用医生", "doctor")
        token, _ = main.auth_store.create_session(doctor["id"])
        doctor_client = TestClient(main.app, headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(doctor_client.get("/api/research/status").status_code, 200)
        self.assertEqual(admin_client.put(f"/api/admin/users/{doctor['id']}/status", json={"status": "disabled"}).status_code, 200)
        self.assertEqual(doctor_client.get("/api/research/status").status_code, 401)


if __name__ == "__main__":
    unittest.main()
