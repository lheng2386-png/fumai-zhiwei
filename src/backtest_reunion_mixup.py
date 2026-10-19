"""Paired scratch/MixUp Transformer evaluation on train-period forward folds.

Only train.npz is opened. Each fold fits scaling on its earlier training days,
selects epochs and a threshold on tuning days, and scores later days once.
The folds have been inspected in earlier studies, so this is a retrospective
development diagnostic, not a new blind test.
"""

import argparse
import hashlib
import json
import time
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import average_precision_score
from torch.nn import functional as F

from backtest_reunion import FOLDS, fold_data
from train_deep import seed_all
from train_reunion import (choose_threshold, day_alert_summary, make_model,
                           predict, summarize_scores)


def train_candidate(train, tuning, outer, *, seed, alpha, folder, args, device):
    seed_all(seed)
    model = make_model("transformer", train["x"].shape[1]).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-3)
    x = torch.from_numpy(train["x"]).to(device)
    y = torch.from_numpy(train["y"]).long().to(device)
    counts = np.bincount(train["y"], minlength=2).astype(np.float32)
    weights = torch.tensor(1 / np.sqrt(np.maximum(counts, 1)), device=device)
    weights /= weights.mean()
    best_ap, best_epoch, stale = -1.0, 0, 0
    history = []
    started = time.monotonic()
    for epoch in range(args.epochs):
        model.train()
        loss_sum = 0.0
        for ids in torch.randperm(len(x), device=device).split(args.batch_size):
            batch = x[ids] + torch.randn_like(x[ids]) * 0.01
            labels = y[ids]
            if alpha > 0:
                partner = torch.randperm(len(ids), device=device)
                lam = float(np.random.beta(alpha, alpha))
                mixed = lam * batch + (1.0 - lam) * batch[partner]
            else:
                mixed = batch
            optimizer.zero_grad(set_to_none=True)
            context = torch.autocast("cuda", dtype=torch.bfloat16) if device.type == "cuda" else nullcontext()
            with context:
                logits = model(mixed).float()
                loss = F.cross_entropy(logits, labels, weight=weights)
                if alpha > 0:
                    loss = lam * loss + (1.0 - lam) * F.cross_entropy(
                        logits, labels[partner], weight=weights)
            if not torch.isfinite(loss):
                raise FloatingPointError(f"nonfinite loss seed={seed} alpha={alpha}")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            loss_sum += float(loss.detach()) * len(ids)
        tune_scores = predict(model, tuning["x"], device, args.batch_size)
        ap = float(average_precision_score(tuning["y"], tune_scores))
        history.append({"epoch": epoch + 1, "tuning_ap": ap,
                        "train_loss": loss_sum / len(x)})
        if ap > best_ap:
            best_ap, best_epoch, stale = ap, epoch + 1, 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            selected_tuning_scores = tune_scores.copy()
        else:
            stale += 1
        if stale >= args.patience:
            break
    model.load_state_dict(best_state)
    threshold = choose_threshold(tuning["y"], selected_tuning_scores)
    outer_scores = predict(model, outer["x"], device, args.batch_size)
    tag = f"alpha_{alpha:g}_seed{seed}"
    torch.save({"architecture": "transformer", "channels": train["x"].shape[1],
                "seed": seed, "mixup_alpha": alpha, "state_dict": best_state}, folder / f"{tag}.pt")
    return {"seed": seed, "alpha": alpha, "best_epoch": best_epoch,
            "epochs_run": len(history), "tuning_ap": best_ap,
            "tuning_threshold": threshold,
            "outer_ap": float(average_precision_score(outer["y"], outer_scores)),
            "outer_window_metrics": summarize_scores(outer["y"], outer_scores, threshold),
            "outer_day_metrics": day_alert_summary(outer, outer_scores, threshold),
            "elapsed_seconds": time.monotonic() - started, "history": history}


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    if not (0 in args.alphas and any(alpha > 0 for alpha in args.alphas)):
        raise ValueError("Include alpha 0 and a positive alpha for a paired comparison")
    args.output.mkdir(parents=True)
    torch.set_num_threads(min(torch.get_num_threads(), 8))
    device = torch.device(args.device if args.device != "auto" else
                          ("cuda" if torch.cuda.is_available() else
                           "mps" if torch.backends.mps.is_available() else "cpu"))
    report = {"protocol": "paired retrospective forward folds within train.npz",
              "prepared_manifest_sha256": hashlib.sha256(
                  (args.prepared / "manifest.json").read_bytes()).hexdigest(),
              "device": str(device), "seeds": args.seeds, "alphas": args.alphas,
              "limitations": ["These fold evaluation days were inspected in earlier studies.",
                              "The second fold has very few normal windows.",
                              "One plant with experimental shading faults only.",
                              "Original validation, test and normal stress files are not opened."],
              "folds": {}}
    for name, *dates in FOLDS:
        train, tuning, outer, scaler, days = fold_data(args.prepared, *dates)
        folder = args.output / name
        folder.mkdir()
        fold = {"dates": days, "train_windows": len(train["y"]),
                "tuning_windows": len(tuning["y"]), "outer_windows": len(outer["y"]),
                "outer_normal_windows": int((outer["y"] == 0).sum()), "runs": {}}
        report["folds"][name] = fold
        for alpha in args.alphas:
            label = f"alpha_{alpha:g}"
            fold["runs"][label] = {}
            for seed in args.seeds:
                result = train_candidate(train, tuning, outer, seed=seed, alpha=alpha,
                                         folder=folder, args=args, device=device)
                fold["runs"][label][str(seed)] = result
                (args.output / "progress.json").write_text(json.dumps(report, indent=2) + "\n")
                print(json.dumps({"fold": name, "alpha": alpha, "seed": seed,
                                  "tuning_ap": result["tuning_ap"],
                                  "outer_ap": result["outer_ap"],
                                  "outer_recall": result["outer_window_metrics"]["fault_recall"],
                                  "outer_fpr": result["outer_window_metrics"]["false_positive_rate"]}), flush=True)
        fold["mean_outer_ap"] = {label: float(np.mean([r["outer_ap"] for r in runs.values()]))
                                 for label, runs in fold["runs"].items()}
    (args.output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({name: fold["mean_outer_ap"] for name, fold in report["folds"].items()}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, default=Path("data/processed/la_reunion_v1"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("auto", "cpu", "mps", "cuda"), default="auto")
    parser.add_argument("--seeds", nargs="+", type=int, default=[2031, 2032, 2033])
    parser.add_argument("--alphas", nargs="+", type=float, default=[0.0, 0.2])
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=8e-4)
    args = parser.parse_args()
    if (min(args.epochs, args.patience, args.batch_size) < 1 or args.lr <= 0 or
            min(args.alphas) < 0 or len(set(args.alphas)) != len(args.alphas)):
        parser.error("Invalid training controls")
    run(args)
