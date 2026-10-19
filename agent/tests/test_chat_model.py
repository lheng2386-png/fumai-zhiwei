import copy
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent.core import Snapshot
from agent.model import ChatCompletionsModel


class ChatModelTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = Snapshot.load(Path(__file__).resolve().parents[1] / 'demo-snapshot.json', 'demo')
        self.event = next(iter(self.snapshot.events))
        self.endpoint = 'https://models.example/v1/chat/completions'

    def analysis(self, evidence_ids=None):
        return {'summary': '需要结合现场资料复核。', 'hypotheses': [], 'checks': [],
                'unknowns': ['尚缺少现场告警。'], 'evidence_ids': evidence_ids if evidence_ids is not None else [self.event]}

    def final_response(self, analysis=None, finish='stop'):
        return {'choices': [{'finish_reason': finish, 'message': {'role': 'assistant',
                'content': json.dumps(analysis if analysis is not None else self.analysis(), ensure_ascii=False)}}]}

    def tool_call(self, name='get_event', arguments=None, call_id='call-1'):
        return {'id': call_id, 'type': 'function', 'function': {'name': name,
                'arguments': json.dumps(arguments if arguments is not None else {'event_id': self.event})}}

    def tool_response(self, calls=None):
        return {'choices': [{'finish_reason': 'tool_calls', 'message': {'role': 'assistant',
                'content': None, 'tool_calls': calls if calls is not None else [self.tool_call()]}}]}

    def run_model(self, transport):
        model = ChatCompletionsModel('test-key', 'qwen-test', self.endpoint, transport)
        return model.enrich(self.snapshot, '请核对候选事件', self.snapshot.analyze('test', self.event))

    def test_tool_round_trip_preserves_evidence_and_protocol(self):
        requests = []
        answers = [self.tool_response(), self.final_response()]

        def transport(payload):
            requests.append(copy.deepcopy(payload))
            return answers[len(requests) - 1]

        result = self.run_model(transport)
        self.assertEqual(result['engine'], 'chat_completions')
        self.assertEqual(result['model_status'], 'connected')
        self.assertEqual(result['model_name'], 'qwen-test')
        self.assertEqual(result['events'][0], self.snapshot.event(self.event))
        self.assertEqual(requests[0]['messages'][0]['role'], 'system')
        self.assertIn('evidence_ids', requests[0]['messages'][0]['content'])
        self.assertEqual(requests[0]['response_format'], {'type': 'json_object'})
        self.assertFalse(requests[0]['parallel_tool_calls'])
        self.assertEqual(requests[0]['tools'][0]['function']['name'], 'list_events')
        messages = requests[1]['messages']
        self.assertEqual([item['role'] for item in messages], ['system', 'user', 'assistant', 'tool'])
        self.assertEqual(messages[-1]['tool_call_id'], 'call-1')
        self.assertEqual(json.loads(messages[-1]['content'])['event_id'], self.event)
        self.assertNotIn('test-key', json.dumps(requests))

    def test_queries_expand_allowed_citations(self):
        other = next(key for key in self.snapshot.events if key != self.event)
        responses = iter([self.tool_response([self.tool_call(arguments={'event_id': other})]),
                          self.final_response(self.analysis([other]))])
        result = self.run_model(lambda _: next(responses))
        self.assertIn(other, result['evidence_ids'])
        self.assertTrue(any(event['event_id'] == other for event in result['events']))

    def test_runbook_tool_without_event_is_valid(self):
        responses = iter([self.tool_response([self.tool_call('get_runbook', {'evidence': 'insufficient_peers'})]),
                          self.final_response()])
        result = self.run_model(lambda _: next(responses))
        self.assertEqual(result['trace'][-1]['tool'], 'get_runbook')
        self.assertEqual(result['trace'][-1]['result_count'], 1)

    def test_unknown_tool_and_extra_arguments_rejected(self):
        cases = [self.tool_call('shutdown', {}),
                 self.tool_call(arguments={'event_id': self.event, 'url': 'https://evil.example'}),
                 self.tool_call(arguments={'event_id': 'missing'})]
        for call in cases:
            with self.subTest(call=call), self.assertRaises(ValueError):
                self.run_model(lambda _: self.tool_response([call]))

    def test_fabricated_and_missing_citations_rejected(self):
        for citations in (['invented-event'], []):
            with self.subTest(citations=citations), self.assertRaises(ValueError):
                self.run_model(lambda _: self.final_response(self.analysis(citations)))

    def test_truncated_non_json_and_wrong_shapes_rejected(self):
        bad = self.final_response()
        bad['choices'][0]['message']['content'] = '```json\n{}\n```'
        for response in (self.final_response(finish='length'), self.final_response(finish='content_filter'),
                         bad, self.final_response([]), self.final_response({'summary': 'missing fields'}),
                         {'error': {'message': 'supplier private details'}},
                         {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': None}}]}):
            with self.subTest(response=response), self.assertRaises(ValueError) as cm:
                self.run_model(lambda _: response)
            self.assertNotIn('supplier private details', str(cm.exception))

    def test_duplicate_json_fields_rejected(self):
        response = self.final_response()
        content = response['choices'][0]['message']['content']
        response['choices'][0]['message']['content'] = content[:-1] + ',"evidence_ids":["invented"]}'
        with self.assertRaises(ValueError):
            self.run_model(lambda _: response)

    def test_excess_tool_calls_rejected_before_tools_execute(self):
        calls = [self.tool_call(call_id=f'call-{i}') for i in range(7)]
        with patch.object(self.snapshot, 'tool', wraps=self.snapshot.tool) as tool:
            with self.assertRaises(ValueError):
                self.run_model(lambda _: self.tool_response(calls))
            tool.assert_not_called()

    def test_third_round_forbids_more_tool_calls(self):
        requests = []

        def transport(payload):
            requests.append(copy.deepcopy(payload))
            return self.tool_response([self.tool_call(call_id=f'call-{len(requests)}')])

        with self.assertRaises(ValueError):
            self.run_model(transport)
        self.assertEqual(len(requests), 3)
        self.assertNotIn('tools', requests[-1])
        self.assertNotIn('tool_choice', requests[-1])
        self.assertEqual(requests[-1]['messages'][-1]['role'], 'system')

    def test_repeated_tool_call_ids_rejected(self):
        with self.assertRaises(ValueError):
            self.run_model(lambda _: self.tool_response())

    def test_time_budget_shared_between_rounds(self):
        now = [100.0]
        requests = []

        def transport(payload):
            requests.append(payload)
            now[0] += 30
            return self.tool_response() if len(requests) == 1 else self.final_response()

        with patch('agent.model.time.monotonic', side_effect=lambda: now[0]):
            with self.assertRaises(TimeoutError):
                self.run_model(transport)
        self.assertEqual(len(requests), 2)

    def test_time_budget_includes_tool_execution(self):
        now = [100.0]
        calls = []
        original = self.snapshot.tool

        def slow_tool(name, args):
            now[0] += 56
            return original(name, args)

        def transport(payload):
            calls.append(payload)
            return self.tool_response()

        with patch('agent.model.time.monotonic', side_effect=lambda: now[0]), patch.object(self.snapshot, 'tool', side_effect=slow_tool):
            with self.assertRaises(TimeoutError):
                self.run_model(transport)
        self.assertEqual(len(calls), 1)

    def test_endpoint_must_be_https_without_redirect_targets(self):
        for endpoint in ('http://models.example/chat', 'https://user@models.example/chat',
                         'https://models.example/chat?api_key=x', 'https://models.example/chat#fragment'):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                ChatCompletionsModel('test-key', 'qwen-test', endpoint)

    def test_network_uses_remaining_budget_and_hides_provider_errors(self):
        model = ChatCompletionsModel('test-key', 'qwen-test', self.endpoint)
        opener = Mock()
        opener.open.side_effect = OSError('supplier-private-error test-key')
        with patch('agent.model.time.monotonic', return_value=100), patch('agent.model.urllib.request.build_opener', return_value=opener):
            with self.assertRaises(ValueError) as cm:
                model.request({'model': 'qwen-test'}, deadline=112)
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 12)
        self.assertNotIn('test-key', str(cm.exception))
        self.assertNotIn('supplier-private', str(cm.exception))


if __name__ == '__main__':
    unittest.main()
