"""Validation-only stress evaluation for the mask-aware Transformer."""

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import average_precision_score

from evaluate_reunion_robustness import perturb
from train_reunion import choose_threshold, summarize_scores
from train_reunion_mask_aware import predict
from train_arch_compare import TransformerPV


def load_model(path, base_channels):
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    model = TransformerPV(channels=base_channels * 2, width=64, layers=3, classes=2)
    model.load_state_dict(checkpoint["state_dict"])
    return model


def with_mask(x, scenario, seed):
    corrupted = perturb(x, scenario, seed)
    mask = np.ones_like(corrupted, dtype=np.float32)
    if scenario == "last_5min_mean_imputation":
        mask[:, :, -5:] = 0.0
    elif scenario == "control_channels_last_5min_missing":
        mask[:, 6:14, -5:] = 0.0
    return corrupted, mask


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    with np.load(args.prepared / "validation.npz") as data:
        x, y = data["x"], data["y"]
    mask = np.ones_like(x, dtype=np.float32)
    model = load_model(args.checkpoint, x.shape[1])
    clean_scores = predict(model, x, mask, torch.device("cpu"), 256)
    threshold = choose_threshold(y, clean_scores)
    report = {"protocol": "frozen mask-aware checkpoints; original validation only",
              "checkpoint": str(args.checkpoint),
              "threshold_from_clean_validation": threshold,
              "scenarios": {},
              "limitations": ["Synthetic missingness is not a field test.",
                              "No old test or normal-stress partition is read."]}
    for scenario in ("clean", "noise_0p1_std", "last_5min_mean_imputation",
                     "control_channels_last_5min_missing", "last_10min_sensor_freeze"):
        xx, mm = with_mask(x, scenario, 2031)
        scores = predict(model, xx, mm, torch.device("cpu"), 256)
        metrics = summarize_scores(y, scores, threshold)
        metrics["average_precision"] = float(average_precision_score(y, scores))
        report["scenarios"][scenario] = metrics
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: {m: v for m, v in value.items()
                          if m in ("average_precision", "fault_recall", "false_positive_rate")}
                      for k, value in report["scenarios"].items()}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, default=Path("data/processed/la_reunion_v1"))
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
