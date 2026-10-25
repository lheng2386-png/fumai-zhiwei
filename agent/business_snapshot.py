"""Read the operations SQLite database as a validated, refreshable Agent snapshot."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .core import Snapshot


def _iso(value):
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _number(metrics, name):
    value = metrics.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name)
    return value


class BusinessDatabaseSnapshot:
    """Snapshot-compatible facade that refreshes from the shared business database."""

    mode = "live"

    def __init__(self, path, refresh_seconds=2):
        self.path = Path(path)
        self.refresh_seconds = max(float(refresh_seconds), 0.5)
        self.lock = threading.Lock()
        self.loaded_at = 0.0
        self.current = None
        self._refresh(force=True)

    def _connect(self):
        uri = f"file:{self.path.as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _document(self):
        with self._connect() as db:
            stations = db.execute("SELECT * FROM substation_info WHERE enabled=1 ORDER BY code").fetchall()
            equipment = db.execute("SELECT * FROM equipment_info WHERE enabled=1 ORDER BY code").fetchall()
            results = db.execute("""SELECT r.*,q.model_name,q.model_version,q.data_quality
                FROM model_inference_result r JOIN model_inference_run q ON q.id=r.run_id
                WHERE q.status='succeeded' AND q.data_quality NOT IN ('blocked','invalid')
                ORDER BY r.observed_at DESC LIMIT 50000""").fetchall()
            latest = db.execute("""SELECT max(value) observed_at FROM (
                SELECT max(observed_at) value FROM telemetry_info UNION ALL
                SELECT max(observed_at) value FROM telesignalling_info UNION ALL
                SELECT max(observed_at) value FROM model_inference_result)""").fetchone()["observed_at"]

        station = stations[0] if stations else None
        timezone_name = station["timezone"] if station else "Asia/Shanghai"
        zone = ZoneInfo(timezone_name)
        devices = []
        groups = {}
        for row in equipment:
            group = row["parent_id"] or row["substation_id"]
            groups[row["id"]] = group
            devices.append({"id": row["id"], "peer_group": group})

        events = []
        for row in results:
            if not row["equipment_id"] or row["equipment_id"] not in groups:
                continue
            try:
                metrics = json.loads(row["metrics_json"] or "{}")
                samples = int(_number(metrics, "samples"))
                peer_samples = int(_number(metrics, "peer_flag_samples"))
                peer_count = int(_number(metrics, "min_peer_count"))
                span = (samples - 1) * 5
                end = _iso(row["observed_at"]).astimezone(zone).replace(tzinfo=None)
                start = end - timedelta(minutes=span)
                event = {
                    "event_id": row["id"], "inverter": row["equipment_id"], "peer_group": groups[row["equipment_id"]],
                    "start_local": start.isoformat(timespec="seconds"), "last_sample_local": end.isoformat(timespec="seconds"),
                    "samples": samples, "observed_span_minutes": span,
                    "mean_actual_kw": _number(metrics, "mean_actual_kw"),
                    "mean_reference_kw": _number(metrics, "mean_reference_kw"),
                    "reference_shortfall_kwh": _number(metrics, "reference_shortfall_kwh"),
                    "peer_flag_samples": peer_samples, "min_peer_count": peer_count,
                    "mean_peer_gap": metrics.get("mean_peer_gap"),
                    "evidence": metrics.get("evidence", "shared_or_model_candidate"),
                    "review_status": metrics.get("review_status", "unreviewed"),
                }
                # Validate each candidate with the same contract used by the Agent. Invalid model payloads stay queryable
                # in the operations API but cannot silently become Agent evidence.
                probe = {"schema_version": 1, "site_id": station["id"] if station else "business_database",
                    "source": "measured", "dataset_version": "business-db-live", "screening_version": "model-contract-v1",
                    "timezone": timezone_name, "observed_at": _iso(latest).isoformat(), "devices": devices, "events": [event]}
                Snapshot(probe, "live")
                events.append(event)
            except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue

        return {"schema_version": 1, "site_id": station["id"] if station else "business_database",
            "source": "measured", "dataset_version": "business-db-live", "screening_version": "model-contract-v1",
            "timezone": timezone_name, "observed_at": _iso(latest).isoformat(), "devices": devices, "events": events}

    def _refresh(self, force=False):
        if not force and time.monotonic() - self.loaded_at < self.refresh_seconds:
            return self.current
        with self.lock:
            if force or time.monotonic() - self.loaded_at >= self.refresh_seconds:
                self.current = Snapshot(self._document(), "live")
                self.loaded_at = time.monotonic()
        return self.current

    @property
    def meta(self): return self._refresh().meta
    @property
    def devices(self): return self._refresh().devices
    @property
    def events(self): return self._refresh().events
    @property
    def stale(self): return self._refresh().stale
    @property
    def age_seconds(self): return self._refresh().age_seconds
    def event(self, event_id): return self._refresh().event(event_id)
    def list_events(self, device_id=None, limit=6): return self._refresh().list_events(device_id, limit)
    def tool(self, name, args): return self._refresh().tool(name, args)
    def analyze(self, message, event_id=None, device_id=None): return self._refresh().analyze(message, event_id, device_id)
