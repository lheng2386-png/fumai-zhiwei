"""Local SQLite human review log. No automatic fault confirmation."""
import argparse
import sqlite3
from pathlib import Path
import pandas as pd

STATUSES = ('confirmed_fault', 'expected_operation', 'data_issue', 'needs_investigation')


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute('''CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY, event_id TEXT NOT NULL,
        status TEXT NOT NULL, note TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))''')
    return db


def record(db_path, events_path, event_id, status, note):
    if status not in STATUSES or not note.strip():
        raise ValueError('Valid review status and a nonempty note are required')
    events = pd.read_csv(events_path)
    if event_id not in set(events.event_id):
        raise ValueError('Unknown event ID; review must reference this replay')
    with connect(db_path) as db:
        db.execute('INSERT INTO reviews(event_id,status,note) VALUES (?,?,?)',
                   (event_id, status, note.strip()))


def latest(db_path):
    with connect(db_path) as db:
        return pd.read_sql_query('''SELECT event_id,status,note,created_at FROM reviews
            WHERE id IN (SELECT MAX(id) FROM reviews GROUP BY event_id)''', db)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--db', type=Path, default=Path('reports/reviews.sqlite'))
    p.add_argument('--events', type=Path, default=Path('reports/replay/events.csv'))
    p.add_argument('--event-id', required=True)
    p.add_argument('--status', choices=STATUSES, required=True)
    p.add_argument('--note', required=True)
    a = p.parse_args()
    record(a.db, a.events, a.event_id, a.status, a.note)
    print('Review saved; history retained. Rerun replay to refresh the report.')
