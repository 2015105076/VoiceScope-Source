"""Optional MySQL 8 mirror for durable patient/session/audio retrieval.

Set VOICESCOPE_MYSQL_URL=mysql://user:password@host:3306/voicescope and run
backend/mysql_schema.sql. SQLite remains the local fallback when unset.
"""
from __future__ import annotations
import hashlib, json, os, uuid
from datetime import datetime, timezone
from .mysql_config import MySQLSettings, load_mysql_settings

class MySQLMirror:
    def __init__(self):
        self.last_error: str | None = None
        try:
            self.settings = load_mysql_settings()
        except RuntimeError as error:
            self.settings = MySQLSettings(None, 3306, None, "", None, "invalid")
            self.last_error = str(error)
    @property
    def enabled(self): return self.settings.enabled
    def _connect(self):
        if not self.enabled:
            return None
        try:
            import pymysql
        except ImportError as error:
            raise RuntimeError("配置了 MySQL，但未安装 PyMySQL") from error
        return pymysql.connect(host=self.settings.host, port=self.settings.port, user=self.settings.user,
            password=self.settings.password.encode("utf-8"), database=self.settings.database, charset="utf8mb4", autocommit=False)
    @staticmethod
    def _hex(value): return bytes.fromhex(value.replace('-', ''))
    def _run(self, callback):
        if not self.enabled: return None
        try:
            connection=self._connect()
            with connection.cursor() as cursor: result=callback(cursor)
            connection.commit(); self.last_error=None; return result
        except Exception as error:
            self.last_error=type(error).__name__ + ": " + str(error)[:180]
            # MySQL is an optional mirror. Keep the local SQLite transaction usable
            # and expose the connection problem through /api/health.
            return None
        finally:
            if "connection" in locals() and connection is not None:
                connection.close()
    def create_conversation(self, conversation_id, patient):
        now=datetime.now(timezone.utc).replace(tzinfo=None)
        pid=uuid.uuid4().bytes; cid=self._hex(conversation_id)
        def run(cursor):
            cursor.execute("INSERT INTO patients(id,name,age,gender,location,chief_complaint,created_at,updated_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)", (pid,patient['name'],patient['age'],patient['gender'],patient['location'],patient['chief_complaint'],now,now))
            cursor.execute("INSERT INTO conversations(id,patient_id,case_points_json,summary_json,created_at,updated_at) VALUES(%s,%s,%s,%s,%s,%s)", (cid,pid,'{}','{}',now,now))
        self._run(run)
    def add_message(self, conversation_id, role, content, source):
        now=datetime.now(timezone.utc).replace(tzinfo=None); cid=self._hex(conversation_id)
        self._run(lambda cursor: (cursor.execute("INSERT INTO messages(conversation_id,role,source,content,created_at) VALUES(%s,%s,%s,%s,%s)",(cid,role,source,content,now)), cursor.execute("UPDATE conversations SET updated_at=%s WHERE id=%s",(now,cid))))
    def save_case_points(self, conversation_id, state):
        now=datetime.now(timezone.utc).replace(tzinfo=None); cid=self._hex(conversation_id)
        def run(cursor):
            cursor.execute("UPDATE conversations SET case_points_json=%s,updated_at=%s WHERE id=%s",(json.dumps(state,ensure_ascii=False),now,cid))
            for key,point in state.get('points',{}).items():
                cursor.execute("INSERT INTO case_points(conversation_id,point_key,value,status,evidence_json,updated_at) VALUES(%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE value=VALUES(value),status=VALUES(status),evidence_json=VALUES(evidence_json),updated_at=VALUES(updated_at)",(cid,key,point.get('value',''),point.get('status','missing'),json.dumps(point.get('evidence',[]),ensure_ascii=False),now))
        self._run(run)
    def save_audio(self, conversation_id, audio, vowel_key=None):
        data=audio.content; aid=uuid.uuid4().bytes; now=datetime.now(timezone.utc).replace(tzinfo=None); cid=self._hex(conversation_id)
        self._run(lambda cursor: cursor.execute("INSERT INTO audio_files(id,conversation_id,vowel_key,original_filename,mime_type,size_bytes,sha256,storage_key,audio_data,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",(aid,cid,vowel_key,audio.filename,'application/octet-stream',len(data),hashlib.sha256(data).hexdigest(),f"mysql://audio/{aid.hex()}",data,now)))
        return aid
    def save_result(self, conversation_id, kind, result):
        now=datetime.now(timezone.utc).replace(tzinfo=None); cid=self._hex(conversation_id); rid=uuid.uuid4().bytes
        analysis='acoustic' if kind=='acoustic_analysis' else 'research_model'
        self._run(lambda cursor: cursor.execute("INSERT INTO voice_analysis_results(id,conversation_id,analysis_type,predicted_label,predicted_probability,result_json,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)",(rid,cid,analysis,result.get('predicted_label'),result.get('predicted_probability'),json.dumps(result,ensure_ascii=False),now)))
    def complete(self, conversation_id):
        self._run(lambda cursor: cursor.execute("UPDATE conversations SET status='completed',updated_at=UTC_TIMESTAMP(6) WHERE id=%s", (self._hex(conversation_id),)))
    def delete(self, conversation_id): self._run(lambda cursor: cursor.execute("DELETE FROM conversations WHERE id=%s",(self._hex(conversation_id),)))
    def search(self,*args,**kwargs): return None
