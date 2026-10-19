"""Development-only channel-dropout robustness comparison.

Both arms use the same La Reunion train/validation split and Transformer
architecture. The dropout arm corrupts training inputs only; validation stays
clean for model selection and is evaluated with the frozen quality-gated
stress script afterwards. Existing held-out periods are not opened.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import torch

from train_reunion import (choose_threshold, day_alert_summary, load_split,
                           summarize_scores, train_one)


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    device = torch.device(args.device if args.device != "auto" else
                          ("cuda" if torch.cuda.is_available() else
                           "mps" if torch.backends.mps.is_available() else "cpu"))
    torch.set_num_threads(min(torch.get_num_threads(), 8))
    train = load_split(args.prepared, "train")
    validation = load_split(args.prepared, "validation")
    manifest_bytes = (args.prepared / "manifest.json").read_bytes()
    report = {
        "protocol": "paired Transformer channel-dropout augmentation vs clean training; train/validation only",
        "prepared_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "device": str(device), "seeds": args.seeds,
        "channel_dropout": args.channel_dropout,
        "channel_dropout_span": args.channel_dropout_span,
        "limitations": ["One plant and one development validation period.",
                        "Validation is used for early stopping and threshold description.",
                        "Old test and normal-stress partitions are not opened.",
                        "Synthetic channel loss is not a substitute for field communication traces."],
        "runs": {"clean": {}, "channel_dropout": {}}
    }
    for mode, dropout in (("clean", 0.0), ("channel_dropout", args.channel_dropout)):
        folder = args.output / mode
        folder.mkdir()
        for seed in args.seeds:
            record = train_one("transformer", seed, train, validation, folder, device, args,
                               channel_dropout=dropout,
                               channel_dropout_span=args.channel_dropout_span)
            scores = np.load(folder / f"{record['tag']}_validation.npy")
            threshold = choose_threshold(validation["y"], scores)
            report["runs"][mode][str(seed)] = {
                "validation_ap": record["validation_ap"],
                "best_epoch": record["best_epoch"],
                "epochs_run": record["epochs_run"],
                "elapsed_seconds": record["elapsed_seconds"],
                "threshold_chosen_on_validation": threshold,
                "window_metrics": summarize_scores(validation["y"], scores, threshold),
                "day_metrics": day_alert_summary(validation, scores, threshold),
            }
            (args.output / "progress.json").write_text(json.dumps(report, indent=2) + "\n")
    for mode in report["runs"]:
        values = [item["validation_ap"] for item in report["runs"][mode].values()]
        report[mode + "_mean_validation_ap"] = float(np.mean(values))
        report[mode + "_std_validation_ap"] = float(np.std(values))
    report["dropout_minus_clean_mean_ap"] = (
        report["channel_dropout_mean_validation_ap"] - report["clean_mean_validation_ap"])
    (args.output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in
                      ("clean_mean_validation_ap", "channel_dropout_mean_validation_ap",
                       "dropout_minus_clean_mean_ap")}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    parser.add_argument("--seeds", type=int, nargs="+", default=[2031, 2032, 2033])
    parser.add_argument("--channel-dropout", type=float, default=0.15)
    parser.add_argument("--channel-dropout-span", type=int, default=5)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=8e-4)
    args = parser.parse_args()
    if min(args.channel_dropout, args.channel_dropout_span, args.epochs,
           args.patience, args.batch_size, args.lr) <= 0:
        parser.error("Training controls must be positive")
    run(args)
