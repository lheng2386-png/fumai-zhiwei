"""Persistent, owner-scoped analysis jobs. Workers continue after HTTP disconnects."""
import json
import sqlite3
import threading
import time
import uuid

from .core import InputError, utcnow


TERMINAL = {"completed", "failed"}


class TaskBusy(Exception):
    pass


class AnalysisTasks:
    def __init__(self, store, slots, analyze):
        self.store, self.slots, self.analyze = store, slots, analyze
        self._failure_lock = threading.Lock()
        # A worker must remain terminal even if its final database write fails.
        # Entries are bounded by the task table's 10,000-row admission limit.
        self._pending_failures = {}
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS analysis_tasks (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL,
                    request_json TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL,
                    created_at TEXT NOT NULL, created_epoch REAL NOT NULL, finished_epoch REAL,
                    result_run_id TEXT, error TEXT, UNIQUE(owner, request_id));
            """)
            # A process restart cannot resume a model connection. Report a known
            # interrupted state instead of leaving a job running indefinitely.
            db.execute("UPDATE analysis_tasks SET status='failed', stage='interrupted', finished_epoch=?, error=? "
                       "WHERE status IN ('queued','running')", (time.time(), "服务已重启，本次分析中断，请重新提交。"))

    def submit(self, owner, body, reserve_model_slot=True):
        request_id = body.get("request_id")
        try:
            uuid.UUID(request_id)
        except (ValueError, TypeError, AttributeError):
            raise InputError("需要有效的 request_id，重试时请使用同一个编号") from None
        request = {key: value for key, value in body.items() if key != "request_id"}
        encoded = json.dumps(request, sort_keys=True, ensure_ascii=False, allow_nan=False)
        with self.store.connect() as db:
            existing = db.execute("SELECT id,request_json FROM analysis_tasks WHERE owner=? AND request_id=?", (owner, request_id)).fetchone()
        if existing:
            if existing["request_json"] != encoded:
                raise InputError("request_id 已用于其他请求，请生成新的编号")
            return self.get(owner, existing["id"])
        if reserve_model_slot and not self.slots.acquire(blocking=False):
            raise TaskBusy("模型正在处理其他任务，请稍后重试")
        task_id, created = str(uuid.uuid4()), time.time()
        owns_slot = reserve_model_slot
        try:
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT count(*) FROM analysis_tasks").fetchone()[0] >= 10000:
                    raise InputError("任务存储已达上限，请管理员归档后再试")
                inserted = db.execute("INSERT OR IGNORE INTO analysis_tasks "
                                      "(id,owner,request_id,request_json,status,stage,created_at,created_epoch) VALUES(?,?,?,?,?,?,?,?)",
                                      (task_id, owner, request_id, encoded, "queued", "queued", utcnow(), created)).rowcount
                row = db.execute("SELECT id,request_json FROM analysis_tasks WHERE owner=? AND request_id=?", (owner, request_id)).fetchone()
            if not inserted:
                if owns_slot:
                    self.slots.release()
                owns_slot = False
                if row["request_json"] != encoded:
                    raise InputError("request_id 已用于其他请求，请生成新的编号")
                return self.get(owner, row["id"])
        except Exception:
            # Only release reservations still owned by this attempt.
            if owns_slot:
                self.slots.release()
            raise
        if not reserve_model_slot:
            self.run(owner, task_id, request, release_slot=False)
            return self.get(owner, task_id)
        try:
            worker = threading.Thread(target=self.run, args=(owner, task_id, request), daemon=True, name="agent-analysis")
            worker.start()
        except Exception:
            try:
                self.fail(task_id, "任务无法启动，请稍后重试。")
            finally:
                self.slots.release()
            raise
        return self.get(owner, task_id)

    def progress(self, task_id, stage):
        with self.store.connect() as db:
            db.execute("UPDATE analysis_tasks SET status='running',stage=? WHERE id=? AND status NOT IN ('completed','failed')", (stage, task_id))

    def fail(self, task_id, message):
        with self._failure_lock:
            failure = self._pending_failures.setdefault(task_id, (message, time.time()))
        self._persist_failure(task_id, failure)

    def _persist_failure(self, task_id, failure):
        try:
            with self.store.connect() as db:
                db.execute("UPDATE analysis_tasks SET status='failed',stage='failed',error=?,finished_epoch=? "
                           "WHERE id=? AND status IN ('queued','running')", (*failure, task_id))
        except sqlite3.Error:
            # A later owner-authorized read retries this write. A process restart
            # also recovers unfinished records before serving requests.
            return
        self._forget_failure(task_id, failure)

    def _forget_failure(self, task_id, failure):
        with self._failure_lock:
            if self._pending_failures.get(task_id) == failure:
                self._pending_failures.pop(task_id, None)

    def run(self, owner, task_id, request, release_slot=True):
        try:
            result = self.analyze(owner, request, lambda stage: self.progress(task_id, stage))
            with self.store.connect() as db:
                db.execute("UPDATE analysis_tasks SET status='completed',stage='completed',error=NULL,result_run_id=?,finished_epoch=? WHERE id=? AND status IN ('queued','running')",
                           (result["run_id"], time.time(), task_id))
        except Exception:
            # Model exceptions are handled by the analyzer. Never persist raw
            # exceptions (which can contain provider credentials or payloads).
            self.fail(task_id, "分析任务未完成，请稍后重试；如持续失败请联系管理员。")
        finally:
            if release_slot:
                self.slots.release()

    def get(self, owner, task_id):
        try:
            uuid.UUID(task_id)
        except (ValueError, TypeError, AttributeError):
            raise InputError("无效的任务编号") from None
        with self.store.connect() as db:
            allowed=self.store.owners(owner)
            row = db.execute('SELECT * FROM analysis_tasks WHERE id=? AND owner IN ('+','.join('?' for _ in allowed)+')',(task_id,*allowed)).fetchone()
        if not row:
            raise InputError("任务不存在或无权访问")
        with self._failure_lock:
            failure = self._pending_failures.get(task_id)
        if failure:
            if row["status"] in TERMINAL:
                self._forget_failure(task_id, failure)
            else:
                self._persist_failure(task_id, failure)
                # Reads can recover before writes (for example, a SQLite writer
                # lock). Keep the stopped worker's state and finish time stable.
                row = {**dict(row), "status": "failed", "stage": "failed", "error": failure[0],
                       "finished_epoch": failure[1], "result_run_id": None}
        request = json.loads(row["request_json"])
        result = {"task_id": row["id"], "status": row["status"], "stage": row["stage"], "created_at": row["created_at"],
                  "elapsed_seconds": max(0, int((row["finished_epoch"] or time.time()) - row["created_epoch"])),
                  "question": request["message"]}
        if row["result_run_id"]:
            result["result"] = self.store.get_run(owner, row["result_run_id"])
        if row["error"]:
            result["error"] = row["error"]
        return result
