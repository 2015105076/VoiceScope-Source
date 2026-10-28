"""HTTP API for VoiceScope accounts, consent, grants, and audit review."""
from __future__ import annotations

import os
import sqlite3
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .auth import CONSENT_TYPE, CONSENT_VERSION, SESSION_COOKIE, AuthStore, iso_now, require_principal, require_role


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class Registration(Credentials):
    display_name: str = Field(min_length=1, max_length=64)
    consent_accepted: Literal[True]
    consent_version: Literal[CONSENT_VERSION]


class CreateUser(Credentials):
    display_name: str = Field(min_length=1, max_length=64)
    role: Literal["patient", "doctor", "admin"]


class UserStatus(BaseModel):
    status: Literal["active", "disabled"]


class ConsentUpdate(BaseModel):
    accepted: bool
    version: Literal[CONSENT_VERSION]


class OwnerUpdate(BaseModel):
    patient_user_id: str = Field(min_length=32, max_length=32)
    replace: bool = False


class GrantRequest(BaseModel):
    doctor_user_id: str = Field(min_length=32, max_length=32)
    expires_at: str | None = Field(default=None, max_length=64)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=12 * 60 * 60,
        httponly=True,
        secure=os.environ.get("VOICESCOPE_SECURE_COOKIES", "").strip().lower() in {"1", "true", "yes"},
        samesite="lax",
        path="/",
    )


def auth_payload(auth_store: AuthStore, user: dict, csrf_token: str) -> dict:
    return {
        "user": auth_store.public_user(user),
        "csrf_token": csrf_token,
        "consent": auth_store.consent(user["id"]),
        "consent_type": CONSENT_TYPE,
        "required_consent_version": CONSENT_VERSION,
    }


def create_auth_router(auth_store: AuthStore) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["auth"])

    @router.post("/auth/register")
    async def register(request: Request, payload: Registration, response: Response) -> dict:
        try:
            user = auth_store.create_user(payload.username, payload.password, payload.display_name, "patient")
        except sqlite3.IntegrityError as error:
            raise HTTPException(status_code=409, detail="该账号已存在") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        auth_store.record_consent(user["id"], True, client_ip(request))
        token, csrf = auth_store.create_session(user["id"])
        set_session_cookie(response, token)
        auth_store.audit("patient_register", "success", user, "user", user["id"], "POST", request.url.path, 200, client_ip(request))
        return auth_payload(auth_store, auth_store.user(user["id"]), csrf)

    @router.post("/auth/login")
    async def login(request: Request, payload: Credentials, response: Response) -> dict:
        try:
            user = auth_store.authenticate(payload.username, payload.password)
        except PermissionError as error:
            auth_store.audit("login", "denied", None, "user", None, "POST", request.url.path, 423, client_ip(request))
            raise HTTPException(status_code=423, detail=str(error)) from error
        except ValueError as error:
            auth_store.audit("login", "denied", None, "user", None, "POST", request.url.path, 401, client_ip(request))
            raise HTTPException(status_code=401, detail=str(error)) from error
        token, csrf = auth_store.create_session(user["id"])
        set_session_cookie(response, token)
        auth_store.audit("login", "success", user, "user", user["id"], "POST", request.url.path, 200, client_ip(request))
        return auth_payload(auth_store, user, csrf)

    @router.get("/auth/me")
    async def me(request: Request) -> dict:
        principal = require_principal(request)
        return auth_payload(auth_store, principal, request.state.csrf_token)

    @router.post("/auth/logout")
    async def logout(request: Request, response: Response) -> dict:
        principal = require_principal(request)
        if request.state.raw_session_token:
            auth_store.revoke_session(request.state.raw_session_token)
        response.delete_cookie(SESSION_COOKIE, path="/")
        auth_store.audit("logout", "success", principal, "user", principal["id"], "POST", request.url.path, 200, client_ip(request))
        return {"logged_out": True}

    @router.post("/auth/consent")
    async def update_consent(request: Request, payload: ConsentUpdate) -> dict:
        principal = require_principal(request)
        require_role(principal, "patient")
        consent = auth_store.record_consent(principal["id"], payload.accepted, client_ip(request))
        auth_store.audit(
            "consent_accept" if payload.accepted else "consent_revoke",
            "success",
            principal,
            "user",
            principal["id"],
            "POST",
            request.url.path,
            200,
            client_ip(request),
            {"version": payload.version},
        )
        return {"consent": consent}

    @router.get("/admin/users")
    async def users(request: Request) -> dict:
        principal = require_principal(request)
        require_role(principal, "admin")
        return {"users": auth_store.users()}

    @router.post("/admin/users")
    async def create_user(request: Request, payload: CreateUser) -> dict:
        principal = require_principal(request)
        require_role(principal, "admin")
        try:
            user = auth_store.create_user(payload.username, payload.password, payload.display_name, payload.role)
        except sqlite3.IntegrityError as error:
            raise HTTPException(status_code=409, detail="该账号已存在") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        auth_store.audit("user_create", "success", principal, "user", user["id"], "POST", request.url.path, 200, client_ip(request), {"role": user["role"]})
        return user

    @router.put("/admin/users/{user_id}/status")
    async def set_user_status(user_id: str, request: Request, payload: UserStatus) -> dict:
        principal = require_principal(request)
        require_role(principal, "admin")
        if user_id == principal["id"] and payload.status == "disabled":
            raise HTTPException(status_code=409, detail="不能停用当前登录的管理员")
        with auth_store._connect() as connection:
            now = iso_now()
            result = connection.execute("UPDATE users SET status = ?, updated_at = ? WHERE id = ?", (payload.status, now, user_id))
            if payload.status == "disabled":
                connection.execute("UPDATE user_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL", (now, user_id))
        if result.rowcount != 1:
            raise HTTPException(status_code=404, detail="账户不存在")
        auth_store.audit("user_status_update", "success", principal, "user", user_id, "PUT", request.url.path, 200, client_ip(request), {"status": payload.status})
        return auth_store.public_user(auth_store.user(user_id))

    @router.put("/admin/cases/{conversation_id}/owner")
    async def set_case_owner(conversation_id: str, request: Request, payload: OwnerUpdate) -> dict:
        principal = require_principal(request)
        require_role(principal, "admin")
        try:
            auth_store.set_owner(conversation_id, payload.patient_user_id, payload.replace)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="病例不存在") from error
        except (ValueError, PermissionError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        auth_store.audit("case_owner_update", "success", principal, "conversation", conversation_id, "PUT", request.url.path, 200, client_ip(request), {"patient_user_id": payload.patient_user_id})
        return {"conversation_id": conversation_id, "owner_user_id": payload.patient_user_id}

    @router.get("/admin/case-grants")
    async def list_grants(request: Request, conversation_id: str | None = None) -> dict:
        principal = require_principal(request)
        require_role(principal, "admin")
        return {"grants": auth_store.grants(conversation_id)}

    @router.post("/admin/cases/{conversation_id}/grants")
    async def grant_case(conversation_id: str, request: Request, payload: GrantRequest) -> dict:
        principal = require_principal(request)
        require_role(principal, "admin")
        try:
            grant = auth_store.grant_case(conversation_id, payload.doctor_user_id, principal["id"], payload.expires_at)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="病例不存在") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        auth_store.audit("case_grant_create", "success", principal, "conversation", conversation_id, "POST", request.url.path, 200, client_ip(request), {"doctor_user_id": payload.doctor_user_id, "expires_at": payload.expires_at})
        return grant

    @router.delete("/admin/case-grants/{grant_id}")
    async def revoke_grant(grant_id: str, request: Request) -> dict:
        principal = require_principal(request)
        require_role(principal, "admin")
        if not auth_store.revoke_grant(grant_id):
            raise HTTPException(status_code=404, detail="授权不存在或已撤回")
        auth_store.audit("case_grant_revoke", "success", principal, "grant", grant_id, "DELETE", request.url.path, 200, client_ip(request))
        return {"revoked": True}

    @router.get("/admin/audit")
    async def audit(request: Request, limit: int = 200, actor_user_id: str | None = None, target_id: str | None = None) -> dict:
        principal = require_principal(request)
        require_role(principal, "admin")
        return {"entries": auth_store.audit_entries(limit, actor_user_id, target_id)}

    return router
