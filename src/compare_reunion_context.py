"""Compare 5/15/30-minute Transformer context on matched training-period folds.

Only the original train.npz is used. Every context has the same endpoints,
labels and fold-specific normalization. Epochs, thresholds and the context
choice use inner tuning days; outer scores are computed after that choice is
saved. These historical folds have already been inspected in other studies.
"""

import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import torch
from sklearn.metrics import average_precision_score

from backtest_reunion import FOLDS, fold_data
from train_reunion import (choose_threshold, day_alert_summary, make_model,
                           predict, summarize_scores, train_one)


def trailing_context(data, minutes):
    """Preserve endpoint/label membership while taking a contiguous suffix."""
    x = data["x"]
    if x.ndim != 3 or not 2 <= minutes <= x.shape[-1]:
        raise ValueError("Context must be between 2 and the source window length")
    if len(data["y"]) != len(x) or len(data["end_time_ns"]) != len(x):
        raise ValueError("Window, label and endpoint counts differ")
    return {**data, "x": np.ascontiguousarray(x[:, :, -minutes:])}


def prepare_fold(prepared, dates, contexts):
    """Read train only and reuse one earlier-training scaler for all contexts."""
    training, tuning, outer, scaler, days = fold_data(prepared, *dates)
    if training["x"].shape[-1] != 30:
        raise ValueError("This comparison requires 30-minute source windows")
    return ({minutes: tuple(trailing_context(data, minutes)
                            for data in (training, tuning, outer))
             for minutes in contexts}, scaler, days)


def select_context(tuning_ap):
    """Use mean inner AP; an exact tie preserves the longer-context baseline."""
    return max(tuning_ap, key=lambda minutes: (np.mean(tuning_ap[minutes]), minutes))


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    if (30 not in args.contexts or len(args.contexts) < 2
            or len(set(args.contexts)) != len(args.contexts)
            or any(not 2 <= value <= 30 for value in args.contexts)):
        raise ValueError("Include the 30-minute baseline and unique shorter contexts")
    if not args.seeds or len(set(args.seeds)) != len(args.seeds):
        raise ValueError("Provide unique seeds")
    if min(args.epochs, args.patience, args.batch_size, args.lr) <= 0:
        raise ValueError("Training controls must be positive")
    manifest_bytes = (args.prepared / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest["window"] != 30 or len(manifest["features"]) != 14:
        raise ValueError("Expected the 30-minute, 14-channel electrical dataset")
    device = torch.device(args.device if args.device != "auto" else
                          "cuda" if torch.cuda.is_available() else
                          "mps" if torch.backends.mps.is_available() else "cpu")
    torch.set_num_threads(min(torch.get_num_threads(), 8))
    started = time.monotonic()
    args.output.mkdir(parents=True)
    report = {
        "protocol": "matched-endpoint Transformer context comparison in train-only forward folds",
        "prepared_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "device": str(device), "torch_version": torch.__version__,
        "contexts_minutes": args.contexts, "seeds": args.seeds,
        "epochs": args.epochs, "patience": args.patience,
        "batch_size": args.batch_size, "lr": args.lr,
        "normalization": "One scaler per fold, fitted on its earlier 30-minute training windows; shared by all contexts",
        "selection": "Epoch and threshold on inner tuning; context by mean seed tuning AP, ties favor longer context; save before outer inference",
        "held_out_status": "Original validation/test/normal_stress files are not read",
        "limitations": [
            "Same plant with experimental shading faults only.",
            "These outer fold dates were inspected in earlier experiments; this is a retrospective diagnostic.",
            "The second outer fold has few normal windows and no full normal-only day.",
            "Shorter contexts use the same eligible 30-minute endpoints; this does not assess extra availability under missing data.",
            "Changing sequence length changes random-number consumption even with matched seeds.",
        ],
        "status": "training", "folds": {},
    }
    write_json(args.output / "progress.json", report)
    for name, *dates in FOLDS:
        datasets, scaler, days = prepare_fold(args.prepared, dates, args.contexts)
        folder = args.output / name
        folder.mkdir()
        np.savez(folder / "normalization.npz", **scaler)
        training, tuning, outer = datasets[30]
        fold = {
            "dates": days,
            "train_windows": len(training["y"]),
            "tuning_windows": len(tuning["y"]),
            "outer_windows": len(outer["y"]),
            "outer_normal_windows": int((outer["y"] == 0).sum()),
            "outer_fault_windows": int((outer["y"] == 1).sum()),
            "endpoint_sha256": {
                split: hashlib.sha256(data["end_time_ns"].tobytes()).hexdigest()
                for split, data in zip(("train", "tuning", "outer"), datasets[30])
            },
            "runs": {},
        }
        report["folds"][name] = fold
        tuning_ap = {}
        # Complete all inner selection before making any outer predictions.
        for minutes in args.contexts:
            training, tuning, _ = datasets[minutes]
            candidate_folder = folder / f"context_{minutes}"
            candidate_folder.mkdir()
            runs = fold["runs"][str(minutes)] = {}
            tuning_ap[minutes] = []
            for seed in args.seeds:
                record = train_one("transformer", seed, training, tuning,
                                   candidate_folder, device, args)
                tuning_scores = np.load(
                    candidate_folder / f"{record['tag']}_validation.npy")
                threshold = choose_threshold(tuning["y"], tuning_scores)
                runs[str(seed)] = {
                    "seed": seed, "context_minutes": minutes,
                    "tuning_ap": record["validation_ap"],
                    "tuning_threshold": threshold,
                    "best_epoch": record["best_epoch"],
                    "epochs_run": record["epochs_run"],
                    "training_seconds": record["elapsed_seconds"],
                    "parameters": record["parameters"], "history": record["history"],
                }
                tuning_ap[minutes].append(record["validation_ap"])
                checkpoint_path = candidate_folder / f"{record['tag']}.pt"
                checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
                checkpoint["context_minutes"] = minutes
                checkpoint["source_window_minutes"] = 30
                torch.save(checkpoint, checkpoint_path)
                write_json(args.output / "progress.json", report)
        decision = {
            "mean_tuning_ap": {str(k): float(np.mean(v)) for k, v in tuning_ap.items()},
            "selected_context_minutes": select_context(tuning_ap),
            "outer_status": "not scored",
        }
        write_json(folder / "inner_selection.json", decision)
        fold.update({k: v for k, v in decision.items() if k != "outer_status"})
        report["status"] = "evaluating_outer"
        for minutes in args.contexts:
            _, _, outer = datasets[minutes]
            candidate_folder = folder / f"context_{minutes}"
            for seed in args.seeds:
                run_record = fold["runs"][str(minutes)][str(seed)]
                evaluation_started = time.monotonic()
                checkpoint = torch.load(
                    candidate_folder / f"transformer_seed{seed}.pt",
                    map_location="cpu", weights_only=True)
                model = make_model("transformer", outer["x"].shape[1]).to(device)
                model.load_state_dict(checkpoint["state_dict"])
                scores = predict(model, outer["x"], device, args.batch_size)
                threshold = run_record["tuning_threshold"]
                metrics = summarize_scores(outer["y"], scores, threshold)
                run_record.update({
                    "outer_ap": float(average_precision_score(outer["y"], scores)),
                    "outer_window_metrics": metrics,
                    "outer_healthy_window_false_alarms": metrics["confusion_matrix"][0][1],
                    "outer_day_metrics": day_alert_summary(outer, scores, threshold),
                    "outer_evaluation_seconds": time.monotonic() - evaluation_started,
                })
                np.savez_compressed(
                    candidate_folder / f"transformer_seed{seed}_outer_predictions.npz",
                    scores=scores, y=outer["y"], end_time_ns=outer["end_time_ns"])
                print(json.dumps({
                    "fold": name, "context_minutes": minutes, "seed": seed,
                    "tuning_ap": run_record["tuning_ap"],
                    "outer_ap": run_record["outer_ap"],
                    "outer_healthy_window_false_alarms": metrics["confusion_matrix"][0][1],
                    "outer_fault_recall": metrics["fault_recall"],
                    "training_seconds": run_record["training_seconds"],
                }), flush=True)
                write_json(args.output / "progress.json", report)
                del model
        fold["mean_outer_ap"] = {
            str(minutes): float(np.mean([r["outer_ap"] for r in
                                        fold["runs"][str(minutes)].values()]))
            for minutes in args.contexts}
        fold["paired_outer_ap_delta_vs_30"] = {
            str(minutes): {
                str(seed): fold["runs"][str(minutes)][str(seed)]["outer_ap"]
                - fold["runs"]["30"][str(seed)]["outer_ap"]
                for seed in args.seeds}
            for minutes in args.contexts if minutes != 30}
        report["status"] = "training"
        write_json(args.output / "progress.json", report)
    report["status"] = "complete"
    report["elapsed_seconds"] = time.monotonic() - started
    write_json(args.output / "results.json", report)
    write_json(args.output / "progress.json", report)
    print(json.dumps({name: fold["mean_outer_ap"]
                      for name, fold in report["folds"].items()}, indent=2), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, default=Path("data/processed/la_reunion_v1"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("auto", "cpu", "mps", "cuda"), default="auto")
    parser.add_argument("--contexts", type=int, nargs="+", default=[5, 15, 30])
    parser.add_argument("--seeds", type=int, nargs="+", default=[2031, 2032, 2033])
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=8e-4)
    run(parser.parse_args())
