"""Audit the downloaded PVDAQ-derived daily triage data before model use.

The source labels are rule/referee derived, not field work-order truth. This
script records provenance and site coverage; it does not train or score a model.
"""

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd


def audit(source, output):
    if output.exists():
        raise FileExistsError(output)
    frame = pd.read_parquet(source)
    required = {"date", "site", "final_label", "provenance", "verdict", "rule_label"}
    if not required.issubset(frame.columns):
        raise ValueError(f"Missing columns: {sorted(required - set(frame.columns))}")
    if frame.duplicated(["site", "date"]).any():
        raise ValueError("Duplicate site-days")
    labels = frame.final_label.value_counts(dropna=False)
    provenance = frame.provenance.value_counts(dropna=False)
    report = {
        "source": "https://huggingface.co/datasets/hoodsy/sn-triage-dataset",
        "file": source.name,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "rows": len(frame), "sites": int(frame.site.nunique()),
        "date_min": str(frame.date.min()), "date_max": str(frame.date.max()),
        "duplicate_site_days": 0,
        "labels": {str(k): int(v) for k, v in labels.items()},
        "provenance": {str(k): int(v) for k, v in provenance.items()},
        "verdict": {str(k): int(v) for k, v in frame.verdict.value_counts(dropna=False).items()},
        "label_by_provenance": {
            str(label): {str(k): int(v) for k, v in group.provenance.value_counts().items()}
            for label, group in frame.groupby("final_label")},
        "referee_rows": int((frame.provenance == "referee").sum()),
        "healthy_rows_with_referee_provenance": int(
            ((frame.final_label == "healthy") & (frame.provenance == "referee")).sum()),
        "data_gap_or_unclassified_rows": int(frame.final_label.isin(["data_gap", "unclassified"]).sum()),
        "limitations": [
            "Daily engineered features cannot be fed into the 5-minute La Reunion model.",
            "All healthy labels have rule provenance; there are no field-certified healthy negatives.",
            "Referee provenance comes from fleet-relative sub-meter grading, not repair work orders.",
            "Many features directly encode the source's label rules; reproducing labels is not independent fault detection.",
            "A site-held-out split is necessary for any exploratory model on this source."],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({key: report[key] for key in ("sha256", "rows", "sites", "provenance",
                                                   "healthy_rows_with_referee_provenance")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("data/raw/hf_sn_triage/triage-days.parquet"))
    parser.add_argument("--output", type=Path, default=Path("data/raw/hf_sn_triage/audit.json"))
    args = parser.parse_args()
    audit(args.source, args.output)
