"""Append-only human feedback referencing an existing inference/review artifact."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3

STATUSES=('confirmed_fault','expected_operation','data_issue','needs_investigation')


def job_result(root,request_id):
    if not isinstance(request_id,str) or not re.fullmatch(r'[a-f0-9]{32}',request_id):
        raise ValueError('Invalid request ID')
    folder=Path(root)/request_id
    if not folder.is_dir():raise FileNotFoundError('Unknown request ID')
    candidates=[folder/name for name in ('result.json','review.json') if (folder/name).is_file()]
    if not candidates:raise FileNotFoundError('No completed result for request ID')
    if len(candidates)!=1:raise ValueError('Ambiguous job result')
    raw=candidates[0].read_bytes();result=json.loads(raw)
    if not isinstance(result,dict) or not ('evidence' in result or 'source_evidence' in result):
        raise ValueError('Unsupported result artifact')
    return result,hashlib.sha256(raw).hexdigest()


def connect(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(root/'feedback.sqlite',timeout=10)
    db.execute('''CREATE TABLE IF NOT EXISTS competition_feedback (
        id INTEGER PRIMARY KEY, request_id TEXT NOT NULL,
        report_sha256 TEXT NOT NULL, status TEXT NOT NULL,
        reviewer TEXT NOT NULL, note TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )''')
    return db


def save(root,request_id,status,reviewer,note):
    if status not in STATUSES or not isinstance(reviewer,str) or not reviewer.strip() or not isinstance(note,str) or not note.strip():
        raise ValueError('Human reviewer, supported status and nonempty note required')
    if len(reviewer)>128 or len(note)>4000:raise ValueError('Feedback text exceeds demo limits')
    _,digest=job_result(root,request_id)
    with connect(root) as db:
        cur=db.execute('INSERT INTO competition_feedback(request_id,report_sha256,status,reviewer,note) VALUES (?,?,?,?,?)',
                       (request_id,digest,status,reviewer.strip(),note.strip()))
        identifier=cur.lastrowid
    # Original model output remains unchanged; feedback never becomes training truth automatically.
    return {'feedback_id':identifier,'request_id':request_id,'report_sha256':digest,
            'status':status,'reviewer':reviewer.strip(),'note':note.strip()}


def history(root,request_id):
    _,digest=job_result(root,request_id)
    with connect(root) as db:
        db.row_factory=sqlite3.Row
        rows=db.execute('SELECT * FROM competition_feedback WHERE request_id=? ORDER BY id',(request_id,)).fetchall()
    return [{**dict(row),'matches_current_report':row['report_sha256']==digest} for row in rows]
