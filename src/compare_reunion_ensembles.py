"""Exploratory fixed-weight ensemble comparison on development validation only.

This script never reads held-out test or normal-stress files. The validation
period has already informed prior research, so scores are not fresh holdouts.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

from train_reunion import load_split


SEEDS = (2031, 2032, 2033)


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    validation = load_split(args.prepared, "validation")
    labels = validation["y"]
    days = np.asarray(pd.to_datetime(validation["end_time_ns"], utc=True)
                      .tz_convert("Indian/Reunion").date, dtype="str")
    cnn = np.stack([np.load(args.training_output / f"multiscale_cnn_seed{seed}_validation.npy")
                    for seed in SEEDS])
    transformer = np.stack([np.load(args.training_output / f"transformer_seed{seed}_validation.npy")
                            for seed in SEEDS])
    if any(len(item) != len(labels) for item in (*cnn, *transformer)):
        raise ValueError("Prediction and validation lengths differ")
    candidates = {
        "selected_transformer_seed2032": transformer[1],
        "transformer_three_seed_mean": transformer.mean(axis=0),
        "cnn_three_seed_mean": cnn.mean(axis=0),
        "best_transformer_plus_best_cnn_equal": (transformer[1] + cnn[0]) / 2,
        "transformer_mean_plus_cnn_mean_equal": (transformer.mean(axis=0) + cnn.mean(axis=0)) / 2,
    }
    if (args.rnn_output is None) != (args.lstm_output is None):
        raise ValueError("Provide both recurrent output directories or neither")
    if args.rnn_output is not None:
        rnn = np.load(args.rnn_output / "rnn_seed2031_validation.npy")
        lstm = np.load(args.lstm_output / "lstm_seed2032_validation.npy")
        if len(rnn) != len(labels) or len(lstm) != len(labels):
            raise ValueError("Recurrent prediction and validation lengths differ")
        candidates.update({
            "rnn_best_seed2031": rnn,
            "lstm_best_seed2032": lstm,
            "transformer_plus_rnn_equal": (transformer[1] + rnn) / 2,
            "transformer_plus_cnn_plus_rnn_equal": (transformer[1] + cnn[0] + rnn) / 3,
            "four_architectures_equal": (transformer[1] + cnn[0] + rnn + lstm) / 4,
        })
    point = {name: float(average_precision_score(labels, scores))
             for name, scores in candidates.items()}
    rng = np.random.default_rng(args.bootstrap_seed)
    unique_days = np.unique(days)
    by_day = {day: np.flatnonzero(days == day) for day in unique_days}
    deltas = {name: [] for name in candidates if name != "selected_transformer_seed2032"}
    for _ in range(args.bootstrap_replicates):
        sampled = rng.choice(unique_days, len(unique_days), replace=True)
        indices = np.concatenate([by_day[day] for day in sampled])
        if len(np.unique(labels[indices])) < 2:
            continue
        reference = average_precision_score(labels[indices],
                                            candidates["selected_transformer_seed2032"][indices])
        for name in deltas:
            deltas[name].append(average_precision_score(labels[indices], candidates[name][indices])
                                - reference)
    report = {"protocol": "Fixed equal-weight development-validation comparison; no held-out data accessed",
              "validation_days": len(unique_days), "seeds": SEEDS,
              "average_precision": point,
              "day_bootstrap_delta_ap_vs_selected_transformer": {
                  name: {"median": float(np.median(values)),
                         "ci95": np.quantile(values, [.025, .975]).tolist(),
                         "valid_resamples": len(values)}
                  for name, values in deltas.items()},
              "limitations": ["Validation period previously used to select the best transformer and CNN seeds.",
                              "Window overlap makes per-window uncertainty too optimistic; resampling whole days helps but there are few days.",
                              "No ensemble is promoted to production without a new independent site/event evaluation."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--training-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rnn-output", type=Path)
    parser.add_argument("--lstm-output", type=Path)
    parser.add_argument("--bootstrap-replicates", type=int, default=1000)
    parser.add_argument("--bootstrap-seed", type=int, default=2026)
    args = parser.parse_args()
    if args.bootstrap_replicates < 1:
        parser.error("bootstrap-replicates must be positive")
    run(args)
