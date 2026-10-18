"""Rolling, event-separated development backtests using only the train split.

The frozen validation/test/stress partitions are never opened here. Fold-specific
normalization is refit from each fold's earlier training windows.
"""
import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import torch
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import average_precision_score

from train_reunion import (ARCHITECTURES, choose_threshold, selected_scores,
                           summarize_scores, train_one, window_statistics)


FOLDS = (
    ("mid_february", "2022-02-09", "2022-02-11", "2022-02-13",
     "2022-02-14", "2022-02-18"),
    ("early_march", "2022-02-18", "2022-02-24", "2022-03-01",
     "2022-03-09", "2022-03-11"),
)


def fold_data(prepared, train_through, tune_from, tune_through,
              evaluate_from, evaluate_through):
    with np.load(prepared / "train.npz") as stream:
        x, y, time_ns = (stream[name] for name in ("x", "y", "end_time_ns"))
    dates = np.asarray(pd.to_datetime(time_ns, utc=True).tz_convert("Indian/Reunion").date,
                       dtype="str")
    train_mask = dates <= train_through
    tune_mask = (dates >= tune_from) & (dates <= tune_through)
    eval_mask = (dates >= evaluate_from) & (dates <= evaluate_through)
    if (not train_through < tune_from <= tune_through < evaluate_from <= evaluate_through
            or not train_mask.any() or not tune_mask.any() or not eval_mask.any()):
        raise ValueError("Fold dates are empty or overlap")
    if any(len(np.unique(y[mask])) != 2 for mask in (train_mask, tune_mask, eval_mask)):
        raise ValueError("Train, tuning and evaluation need normal and fault windows")
    # Undo the full development-train scaling, then fit scaling using only the
    # earlier windows. This prevents fold evaluation days influencing statistics.
    with np.load(prepared / "normalization.npz") as stream:
        old_mean, old_scale = stream["mean"], stream["scale"]
    raw = x * old_scale[None, :, None] + old_mean[None, :, None]
    mean = raw[train_mask].mean(axis=(0, 2), dtype=np.float64).astype(np.float32)
    scale = raw[train_mask].std(axis=(0, 2), dtype=np.float64).astype(np.float32)
    scale = np.maximum(scale, 1e-4)
    normalized = ((raw - mean[None, :, None]) / scale[None, :, None]).astype(np.float32)
    return ({"x": normalized[train_mask], "y": y[train_mask]},
            {"x": normalized[tune_mask], "y": y[tune_mask]},
            {"x": normalized[eval_mask], "y": y[eval_mask]},
            {"mean": mean, "scale": scale},
            {"train_days": sorted(set(dates[train_mask])),
             "tuning_days": sorted(set(dates[tune_mask])),
             "evaluation_days": sorted(set(dates[eval_mask]))})


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    torch.set_num_threads(min(torch.get_num_threads(), 8))
    device = torch.device(args.device if args.device != "auto" else
                          ("cuda" if torch.cuda.is_available() else
                           "mps" if torch.backends.mps.is_available() else "cpu"))
    result = {"protocol": "rolling train-only backtest; frozen validation/test/stress not read",
              "device": str(device), "seeds": args.seeds, "folds": {}}
    for name, train_through, tune_from, tune_through, evaluate_from, evaluate_through in FOLDS:
        train, tuning, evaluation, scaler, days = fold_data(
            args.prepared, train_through, tune_from, tune_through,
            evaluate_from, evaluate_through)
        fold_output = args.output / name
        fold_output.mkdir()
        np.savez(fold_output / "normalization.npz", **scaler)
        tree = ExtraTreesClassifier(n_estimators=500, min_samples_leaf=2,
                                    class_weight="balanced", n_jobs=-1,
                                    random_state=args.seeds[0])
        tree.fit(window_statistics(train["x"]), train["y"])
        joblib.dump(tree, fold_output / "extra_trees.joblib")
        scores = {"extra_trees": tree.predict_proba(window_statistics(tuning["x"]))[:, 1]}
        training = []
        for architecture in ARCHITECTURES:
            for seed in args.seeds:
                record = train_one(architecture, seed, train, tuning,
                                   fold_output, device, args)
                training.append(record)
                scores[record["tag"]] = np.load(fold_output / f"{record['tag']}_validation.npy")
            if len(args.seeds) > 1:
                scores[f"{architecture}_ensemble"] = np.mean(
                    [scores[f"{architecture}_seed{seed}"] for seed in args.seeds], axis=0)
        tuning_ap = {candidate: float(average_precision_score(tuning["y"], score))
                     for candidate, score in scores.items()}
        selected = max(tuning_ap, key=tuning_ap.get)
        selected_members = ([f"{selected.removesuffix('_ensemble')}_seed{seed}" for seed in args.seeds]
                            if selected.endswith("_ensemble") else [selected])
        threshold = choose_threshold(tuning["y"], scores[selected])
        outer_scores = selected_scores({"selected_members": selected_members},
                                       evaluation, fold_output, device, args.batch_size)
        metrics = summarize_scores(evaluation["y"], outer_scores, threshold)
        result["folds"][name] = {"train_through": train_through,
                                 "tuning_from": tune_from,
                                 "tuning_through": tune_through,
                                 "evaluation_from": evaluate_from,
                                 "evaluation_through": evaluate_through,
                                 "train_windows": len(train["y"]),
                                 "tuning_windows": len(tuning["y"]),
                                 "evaluation_windows": len(evaluation["y"]),
                                 "train_fault_windows": int(train["y"].sum()),
                                 "tuning_fault_windows": int(tuning["y"].sum()),
                                 "evaluation_fault_windows": int(evaluation["y"].sum()),
                                 "days": days, "training": training,
                                 "tuning_ap_by_candidate": tuning_ap,
                                 "selected": selected,
                                 "selected_members": selected_members,
                                 "threshold_from_tuning": threshold,
                                 "outer_evaluation": metrics}
        (args.output / "results.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({"fold": name, "selected": selected,
                          "outer_evaluation": metrics}, indent=2), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("auto", "cpu", "mps", "cuda"), default="auto")
    parser.add_argument("--seeds", type=int, nargs="+", default=[2031, 2032, 2033])
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=8e-4)
    run(parser.parse_args())
