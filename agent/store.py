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
                CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS legacy_access (legacy_owner TEXT PRIMARY KEY, owner TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS run_devices (run_id TEXT NOT NULL, device_id TEXT NOT NULL, PRIMARY KEY(run_id,device_id));
                CREATE TABLE IF NOT EXISTS run_context (run_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, response_type TEXT NOT NULL, intent TEXT NOT NULL);
            """)
            # Backfill indexes and conversation IDs, preserving stored answers and actors.
            for row in db.execute('SELECT r.* FROM runs r LEFT JOIN run_context x ON x.run_id=r.id WHERE x.run_id IS NULL').fetchall():
                result = json.loads(row['result'])
                conversation = self.default_conversation(row['owner'])
                db.execute('INSERT OR IGNORE INTO conversations VALUES(?,?,?,?)', (conversation,row['owner'],row['question'][:60],row['created_at']))
                db.execute('INSERT INTO run_context VALUES(?,?,?,?)',
                           (row['id'],conversation,result.get('response_type','analysis'),result.get('intent','maintenance')))
                for event in result.get('events',[]):
                    if isinstance(event,dict) and isinstance(event.get('inverter'),str):
                        db.execute('INSERT OR IGNORE INTO run_devices VALUES(?,?)',(row['id'],event['inverter']))
            db.execute('CREATE INDEX IF NOT EXISTS runs_conversation ON run_context(conversation_id)')
            db.execute('CREATE INDEX IF NOT EXISTS runs_owner ON runs(owner,created_at)')

    @staticmethod
    def default_conversation(owner):
        try:
            if owner.startswith('demo:'):
                return str(uuid.UUID(owner[5:]))
        except ValueError:
            pass
        return str(uuid.uuid5(uuid.NAMESPACE_URL, 'fumai-legacy:'+owner))

    def owners(self, owner):
        with self.connect() as db:
            return [owner] + [r[0] for r in db.execute('SELECT legacy_owner FROM legacy_access WHERE owner=?',(owner,))]

    def claim_legacy(self, owner, session):
        """The old random session was a demo capability; retain only its own records."""
        legacy = 'demo:'+session
        if not owner.startswith('browser:'):
            return
        with self.connect() as db:
            if db.execute('SELECT 1 FROM runs WHERE owner=? LIMIT 1',(legacy,)).fetchone():
                db.execute('INSERT OR IGNORE INTO legacy_access VALUES(?,?)',(legacy,owner))

    def ensure_conversation(self, owner, conversation):
        try:
            conversation = str(uuid.UUID(conversation))
        except (ValueError,TypeError,AttributeError):
            raise InputError('需要有效的会话编号') from None
        allowed = self.owners(owner)
        with self.connect() as db:
            row = db.execute('SELECT owner FROM conversations WHERE id=?',(conversation,)).fetchone()
            if row and row['owner'] not in allowed:
                raise InputError('会话不存在或无权访问')
            if not row:
                if db.execute('SELECT count(*) FROM conversations').fetchone()[0] >= 20000:
                    raise InputError('会话数量已达上限，请管理员归档')
                db.execute('INSERT INTO conversations VALUES(?,?,?,?)',(conversation,owner,'',utcnow()))
        return conversation

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save_run(self, owner, question, result, conversation_id=None):
        conversation_id = self.ensure_conversation(owner, conversation_id or self.default_conversation(owner))
        run_id = str(uuid.uuid4())
        result["run_id"] = run_id
        result["created_at"] = utcnow()
        result['conversation_id'] = conversation_id
        result['question'] = question
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT count(*) FROM runs").fetchone()[0] >= 10000:
                raise InputError("审计存储已达上限，请管理员归档后再试")
            db.execute('INSERT INTO runs(id,owner,created_at,question,result) VALUES(?,?,?,?,?)',
                       (run_id,owner,result['created_at'],question,json.dumps(result,ensure_ascii=False,allow_nan=False)))
            db.execute('INSERT INTO run_context VALUES(?,?,?,?)',(run_id,conversation_id,result.get('response_type','analysis'),result.get('intent','maintenance')))
            db.execute("UPDATE conversations SET title=? WHERE id=? AND title=''",(question[:60],conversation_id))
            for event in result.get('events',[]):
                if isinstance(event,dict) and isinstance(event.get('inverter'),str):
                    db.execute('INSERT OR IGNORE INTO run_devices VALUES(?,?)',(run_id,event['inverter']))
            for target in result.get('target_device_ids',[]):
                db.execute('INSERT OR IGNORE INTO run_devices VALUES(?,?)',(run_id,target))
        return result

    def history(self, owner, conversation_id=None, device_id=None, analysis_only=False, limit=20, offset=0):
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 10000:
            raise InputError('历史查询分页参数无效')
        allowed = self.owners(owner)
        clauses = ['r.owner IN ('+','.join('?' for _ in allowed)+')']
        params = list(allowed)
        if conversation_id:
            clauses.append('x.conversation_id=?');params.append(conversation_id)
        if device_id:
            clauses.append('EXISTS(SELECT 1 FROM run_devices d WHERE d.run_id=r.id AND d.device_id=?)');params.append(device_id)
        if analysis_only:
            clauses.append("x.response_type='analysis'")
        with self.connect() as db:
            rows = db.execute('SELECT r.*,x.conversation_id,x.response_type,x.intent FROM runs r JOIN run_context x ON x.run_id=r.id WHERE '+' AND '.join(clauses)+' ORDER BY r.created_at DESC,r.rowid DESC LIMIT ? OFFSET ?',(*params,limit,offset)).fetchall()
            return [{'run_id':r['id'],'created_at':r['created_at'],'question':r['question'],'conversation_id':r['conversation_id'],'response_type':r['response_type'],
                     'devices':[d[0] for d in db.execute('SELECT device_id FROM run_devices WHERE run_id=?',(r['id'],))]} for r in rows]

    def turns(self, owner, conversation_id, limit=20):
        self.ensure_conversation(owner,conversation_id)
        rows = self.history(owner,conversation_id=conversation_id,limit=limit)
        return [self.get_run(owner,row['run_id']) for row in reversed(rows)]

    def conversations(self, owner):
        allowed=self.owners(owner)
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT c.id,c.title,c.created_at,count(r.id) AS turns,max(r.created_at) AS updated_at FROM conversations c LEFT JOIN run_context x ON x.conversation_id=c.id LEFT JOIN runs r ON r.id=x.run_id WHERE c.owner IN ('+','.join('?' for _ in allowed)+') GROUP BY c.id HAVING count(r.id)>0 ORDER BY max(r.created_at) DESC LIMIT 30',allowed)]

    def get_run(self, owner, run_id):
        try:
            uuid.UUID(run_id)
        except (ValueError, TypeError, AttributeError):
            raise InputError("无效的审计编号") from None
        allowed=self.owners(owner)
        with self.connect() as db:
            row = db.execute('SELECT r.result,x.conversation_id,r.question FROM runs r JOIN run_context x ON x.run_id=r.id WHERE r.id=? AND r.owner IN ('+','.join('?' for _ in allowed)+')',(run_id,*allowed)).fetchone()
        if not row:
            raise InputError("记录不存在或无权访问")
        result=json.loads(row['result'])
        result.update(conversation_id=row['conversation_id'],question=row['question'])
        return result

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
            row = db.execute('SELECT * FROM drafts WHERE run_id=? AND event_id=? AND owner IN ('+','.join('?' for _ in self.owners(owner))+')', (run_id,event_id,*self.owners(owner))).fetchone()
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
            allowed=self.owners(owner)
            row = db.execute('SELECT * FROM drafts WHERE id=? AND owner IN ('+','.join('?' for _ in allowed)+')',(draft_id,*allowed)).fetchone()
            if not row:
                raise InputError("草稿不存在或无权访问")
            if fingerprint and json.loads(row["content"])["metadata"]["fingerprint"] != fingerprint:
                raise InputError("草稿引用的数据版本已变化，请重新分析并生成草稿")
            if row["status"] == "pending_confirmation":
                db.execute("INSERT INTO confirmations VALUES(?,?,?,?,?)", (str(uuid.uuid4()), draft_id, owner, note.strip(), utcnow()))
                db.execute("UPDATE drafts SET status='confirmed_draft' WHERE id=?", (draft_id,))
        return {"draft_id": draft_id, "status": "confirmed_draft", "dispatched": False, "message": "草稿已确认留档，尚未派单或操作设备。"}
