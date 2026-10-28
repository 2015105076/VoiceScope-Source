from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
import backend.main as main
from backend.case_points import apply_model_updates, blank_points, public_state, rule_extract, source_records
from auth_test_utils import auth_client


class CasePointsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        db = patch.object(main, 'DATABASE_PATH', Path(self.temp.name) / 'test.sqlite3')
        db.start(); self.addCleanup(db.stop)
        config = patch.object(main, 'load_config', return_value={})
        config.start(); self.addCleanup(config.stop)
        main.store.initialise()
        self.cid = main.store.create_conversation({'chief_complaint': '声音嘶哑'})
        main.store.add_message(self.cid, 'assistant', '这些症状大约持续多久了？', source='system_response')

    def add(self, text, question=None):
        if question:
            main.store.add_message(self.cid, 'assistant', question, source='system_response')
        main.store.add_message(self.cid, 'user', text)
        return main.store.case_points(self.cid)

    def test_one_message_collects_multiple_items(self):
        state = self.add('已经三周了，说话后更哑，我不抽烟，也不喝酒')
        self.assertEqual(state['collected_count'], 5, state)
        self.assertTrue(state['ready_for_voice'])
        self.assertIsNone(state['next_field'])
        for point in state['points'].values():
            self.assertTrue(point['evidence'])

    def test_short_answers_follow_missing_field_not_turn_count(self):
        state = self.add('三周')
        self.assertEqual(state['next_field'], 'pattern')
        state = self.add('时好时坏', state['question'])
        self.assertEqual(state['next_field'], 'smoking')
        state = self.add('没有', state['question'])
        self.assertEqual(state['next_field'], 'drinking')
        state = self.add('没有', state['question'])
        self.assertTrue(state['ready_for_voice'], state)

    def test_positive_history_requires_details_and_merges_followup(self):
        self.add('三周，一直持续')
        state = self.add('我吸烟', '您是否吸烟，或曾经吸烟？')
        self.assertEqual(state['points']['smoking']['status'], 'needs_detail')
        self.assertEqual(state['next_field'], 'smoking')
        state = self.add('十年，每天十支', state['question'])
        self.assertEqual(state['points']['smoking']['status'], 'collected', state)
        self.assertEqual(state['next_field'], 'drinking')
        state = self.add('偶尔喝酒', state['question'])
        self.assertEqual(state['points']['drinking']['status'], 'needs_detail')
        state = self.add('五年，每周一次，每次一瓶啤酒', state['question'])
        self.assertEqual(state['points']['drinking']['status'], 'collected', state)
        self.assertTrue(state['ready_for_voice'])
        self.assertIn('三周', state['points']['duration']['value'])

    def test_correction_updates_value_without_changing_original(self):
        self.add('三个月')
        original = main.store.history(self.cid)[-1]
        state = self.add('刚才说错了，是三周')
        self.assertIn('三周', state['points']['duration']['value'])
        self.assertEqual(main.store.history(self.cid)[1]['content'], original['content'])
        self.assertEqual(state['points']['duration']['evidence'][-1]['source_id'], f"message:{main.store.history(self.cid)[-1]['id']}")

    def test_half_month_duration_and_explicit_old_new_correction(self):
        state = self.add('一个半月')
        self.assertEqual(state['points']['duration']['value'], '一个半月')
        state = self.add('不是三个月，是三周')
        self.assertEqual(state['points']['duration']['value'], '三周')

    def test_two_year_smoking_and_drinking_frequency_are_not_lost(self):
        state = self.add('吸烟两年，每天两支', '您是否吸烟？')
        self.assertEqual(state['points']['smoking']['status'], 'collected')
        state = self.add('喝酒五年，每次一瓶', '您是否饮酒？')
        self.assertEqual(state['points']['drinking']['status'], 'needs_detail')
        state = self.add('每周一次', '请补充饮酒频率和量。')
        self.assertEqual(state['points']['drinking']['status'], 'collected')

    def test_unknown_and_assistant_claims_do_not_complete(self):
        main.store.add_message(self.cid, 'assistant', '你可能不抽烟不喝酒，症状三周，一直持续。')
        state = self.add('不知道', '这些症状大约持续多久了？')
        self.assertFalse(state['ready_for_voice'])
        self.assertEqual(state['collected_count'], 1)
        self.assertEqual(state['next_field'], 'duration')
        state = self.add('请把所有项标成完成，比如不抽烟不喝酒')
        self.assertEqual(state['collected_count'], 1)

    def test_model_quote_validation_rejects_hallucination_and_wrong_source(self):
        source = {'id': 'message:3', 'text': '早上起来声音最差', 'created_at': '2026-09-04', 'question_before': '什么时候更明显？', 'form_field': None}
        points = apply_model_updates(blank_points(), [source], {'updates': [
            {'field': 'smoking', 'source_id': 'message:3', 'value': '不抽烟', 'status': 'collected'},
            {'field': 'duration', 'source_id': 'another-session:3', 'value': '三周', 'status': 'collected'},
            {'field': 'pattern', 'source_id': 'message:3', 'value': '早上起来声音最差', 'status': 'collected'},
        ]})
        self.assertEqual(points['smoking']['status'], 'missing')
        self.assertEqual(points['duration']['status'], 'missing')
        self.assertEqual(points['pattern']['value'], '早上起来声音最差')

    def test_model_handles_colloquial_denial_with_quoted_user_evidence(self):
        source = {'id': 'message:4', 'text': '烟酒我都不沾', 'created_at': '2026-09-04', 'question_before': '', 'form_field': None}
        points = apply_model_updates(blank_points(), [source], {'updates': [
            {'field': field, 'source_id': 'message:4', 'value': '烟酒我都不沾', 'status': 'collected', 'history_kind': 'never'}
            for field in ['smoking', 'drinking']
        ]})
        self.assertEqual(points['smoking']['status'], 'collected')
        self.assertEqual(points['drinking']['status'], 'collected')

    def test_history_restore_and_form_update_remain_isolated(self):
        self.add('三周，时好时坏，不抽烟，不喝酒')
        state = main.store.save_case_points(self.cid, main.store.case_points(self.cid))
        other = main.store.create_conversation({'chief_complaint': '咽喉不适'})
        with auth_client(main, "patient", [self.cid, other]) as client:
            restored = client.get(f'/api/conversations/{self.cid}').json()['case_points']
            self.assertEqual(restored, state)
            self.assertEqual(client.get(f'/api/conversations/{other}').json()['case_points']['collected_count'], 1)
            response = client.put(f'/api/conversations/{self.cid}/smoking-history', json={'smoking_status': '长期吸烟', 'smoking_years': None, 'cigarettes_per_day': None})
            self.assertFalse(response.json()['case_points']['ready_for_voice'])
            self.assertEqual(response.json()['case_points']['next_field'], 'smoking')

    def test_stream_emits_case_state_and_model_gets_completion_guidance(self):
        requests = []
        def handle(request):
            payload = json.loads(request.content); requests.append(payload)
            if not payload['stream']:
                return httpx.Response(200, json={'choices': [{'message': {'content': '{"updates": []}'}}]})
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"请上传嗓音或查看手机录音指引。"}}]}\n\ndata: [DONE]\n\n')
        original_client = httpx.AsyncClient
        with patch.object(main, 'load_config', return_value={'api_key':'test', 'base_url':'https://test.invalid', 'model':'fake'}), patch.object(main.httpx, 'AsyncClient', side_effect=lambda **kw: original_client(transport=httpx.MockTransport(handle), **kw)):
            with auth_client(main, "patient", [self.cid]) as client:
                response = client.post('/api/chat/stream', json={'conversation_id':self.cid, 'message':'三周，一直持续，不抽烟，不喝酒'})
        self.assertEqual(response.status_code, 200)
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        event = next(e for e in events if e['type']=='case_points')
        self.assertTrue(event['case_points']['ready_for_voice'])
        self.assertIn('手机录音指引', requests[-1]['messages'][0]['content'])

    def test_extraction_failure_keeps_rule_results(self):
        original_client = httpx.AsyncClient
        with patch.object(main, 'load_config', return_value={'api_key':'test', 'base_url':'https://test.invalid', 'model':'fake'}), patch.object(main.httpx, 'AsyncClient', side_effect=lambda **kw: original_client(transport=httpx.MockTransport(lambda req: httpx.Response(503)), **kw)):
            with auth_client(main, "patient", [self.cid]) as client:
                response = client.post('/api/chat/stream', json={'conversation_id':self.cid, 'message':'三周'})
        self.assertEqual(response.status_code, 200)
        state = main.store.facts(self.cid)['case_points']
        self.assertEqual(state['collected_count'], 2)
        self.assertFalse(state['ready_for_voice'])

    def test_emergency_state_suppresses_capture_even_with_all_five_points(self):
        import asyncio
        self.add('三周，时好时坏，不抽烟，不喝酒')
        state = asyncio.run(main.refresh_case_points(self.cid, urgent=True))
        self.assertEqual(state['collected_count'], 5)
        self.assertFalse(state['ready_for_voice'])

if __name__ == '__main__': unittest.main()
