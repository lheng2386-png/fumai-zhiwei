"""Adapt project events.csv plus an explicit device registry; validate before publish."""
import argparse
import csv
import json
import os
import tempfile
from pathlib import Path

from .core import Snapshot


def convert(events_path, devices_path, metadata):
    with Path(events_path).open(encoding="utf-8-sig", newline="") as file:
        events = list(csv.DictReader(file))
    for event in events:
        for key in ("samples", "observed_span_minutes", "peer_flag_samples", "min_peer_count"):
            event[key] = int(event[key])
        for key in ("mean_actual_kw", "mean_reference_kw", "reference_shortfall_kwh"):
            event[key] = float(event[key])
        event["mean_peer_gap"] = None if event.get("mean_peer_gap", "").strip() == "" else float(event["mean_peer_gap"])
    document = {"schema_version": 1, "source": "measured", **metadata,
                "devices": json.loads(Path(devices_path).read_text(encoding="utf-8")), "events": events}
    Snapshot(document, "live")
    return document


def main():
    parser = argparse.ArgumentParser()
    for name in ("events", "devices", "site-id", "dataset-version", "screening-version", "timezone", "observed-at", "output"):
        parser.add_argument("--"+name, required=True)
    args = parser.parse_args()
    metadata = {key: getattr(args, key) for key in ("site_id", "dataset_version", "screening_version", "timezone", "observed_at")}
    document = convert(args.events, args.devices, metadata)
    target = Path(args.output).resolve()
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False, suffix=".json") as file:
        temporary = Path(file.name)
        json.dump(document, file, ensure_ascii=False, allow_nan=False, indent=2)
        file.write("\n")
    try:
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Validated {len(document['events'])} events; snapshot published to {target.name}")


if __name__ == "__main__":
    main()
