"""Account, session, consent, case authorization, and access-audit primitives."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import HTTPException, Request

Role = Literal["patient", "doctor", "admin"]
SESSION_COOKIE = "voicescope_session"
CONSENT_TYPE = "clinical_screening_data_use"
CONSENT_VERSION = "2026-09-14-v1"
PASSWORD_ITERATIONS = 600_000
SESSION_HOURS = 12
USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,63}$")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().isoformat()


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def normalize_username(username: str) -> str:
    return username.strip().casefold()


def validate_password(password: str) -> None:
    if len(password) < 12 or len(password) > 128:
        raise ValueError("密码需为 12–128 个字符")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValueError("密码至少需同时包含字母和数字")


def password_digest(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS).hex()


class AuthStore:
    def __init__(self, database_path: Callable[[], Path]) -> None:
        self._database_path = database_path

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self._database_path())
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialise(self) -> None:
        self._database_path().parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
                    password_salt TEXT NOT NULL, display_name TEXT NOT NULL, role TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active', failed_attempts INTEGER NOT NULL DEFAULT 0,
                    locked_until TEXT, last_login_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    CHECK(role IN ('patient','doctor','admin')),
                    CHECK(status IN ('active','disabled'))
                );
                CREATE TABLE IF NOT EXISTS user_sessions (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token_hash TEXT NOT NULL UNIQUE, csrf_token TEXT NOT NULL, created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, revoked_at TEXT
                );
                CREATE INDEX IF NOT EXISTS user_sessions_by_user ON user_sessions(user_id, expires_at);
                CREATE TABLE IF NOT EXISTS data_consents (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    consent_type TEXT NOT NULL, version TEXT NOT NULL, accepted INTEGER NOT NULL,
                    ip_address TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS data_consents_by_user ON data_consents(user_id, consent_type, created_at);
                CREATE TABLE IF NOT EXISTS case_access_grants (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                    grantee_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    granted_by_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                    permission TEXT NOT NULL DEFAULT 'clinical_review', created_at TEXT NOT NULL,
                    expires_at TEXT, revoked_at TEXT,
                    UNIQUE(conversation_id, grantee_user_id, permission, revoked_at)
                );
                CREATE INDEX IF NOT EXISTS grants_by_case ON case_access_grants(conversation_id, grantee_user_id);
                CREATE TABLE IF NOT EXISTS access_audit (
                    id TEXT PRIMARY KEY, actor_user_id TEXT, actor_username TEXT, actor_role TEXT,
                    action TEXT NOT NULL, target_type TEXT, target_id TEXT, http_method TEXT,
                    request_path TEXT, outcome TEXT NOT NULL, status_code INTEGER, ip_address TEXT,
                    detail_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS access_audit_by_time ON access_audit(created_at);
                CREATE INDEX IF NOT EXISTS access_audit_by_actor ON access_audit(actor_user_id, created_at);
                CREATE INDEX IF NOT EXISTS access_audit_by_target ON access_audit(target_type, target_id, created_at);
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(conversations)")}
            if "owner_user_id" not in columns:
                connection.execute("ALTER TABLE conversations ADD COLUMN owner_user_id TEXT")
            connection.execute("DELETE FROM user_sessions WHERE expires_at <= ? OR revoked_at IS NOT NULL", (iso_now(),))
        self._bootstrap_admin()

    def _bootstrap_admin(self) -> None:
        username = os.environ.get("VOICESCOPE_BOOTSTRAP_ADMIN_USERNAME", "").strip()
        password = os.environ.get("VOICESCOPE_BOOTSTRAP_ADMIN_PASSWORD", "")
        if not username or not password:
            return
        with self._connect() as connection:
            if connection.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone():
                return
        self.create_user(username, password, os.environ.get("VOICESCOPE_BOOTSTRAP_ADMIN_NAME", "系统管理员"), "admin")

    def create_user(self, username: str, password: str, display_name: str, role: Role = "patient") -> dict[str, Any]:
        normalized = normalize_username(username)
        if not USERNAME_PATTERN.fullmatch(normalized):
            raise ValueError("账号需为 3–64 位小写字母、数字或 _.-")
        validate_password(password)
        name = display_name.strip()
        if not name or len(name) > 64:
            raise ValueError("显示名称需为 1–64 个字符")
        if role not in {"patient", "doctor", "admin"}:
            raise ValueError("账户角色无效")
        salt = secrets.token_bytes(32)
        now = iso_now()
        record = {
            "id": uuid.uuid4().hex,
            "username": normalized,
            "password_hash": password_digest(password, salt),
            "password_salt": salt.hex(),
            "display_name": name,
            "role": role,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO users(id,username,password_hash,password_salt,display_name,role,status,created_at,updated_at)
                   VALUES(:id,:username,:password_hash,:password_salt,:display_name,:role,:status,:created_at,:updated_at)""",
                record,
            )
        return self.public_user(record)

    @staticmethod
    def public_user(user: dict[str, Any] | sqlite3.Row) -> dict[str, Any]:
        return {key: user[key] for key in ("id", "username", "display_name", "role", "status", "created_at")}

    def user(self, user_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise KeyError(user_id)
        return dict(row)

    def users(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM users ORDER BY created_at").fetchall()
        return [self.public_user(row) for row in rows]

    def authenticate(self, username: str, password: str) -> dict[str, Any]:
        normalized = normalize_username(username)
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE username = ?", (normalized,)).fetchone()
            if not row:
                password_digest(password, bytes(32))
                raise ValueError("账号或密码错误")
            user = dict(row)
            locked_until = parse_time(user.get("locked_until"))
            if locked_until and locked_until > utcnow():
                raise PermissionError("账号暂时锁定，请稍后再试")
            valid = hmac.compare_digest(
                user["password_hash"],
                password_digest(password, bytes.fromhex(user["password_salt"])),
            )
            if not valid:
                attempts = int(user["failed_attempts"]) + 1
                lock = (utcnow() + timedelta(minutes=15)).isoformat() if attempts >= 5 else None
                connection.execute(
                    "UPDATE users SET failed_attempts = ?, locked_until = ?, updated_at = ? WHERE id = ?",
                    (0 if lock else attempts, lock, iso_now(), user["id"]),
                )
                raise ValueError("账号或密码错误")
            if user["status"] != "active":
                raise PermissionError("账号已停用")
            now = iso_now()
            connection.execute(
                "UPDATE users SET failed_attempts = 0, locked_until = NULL, last_login_at = ?, updated_at = ? WHERE id = ?",
                (now, now, user["id"]),
            )
        return user

    def create_session(self, user_id: str) -> tuple[str, str]:
        raw_token = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        now = utcnow()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO user_sessions(id,user_id,token_hash,csrf_token,created_at,expires_at,last_seen_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (
                    uuid.uuid4().hex,
                    user_id,
                    hashlib.sha256(raw_token.encode()).hexdigest(),
                    csrf_token,
                    now.isoformat(),
                    (now + timedelta(hours=SESSION_HOURS)).isoformat(),
                    now.isoformat(),
                ),
            )
        return raw_token, csrf_token

    def resolve_session(self, raw_token: str) -> tuple[dict[str, Any], str] | None:
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        with self._connect() as connection:
            row = connection.execute(
                """SELECT u.*, s.id AS session_id, s.csrf_token, s.expires_at, s.revoked_at
                   FROM user_sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?""",
                (token_hash,),
            ).fetchone()
            if not row or row["revoked_at"] or row["status"] != "active":
                return None
            expiry = parse_time(row["expires_at"])
            if not expiry or expiry <= utcnow():
                connection.execute("UPDATE user_sessions SET revoked_at = ? WHERE id = ?", (iso_now(), row["session_id"]))
                return None
            connection.execute("UPDATE user_sessions SET last_seen_at = ? WHERE id = ?", (iso_now(), row["session_id"]))
        return dict(row), str(row["csrf_token"])

    def revoke_session(self, raw_token: str) -> None:
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        with self._connect() as connection:
            connection.execute("UPDATE user_sessions SET revoked_at = ? WHERE token_hash = ?", (iso_now(), token_hash))

    def record_consent(self, user_id: str, accepted: bool, ip_address: str) -> dict[str, Any]:
        record = {
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "consent_type": CONSENT_TYPE,
            "version": CONSENT_VERSION,
            "accepted": 1 if accepted else 0,
            "ip_address": ip_address,
            "created_at": iso_now(),
        }
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO data_consents(id,user_id,consent_type,version,accepted,ip_address,created_at)
                   VALUES(:id,:user_id,:consent_type,:version,:accepted,:ip_address,:created_at)""",
                record,
            )
        return {**record, "accepted": accepted}

    def consent(self, user_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM data_consents WHERE user_id = ? AND consent_type = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (user_id, CONSENT_TYPE),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["accepted"] = bool(result["accepted"])
        return result

    def consent_active(self, user_id: str) -> bool:
        consent = self.consent(user_id)
        return bool(consent and consent["accepted"] and consent["version"] == CONSENT_VERSION)

    def assign_owner(self, conversation_id: str, user_id: str) -> None:
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE conversations SET owner_user_id = ? WHERE id = ? AND (owner_user_id IS NULL OR owner_user_id = ?)",
                (user_id, conversation_id, user_id),
            )
        if result.rowcount != 1:
            raise PermissionError("病例已归属其他患者")

    def set_owner(self, conversation_id: str, user_id: str, replace: bool = False) -> None:
        with self._connect() as connection:
            user = connection.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user or user["role"] != "patient":
                raise ValueError("病例所有者必须是患者账号")
            current = connection.execute("SELECT owner_user_id FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if not current:
                raise KeyError(conversation_id)
            if current["owner_user_id"] and current["owner_user_id"] != user_id and not replace:
                raise PermissionError("病例已绑定其他患者")
            connection.execute("UPDATE conversations SET owner_user_id = ? WHERE id = ?", (user_id, conversation_id))

    def permitted_case_ids(self, principal: dict[str, Any]) -> set[str] | None:
        if principal["role"] == "admin":
            return None
        with self._connect() as connection:
            if principal["role"] == "patient":
                rows = connection.execute("SELECT id FROM conversations WHERE owner_user_id = ?", (principal["id"],)).fetchall()
            else:
                now = iso_now()
                rows = connection.execute(
                    """SELECT DISTINCT conversation_id AS id FROM case_access_grants
                       WHERE grantee_user_id = ? AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)""",
                    (principal["id"], now),
                ).fetchall()
        return {str(row["id"]) for row in rows}

    def can_access_case(self, principal: dict[str, Any], conversation_id: str) -> bool:
        permitted = self.permitted_case_ids(principal)
        return permitted is None or conversation_id in permitted

    def grant_case(self, conversation_id: str, doctor_id: str, admin_id: str, expires_at: str | None) -> dict[str, Any]:
        if expires_at and (parse_time(expires_at) is None or parse_time(expires_at) <= utcnow()):
            raise ValueError("授权过期时间必须晚于当前时间")
        with self._connect() as connection:
            doctor = connection.execute("SELECT role,status FROM users WHERE id = ?", (doctor_id,)).fetchone()
            if not doctor or doctor["role"] != "doctor" or doctor["status"] != "active":
                raise ValueError("只能授权给启用状态的医生账号")
            if not connection.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone():
                raise KeyError(conversation_id)
            connection.execute(
                "UPDATE case_access_grants SET revoked_at = ? WHERE conversation_id = ? AND grantee_user_id = ? AND permission = 'clinical_review' AND revoked_at IS NULL",
                (iso_now(), conversation_id, doctor_id),
            )
            record = {
                "id": uuid.uuid4().hex,
                "conversation_id": conversation_id,
                "grantee_user_id": doctor_id,
                "granted_by_user_id": admin_id,
                "permission": "clinical_review",
                "created_at": iso_now(),
                "expires_at": expires_at,
            }
            connection.execute(
                """INSERT INTO case_access_grants(id,conversation_id,grantee_user_id,granted_by_user_id,permission,created_at,expires_at)
                   VALUES(:id,:conversation_id,:grantee_user_id,:granted_by_user_id,:permission,:created_at,:expires_at)""",
                record,
            )
        return record

    def revoke_grant(self, grant_id: str) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE case_access_grants SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (iso_now(), grant_id),
            )
        return result.rowcount == 1

    def grants(self, conversation_id: str | None = None) -> list[dict[str, Any]]:
        query = """SELECT g.*, u.username, u.display_name FROM case_access_grants g
                   JOIN users u ON u.id = g.grantee_user_id"""
        parameters: tuple[Any, ...] = ()
        if conversation_id:
            query += " WHERE g.conversation_id = ?"
            parameters = (conversation_id,)
        query += " ORDER BY g.created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    def audit(
        self,
        action: str,
        outcome: str,
        principal: dict[str, Any] | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        method: str | None = None,
        path: str | None = None,
        status_code: int | None = None,
        ip_address: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO access_audit(id,actor_user_id,actor_username,actor_role,action,target_type,target_id,
                   http_method,request_path,outcome,status_code,ip_address,detail_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    uuid.uuid4().hex,
                    principal.get("id") if principal else None,
                    principal.get("username") if principal else None,
                    principal.get("role") if principal else None,
                    action,
                    target_type,
                    target_id,
                    method,
                    path,
                    outcome,
                    status_code,
                    ip_address,
                    json.dumps(detail or {}, ensure_ascii=False),
                    iso_now(),
                ),
            )

    def audit_entries(self, limit: int = 200, actor_user_id: str | None = None, target_id: str | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if actor_user_id:
            clauses.append("actor_user_id = ?")
            values.append(actor_user_id)
        if target_id:
            clauses.append("target_id = ?")
            values.append(target_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM access_audit{where} ORDER BY created_at DESC LIMIT ?",
                (*values, min(max(limit, 1), 1000)),
            ).fetchall()
        return [{**dict(row), "detail": json.loads(row["detail_json"])} for row in rows]


def extract_raw_token(request: Request) -> tuple[str | None, bool]:
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip(), False
    return request.cookies.get(SESSION_COOKIE), True


def require_principal(request: Request) -> dict[str, Any]:
    principal = getattr(request.state, "principal", None)
    if not principal:
        raise HTTPException(status_code=401, detail="请先登录")
    return principal


def require_role(principal: dict[str, Any], *roles: Role) -> None:
    if principal.get("role") not in roles:
        raise HTTPException(status_code=403, detail="当前账号无权执行此操作")
