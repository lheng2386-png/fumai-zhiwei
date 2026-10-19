import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
import sqlite3
from contextlib import contextmanager
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from pathlib import Path

from agent.core import InputError, Snapshot, utcnow
from agent.server import Application, Handler, HTTPError
from agent.store import Store
from agent.tasks import AnalysisTasks


class HeldModel:
    def __init__(self):
        self.release = threading.Event()
        self.entered = threading.Event()
        self.calls = 0

    def enrich(self, snapshot, question, result, progress=None):
        self.calls += 1
        if progress:
            progress('model_round_1')
        self.entered.set()
        if not self.release.wait(5):
            raise TimeoutError('test model timed out')
        return result


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.store = Store(Path(self.temp.name) / 'audit.sqlite')
        self.model = HeldModel()
        self.snapshot = Snapshot.load(Path(__file__).resolve().parents[1] / 'demo-snapshot.json', 'demo')
        self.app = Application(self.snapshot, self.store, 'http://localhost', model=self.model)
        self.headers = {'Origin': 'http://localhost', 'X-Fumai-Session': str(uuid.uuid4())}
        self.owner = 'demo:' + self.headers['X-Fumai-Session']
        self.ids = []

    def tearDown(self):
        self.model.release.set()
        for task_id in self.ids:
            self.wait_done(task_id)
        self.temp.cleanup()

    def submit(self, body=None):
        body = body or {'message': '分析 inv_07', 'request_id': str(uuid.uuid4())}
        task = self.app.dispatch('POST', '/agent-api/analysis-tasks', self.headers, body, 'client')
        self.ids.append(task['task_id'])
        return task

    def wait_done(self, task_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            task = self.app.tasks.get(self.owner, task_id)
            if task['status'] in {'completed', 'failed'}:
                return task
            time.sleep(.01)
        self.fail('task failed to finish')

    def test_submit_returns_before_model_and_persists_result(self):
        started = time.monotonic()
        task = self.submit()
        self.assertLess(time.monotonic() - started, 1)
        self.assertIn(task['status'], {'queued', 'running'})
        self.assertTrue(self.model.entered.wait(1))
        self.model.release.set()
        done = self.wait_done(task['task_id'])
        self.assertEqual(done['status'], 'completed')
        self.assertEqual(done['result'], self.store.get_run(self.owner, done['result']['run_id']))
        self.assertIsInstance(done['elapsed_seconds'], int)

    def test_idempotency_and_payload_conflict(self):
        body = {'message': '分析 inv_07', 'request_id': str(uuid.uuid4())}
        first = self.submit(body)
        second = self.submit(body)
        self.assertEqual(first['task_id'], second['task_id'])
        self.assertTrue(self.model.entered.wait(1))
        self.assertEqual(self.model.calls, 1)
        with self.assertRaises(InputError):
            self.submit({**body, 'message': '另一个问题'})
        self.model.release.set()
        self.wait_done(first['task_id'])
        self.assertEqual(self.submit(body)['task_id'], first['task_id'])

    def test_task_owner_isolation_and_bad_inputs(self):
        task = self.submit()
        with self.assertRaises(InputError):
            self.app.tasks.get('someone-else', task['task_id'])
        for body in [{'message': 'test'}, {'message': 'test', 'request_id': 'bad'},
                     {'message': '', 'request_id': str(uuid.uuid4())},
                     {'message': 'test', 'request_id': str(uuid.uuid4()), 'url': 'https://example.com'}]:
            with self.assertRaises(InputError):
                self.submit(body)

    def test_bounded_workers_and_duplicate_at_capacity(self):
        body = {'message': 'test', 'request_id': str(uuid.uuid4())}
        first = self.submit(body)
        self.submit()
        self.submit()
        with self.assertRaises(HTTPError) as error:
            self.submit()
        self.assertEqual(error.exception.status, 503)
        self.assertEqual(self.submit(body)['task_id'], first['task_id'])
        self.model.release.set()
        for task_id in self.ids:
            self.wait_done(task_id)
        self.assertEqual(self.wait_done(self.submit()['task_id'])['status'], 'completed')

    def test_polling_budget_is_separate(self):
        task = self.submit()
        for _ in range(35):
            got = self.app.dispatch('GET', '/agent-api/analysis-tasks/' + task['task_id'], self.headers, {}, 'client')
            self.assertEqual(got['task_id'], task['task_id'])
        self.assertTrue(self.app.dispatch('GET', '/agent-api/bootstrap', self.headers, {}, 'client'))

    def test_model_failure_is_explicit_fallback(self):
        class BrokenModel:
            def enrich(self, *args, **kwargs):
                raise TimeoutError('API_KEY_should_never_appear')
        self.app.model = BrokenModel()
        done = self.wait_done(self.submit()['task_id'])
        self.assertEqual(done['status'], 'completed')
        self.assertEqual(done['result']['model_status'], 'failed_fallback')
        self.assertNotIn('API_KEY_should_never_appear', json.dumps(done))

    def test_restart_marks_pending_failed_without_replaying(self):
        task_id = str(uuid.uuid4())
        with self.store.connect() as db:
            db.execute('INSERT INTO analysis_tasks (id,owner,request_id,request_json,status,stage,created_at,created_epoch) VALUES(?,?,?,?,?,?,?,?)',
                       (task_id, self.owner, str(uuid.uuid4()), '{"message":"test"}', 'running', 'model', utcnow(), time.time()))
        tasks = AnalysisTasks(self.store, self.app.slots, lambda *args: self.fail('must not replay'))
        task = tasks.get(self.owner, task_id)
        self.assertEqual(task['status'], 'failed')
        self.assertEqual(task['stage'], 'interrupted')

    def test_start_failure_releases_capacity_even_when_storage_fails(self):
        with patch('agent.tasks.threading.Thread.start', side_effect=RuntimeError('cannot start')):
            with patch.object(self.app.tasks, 'fail', side_effect=sqlite3.OperationalError('unavailable')):
                with self.assertRaises(sqlite3.OperationalError):
                    self.submit()
        for _ in range(3):
            self.assertTrue(self.app.slots.acquire(blocking=False))
        self.assertFalse(self.app.slots.acquire(blocking=False))
        for _ in range(3):
            self.app.slots.release()

    def test_failed_worker_is_terminal_while_writes_fail_and_recovers(self):
        task = self.submit()
        self.assertTrue(self.model.entered.wait(1))
        real_connect = self.store.connect

        @contextmanager
        def read_only_connect():
            with real_connect() as db:
                db.execute('PRAGMA query_only=ON')
                yield db

        with patch.object(self.store, 'connect', read_only_connect):
            self.model.release.set()
            failed = self.wait_done(task['task_id'])
            self.assertEqual(failed['status'], 'failed')
            self.assertIn('分析任务未完成', failed['error'])
            self.assertIn(task['task_id'], self.app.tasks._pending_failures)
            for _ in range(3):
                self.assertTrue(self.app.slots.acquire(timeout=1))
            for _ in range(3):
                self.app.slots.release()
            with real_connect() as db:
                stored = db.execute('SELECT status FROM analysis_tasks WHERE id=?', (task['task_id'],)).fetchone()
                self.assertEqual(stored['status'], 'running')
            with self.assertRaises(InputError):
                self.app.tasks.get('someone-else', task['task_id'])
            self.assertIn(task['task_id'], self.app.tasks._pending_failures)
            with patch('agent.tasks.time.time', return_value=time.time() + 60):
                later = self.app.tasks.get(self.owner, task['task_id'])
            self.assertEqual(later['elapsed_seconds'], failed['elapsed_seconds'])

        recovered = self.app.tasks.get(self.owner, task['task_id'])
        self.assertEqual(recovered, failed)
        self.assertNotIn(task['task_id'], self.app.tasks._pending_failures)
        with real_connect() as db:
            stored = db.execute('SELECT status,error,finished_epoch FROM analysis_tasks WHERE id=?', (task['task_id'],)).fetchone()
        self.assertEqual(stored['status'], 'failed')
        self.assertEqual(stored['error'], failed['error'])
        self.assertIsNotNone(stored['finished_epoch'])

    def test_pending_failure_does_not_replace_completed_result(self):
        self.model.release.set()
        completed = self.wait_done(self.submit()['task_id'])
        task_id = completed['task_id']
        with patch.object(self.store, 'connect', side_effect=sqlite3.OperationalError('database unavailable')):
            self.app.tasks.fail(task_id, 'delayed failure')
        self.assertIn(task_id, self.app.tasks._pending_failures)
        self.assertEqual(self.app.tasks.get(self.owner, task_id), completed)
        self.assertNotIn(task_id, self.app.tasks._pending_failures)

    def test_http_sse_disconnect_does_not_cancel_task(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.app = self.app
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        response = None
        try:
            task = self.submit()
            route = base + '/agent-api/analysis-tasks/' + task['task_id'] + '/events'
            response = urllib.request.urlopen(urllib.request.Request(route, headers=self.headers), timeout=3)
            self.assertEqual(response.headers.get_content_type(), 'text/event-stream')
            self.assertEqual(response.headers['X-Accel-Buffering'], 'no')
            self.assertEqual(response.readline(), b'event: progress\n')
            first = json.loads(response.readline()[6:])
            self.assertEqual(first['task_id'], task['task_id'])
            response.close()
            self.model.release.set()
            self.wait_done(task['task_id'])
            with urllib.request.urlopen(urllib.request.Request(route, headers=self.headers), timeout=3) as done:
                self.assertEqual(done.readline(), b'event: done\n')
                self.assertIn('run_id', json.loads(done.readline()[6:])['result'])
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(route, headers={**self.headers, 'X-Fumai-Session': str(uuid.uuid4())}), timeout=3)
            self.assertEqual(error.exception.code, 400)
        finally:
            if response:
                response.close()
            self.model.release.set()
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
