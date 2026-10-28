"""One-way, resumable SQLite -> MySQL research-data migration.

Run --dry-run first. --apply requires backend/mysql_schema.sql to be installed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

from .mysql_config import load_mysql_settings


def identifier(value: str) -> bytes:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"voicescope:{value}").bytes


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def summary(source: Path) -> dict[str, int]:
    with sqlite3.connect(source) as connection:
        tables = ["conversations", "messages", "conversation_events", "audio_assets", "analysis_records", "clinical_labels", "clinical_examinations", "follow_up_records", "model_versions", "experiment_runs"]
        return {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] if table_exists(connection, table) else 0 for table in tables}


def migrate(source: Path) -> dict[str, int]:
    settings = load_mysql_settings()
    if not settings.enabled:
        raise RuntimeError("请设置 VOICESCOPE_MYSQL_HOST、USER、PASSWORD、DATABASE 后再执行 --apply")
    try:
        import pymysql
    except ImportError as error:
        raise RuntimeError("请先安装 PyMySQL：.venv/bin/pip install -r backend/requirements.txt") from error
    counts = summary(source)
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
    mysql = pymysql.connect(host=settings.host, port=settings.port, user=settings.user, password=settings.password.encode("utf-8"), database=settings.database, charset="utf8mb4", autocommit=False)
    local = sqlite3.connect(source); local.row_factory = sqlite3.Row
    try:
        with mysql.cursor() as cursor:
            if cursor.execute("SELECT 1 FROM sqlite_migration_runs WHERE source_sha256=%s", (fingerprint,)):
                return {**counts, "already_imported": 1}
            for row in local.execute("SELECT * FROM conversations ORDER BY created_at"):
                facts = json.loads(row["facts_json"] or "{}") if "facts_json" in row.keys() else {}
                patient = facts.get("patient_info", {})
                patient_id = identifier("patient:" + row["id"])
                cursor.execute("""INSERT INTO patients(id,name,age,gender,location,chief_complaint,created_at,updated_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE updated_at=VALUES(updated_at)""",
                    (patient_id, str(patient.get("name", "未登记患者")), int(patient.get("age", 0) or 0), str(patient.get("gender", "其他/不便透露")), str(patient.get("location", "未说明")), str(patient.get("chief_complaint", "待补充")), row["created_at"], row["updated_at"]))
                cursor.execute("""INSERT INTO conversations(id,patient_id,case_points_json,summary_json,created_at,updated_at)
                    VALUES(%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE case_points_json=VALUES(case_points_json),summary_json=VALUES(summary_json),updated_at=VALUES(updated_at)""",
                    (identifier(row["id"]), patient_id, json.dumps(facts.get("case_points", {}), ensure_ascii=False), row["summary"] or "{}", row["created_at"], row["updated_at"]))
                for message in local.execute("SELECT role,source,content,created_at FROM messages WHERE conversation_id=? ORDER BY id", (row["id"],)):
                    role = message["role"] if message["role"] in {"user", "assistant"} else "system"
                    cursor.execute("INSERT INTO messages(conversation_id,role,source,content,created_at) VALUES(%s,%s,%s,%s,%s)", (identifier(row["id"]), role, message["source"], message["content"], message["created_at"]))
                if table_exists(local, "conversation_events"):
                    for event in local.execute("SELECT source,kind,payload_json,created_at FROM conversation_events WHERE conversation_id=? ORDER BY id", (row["id"],)):
                        cursor.execute("INSERT INTO conversation_events(conversation_id,source,event_type,payload_json,created_at) VALUES(%s,%s,%s,%s,%s)", (identifier(row["id"]), event["source"], event["kind"], event["payload_json"], event["created_at"]))
                if table_exists(local, "audio_assets"):
                    for asset in local.execute("SELECT * FROM audio_assets WHERE conversation_id=?", (row["id"],)):
                        content = Path(asset["storage_path"]).read_bytes() if Path(asset["storage_path"]).is_file() else None
                        cursor.execute("INSERT INTO audio_files(id,conversation_id,vowel_key,original_filename,mime_type,size_bytes,sha256,storage_key,audio_data,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE audio_data=COALESCE(VALUES(audio_data),audio_data)", (identifier("audio:"+asset["id"]), identifier(row["id"]), asset["vowel_key"], asset["original_filename"], asset["media_type"], asset["size_bytes"], asset["sha256"], asset["storage_path"], content, asset["created_at"]))
                for table, columns, sql in [
                    ("clinical_labels", "id,label_type,label_value,label_status,annotator,source,notes,created_at", "INSERT INTO clinical_labels(id,conversation_id,label_type,label_value,label_status,annotator,source,notes,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)"),
                    ("clinical_examinations", "id,examination_type,examination_date,findings,clinician,structured_json,created_at", "INSERT INTO clinical_examinations(id,conversation_id,examination_type,examination_date,findings,clinician,structured_json,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)"),
                    ("follow_up_records", "id,follow_up_date,outcome_status,outcome_notes,recorder,created_at", "INSERT INTO follow_up_records(id,conversation_id,follow_up_date,outcome_status,outcome_notes,recorder,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)")]:
                    if table_exists(local, table):
                        for item in local.execute(f"SELECT {columns} FROM {table} WHERE conversation_id=?", (row["id"],)):
                            values = list(item)
                            cursor.execute(sql, (identifier(table+":"+values.pop(0)), identifier(row["id"]), *values))
                if table_exists(local, "analysis_records"):
                    for item in local.execute("SELECT id,audio_asset_ids_json,analysis_type,result_json,created_at FROM analysis_records WHERE conversation_id=?", (row["id"],)):
                        cursor.execute("INSERT INTO analysis_records(id,conversation_id,audio_asset_ids_json,analysis_type,result_json,created_at) VALUES(%s,%s,%s,%s,%s,%s)", (identifier("analysis:"+item["id"]), identifier(row["id"]), item["audio_asset_ids_json"], item["analysis_type"], item["result_json"], item["created_at"]))
                if table_exists(local, "data_quality_checks"):
                    item = local.execute("SELECT status,issues_json,checked_at FROM data_quality_checks WHERE conversation_id=?", (row["id"],)).fetchone()
                    if item:
                        cursor.execute("INSERT INTO data_quality_checks(conversation_id,status,issues_json,checked_at) VALUES(%s,%s,%s,%s)", (identifier(row["id"]), item["status"], item["issues_json"], item["checked_at"]))
            if table_exists(local, "model_versions"):
                for item in local.execute("SELECT * FROM model_versions"):
                    cursor.execute("INSERT INTO model_versions(id,model_name,version,model_type,artifact_reference,artifact_sha256,metrics_json,status,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)", (identifier("model:"+item["id"]), item["model_name"], item["version"], item["model_type"], item["artifact_reference"], item["artifact_sha256"], item["metrics_json"], item["status"], item["created_at"]))
            if table_exists(local, "experiment_runs"):
                for item in local.execute("SELECT * FROM experiment_runs"):
                    cursor.execute("INSERT INTO experiment_runs(id,model_version_id,dataset_version,split_strategy,random_seed,run_status,metrics_json,notes,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)", (identifier("experiment:"+item["id"]), identifier("model:"+item["model_version_id"]), item["dataset_version"], item["split_strategy"], item["random_seed"], item["run_status"], item["metrics_json"], item["notes"], item["created_at"]))
        with mysql.cursor() as cursor:
            cursor.execute("INSERT INTO sqlite_migration_runs(source_sha256,source_name,imported_at) VALUES(%s,%s,UTC_TIMESTAMP(6))", (fingerprint, str(source)))
        mysql.commit()
    except Exception:
        mysql.rollback(); raise
    finally:
        local.close(); mysql.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", type=Path, default=Path(__file__).parent / "data" / "voicescope.sqlite3")
    parser.add_argument("--apply", action="store_true", help="write to configured MySQL; omitted means dry run")
    args = parser.parse_args()
    if not args.sqlite.is_file():
        raise SystemExit(f"SQLite 文件不存在：{args.sqlite}")
    counts = migrate(args.sqlite) if args.apply else summary(args.sqlite)
    print(json.dumps({"mode": "apply" if args.apply else "dry_run", "counts": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
