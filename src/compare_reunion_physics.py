"""Test train-scaled physical context channels on matched Reunion windows.

Only weather-prepared train and validation arrays are opened. Derived channels
use the raw physical values and their scale is fitted on train windows only.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import torch

from train_reunion import (choose_threshold, day_alert_summary, load_split,
                           summarize_scores, train_one)


DERIVED = ("faulted_power_per_gti", "control_power_per_gti", "module_air_temperature_gap")


def derived_channels(x, mean, scale):
    if x.shape[1] != 18:
        raise ValueError("Expected 18 source channels")
    raw = x * scale[None, :, None] + mean[None, :, None]
    gti = np.maximum(raw[:, 15], 0.0) + 100.0
    derived = np.stack((raw[:, 0] / gti,
                        raw[:, 6] / gti,
                        raw[:, 17] - raw[:, 16]), axis=1).astype(np.float32)
    if not np.isfinite(derived).all():
        raise ValueError("Non-finite physical channel")
    return derived


def augment(train, validation, normalization):
    mean, scale = normalization["mean"], normalization["scale"]
    train_derived = derived_channels(train["x"], mean, scale)
    validation_derived = derived_channels(validation["x"], mean, scale)
    extra_mean = train_derived.mean(axis=(0, 2), dtype=np.float64).astype(np.float32)
    extra_scale = np.maximum(train_derived.std(axis=(0, 2), dtype=np.float64), 1e-4).astype(np.float32)
    def add(data, channels):
        standard = ((channels - extra_mean[None, :, None]) / extra_scale[None, :, None]).astype(np.float32)
        return {**data, "x": np.concatenate((data["x"], standard), axis=1)}
    return add(train, train_derived), add(validation, validation_derived), {
        "features": DERIVED, "mean": extra_mean.tolist(), "scale": extra_scale.tolist(),
        "fit": "all train windows only"}


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    source_manifest = (args.prepared / "manifest.json").read_bytes()
    metadata = json.loads(source_manifest)
    if len(metadata["features"]) != 18:
        raise ValueError("Expected 18-channel meteorological source")
    train = load_split(args.prepared, "train")
    validation = load_split(args.prepared, "validation")
    with np.load(args.prepared / "normalization.npz") as stream:
        normalization = {key: stream[key] for key in ("mean", "scale")}
    augmented_train, augmented_validation, engineered_scaler = augment(train, validation, normalization)
    device = torch.device(args.device if args.device != "auto" else
                          "cuda" if torch.cuda.is_available() else
                          "mps" if torch.backends.mps.is_available() else "cpu")
    torch.set_num_threads(min(torch.get_num_threads(), 8))
    args.output.mkdir(parents=True)
    report = {"protocol": "Matched-window raw weather versus train-scaled physical channels; train/validation only",
              "source_manifest_sha256": hashlib.sha256(source_manifest).hexdigest(),
              "device": str(device), "seeds": args.seeds, "learning_rate": args.lr,
              "engineered_scaler": engineered_scaler,
              "train_windows": len(train["y"]), "validation_windows": len(validation["y"]),
              "limitations": ["Single plant and validation period; no independent test.",
                              "Threshold and day statistics are descriptive on validation.",
                              "Existing test and normal-stress periods are not opened."],
              "runs": {}}
    for name, training, validating in (("weather_raw", train, validation),
                                       ("weather_physics", augmented_train, augmented_validation)):
        folder = args.output / name
        folder.mkdir()
        report["runs"][name] = {}
        for seed in args.seeds:
            record = train_one("multiscale_cnn", seed, training, validating, folder, device, args)
            scores = np.load(folder / f"{record['tag']}_validation.npy")
            threshold = choose_threshold(validating["y"], scores)
            report["runs"][name][str(seed)] = {
                "validation_ap": record["validation_ap"], "best_epoch": record["best_epoch"],
                "elapsed_seconds": record["elapsed_seconds"],
                "window_metrics": summarize_scores(validating["y"], scores, threshold),
                "day_metrics": day_alert_summary(validating, scores, threshold)}
            (args.output / "progress.json").write_text(json.dumps(report, indent=2) + "\n")
    for name in ("weather_raw", "weather_physics"):
        report[name + "_mean_validation_ap"] = float(np.mean([
            item["validation_ap"] for item in report["runs"][name].values()]))
    report["physics_minus_raw_mean_ap"] = (report["weather_physics_mean_validation_ap"] -
                                           report["weather_raw_mean_validation_ap"])
    (args.output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in
                      ("weather_raw_mean_validation_ap", "weather_physics_mean_validation_ap",
                       "physics_minus_raw_mean_ap")}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("auto", "cpu", "mps", "cuda"), default="auto")
    parser.add_argument("--seeds", nargs="+", type=int, default=[2031, 2032, 2033])
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=8e-4)
    args = parser.parse_args()
    if min(args.epochs, args.patience, args.batch_size, args.lr) <= 0:
        parser.error("Training controls must be positive")
    run(args)
