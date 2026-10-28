"""Research data chain stored alongside local conversations.

The store keeps raw audio on the configured local volume and only its metadata
in SQLite. MySQL migration can copy the bytes to its LONGBLOB store later.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResearchStore:
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
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS audio_assets (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    vowel_key TEXT, original_filename TEXT NOT NULL, media_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, storage_path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS audio_assets_by_conversation ON audio_assets(conversation_id, created_at);
                CREATE TABLE IF NOT EXISTS analysis_records (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    audio_asset_ids_json TEXT NOT NULL, analysis_type TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS analyses_by_conversation ON analysis_records(conversation_id, created_at);
                CREATE TABLE IF NOT EXISTS clinical_labels (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    label_type TEXT NOT NULL, label_value TEXT NOT NULL, label_status TEXT NOT NULL,
                    annotator TEXT NOT NULL, source TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS labels_by_conversation ON clinical_labels(conversation_id, created_at);
                CREATE TABLE IF NOT EXISTS clinical_examinations (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    examination_type TEXT NOT NULL, examination_date TEXT, findings TEXT NOT NULL,
                    clinician TEXT NOT NULL, structured_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS examinations_by_conversation ON clinical_examinations(conversation_id, examination_date);
                CREATE TABLE IF NOT EXISTS follow_up_records (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    follow_up_date TEXT NOT NULL, outcome_status TEXT NOT NULL, outcome_notes TEXT NOT NULL,
                    recorder TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS followups_by_conversation ON follow_up_records(conversation_id, follow_up_date);
                CREATE TABLE IF NOT EXISTS data_quality_checks (
                    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
                    status TEXT NOT NULL, issues_json TEXT NOT NULL, checked_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS clinical_reviews (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    review_status TEXT NOT NULL, reviewer TEXT NOT NULL, opinion TEXT NOT NULL,
                    recommendation TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(conversation_id, version)
                );
                CREATE INDEX IF NOT EXISTS clinical_reviews_by_conversation ON clinical_reviews(conversation_id, version);
                CREATE INDEX IF NOT EXISTS clinical_reviews_by_status ON clinical_reviews(review_status, created_at);
                CREATE TABLE IF NOT EXISTS research_audit (
                    id TEXT PRIMARY KEY, action TEXT NOT NULL, conversation_id TEXT,
                    actor TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS research_audit_by_time ON research_audit(created_at);
                CREATE TABLE IF NOT EXISTS model_versions (
                    id TEXT PRIMARY KEY, model_name TEXT NOT NULL, version TEXT NOT NULL,
                    model_type TEXT NOT NULL, artifact_reference TEXT NOT NULL, artifact_sha256 TEXT,
                    metrics_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(model_name, version)
                );
                CREATE TABLE IF NOT EXISTS experiment_runs (
                    id TEXT PRIMARY KEY, model_version_id TEXT NOT NULL REFERENCES model_versions(id) ON DELETE RESTRICT,
                    dataset_version TEXT NOT NULL, split_strategy TEXT NOT NULL, random_seed INTEGER,
                    run_status TEXT NOT NULL, metrics_json TEXT NOT NULL DEFAULT '{}', notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS experiments_by_model ON experiment_runs(model_version_id, created_at);
            """)

    def audit(self, action: str, conversation_id: str | None, actor: str) -> None:
        with self._connect() as connection:
            connection.execute("INSERT INTO research_audit(id,action,conversation_id,actor,created_at) VALUES(?,?,?,?,?)", (uuid.uuid4().hex, action, conversation_id, actor, utcnow()))

    def _require_conversation(self, conversation_id: str) -> None:
        with self._connect() as connection:
            if not connection.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone():
                raise KeyError(conversation_id)

    def save_audio(self, conversation_id: str, filename: str, content: bytes, vowel_key: str | None, media_type: str = "audio/wav") -> dict[str, Any]:
        self._require_conversation(conversation_id)
        safe_name = Path(filename).name
        suffix = Path(safe_name).suffix.lower()
        if suffix not in {".wav", ".flac", ".aiff", ".ogg"}:
            suffix = ".wav"
        asset_id = uuid.uuid4().hex
        root = self._database_path().parent / "audio" / conversation_id
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{asset_id}{suffix}"
        target.write_bytes(content)
        record = {"id": asset_id, "conversation_id": conversation_id, "vowel_key": vowel_key,
                  "original_filename": safe_name, "media_type": media_type, "size_bytes": len(content),
                  "sha256": hashlib.sha256(content).hexdigest(), "storage_path": str(target), "created_at": utcnow()}
        with self._connect() as connection:
            connection.execute("""INSERT INTO audio_assets(id,conversation_id,vowel_key,original_filename,media_type,size_bytes,sha256,storage_path,created_at)
                VALUES(:id,:conversation_id,:vowel_key,:original_filename,:media_type,:size_bytes,:sha256,:storage_path,:created_at)""", record)
        return record

    def add_analysis(self, conversation_id: str, analysis_type: str, result: dict[str, Any], audio_asset_ids: list[str]) -> dict[str, Any]:
        self._require_conversation(conversation_id)
        record = {"id": uuid.uuid4().hex, "conversation_id": conversation_id, "audio_asset_ids_json": json.dumps(audio_asset_ids),
                  "analysis_type": analysis_type, "result_json": json.dumps(result, ensure_ascii=False), "created_at": utcnow()}
        with self._connect() as connection:
            connection.execute("""INSERT INTO analysis_records(id,conversation_id,audio_asset_ids_json,analysis_type,result_json,created_at)
                VALUES(:id,:conversation_id,:audio_asset_ids_json,:analysis_type,:result_json,:created_at)""", record)
        return {**record, "audio_asset_ids": audio_asset_ids, "result": result}

    def add_label(self, conversation_id: str, value: dict[str, Any]) -> dict[str, Any]:
        self._require_conversation(conversation_id)
        record = {"id": uuid.uuid4().hex, "conversation_id": conversation_id, "label_type": value["label_type"],
                  "label_value": value["label_value"], "label_status": value["label_status"],
                  "annotator": value["annotator"], "source": value["source"], "notes": value.get("notes", ""), "created_at": utcnow()}
        with self._connect() as connection:
            connection.execute("""INSERT INTO clinical_labels(id,conversation_id,label_type,label_value,label_status,annotator,source,notes,created_at)
                VALUES(:id,:conversation_id,:label_type,:label_value,:label_status,:annotator,:source,:notes,:created_at)""", record)
        self.refresh_quality(conversation_id)
        return record

    def add_voice_correction(self, conversation_id: str, value: dict[str, Any]) -> dict[str, Any]:
        """Atomically retain a model prediction and its append-only human correction."""
        now = utcnow()
        correction_id = uuid.uuid4().hex
        clinical_label_id = uuid.uuid4().hex
        model_request_id = value["model_request_id"]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not connection.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone():
                raise KeyError(conversation_id)
            voice_rows = connection.execute(
                "SELECT payload_json FROM conversation_events WHERE conversation_id = ? AND kind = 'voice_analysis' ORDER BY id DESC",
                (conversation_id,),
            ).fetchall()
            model_result = next(
                (
                    payload
                    for row in voice_rows
                    for payload in [json.loads(row["payload_json"])]
                    if payload.get("request_id") == model_request_id
                ),
                None,
            )
            if model_result is None:
                raise LookupError("未找到对应的六元音筛查结果，请重新打开会话后再试")
            original_label = str(model_result.get("predicted_label", ""))
            available_labels = [
                str(item.get("label"))
                for item in model_result.get("probabilities", [])
                if isinstance(item, dict) and item.get("label")
            ]
            corrected_label = value["corrected_label"]
            if corrected_label not in available_labels:
                raise ValueError("纠正结果必须从该模型的筛查类别中选择")
            if corrected_label == original_label:
                raise ValueError("纠正结果与模型原诊断相同，无需提交纠正")
            earlier_rows = connection.execute(
                "SELECT payload_json FROM conversation_events WHERE conversation_id = ? AND kind = 'voice_diagnosis_correction' ORDER BY id",
                (conversation_id,),
            ).fetchall()
            correction_number = 1 + sum(
                json.loads(row["payload_json"]).get("model_request_id") == model_request_id
                for row in earlier_rows
            )
            correction = {
                "correction_id": correction_id,
                "conversation_id": conversation_id,
                "model_request_id": model_request_id,
                "original_predicted_label": original_label,
                "original_predicted_probability": model_result.get("predicted_probability"),
                "corrected_label": corrected_label,
                "note": value.get("note", ""),
                "correction_number": correction_number,
                "clinical_label_id": clinical_label_id,
                "created_at": now,
            }
            connection.execute(
                """INSERT INTO clinical_labels(id,conversation_id,label_type,label_value,label_status,annotator,source,notes,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (clinical_label_id, conversation_id, "voice_diagnosis_correction", corrected_label, "confirmed",
                 "clinician_ui", f"voice_analysis:{model_request_id}", value.get("note", ""), now),
            )
            connection.execute(
                "INSERT INTO conversation_events(conversation_id,source,kind,payload_json,created_at) VALUES(?,?,?,?,?)",
                (conversation_id, "clinician_feedback", "voice_diagnosis_correction", json.dumps(correction, ensure_ascii=False), now),
            )
            connection.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
        self.refresh_quality(conversation_id)
        return correction

    def add_examination(self, conversation_id: str, value: dict[str, Any]) -> dict[str, Any]:
        self._require_conversation(conversation_id)
        record = {"id": uuid.uuid4().hex, "conversation_id": conversation_id, "examination_type": value["examination_type"],
                  "examination_date": value.get("examination_date"), "findings": value["findings"],
                  "clinician": value["clinician"], "structured_json": json.dumps(value.get("structured", {}), ensure_ascii=False), "created_at": utcnow()}
        with self._connect() as connection:
            connection.execute("""INSERT INTO clinical_examinations(id,conversation_id,examination_type,examination_date,findings,clinician,structured_json,created_at)
                VALUES(:id,:conversation_id,:examination_type,:examination_date,:findings,:clinician,:structured_json,:created_at)""", record)
        self.refresh_quality(conversation_id)
        return {**record, "structured": value.get("structured", {})}

    def add_follow_up(self, conversation_id: str, value: dict[str, Any]) -> dict[str, Any]:
        self._require_conversation(conversation_id)
        record = {"id": uuid.uuid4().hex, "conversation_id": conversation_id, "follow_up_date": value["follow_up_date"],
                  "outcome_status": value["outcome_status"], "outcome_notes": value["outcome_notes"],
                  "recorder": value["recorder"], "created_at": utcnow()}
        with self._connect() as connection:
            connection.execute("""INSERT INTO follow_up_records(id,conversation_id,follow_up_date,outcome_status,outcome_notes,recorder,created_at)
                VALUES(:id,:conversation_id,:follow_up_date,:outcome_status,:outcome_notes,:recorder,:created_at)""", record)
        self.refresh_quality(conversation_id)
        return record

    def add_review(self, conversation_id: str, value: dict[str, Any]) -> dict[str, Any]:
        """Append a traceable doctor review version without overwriting history."""
        self._require_conversation(conversation_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM clinical_reviews WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0]
            record = {
                "id": uuid.uuid4().hex,
                "conversation_id": conversation_id,
                "review_status": value["review_status"],
                "reviewer": value["reviewer"],
                "opinion": value["opinion"],
                "recommendation": value.get("recommendation", ""),
                "version": version,
                "created_at": utcnow(),
            }
            connection.execute(
                """INSERT INTO clinical_reviews(id,conversation_id,review_status,reviewer,opinion,recommendation,version,created_at)
                   VALUES(:id,:conversation_id,:review_status,:reviewer,:opinion,:recommendation,:version,:created_at)""",
                record,
            )
        return record

    def audio_asset(self, conversation_id: str, asset_id: str) -> dict[str, Any]:
        self._require_conversation(conversation_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM audio_assets WHERE id = ? AND conversation_id = ?",
                (asset_id, conversation_id),
            ).fetchone()
        if not row:
            raise LookupError(asset_id)
        return dict(row)

    def add_model(self, value: dict[str, Any]) -> dict[str, Any]:
        record = {"id": uuid.uuid4().hex, "model_name": value["model_name"], "version": value["version"],
                  "model_type": value["model_type"], "artifact_reference": value["artifact_reference"],
                  "artifact_sha256": value.get("artifact_sha256"), "metrics_json": json.dumps(value.get("metrics", {}), ensure_ascii=False),
                  "status": value["status"], "created_at": utcnow()}
        with self._connect() as connection:
            connection.execute("""INSERT INTO model_versions(id,model_name,version,model_type,artifact_reference,artifact_sha256,metrics_json,status,created_at)
                VALUES(:id,:model_name,:version,:model_type,:artifact_reference,:artifact_sha256,:metrics_json,:status,:created_at)""", record)
        return {**record, "metrics": value.get("metrics", {})}

    def add_experiment(self, value: dict[str, Any]) -> dict[str, Any]:
        record = {"id": uuid.uuid4().hex, "model_version_id": value["model_version_id"], "dataset_version": value["dataset_version"],
                  "split_strategy": value["split_strategy"], "random_seed": value.get("random_seed"), "run_status": value["run_status"],
                  "metrics_json": json.dumps(value.get("metrics", {}), ensure_ascii=False), "notes": value.get("notes", ""), "created_at": utcnow()}
        with self._connect() as connection:
            connection.execute("""INSERT INTO experiment_runs(id,model_version_id,dataset_version,split_strategy,random_seed,run_status,metrics_json,notes,created_at)
                VALUES(:id,:model_version_id,:dataset_version,:split_strategy,:random_seed,:run_status,:metrics_json,:notes,:created_at)""", record)
        return {**record, "metrics": value.get("metrics", {})}

    def _rows(self, connection: sqlite3.Connection, table: str, conversation_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(f"SELECT * FROM {table} WHERE conversation_id = ? ORDER BY created_at", (conversation_id,)).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            if "structured_json" in item:
                item["structured"] = json.loads(item.pop("structured_json") or "{}")
            if "audio_asset_ids_json" in item:
                item["audio_asset_ids"] = json.loads(item.pop("audio_asset_ids_json") or "[]")
            if "result_json" in item:
                item["result"] = json.loads(item.pop("result_json") or "{}")
            output.append(item)
        return output

    def case_detail(self, conversation_id: str) -> dict[str, Any]:
        self._require_conversation(conversation_id)
        with self._connect() as connection:
            return {"audio_assets": self._rows(connection, "audio_assets", conversation_id),
                    "analyses": self._rows(connection, "analysis_records", conversation_id),
                    "labels": self._rows(connection, "clinical_labels", conversation_id),
                    "examinations": self._rows(connection, "clinical_examinations", conversation_id),
                    "follow_ups": self._rows(connection, "follow_up_records", conversation_id),
                    "reviews": self._rows(connection, "clinical_reviews", conversation_id),
                    "quality": self._quality_row(connection, conversation_id)}

    def _quality_row(self, connection: sqlite3.Connection, conversation_id: str) -> dict[str, Any]:
        row = connection.execute("SELECT * FROM data_quality_checks WHERE conversation_id = ?", (conversation_id,)).fetchone()
        if not row:
            return {"conversation_id": conversation_id, "status": "unchecked", "issues": [], "checked_at": None}
        item = dict(row); item["issues"] = json.loads(item.pop("issues_json")); return item

    def refresh_quality(self, conversation_id: str) -> dict[str, Any]:
        self._require_conversation(conversation_id)
        with self._connect() as connection:
            checks = {"audio": "audio_assets", "clinical_label": "clinical_labels", "examination": "clinical_examinations", "follow_up": "follow_up_records"}
            issues = [f"缺少{label}" for label, table in checks.items() if connection.execute(f"SELECT 1 FROM {table} WHERE conversation_id = ? LIMIT 1", (conversation_id,)).fetchone() is None]
            record = {"conversation_id": conversation_id, "status": "ready" if not issues else "incomplete", "issues_json": json.dumps(issues, ensure_ascii=False), "checked_at": utcnow()}
            connection.execute("""INSERT INTO data_quality_checks(conversation_id,status,issues_json,checked_at) VALUES(:conversation_id,:status,:issues_json,:checked_at)
                ON CONFLICT(conversation_id) DO UPDATE SET status=excluded.status,issues_json=excluded.issues_json,checked_at=excluded.checked_at""", record)
            return {"conversation_id": conversation_id, "status": record["status"], "issues": issues, "checked_at": record["checked_at"]}

    def cases(self, conversations: list[dict[str, Any]], facts_for: Callable[[str], dict[str, Any]]) -> list[dict[str, Any]]:
        output = []
        for conversation in conversations:
            cid = conversation["conversation_id"]
            quality = self.refresh_quality(cid)
            facts = facts_for(cid); patient = facts.get("patient_info", {}) if isinstance(facts, dict) else {}
            with self._connect() as connection:
                latest_review = connection.execute(
                    "SELECT review_status,reviewer,opinion,recommendation,version,created_at FROM clinical_reviews "
                    "WHERE conversation_id = ? ORDER BY version DESC LIMIT 1",
                    (cid,),
                ).fetchone()
                audio_count = connection.execute(
                    "SELECT COUNT(*) FROM audio_assets WHERE conversation_id = ?", (cid,)
                ).fetchone()[0]
                examination_count = connection.execute(
                    "SELECT COUNT(*) FROM clinical_examinations WHERE conversation_id = ?", (cid,)
                ).fetchone()[0]
                has_voice_analysis = connection.execute(
                    "SELECT 1 FROM analysis_records WHERE conversation_id = ? AND analysis_type = 'voice_analysis' LIMIT 1",
                    (cid,),
                ).fetchone() is not None
            review = dict(latest_review) if latest_review else {
                "review_status": "pending", "reviewer": None, "opinion": None,
                "recommendation": None, "version": 0, "created_at": None,
            }
            output.append({
                **conversation,
                "patient": {"name": patient.get("name", "未登记"), "gender": patient.get("gender", "未说明"), "age": patient.get("age")},
                "quality": quality,
                "review": review,
                "audio_count": audio_count,
                "examination_count": examination_count,
                "review_eligible": has_voice_analysis or review["version"] > 0,
            })
        return output

    def models(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM model_versions ORDER BY created_at DESC").fetchall()
        return [{**dict(row), "metrics": json.loads(row["metrics_json"])} for row in rows]

    def experiments(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("""SELECT experiment_runs.*, model_versions.model_name, model_versions.version
                FROM experiment_runs JOIN model_versions ON model_versions.id = experiment_runs.model_version_id ORDER BY experiment_runs.created_at DESC""").fetchall()
        return [{**dict(row), "metrics": json.loads(row["metrics_json"])} for row in rows]

    def export_rows(self, conversations: list[dict[str, Any]], facts_for: Callable[[str], dict[str, Any]], include_identifiers: bool = False) -> list[dict[str, Any]]:
        rows = []
        for conversation in conversations:
            cid = conversation["conversation_id"]; facts = facts_for(cid); patient = facts.get("patient_info", {}) if isinstance(facts, dict) else {}
            detail = self.case_detail(cid)
            model_results = [item["result"] for item in detail["analyses"] if item["analysis_type"] == "voice_analysis"]
            latest_model = model_results[-1] if model_results else {}
            model_request_id = latest_model.get("request_id")
            matching_corrections = [
                label for label in detail["labels"]
                if label["label_type"] == "voice_diagnosis_correction"
                and label["source"] == f"voice_analysis:{model_request_id}"
            ] if model_request_id else []
            rows.append({"subject_id": hashlib.sha256(cid.encode()).hexdigest()[:16], "name": patient.get("name") if include_identifiers else None,
                         "gender": patient.get("gender"), "age": patient.get("age"), "conversation_id": cid,
                         "created_at": conversation.get("created_at"), "audio_count": len(detail["audio_assets"]),
                         "label_count": len(detail["labels"]), "examination_count": len(detail["examinations"]),
                         "follow_up_count": len(detail["follow_ups"]), "quality_status": detail["quality"]["status"],
                         "quality_issues": "；".join(detail["quality"]["issues"]),
                         "voice_model_request_id": model_request_id,
                         "voice_model_label": latest_model.get("predicted_label"),
                         "voice_model_probability": latest_model.get("predicted_probability"),
                         "voice_corrected_label": matching_corrections[-1]["label_value"] if matching_corrections else None,
                         "voice_correction_count": len(matching_corrections),
                         "review_status": detail["reviews"][-1]["review_status"] if detail["reviews"] else "pending",
                         "review_version": detail["reviews"][-1]["version"] if detail["reviews"] else 0})
        return rows
