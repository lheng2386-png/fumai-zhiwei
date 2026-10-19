"""Validation-only stress evaluation of the frozen MixUp Transformer ensemble."""

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import average_precision_score

from evaluate_reunion_robustness import perturb
from train_arch_compare import TransformerPV
from train_reunion import choose_threshold, predict, summarize_scores


def load_models(directory):
    models = []
    for seed in (2031, 2032, 2033):
        state = torch.load(directory / f"mixup_transformer_seed{seed}.pt",
                           map_location="cpu", weights_only=True)
        model = TransformerPV(channels=14, width=64, layers=3, classes=2)
        model.load_state_dict(state["state_dict"])
        model.eval()
        models.append(model)
    return models


def scores(models, x):
    return np.mean([predict(model, x, torch.device("cpu"), 256) for model in models], axis=0)


def run(prepared, run_dir, output):
    if output.exists():
        raise FileExistsError(output)
    with np.load(prepared / "validation.npz") as data:
        x, y = data["x"], data["y"]
    models = load_models(run_dir)
    clean = scores(models, x)
    threshold = choose_threshold(y, clean)
    report = {"protocol": "frozen three-seed MixUp ensemble; validation stress only",
              "threshold_from_clean_validation": threshold,
              "scenarios": {},
              "limitations": ["Same-site validation was used for checkpoint selection.",
                              "Synthetic corruption is not a field communication-failure distribution.",
                              "Old test and normal-stress partitions are not read."]}
    for scenario in ("clean", "noise_0p1_std", "last_5min_mean_imputation",
                     "control_channels_last_5min_missing", "last_10min_sensor_freeze"):
        stressed = perturb(x, scenario, 2031)
        prediction = scores(models, stressed)
        metrics = summarize_scores(y, prediction, threshold)
        metrics["average_precision"] = float(average_precision_score(y, prediction))
        report["scenarios"][scenario] = metrics
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({name: {key: value for key, value in metrics.items()
                             if key in ("average_precision", "fault_recall", "false_positive_rate")}
                      for name, metrics in report["scenarios"].items()}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, default=Path("data/processed/la_reunion_v1"))
    parser.add_argument("--run-dir", type=Path, default=Path("reports/reunion_mixup_mps_v1"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.prepared, args.run_dir, args.output)
