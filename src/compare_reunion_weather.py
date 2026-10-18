"""Paired electrical-only versus electrical+weather development comparison.

The 18-channel weather-prepared windows are the single source for both arms.
Only train and validation splits are opened; test/normal-stress stay unused.
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
    manifest_bytes = (args.prepared / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    features = manifest["features"]
    if len(features) != 18 or features[-4:] != ["weather_DTI", "weather_GTI", "weather_TA", "weather_TPV"]:
        raise ValueError("Expected 14 electrical and four weather channels")
    train_all = load_split(args.prepared, "train")
    validation_all = load_split(args.prepared, "validation")
    device = torch.device(args.device if args.device != "auto" else
                          "cuda" if torch.cuda.is_available() else
                          "mps" if torch.backends.mps.is_available() else "cpu")
    torch.set_num_threads(min(torch.get_num_threads(), 8))
    args.output.mkdir(parents=True)
    report = {"protocol": "Paired same-window 14 electrical versus 18 electrical+weather channels; train/validation only",
              "prepared_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
              "source_splits": {name: {"windows": int(len(data["y"])),
                                       "fault": int(data["y"].sum())}
                                for name, data in (("train", train_all), ("validation", validation_all))},
              "device": str(device), "seeds": args.seeds,
              "limitations": ["One plant and one validation period; not external validation.",
                              "Thresholds and day statistics are selected/described on validation.",
                              "Existing test and normal-stress periods are not opened."],
              "runs": {}}
    for name, channels in (("electrical", 14), ("with_weather", 18)):
        train = {**train_all, "x": train_all["x"][:, :channels, :]}
        validation = {**validation_all, "x": validation_all["x"][:, :channels, :]}
        folder = args.output / name
        folder.mkdir()
        report["runs"][name] = {}
        for seed in args.seeds:
            record = train_one("multiscale_cnn", seed, train, validation,
                               folder, device, args)
            scores = np.load(folder / f"{record['tag']}_validation.npy")
            threshold = choose_threshold(validation["y"], scores)
            report["runs"][name][str(seed)] = {
                "validation_ap": record["validation_ap"],
                "best_epoch": record["best_epoch"],
                "epochs_run": record["epochs_run"],
                "elapsed_seconds": record["elapsed_seconds"],
                "threshold_chosen_on_validation": threshold,
                "window_metrics": summarize_scores(validation["y"], scores, threshold),
                "day_metrics": day_alert_summary(validation, scores, threshold)}
            (args.output / "progress.json").write_text(json.dumps(report, indent=2) + "\n")
    for name in ("electrical", "with_weather"):
        report[name + "_mean_validation_ap"] = float(np.mean([
            run["validation_ap"] for run in report["runs"][name].values()]))
    report["weather_minus_electrical_mean_ap"] = (report["with_weather_mean_validation_ap"] -
                                                  report["electrical_mean_validation_ap"])
    (args.output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in
                      ("electrical_mean_validation_ap", "with_weather_mean_validation_ap",
                       "weather_minus_electrical_mean_ap")}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    parser.add_argument("--seeds", nargs="+", type=int, default=[2031, 2032, 2033])
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=8e-4)
    args = parser.parse_args()
    if min(args.epochs, args.patience, args.batch_size, args.lr) <= 0:
        parser.error("Training controls must be positive")
    run(args)
