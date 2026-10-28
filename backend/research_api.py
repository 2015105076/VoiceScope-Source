"""Local-first, audited research-management API."""
from __future__ import annotations

import csv
import json
from io import StringIO
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from .auth import require_principal, require_role
from .screening_report import (
    ScreeningReportConflict,
    ScreeningReportNotFound,
    build_screening_report,
    render_screening_report_html,
    report_download_url,
)


class ClinicalLabelRequest(BaseModel):
    label_type: str = Field(min_length=1, max_length=64)
    label_value: str = Field(min_length=1, max_length=500)
    label_status: Literal["preliminary", "confirmed", "rejected"]
    annotator: str = Field(min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=64)
    notes: str = Field(default="", max_length=1000)


class ExaminationRequest(BaseModel):
    examination_type: str = Field(min_length=1, max_length=64)
    examination_date: str | None = Field(default=None, max_length=32)
    findings: str = Field(min_length=1, max_length=2000)
    clinician: str = Field(min_length=1, max_length=64)
    structured: dict[str, Any] = Field(default_factory=dict)


class FollowUpRequest(BaseModel):
    follow_up_date: str = Field(min_length=1, max_length=32)
    outcome_status: str = Field(min_length=1, max_length=64)
    outcome_notes: str = Field(min_length=1, max_length=2000)
    recorder: str = Field(min_length=1, max_length=64)


class ClinicalReviewRequest(BaseModel):
    review_status: Literal["in_review", "completed", "needs_information"]
    reviewer: str = Field(min_length=1, max_length=64)
    opinion: str = Field(min_length=1, max_length=3000)
    recommendation: str = Field(default="", max_length=2000)

    @field_validator("reviewer", "opinion", "recommendation")
    @classmethod
    def strip_text(cls, value: str, info: ValidationInfo) -> str:
        cleaned = value.strip()
        if info.field_name != "recommendation" and not cleaned:
            raise ValueError("该字段不能为空")
        return cleaned


class ModelVersionRequest(BaseModel):
    model_name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    model_type: str = Field(min_length=1, max_length=64)
    artifact_reference: str = Field(min_length=1, max_length=512)
    artifact_sha256: str | None = Field(default=None, max_length=64)
    metrics: dict[str, Any] = Field(default_factory=dict)
    status: Literal["draft", "validated", "archived"]


class ExperimentRunRequest(BaseModel):
    model_version_id: str = Field(min_length=1, max_length=64)
    dataset_version: str = Field(min_length=1, max_length=128)
    split_strategy: str = Field(min_length=1, max_length=128)
    random_seed: int | None = None
    run_status: Literal["running", "completed", "failed"]
    metrics: dict[str, Any] = Field(default_factory=dict)
    notes: str = Field(default="", max_length=2000)


def create_research_router(store, research_store, auth_store) -> APIRouter:
    router = APIRouter(prefix="/api/research", tags=["research"])

    def access(request: Request, admin_only: bool = False) -> dict:
        principal = require_principal(request)
        require_role(principal, "admin" if admin_only else "doctor", "admin")
        return principal

    def require_case(conversation_id: str, principal: dict) -> None:
        try:
            store.facts(conversation_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="会话不存在") from error
        if not auth_store.can_access_case(principal, conversation_id):
            raise HTTPException(status_code=404, detail="会话不存在或当前医生未获授权")

    @router.get("/status")
    async def status(request: Request) -> dict:
        principal = access(request)
        research_store.audit("status", None, principal["username"])
        return {"access": principal["role"], "storage": "ready", "models": len(research_store.models()), "experiments": len(research_store.experiments())}

    @router.get("/cases")
    async def cases(request: Request, date_from: str | None = None, date_to: str | None = None, name: str | None = None, gender: Literal["男", "女", "其他/不便透露"] | None = None, review_status: Literal["all", "pending", "in_review", "completed", "needs_information"] = "all", review_queue: bool = False) -> dict:
        principal = access(request)
        if date_from and date_to and date_from > date_to:
            raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期")
        research_store.audit("case_search", None, principal["username"])
        rows = store.search(date_from, date_to, name, gender)
        permitted = auth_store.permitted_case_ids(principal)
        if permitted is not None:
            rows = [row for row in rows if row["conversation_id"] in permitted]
        all_cases = research_store.cases(rows, store.facts)
        if review_queue:
            all_cases = [item for item in all_cases if item["review_eligible"]]
        counts = {
            status: sum(item["review"]["review_status"] == status for item in all_cases)
            for status in ("pending", "in_review", "completed", "needs_information")
        }
        filtered = all_cases if review_status == "all" else [
            item for item in all_cases if item["review"]["review_status"] == review_status
        ]
        return {"cases": filtered, "counts": {"all": len(all_cases), **counts}}

    @router.get("/cases/{conversation_id}")
    async def case_detail(conversation_id: str, request: Request) -> dict:
        principal = access(request)
        require_case(conversation_id, principal); research_store.audit("case_detail", conversation_id, principal["username"])
        facts = store.facts(conversation_id)
        messages = store.history(conversation_id)
        detail = research_store.case_detail(conversation_id)
        detail["audio_assets"] = [
            {
                **{key: value for key, value in asset.items() if key != "storage_path"},
                "playback_url": f"/api/research/cases/{conversation_id}/audio/{asset['id']}",
            }
            for asset in detail["audio_assets"]
        ]
        patient = facts.get("patient_info", {}) if isinstance(facts, dict) else {}
        return {
            "conversation": {"conversation_id": conversation_id, "facts": facts, "messages": messages},
            "interview_summary": {
                "patient": patient,
                "smoking_history": facts.get("smoking_history") if isinstance(facts, dict) else None,
                "alcohol_history": facts.get("alcohol_history") if isinstance(facts, dict) else None,
                "case_points": store.case_points(conversation_id),
                "user_message_count": sum(message["role"] == "user" for message in messages),
            },
            **detail,
        }

    @router.get("/cases/{conversation_id}/audio/{asset_id}")
    async def play_audio(conversation_id: str, asset_id: str, request: Request) -> FileResponse:
        principal = access(request)
        require_case(conversation_id, principal)
        try:
            asset = research_store.audio_asset(conversation_id, asset_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="会话不存在") from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail="录音不存在或不属于该病例") from error
        target = Path(asset["storage_path"]).resolve()
        allowed_root = (research_store._database_path().parent / "audio" / conversation_id).resolve()
        try:
            target.relative_to(allowed_root)
        except ValueError as error:
            raise HTTPException(status_code=404, detail="录音存储位置无效") from error
        if not target.is_file():
            raise HTTPException(status_code=404, detail="录音文件不存在")
        research_store.audit("audio_playback", conversation_id, principal["username"])
        return FileResponse(
            target,
            media_type=asset["media_type"],
            filename=asset["original_filename"],
            content_disposition_type="inline",
        )

    @router.get("/cases/{conversation_id}/screening-report")
    async def professional_screening_report(
        conversation_id: str,
        request: Request,
        analysis_request_id: str | None = None,
        format: Literal["json", "html"] = "html",
        download: bool = False,
    ) -> Any:
        principal = access(request)
        require_case(conversation_id, principal)
        try:
            report = build_screening_report(store, research_store, conversation_id, "professional", analysis_request_id)
        except ScreeningReportNotFound as error:
            raise HTTPException(status_code=404, detail="未找到可出具报告的六元音分析") from error
        except ScreeningReportConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        research_store.audit("professional_report_download" if download else "professional_report_view", conversation_id, principal["username"])
        if format == "json":
            return report
        base_path = f"/api/research/cases/{conversation_id}/screening-report"
        content = render_screening_report_html(report, report_download_url(base_path, report["analysis"]["request_id"]))
        disposition = "attachment" if download else "inline"
        filename = f"voicescope-professional-{report['case']['case_number']}.html"
        return Response(
            content,
            media_type="text/html; charset=utf-8",
            headers={
                "Content-Disposition": f'{disposition}; filename="{filename}"',
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
            },
        )

    @router.post("/cases/{conversation_id}/reviews")
    async def add_review(conversation_id: str, request: Request, payload: ClinicalReviewRequest) -> dict:
        principal = access(request)
        require_case(conversation_id, principal)
        value = payload.model_dump(); value["reviewer"] = principal["display_name"]
        review = research_store.add_review(conversation_id, value)
        research_store.audit(f"clinical_review_{payload.review_status}", conversation_id, principal["username"])
        return review

    @router.post("/cases/{conversation_id}/labels")
    async def add_label(conversation_id: str, request: Request, payload: ClinicalLabelRequest) -> dict:
        principal = access(request)
        require_case(conversation_id, principal); research_store.audit("label_create", conversation_id, principal["username"])
        value = payload.model_dump(); value["annotator"] = principal["display_name"]
        return research_store.add_label(conversation_id, value)

    @router.post("/cases/{conversation_id}/examinations")
    async def add_examination(conversation_id: str, request: Request, payload: ExaminationRequest) -> dict:
        principal = access(request)
        require_case(conversation_id, principal); research_store.audit("examination_create", conversation_id, principal["username"])
        value = payload.model_dump(); value["clinician"] = principal["display_name"]
        return research_store.add_examination(conversation_id, value)

    @router.post("/cases/{conversation_id}/follow-ups")
    async def add_follow_up(conversation_id: str, request: Request, payload: FollowUpRequest) -> dict:
        principal = access(request)
        require_case(conversation_id, principal); research_store.audit("follow_up_create", conversation_id, principal["username"])
        value = payload.model_dump(); value["recorder"] = principal["display_name"]
        return research_store.add_follow_up(conversation_id, value)

    @router.post("/cases/{conversation_id}/quality")
    async def refresh_quality(conversation_id: str, request: Request) -> dict:
        principal = access(request)
        require_case(conversation_id, principal); research_store.audit("quality_refresh", conversation_id, principal["username"])
        return research_store.refresh_quality(conversation_id)

    @router.get("/models")
    async def models(request: Request) -> dict:
        principal = access(request)
        research_store.audit("models_list", None, principal["username"])
        return {"models": research_store.models(), "experiments": research_store.experiments()}

    @router.post("/models")
    async def add_model(request: Request, payload: ModelVersionRequest) -> dict:
        principal = access(request, admin_only=True)
        try:
            item = research_store.add_model(payload.model_dump())
        except Exception as error:
            raise HTTPException(status_code=409, detail="同名模型版本已存在") from error
        research_store.audit("model_create", None, principal["username"])
        return item

    @router.post("/experiments")
    async def add_experiment(request: Request, payload: ExperimentRunRequest) -> dict:
        principal = access(request, admin_only=True)
        try:
            item = research_store.add_experiment(payload.model_dump())
        except Exception as error:
            raise HTTPException(status_code=422, detail="模型版本不存在") from error
        research_store.audit("experiment_create", None, principal["username"])
        return item

    @router.get("/export")
    async def export(request: Request, date_from: str | None = None, date_to: str | None = None, name: str | None = None, gender: Literal["男", "女", "其他/不便透露"] | None = None, format: Literal["json", "csv"] = "csv", include_identifiers: bool = False) -> Response:
        principal = access(request, admin_only=True)
        rows = research_store.export_rows(store.search(date_from, date_to, name, gender), store.facts, include_identifiers)
        research_store.audit("export_identified" if include_identifiers else "export_deidentified", None, principal["username"])
        if format == "json":
            return Response(json.dumps(rows, ensure_ascii=False, indent=2), media_type="application/json", headers={"Content-Disposition": "attachment; filename=voicescope-research.json"})
        stream = StringIO(); fields = list(rows[0]) if rows else ["subject_id", "gender", "age", "conversation_id", "created_at", "audio_count", "label_count", "examination_count", "follow_up_count", "quality_status", "quality_issues"]
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
        return Response("\ufeff" + stream.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=voicescope-research.csv"})

    return router
