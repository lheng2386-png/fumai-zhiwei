import json
import tempfile
import time
import unittest
import uuid
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent.core import InputError, Snapshot
from agent.intent import classify_question
from agent.server import Application, Handler
from agent.store import Store


class IntentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.store = Store(Path(self.temp.name)/'audit.sqlite')
        self.snapshot = Snapshot.load(Path(__file__).resolve().parents[1]/'demo-snapshot.json', 'demo')
        self.model = SimpleNamespace(model='Qwen3-30B-A3B-Instruct-2507', enrich=Mock(side_effect=lambda snapshot, message, result, **kwargs: result))
        self.app = Application(self.snapshot, self.store, 'http://localhost', model=self.model)
        self.headers = {'Origin':'http://localhost', 'X-Fumai-Session':str(uuid.uuid4())}
        self.owner = 'demo:'+self.headers['X-Fumai-Session']

    def tearDown(self):
        self.temp.cleanup()

    def call(self, message, asynchronous=False, **context):
        body = {'message':message, **context}
        if asynchronous:
            body['request_id'] = str(uuid.uuid4())
        return self.app.dispatch('POST', '/agent-api/analysis-tasks' if asynchronous else '/agent-api/analyze', self.headers, body, str(uuid.uuid4()))

    def done(self, task):
        deadline = time.monotonic()+3
        while task['status'] not in {'completed','failed'} and time.monotonic()<deadline:
            time.sleep(.01)
            task = self.app.tasks.get(self.owner, task['task_id'])
        self.assertEqual(task['status'], 'completed')
        return task['result']

    def test_shared_intent_contract(self):
        rows = json.loads((Path(__file__).with_name('intent_cases.json')).read_text(encoding='utf-8'))
        for row in rows:
            with self.subTest(question=row['question'],context=row.get('context')):
                self.assertEqual(classify_question(row['question'], row.get('context',False)), row['intent'])

    def test_information_never_reads_evidence_or_calls_model_in_either_entry(self):
        for asynchronous in (False,True):
            for question in ('你是什么模型？','你能分析哪些设备异常？','怎么使用你？','你好','讲个笑话'):
                with self.subTest(question=question,asynchronous=asynchronous):
                    with patch.object(self.snapshot,'analyze',side_effect=AssertionError('Evidence must not be queried')):
                        result = self.call(question,asynchronous,event_id='not-a-registered-event')
                    if asynchronous:
                        self.assertEqual(result['status'],'completed')
                        result = result['result']
                    self.assertEqual(result['response_type'],'direct')
                    self.assertEqual(result['engine'],'direct')
                    self.assertEqual(result['model_status'],'not_invoked')
                    self.assertEqual(result['events'],[])
                    self.assertEqual(result['evidence_ids'],[])
                    self.assertEqual(result['trace'],[])
                    self.assertNotIn('metadata',result)
                    self.assertEqual(self.store.get_run(self.owner,result['run_id']),result)
        self.model.enrich.assert_not_called()

    def test_identity_uses_runtime_model_name_without_exposing_config(self):
        result = self.call('你是什么模型？')
        self.assertIn(self.model.model,result['summary'])
        self.assertNotIn('已检索',result['summary'])
        self.assertNotIn('候选证据',result['summary'])
        self.app.model = None
        result = self.call('你是什么模型？')
        self.assertIn('未接入大模型',result['summary'])
        self.assertIsNone(result['model_name'])
        self.app.model = SimpleNamespace(enrich=Mock())
        self.assertIn('没有提供',self.call('你是什么模型？')['summary'])

    def test_direct_answers_work_when_all_analysis_slots_are_busy(self):
        for _ in range(3):
            self.assertTrue(self.app.slots.acquire(blocking=False))
        try:
            self.assertEqual(self.call('你是什么模型？')['intent'],'identity')
            self.assertEqual(self.call('使用说明',True)['result']['intent'],'help')
            self.assertFalse(self.app.slots.acquire(blocking=False))
        finally:
            for _ in range(3):self.app.slots.release()
        self.model.enrich.assert_not_called()

    def test_direct_async_retries_are_idempotent_and_owner_scoped(self):
        body = {'message':'你是什么模型？','request_id':str(uuid.uuid4())}
        first = self.app.dispatch('POST','/agent-api/analysis-tasks',self.headers,body,'test')
        second = self.app.dispatch('POST','/agent-api/analysis-tasks',self.headers,body,'test')
        self.assertEqual(first,second)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM runs').fetchone()[0],1)
        with self.assertRaises(InputError):
            self.app.tasks.get('another-user',first['task_id'])

    def test_direct_reply_cannot_generate_a_work_order(self):
        result = self.call('你是什么模型？')
        with self.assertRaisesRegex(InputError,'普通问答'):
            self.app.dispatch('POST','/agent-api/drafts',self.headers,{'run_id':result['run_id'],'event_id':'DEMO-2107-001'},'test')

    def test_device_and_mixed_requests_keep_the_model_flow(self):
        for asynchronous in (False,True):
            result = self.call('分析 inv_07 的异常证据',asynchronous)
            if asynchronous:result=self.done(result)
            self.assertEqual(result['intent'],'maintenance')
            self.assertEqual(result['response_type'],'analysis')
            self.assertTrue(result['events'])
        self.assertEqual(self.model.enrich.call_count,2)
        mixed = self.call('你是什么模型？顺便分析 inv_07 的异常')
        self.assertEqual(mixed['intent'],'maintenance')
        self.assertIn(self.model.model,mixed['introduction'])
        self.assertEqual(self.model.enrich.call_count,3)

    def test_validation_applies_before_direct_routing(self):
        for value in (None,[], '   ', 'x'*2001):
            for asynchronous in (False,True):
                with self.assertRaises(InputError):self.call(value,asynchronous)
        self.model.enrich.assert_not_called()

    def test_http_and_sse_return_direct_answers_without_evidence(self):
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        server.app = self.app
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        base = 'http://127.0.0.1:'+str(server.server_port)
        headers = {**self.headers,'Content-Type':'application/json'}
        try:
            with patch.object(self.snapshot,'analyze',side_effect=AssertionError('Evidence must not be queried')):
                for endpoint in ('analyze','analysis-tasks'):
                    body = {'message':'你是什么模型？','event_id':'DEMO-2107-001'}
                    if endpoint=='analysis-tasks':body['request_id']=str(uuid.uuid4())
                    request = urllib.request.Request(base+'/agent-api/'+endpoint,data=json.dumps(body).encode(),headers=headers)
                    with urllib.request.urlopen(request,timeout=3) as response:
                        data = json.load(response)
                    if endpoint=='analysis-tasks':
                        self.assertEqual(data['status'],'completed')
                        route = base+'/agent-api/analysis-tasks/'+data['task_id']+'/events'
                        with urllib.request.urlopen(urllib.request.Request(route,headers=self.headers),timeout=3) as stream:
                            self.assertEqual(stream.readline(),b'event: done\n')
                            streamed = json.loads(stream.readline()[6:])
                            self.assertEqual(streamed['result'],data['result'])
                        data = data['result']
                    self.assertEqual(data['intent'],'identity')
                    self.assertIn(self.model.model,data['summary'])
            self.model.enrich.assert_not_called()
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
