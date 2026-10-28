from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient

import backend.main as main
from auth_test_utils import auth_client


class ConversationMemoryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "memory.sqlite3"
        patcher = patch.object(main, "DATABASE_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        main.store.initialise()
        self.a = main.store.create_conversation({"chief_complaint": "声音嘶哑", "age": 30, "gender": "女"})
        self.b = main.store.create_conversation({"chief_complaint": "B的独有主诉", "age": 60})

    def add_turn(self, conversation, user, answer="请继续补充病程。"):
        main.store.add_message(conversation, "user", user)
        main.store.add_message(conversation, "assistant", answer)

    def test_cumulative_archive_preserves_corrections_and_ai_provenance(self):
        self.add_turn(self.a, "声音嘶哑三个月", "AI推测：可能与用嗓有关，尚未确认。")
        original = main.store.history(self.a)[0]
        self.add_turn(self.a, "刚才说错了，是三周")
        correction = main.store.history(self.a)[2]
        for number in range(35):
            self.add_turn(self.a, f"随访补充第{number}次")
        summary = main.store.memory(self.a)
        self.assertEqual(len(summary["entries"]), 74)
        entry = next(e for e in summary["entries"] if e["source_message_id"] == correction["id"])
        self.assertEqual(entry["kind"], "correction")
        self.assertEqual(entry["source"], "user_statement")
        self.assertEqual(entry["candidate_previous_message_id"], original["id"])
        self.assertEqual(summary["entries"][1]["source"], "ai_response")
        main.store.add_message(self.a, "user", "请结合更正后的病程分析")
        request = main.build_model_messages(self.a)
        text = json.dumps(request, ensure_ascii=False)
        self.assertIn("刚才说错了，是三周", text)
        self.assertIn("声音嘶哑三个月", text)
        self.assertEqual(main.store.history(self.a)[0]["content"], original["content"])
        self.assertEqual(request[-1], {"role": "user", "content": "请结合更正后的病程分析"})
        self.assertNotIn("B的独有主诉", text)
        # Reopening the database preserves all raw sources and cumulative entries.
        main.store.initialise()
        self.assertEqual(len(main.store.memory(self.a)["entries"]), 75)

    def test_unknown_target_correction_does_not_overwrite_facts(self):
        self.add_turn(self.a, "我吸烟十年，症状持续三个月")
        self.add_turn(self.a, "刚才记错了，是三周")
        entry = main.store.memory(self.a)["entries"][2]
        self.assertEqual(entry["target_status"], "requires_context_confirmation")
        self.assertNotIn("duration", main.store.facts(self.a))
        self.assertIn("三个月", main.store.history(self.a)[0]["content"])

    def test_detection_events_form_revisions_and_cross_session_queries(self):
        self.add_turn(self.a, "A独有陈述")
        self.add_turn(self.b, "B独有陈述")
        main.store.update_smoking_history(self.a, {"smoking_status": "长期吸烟", "smoking_years": 10})
        main.store.update_smoking_history(self.a, {"smoking_status": "长期吸烟", "smoking_years": 3})
        result = main.store.save_detection(self.a, "acoustic_analysis", {
            "request_id": "A-test", "mpt_seconds": 12.7, "warnings": ["A质量提醒"], "disclaimer": "不是临床诊断"})
        main.store.save_detection(self.b, "voice_analysis", {"predicted_label": "B独有检测", "warnings": []})
        self.assertEqual(result["conversation_id"], self.a)
        revisions = [e for e in main.store.events(self.a) if e["kind"] == "smoking_history"]
        self.assertEqual(revisions[-1]["payload"]["previous_value"]["smoking_years"], 10)
        self.assertEqual(revisions[-1]["source"], "user_form")
        context = json.dumps(main.build_model_messages(self.a), ensure_ascii=False)
        self.assertIn("A质量提醒", context)
        self.assertIn("acoustic_measurement", context)
        self.assertNotIn("B独有", context)
        self.assertNotIn("B的独有", context)
        message_id = main.store.history(self.b)[0]["id"]
        with auth_client(main, "patient", [self.a, self.b]) as client:
            self.assertEqual(client.get(f"/api/conversations/{self.a}/messages/{message_id}").status_code, 404)
            response = client.get(f"/api/conversations/{self.b}/messages/{message_id}")
            self.assertEqual(response.json()["content"], "B独有陈述")
            self.assertEqual(client.get(f"/api/conversations/{self.a}/memory").status_code, 200)
            payload = client.get(f"/api/conversations/{self.a}").json()
            self.assertTrue(any(e["kind"] == "acoustic_analysis" for e in payload["events"]))

    def test_detection_requires_existing_conversation_and_persists(self):
        files = {"a": ("a.wav", b"fake", "audio/wav")}
        with patch.object(main, "calculate_acoustic_metrics", return_value={"mpt_seconds": 8.5, "warnings": ["test"]}) as compute:
            with auth_client(main, "patient", [self.a, self.b]) as client:
                self.assertEqual(client.post("/api/voice/acoustic-analyze", files=files).status_code, 422)
                self.assertEqual(client.post("/api/voice/acoustic-analyze", files=files, data={"conversation_id": "missing"}).status_code, 404)
                compute.assert_not_called()
                response = client.post("/api/voice/acoustic-analyze", files=files, data={"conversation_id": self.a})
                self.assertEqual(response.status_code, 200)
                event = main.store.events(self.a)[-1]
                self.assertEqual(event["payload"]["request_id"], response.json()["request_id"])
                self.assertEqual(event["source"], "acoustic_measurement")
                self.assertEqual(len(main.store.events(self.b)), 1)

    def test_unknown_chat_does_not_create_conversation(self):
        with auth_client(main, "patient", [self.a, self.b]) as client:
            response = client.post("/api/chat/stream", json={"message": "三周", "conversation_id": "deleted-id"})
            self.assertEqual(response.status_code, 404)
        with self.assertRaises(KeyError):
            main.store.facts("deleted-id")

    def test_model_receives_context_for_short_reply_once(self):
        self.add_turn(self.a, "声音嘶哑", "症状持续多久了？")
        captured = []
        def handler(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"已结合前文理解三周。"}}]}\n\ndata: [DONE]\n\n')
        original_client = httpx.AsyncClient
        config = {"api_key": "test-key", "base_url": "https://test.invalid/v1", "model": "fake"}
        with patch.object(main, "load_config", return_value=config), patch.object(
            main.httpx, "AsyncClient", side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs)
        ):
            with auth_client(main, "patient", [self.a, self.b]) as client:
                response = client.post("/api/chat/stream", json={"message": "三周", "conversation_id": self.a})
        self.assertEqual(response.status_code, 200)
        self.assertIn("已结合前文", response.text)
        messages = next(item for item in captured if item.get("stream"))["messages"]
        self.assertEqual(sum(m["content"] == "三周" for m in messages), 1)
        self.assertEqual(messages[-2]["content"], "症状持续多久了？")
        self.assertEqual(messages[-1]["content"], "三周")
        self.assertEqual(main.store.history(self.a)[-1]["source"], "ai_response")
        self.assertNotIn(self.a, main.active_chat_turns)

    def test_overlapping_turn_rejected_and_stream_close_releases(self):
        async def run():
            response = await main.chat(main.ChatRequest(message="嗓音问题", conversation_id=self.a))
            await anext(response.body_iterator)
            with self.assertRaises(HTTPException) as caught:
                await main.chat(main.ChatRequest(message="再问一句", conversation_id=self.a))
            self.assertEqual(caught.exception.status_code, 409)
            await response.body_iterator.aclose()
            self.assertNotIn(self.a, main.active_chat_turns)
        asyncio.run(run())

    def test_full_original_survives_excerpt_budget_and_long_history(self):
        original = "很长的用户原文" * 200 + "中部重要细节" + "原文末尾" * 200
        self.add_turn(self.a, original)
        for i in range(45):
            self.add_turn(self.a, f"后续{i}" + "补充内容" * 400, "助手回答" * 1200)
        summary, recent = main.store.context(self.a)
        self.assertLess(len(summary), 15000)
        self.assertLess(sum(len(m["content"]) for m in recent), 35000)
        message_id = main.store.history(self.a)[0]["id"]
        with auth_client(main, "patient", [self.a, self.b]) as client:
            self.assertEqual(client.get(f"/api/conversations/{self.a}/messages/{message_id}").json()["content"], original)
        self.assertTrue(main.store.memory(self.a)["entries"][0]["has_omissions"])

    def test_real_legacy_schema_recovers_orphan_without_reassigning_message(self):
        legacy = self.path.with_name("legacy.sqlite3")
        with closing(sqlite3.connect(legacy)) as connection, connection:
            connection.executescript("""
                CREATE TABLE conversations(id TEXT PRIMARY KEY, summary TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE messages(id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id), role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
                INSERT INTO messages VALUES (7, 'original-orphan-id', 'assistant', '原来的助手消息', '2026-09-03');
            """)
        with patch.object(main, "DATABASE_PATH", legacy):
            main.store.initialise()
            main.store.initialise()
            restored = main.store.history("original-orphan-id")
            self.assertEqual(restored[0]["id"], 7)
            self.assertEqual(restored[0]["content"], "原来的助手消息")
            self.assertEqual(restored[0]["source"], "legacy_assistant")
            self.assertEqual(main.store.facts("original-orphan-id"), {})
            self.assertEqual(len(main.store.events("original-orphan-id")), 1)
            with closing(sqlite3.connect(legacy)) as connection:
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_legacy_migration_is_idempotent_and_deletion_cleans_archive(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE conversations SET summary = '旧截断摘要', facts_json = ? WHERE id = ?",
                               (json.dumps({"smoking_history": {"smoking_status": "从不吸烟"}}), self.a))
        self.add_turn(self.a, "原始病史")
        main.store.initialise()
        count = len(main.store.events(self.a))
        main.store.initialise()
        self.assertEqual(len(main.store.events(self.a)), count)
        self.assertEqual(main.store.memory(self.a)["entries"][0]["excerpt"], "原始病史")
        main.store.save_detection(self.a, "voice_analysis", {"predicted_label": "test"})
        main.store.delete(self.a)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (self.a,)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM conversation_events WHERE conversation_id = ?", (self.a,)).fetchone()[0], 0)
        self.assertEqual(main.store.facts(self.b)["patient_info"]["age"], 60)


if __name__ == "__main__":
    unittest.main()
