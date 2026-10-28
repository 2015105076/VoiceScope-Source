from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient


def _user(module, role: str):
    module.auth_store.initialise()
    username = f"test_{role}"
    try:
        return module.auth_store.create_user(username, "TestingPass12345", f"测试{role}", role)
    except sqlite3.IntegrityError:
        with module.auth_store._connect() as connection:
            return dict(connection.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone())


def auth_client(module, role: str = "patient", case_ids=()) -> TestClient:
    user = _user(module, role)
    if role == "patient":
        if not module.auth_store.consent_active(user["id"]):
            module.auth_store.record_consent(user["id"], True, "testclient")
        for case_id in case_ids:
            module.auth_store.assign_owner(case_id, user["id"])
    elif role == "doctor":
        admin = _user(module, "admin")
        for case_id in case_ids:
            module.auth_store.grant_case(case_id, user["id"], admin["id"], None)
    token, _ = module.auth_store.create_session(user["id"])
    return TestClient(module.app, headers={"Authorization": f"Bearer {token}"})
