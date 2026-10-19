"""Conservative GPVS-Faults pilot: train in one mode, evaluate in the other.

The readme only says faults are introduced halfway; no exact onset is given.
For F1-F7, only first-third (healthy) and final-third (fault) windows are
scored. The middle third is discarded. F0 is healthy throughout. Results are
laboratory mode-transfer evidence, never a field deployment estimate.
"""

import argparse
import json
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import average_precision_score, roc_auc_score


CHANNELS = ("Ipv", "Vpv", "Vdc", "ia", "ib", "ic", "va", "vb", "vc",
            "Iabc", "If", "Vabc", "Vf")


def summarize(x):
    q05, q25, q50, q75, q95 = np.quantile(x, (0.05, 0.25, 0.5, 0.75, 0.95), axis=0)
    return np.concatenate((q50, q75 - q25, q95 - q05,
                           np.sqrt(np.mean(x * x, axis=0)), np.std(x, axis=0)))


def load_windows(archive, seconds):
    all_features, all_labels, all_modes, all_files, all_reference, audit = [], [], [], [], [], {}
    with ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise ValueError("GPVS-Faults archive has a CRC error")
        for fault in range(8):
            for mode in "LM":
                stem = f"F{fault}{mode}"
                with bundle.open(f"CSV_Files/{stem}.csv") as stream:
                    frame = pd.read_csv(stream)
                if frame.columns.tolist() != ["Time", *CHANNELS]:
                    raise ValueError(f"Unexpected GPVS-Faults columns: {stem}")
                values = frame.loc[:, CHANNELS].to_numpy(np.float64)
                times = frame.Time.to_numpy(np.float64)
                delta = np.diff(times)
                if not (np.isfinite(values).all() and np.isfinite(times).all()
                        and np.all(delta > 0)):
                    raise ValueError(f"Non-finite or unsorted measurements: {stem}")
                duration = float(times[-1] - times[0])
                dt = float(np.median(delta))
                windows = 0
                label_counts = {"healthy": 0, "fault": 0, "excluded": 0}
                left = 0
                while left < len(times):
                    stop_time = times[left] + seconds
                    right = int(np.searchsorted(times, stop_time, side="left"))
                    if right <= left:
                        right = left + 1
                    if right > len(times) or times[right - 1] - times[left] < seconds * .9:
                        break
                    start = times[left] - times[0]
                    end = times[right - 1] - times[0]
                    if fault == 0:
                        label = 0
                    elif end <= duration / 3:
                        label = 0
                    elif start >= duration * 2 / 3:
                        label = 1
                    else:
                        label = None
                    if label is None:
                        label_counts["excluded"] += 1
                    else:
                        all_features.append(summarize(values[left:right]))
                        all_labels.append(label)
                        all_modes.append(mode)
                        all_files.append(stem)
                        all_reference.append(end <= duration / 3)
                        label_counts["fault" if label else "healthy"] += 1
                    windows += 1
                    left = right
                audit[stem] = {"rows": len(frame), "duration_seconds": duration,
                               "median_sample_interval_seconds": dt,
                               "approximate_sampling_hz": 1 / dt,
                               "windows": windows, **label_counts}
    return (np.asarray(all_features, dtype=np.float32),
            np.asarray(all_labels, dtype=np.int8), np.asarray(all_modes),
            np.asarray(all_files), np.asarray(all_reference), audit)


def subtract_healthy_reference(x, files, reference):
    """Calibrate each experiment using only its documented pre-injection prefix."""
    adjusted = x.copy()
    for name in sorted(set(files)):
        mask = files == name
        baseline = np.median(x[mask & reference], axis=0)
        adjusted[mask] -= baseline
    return adjusted


def evaluate(x, y, modes, files, seed):
    results = {}
    for source, target in (("L", "M"), ("M", "L")):
        train, test = modes == source, modes == target
        # A source-mode threshold must be selected without fitting on the
        # same experiment being scored. It is frozen before the target mode.
        oof_scores = np.empty(int(train.sum()), dtype=np.float64)
        source_files = files[train]
        for held_out in sorted(set(source_files)):
            fit = train & (files != held_out)
            held = train & (files == held_out)
            fold_model = ExtraTreesClassifier(n_estimators=500, min_samples_leaf=2,
                                              max_features="sqrt", class_weight="balanced",
                                              random_state=seed, n_jobs=-1)
            fold_model.fit(x[fit], y[fit])
            oof_scores[source_files == held_out] = fold_model.predict_proba(x[held])[:, 1]
        threshold = float(np.quantile(oof_scores[y[train] == 0], .99, method="higher"))
        model = ExtraTreesClassifier(n_estimators=500, min_samples_leaf=2,
                                     max_features="sqrt", class_weight="balanced",
                                     random_state=seed, n_jobs=-1)
        model.fit(x[train], y[train])
        scores = model.predict_proba(x[test])[:, 1]
        alarms = scores >= threshold
        item = {"train_mode": source, "test_mode": target,
                "train_windows": int(train.sum()), "test_windows": int(test.sum()),
                "test_fault_windows": int(y[test].sum()),
                "average_precision": float(average_precision_score(y[test], scores)),
                "roc_auc": float(roc_auc_score(y[test], scores)),
                "source_oof_threshold_for_1pct_healthy_window_alarm": threshold,
                "target_fault_recall_at_threshold": float(np.mean(alarms[y[test] == 1])),
                "target_healthy_alarm_rate_at_threshold": float(np.mean(alarms[y[test] == 0])),
                "target_fault_windows_detected": int(np.sum(alarms & (y[test] == 1))),
                "target_healthy_windows_alarmed": int(np.sum(alarms & (y[test] == 0))),
                "by_experiment": {}}
        for name in sorted(set(files[test])):
            mask = test & (files == name)
            these = scores[files[test] == name]
            item["by_experiment"][name] = {
                "windows": int(mask.sum()), "fault_windows": int(y[mask].sum()),
                "mean_score": float(np.mean(these)),
                "mean_healthy_score": float(np.mean(these[y[mask] == 0])) if np.any(y[mask] == 0) else None,
                "mean_fault_score": float(np.mean(these[y[mask] == 1])) if np.any(y[mask] == 1) else None,
                "healthy_windows_alarmed": int(np.sum((these >= threshold) & (y[mask] == 0))),
                "fault_windows_detected": int(np.sum((these >= threshold) & (y[mask] == 1))),
            }
        results[f"{source}_to_{target}"] = item
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path,
                        default=Path("data/raw/gpvs_faults/CSV_Files.zip"))
    parser.add_argument("--output", type=Path,
                        default=Path("reports/gpvs_mode_transfer_v1.json"))
    parser.add_argument("--window-seconds", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=2032)
    args = parser.parse_args()
    if args.window_seconds <= 0 or args.output.exists():
        raise ValueError("Use a positive window duration and a new output path")
    x, y, modes, files, reference, audit = load_windows(args.archive, args.window_seconds)
    relative = subtract_healthy_reference(x, files, reference)
    result = {"protocol": "first/final third only; F0 all healthy; train one mode/test other",
              "limitations": ["Fault onset is not timestamped; labels are approximate.",
                              "Each fault/mode has one short laboratory experiment.",
                              "Overlapping modes share the same fault designs, not independent sites.",
                              "The relative variant requires a known healthy startup prefix per experiment."],
              "window_seconds": args.window_seconds, "seed": args.seed,
              "features": "per-channel median, IQR, p95-p05, RMS, standard deviation; no Time feature",
              "source_audit": audit,
              "results": {"absolute": evaluate(x, y, modes, files, args.seed),
                          "healthy_reference_subtracted": evaluate(relative, y, modes, files, args.seed)}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({variant: {direction: {m: item[m] for m in
                      ("train_windows", "test_windows", "test_fault_windows",
                       "average_precision", "roc_auc", "source_oof_threshold_for_1pct_healthy_window_alarm",
                       "target_fault_recall_at_threshold", "target_healthy_alarm_rate_at_threshold")}
                      for direction, item in runs.items()}
                      for variant, runs in result["results"].items()}, indent=2))


if __name__ == "__main__":
    main()
