"""Retrospective alarm-threshold sensitivity for the frozen Reunion model.

The original test and normal-stress scores have already been inspected in prior
work. They are never used to choose a threshold here; this is diagnostic,
not a new blind model comparison.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def choose_zero_validation_false_positive_threshold(y, scores):
    healthy = np.asarray(scores)[np.asarray(y) == 0]
    if not len(healthy):
        raise ValueError("Validation has no healthy windows")
    return float(np.nextafter(healthy.max(), np.float32(np.inf)))


def describe(scores, y, times, threshold):
    scores = np.asarray(scores)
    y = np.asarray(y)
    times = np.asarray(times)
    dates = np.asarray(pd.to_datetime(times, utc=True).tz_convert("Indian/Reunion").date,
                       dtype="str")
    alarms = scores >= threshold
    fault_days = set(dates[y == 1])
    healthy_days = sorted(set(dates) - fault_days)
    return {
        "threshold": float(threshold),
        "fault_windows": int(np.sum(y == 1)),
        "fault_windows_detected": int(np.sum(alarms & (y == 1))),
        "healthy_windows": int(np.sum(y == 0)),
        "healthy_windows_alarmed": int(np.sum(alarms & (y == 0))),
        "pure_healthy_days": len(healthy_days),
        "pure_healthy_days_with_alarm": int(sum(np.any(alarms[dates == day])
                                                 for day in healthy_days)),
        "fault_days": len(fault_days),
        "fault_days_detected": int(sum(np.any(alarms[(dates == day) & (y == 1)])
                                       for day in fault_days)),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path,
                        default=Path("data/processed/la_reunion_v1"))
    parser.add_argument("--run", type=Path, default=Path("reports/reunion_v1_mps"))
    parser.add_argument("--output", type=Path,
                        default=Path("reports/reunion_alarm_thresholds_v1.json"))
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    selection = json.loads((args.run / "selection.json").read_text())
    if selection["selected"] != "transformer_seed2032":
        raise ValueError("Expected the frozen first-round selected Transformer")
    with np.load(args.prepared / "validation.npz") as data:
        val_y, val_times = data["y"], data["end_time_ns"]
    val_scores = np.load(args.run / "transformer_seed2032_validation.npy")
    if len(val_scores) != len(val_y):
        raise ValueError("Validation score/label count mismatch")
    zero_fp_threshold = choose_zero_validation_false_positive_threshold(val_y, val_scores)
    thresholds = {"original_macro_f1": float(selection["validation_metrics"]["threshold"]),
                  "zero_validation_healthy_windows": zero_fp_threshold}
    outputs = {"validation": {name: describe(val_scores, val_y, val_times, value)
                              for name, value in thresholds.items()}}
    for split, filename in (("previously_seen_test", "test_predictions.csv"),
                            ("previously_seen_normal_stress", "normal_stress_predictions.csv")):
        frame = pd.read_csv(args.run / filename)
        outputs[split] = {name: describe(frame.fault_probability, frame.label,
                                         frame.end_time_ns, value)
                          for name, value in thresholds.items()}
    result = {"protocol": "validation-only threshold choice on frozen model; old test/stress retrospective diagnostics",
              "selected_model": selection["selected"],
              "limitations": ["The old test and normal-stress periods were already inspected; these are not new blind results.",
                              "Zero observed validation false positives on a small same-site period does not guarantee field false-alarm control."],
              "thresholds": thresholds, "results": outputs}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["results"], indent=2))


if __name__ == "__main__":
    main()
