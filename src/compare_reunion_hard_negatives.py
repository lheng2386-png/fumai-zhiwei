"""Paired hard-negative training on the two train-only rolling backtests.

Each fold refits normalization on its earlier training dates, chooses a
threshold on tuning dates, and evaluates on later dates. The original
validation, test, and normal-stress files are not opened.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import average_precision_score

from backtest_reunion import FOLDS, fold_data
from train_reunion import (choose_threshold, day_alert_summary, selected_scores,
                           summarize_scores, train_one)


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    torch.set_num_threads(min(torch.get_num_threads(), 8))
    device = torch.device(args.device if args.device != "auto" else
                          "cuda" if torch.cuda.is_available() else
                          "mps" if torch.backends.mps.is_available() else "cpu")
    report = {"protocol": "paired CNN loss comparison; rolling train-only folds; threshold from tuning period",
              "prepared_manifest_sha256": hashlib.sha256((args.prepared / "manifest.json").read_bytes()).hexdigest(),
              "device": str(device), "alphas": args.alphas, "seeds": args.seeds,
              "limitations": ["Only one plant and two historical episodes; the second outer fold has few normal windows.",
                              "Old validation/test/normal-stress files are not opened."],
              "folds": {}}
    for fold_name, *dates in FOLDS:
        train, tuning, outer, scaler, day_ranges = fold_data(args.prepared, *dates)
        fold_report = {"dates": day_ranges,
                       "train_windows": len(train["y"]),
                       "tuning_windows": len(tuning["y"]),
                       "outer_windows": len(outer["y"]),
                       "runs": {}}
        report["folds"][fold_name] = fold_report
        for alpha in args.alphas:
            label = f"alpha_{alpha:g}"
            folder = args.output / fold_name / label
            folder.mkdir(parents=True)
            fold_report["runs"][label] = {}
            for seed in args.seeds:
                record = train_one("multiscale_cnn", seed, train, tuning,
                                   folder, device, args, hard_negative_alpha=alpha)
                tune_scores = np.load(folder / f"{record['tag']}_validation.npy")
                threshold = choose_threshold(tuning["y"], tune_scores)
                outer_scores = selected_scores(
                    {"selected_members": [record["tag"]]}, outer, folder,
                    device, args.batch_size)
                fold_report["runs"][label][str(seed)] = {
                    "tuning_ap": record["validation_ap"],
                    "best_epoch": record["best_epoch"],
                    "elapsed_seconds": record["elapsed_seconds"],
                    "tuning_threshold": threshold,
                    "outer_ap": float(average_precision_score(outer["y"], outer_scores)),
                    "outer_window_metrics": summarize_scores(outer["y"], outer_scores, threshold),
                    "outer_day_metrics": day_alert_summary(outer, outer_scores, threshold)}
                (args.output / "progress.json").write_text(json.dumps(report, indent=2) + "\n")
    for fold in report["folds"].values():
        fold["mean_outer_ap_by_alpha"] = {label: float(np.mean([
            item["outer_ap"] for item in entries.values()]))
            for label, entries in fold["runs"].items()}
    (args.output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({name: fold["mean_outer_ap_by_alpha"]
                      for name, fold in report["folds"].items()}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("auto", "cpu", "mps", "cuda"), default="auto")
    parser.add_argument("--seeds", nargs="+", type=int, default=[2031, 2032, 2033])
    parser.add_argument("--alphas", nargs="+", type=float, default=[0.0, 2.0, 4.0])
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=8e-4)
    args = parser.parse_args()
    if min(args.epochs, args.patience, args.batch_size, args.lr) <= 0 or min(args.alphas) < 0:
        parser.error("Training controls must be positive and alphas nonnegative")
    if len(set(args.alphas)) != len(args.alphas):
        parser.error("Alphas must be unique")
    run(args)
