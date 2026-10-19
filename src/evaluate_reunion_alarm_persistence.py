"""Select a causal alarm persistence rule on validation, then audit old periods.

Only the original validation period selects the rule. Previously inspected test
and normal-stress periods are reported as retrospective diagnostics, not blind
performance estimates or new training data.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

FIVE_MINUTES_NS = 300_000_000_000


def persistent_alarms(scores, end_time_ns, threshold, consecutive):
    """Alert at the current window only after consecutive same-day 5-min hits."""
    scores = np.asarray(scores)
    times = np.asarray(end_time_ns, dtype=np.int64)
    if len(scores) != len(times) or consecutive < 1 or not np.isfinite(scores).all():
        raise ValueError("Invalid scores, times, or persistence")
    if len(times) > 1 and np.any(np.diff(times) <= 0):
        raise ValueError("Window end times must be strictly increasing")
    dates = np.asarray(pd.to_datetime(times, utc=True).tz_convert("Indian/Reunion").date,
                       dtype="str")
    hits = scores >= threshold
    alarms = np.zeros(len(scores), dtype=bool)
    streak = 0
    for i, hit in enumerate(hits):
        adjacent = i > 0 and times[i] - times[i - 1] == FIVE_MINUTES_NS and dates[i] == dates[i - 1]
        streak = (streak if adjacent else 0) + 1 if hit else 0
        alarms[i] = streak >= consecutive
    return alarms


def summarize(alarms, labels, end_time_ns):
    """Window and day counts; day recall requires a true alert on that day."""
    labels = np.asarray(labels)
    times = np.asarray(end_time_ns, dtype=np.int64)
    dates = np.asarray(pd.to_datetime(times, utc=True).tz_convert("Indian/Reunion").date,
                       dtype="str")
    if len(alarms) != len(labels) or len(labels) != len(times):
        raise ValueError("Array lengths differ")
    fault_days = set(dates[labels == 1])
    pure_normal_days = set(dates) - fault_days
    return {"fault_windows": int((labels == 1).sum()),
            "fault_windows_alarmed": int((alarms & (labels == 1)).sum()),
            "normal_windows": int((labels == 0).sum()),
            "normal_windows_alarmed": int((alarms & (labels == 0)).sum()),
            "fault_days": len(fault_days),
            "fault_days_with_true_alarm": sum(bool((alarms & (labels == 1))[dates == d].any())
                                              for d in fault_days),
            "pure_normal_days": len(pure_normal_days),
            "pure_normal_days_with_alarm": sum(bool(alarms[dates == d].any())
                                                for d in pure_normal_days)}


def run(prepared, model_run, output):
    if output.exists():
        raise FileExistsError(output)
    selection = json.loads((model_run / "selection.json").read_text())
    if selection["selected"] != "transformer_seed2032":
        raise ValueError("Expected the frozen first-round selected Transformer")
    threshold = float(selection["validation_metrics"]["threshold"])
    with np.load(prepared / "validation.npz") as data:
        val_labels, val_times = data["y"], data["end_time_ns"]
    val_scores = np.load(model_run / "transformer_seed2032_validation.npy")
    candidates = {}
    for count in range(1, 7):
        alarms = persistent_alarms(val_scores, val_times, threshold, count)
        candidates[str(count)] = summarize(alarms, val_labels, val_times)
    qualifying = [count for count in range(1, 7)
                  if candidates[str(count)]["normal_windows_alarmed"] == 0 and
                  candidates[str(count)]["fault_days_with_true_alarm"] ==
                  candidates[str(count)]["fault_days"]]
    if not qualifying:
        raise ValueError("No validation-only persistence rule meets the predeclared criteria")
    selected = min(qualifying)
    results = {"validation": candidates[str(selected)]}
    for split, filename in (("previously_seen_test", "test_predictions.csv"),
                            ("previously_seen_normal_stress", "normal_stress_predictions.csv")):
        frame = pd.read_csv(model_run / filename)
        alarms = persistent_alarms(frame.fault_probability, frame.end_time_ns,
                                   threshold, selected)
        results[split] = summarize(alarms, frame.label, frame.end_time_ns)
    report = {"protocol": "validation-only selection of shortest causal same-day 5-minute hit streak",
              "model": selection["selected"], "threshold": threshold,
              "selected_consecutive_windows": selected,
              "selection_criteria": "zero validation normal-window alarms and all validation fault days detected",
              "validation_candidates": candidates, "results": results,
              "limitations": ["Test and stress periods were inspected previously; results are retrospective.",
                              "One plant and few independent fault days; no industrial false-alarm guarantee.",
                              "Persistence adds at least (selected_consecutive_windows - 1) * 5 minutes after first high-score window."]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"selected_consecutive_windows": selected, "results": results}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, default=Path("data/processed/la_reunion_v1"))
    parser.add_argument("--run", type=Path, default=Path("reports/reunion_v1_mps"))
    parser.add_argument("--output", type=Path, default=Path("reports/reunion_alarm_persistence_v1.json"))
    args = parser.parse_args()
    run(args.prepared, args.run, args.output)
