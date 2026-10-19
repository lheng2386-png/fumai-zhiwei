"""Measure normal-day alarms after frozen training-period backtest folds.

Only La Reunion's original train.npz is opened. The old validation, test and
normal-stress files are never read. This is a supplementary development probe.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from train_reunion import selected_scores


NORMAL_PERIODS = {
    "mid_february": ("2022-02-19", "2022-02-23"),
    "early_march": ("2022-03-02", "2022-03-08"),
}


def run(prepared, backtest, output, device, batch_size):
    if output.exists():
        raise FileExistsError(output)
    with np.load(prepared / "train.npz") as data:
        x, y, times = (data[key] for key in ("x", "y", "end_time_ns"))
    dates = np.asarray(pd.to_datetime(times, utc=True)
                       .tz_convert("Indian/Reunion").date, dtype="str")
    with np.load(prepared / "normalization.npz") as normalization:
        old_mean, old_scale = normalization["mean"], normalization["scale"]
    raw = x * old_scale[None, :, None] + old_mean[None, :, None]
    backtest_results = json.loads((backtest / "results.json").read_text())
    result = {
        "protocol": "frozen fold model and threshold on later normal-only dates in original training partition",
        "source_split": "train.npz only; original validation/test/normal_stress not opened",
        "limitations": ["One rooftop plant and a small number of normal dates.",
                        "Fold models were selected on prior tuning dates; this is exploratory follow-up, not a new external test."],
        "folds": {},
    }
    for name, (first, last) in NORMAL_PERIODS.items():
        mask = (dates >= first) & (dates <= last)
        if not mask.any() or np.any(y[mask] != 0):
            raise ValueError(f"Period {name} is empty or not wholly normal")
        fold = backtest_results["folds"][name]
        if first <= fold["tuning_through"]:
            raise ValueError(f"Normal period overlaps fold tuning: {name}")
        with np.load(backtest / name / "normalization.npz") as normalization:
            mean, scale = normalization["mean"], normalization["scale"]
        normalized = ((raw[mask] - mean[None, :, None]) /
                      scale[None, :, None]).astype(np.float32)
        data = {"x": normalized, "y": y[mask], "end_time_ns": times[mask]}
        scores = selected_scores({"selected_members": fold["selected_members"]},
                                 data, backtest / name, torch.device(device), batch_size)
        threshold = fold["threshold_from_tuning"]
        alerts = scores >= threshold
        period_dates = dates[mask]
        day_counts = {day: int(alerts[period_dates == day].sum())
                      for day in sorted(set(period_dates))}
        result["folds"][name] = {
            "selected": fold["selected"], "threshold_from_tuning": threshold,
            "first_day": first, "last_day": last,
            "normal_windows": int(len(scores)),
            "normal_days": len(day_counts),
            "normal_days_with_alert": sum(count > 0 for count in day_counts.values()),
            "false_alarm_windows": int(alerts.sum()),
            "false_alarm_window_rate": float(alerts.mean()),
            "false_alarm_windows_by_day": day_counts,
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["folds"], indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--backtest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "mps", "cuda"), default="cpu")
    parser.add_argument("--batch-size", type=int, default=256)
    args = parser.parse_args()
    run(args.prepared, args.backtest, args.output, args.device, args.batch_size)
