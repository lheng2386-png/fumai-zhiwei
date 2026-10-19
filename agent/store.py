"""SQLite append-only run audit and human-confirmed drafts; no device commands."""
import json
import sqlite3
import uuid
from contextlib import contextmanager

from .core import InputError, utcnow


class Store:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, owner TEXT NOT NULL, created_at TEXT NOT NULL, question TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, owner TEXT NOT NULL, event_id TEXT NOT NULL, status TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(run_id, event_id));
                CREATE TABLE IF NOT EXISTS confirmations (id TEXT PRIMARY KEY, draft_id TEXT NOT NULL UNIQUE, actor TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save_run(self, owner, question, result):
        run_id = str(uuid.uuid4())
        result["run_id"] = run_id
        result["created_at"] = utcnow()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT count(*) FROM runs").fetchone()[0] >= 10000:
                raise InputError("审计存储已达上限，请管理员归档后再试")
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?)", (run_id, owner, result["created_at"], question, json.dumps(result, ensure_ascii=False, allow_nan=False)))
        return result

    def history(self, owner):
        with self.connect() as db:
            return [{"run_id": r["id"], "created_at": r["created_at"], "question": r["question"]} for r in db.execute("SELECT id, created_at, question FROM runs WHERE owner=? ORDER BY created_at DESC LIMIT 20", (owner,))]

    def get_run(self, owner, run_id):
        try:
            uuid.UUID(run_id)
        except (ValueError, TypeError, AttributeError):
            raise InputError("无效的审计编号") from None
        with self.connect() as db:
            row = db.execute("SELECT result FROM runs WHERE id=? AND owner=?", (run_id, owner)).fetchone()
        if not row:
            raise InputError("记录不存在或无权访问")
        return json.loads(row[0])

    def draft(self, owner, run_id, event_id):
        run = self.get_run(owner, run_id)
        event = next((e for e in run["events"] if e["event_id"] == event_id), None)
        if not event:
            raise InputError("草稿必须引用本次分析中的事件")
        content = {"title": f"{event['inverter']} 候选异常核查", "event": event, "metadata": run["metadata"],
                   "checks": run["checks"], "unknowns": run["unknowns"], "run_id": run_id,
                   "notice": "仅生成核查草稿，确认后留档；不向外部工单系统派单。"}
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO drafts VALUES(?,?,?,?,?,?,?)", (str(uuid.uuid4()), run_id, owner, event_id, "pending_confirmation", json.dumps(content, ensure_ascii=False), utcnow()))
            row = db.execute("SELECT * FROM drafts WHERE run_id=? AND event_id=? AND owner=?", (run_id, event_id, owner)).fetchone()
        return {**dict(row), "content": json.loads(row["content"])}

    def confirm(self, owner, draft_id, note, fingerprint=None):
        try:
            uuid.UUID(draft_id)
        except (ValueError, TypeError, AttributeError):
            raise InputError("无效的草稿编号") from None
        if not isinstance(note, str) or not 2 <= len(note.strip()) <= 1000:
            raise InputError("确认说明需为 2–1000 个字符")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM drafts WHERE id=? AND owner=?", (draft_id, owner)).fetchone()
            if not row:
                raise InputError("草稿不存在或无权访问")
            if fingerprint and json.loads(row["content"])["metadata"]["fingerprint"] != fingerprint:
                raise InputError("草稿引用的数据版本已变化，请重新分析并生成草稿")
            if row["status"] == "pending_confirmation":
                db.execute("INSERT INTO confirmations VALUES(?,?,?,?,?)", (str(uuid.uuid4()), draft_id, owner, note.strip(), utcnow()))
                db.execute("UPDATE drafts SET status='confirmed_draft' WHERE id=?", (draft_id,))
        return {"draft_id": draft_id, "status": "confirmed_draft", "dispatched": False, "message": "草稿已确认留档，尚未派单或操作设备。"}
