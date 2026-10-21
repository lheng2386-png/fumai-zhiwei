"""Compare temporal models on a fixed La Reunion fault-event protocol.

`sweep` reads only train/validation. `evaluate` loads the frozen selection and
reads the held-out test and later normal-only stress data exactly once.
"""
import argparse
from contextlib import nullcontext
import csv
import json
from pathlib import Path
import time

import joblib
import numpy as np
import pandas as pd
import torch
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import (average_precision_score, confusion_matrix, f1_score,
                             precision_score, recall_score, roc_auc_score)
from torch.nn import functional as F

from multiscale_model import MultiscalePV
from lstm_model import LSTMPV, RNNPV
from train_arch_compare import TransformerPV
from train_deep import seed_all


ARCHITECTURES = {
    "multiscale_cnn": {"width": 64, "depth": 3},
    "transformer": {"width": 64, "layers": 3},
}


def load_split(prepared, split):
    with np.load(prepared / f"{split}.npz") as data:
        return {name: data[name] for name in ("x", "y", "end_time_ns")}


def make_model(name, channels):
    if name == "lstm":
        return LSTMPV(channels=channels, width=64, layers=2, classes=2)
    if name == "rnn_recent":
        return RNNPV(channels=channels, width=64, layers=2, classes=2, recent_steps=5)
    if name == "rnn":
        return RNNPV(channels=channels, width=64, layers=2, classes=2)
    spec = ARCHITECTURES[name]
    if name == "multiscale_cnn":
        return MultiscalePV(channels=channels, width=spec["width"], depth=spec["depth"], classes=2)
    return TransformerPV(channels=channels, width=spec["width"], layers=spec["layers"], classes=2)


def summarize_scores(y, scores, threshold):
    predicted = scores >= threshold
    matrix = confusion_matrix(y, predicted, labels=[0, 1])
    result = {"threshold": float(threshold), "confusion_matrix": matrix.tolist(),
              "macro_f1": float(f1_score(y, predicted, average="macro", zero_division=0)),
              "fault_precision": float(precision_score(y, predicted, zero_division=0)),
              "fault_recall": float(recall_score(y, predicted, zero_division=0)),
              "false_positive_rate": float(matrix[0, 1] / max(matrix[0].sum(), 1))}
    if len(np.unique(y)) == 2:
        result["average_precision"] = float(average_precision_score(y, scores))
        result["roc_auc"] = float(roc_auc_score(y, scores))
    return result


def choose_threshold(y, scores):
    candidates = np.linspace(0.02, 0.98, 97)
    values = [(f1_score(y, scores >= threshold, average="macro", zero_division=0),
               threshold) for threshold in candidates]
    return float(max(values)[1])


def window_statistics(x):
    return np.concatenate((x.mean(axis=2), x.std(axis=2), x[:, :, -1]), axis=1)


@torch.no_grad()
def predict(model, x, device, batch_size):
    model.eval()
    chunks = []
    for start in range(0, len(x), batch_size):
        batch = torch.from_numpy(x[start:start + batch_size]).to(device)
        chunks.append(model(batch).float().softmax(-1)[:, 1].cpu().numpy())
    return np.concatenate(chunks)


def classification_loss(logits, target, weights, hard_negative_alpha=0.0):
    """Emphasize training normals currently mistaken for faults, if requested."""
    if hard_negative_alpha == 0:
        return F.cross_entropy(logits, target, weight=weights)
    if hard_negative_alpha < 0:
        raise ValueError("hard_negative_alpha must be nonnegative")
    cross_entropy = F.cross_entropy(logits, target, weight=weights, reduction="none")
    fault_probability = logits.detach().softmax(-1)[:, 1]
    emphasis = torch.where(target == 0,
                           1.0 + hard_negative_alpha * fault_probability.square(),
                           torch.ones_like(fault_probability))
    return (cross_entropy * emphasis).sum() / (weights[target] * emphasis).sum()


def train_one(name, seed, train, validation, output, device, args,
              initial_state=None, hard_negative_alpha=0.0,
              channel_dropout=0.0, channel_dropout_span=1, batch_transform=None):
    seed_all(seed)
    model = make_model(name, train["x"].shape[1]).to(device)
    if initial_state is not None:
        model.load_state_dict(initial_state)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-3)
    counts = np.bincount(train["y"], minlength=2).astype(np.float32)
    weights = torch.tensor(1 / np.sqrt(np.maximum(counts, 1)), device=device)
    weights = weights / weights.mean()
    x = torch.from_numpy(train["x"]).to(device)
    y = torch.from_numpy(train["y"]).long().to(device)
    best_ap, best_epoch, stale, history = -1.0, 0, 0, []
    tag = f"{name}_seed{seed}"
    started = time.monotonic()
    if not 0 <= channel_dropout < 1:
        raise ValueError("channel_dropout must be in [0, 1)")
    if channel_dropout_span < 1:
        raise ValueError("channel_dropout_span must be positive")
    for epoch in range(args.epochs):
        model.train()
        permutation = torch.randperm(len(x), device=device)
        loss_sum = 0.0
        for indices in permutation.split(args.batch_size):
            batch = x[indices]
            if batch_transform is not None:
                batch = batch_transform(batch)
            batch = batch + torch.randn_like(batch) * 0.01
            if channel_dropout:
                # Drop contiguous temporal spans for a channel. The model sees
                # the training normalization value (zero) and learns a
                # degraded-sensor representation without a fake alarm label.
                drop = torch.rand((len(indices), x.shape[1], 1), device=device) < channel_dropout
                if channel_dropout_span > 1:
                    starts = torch.randint(0, x.shape[2] - channel_dropout_span + 1,
                                           (len(indices), x.shape[1], 1), device=device)
                    positions = torch.arange(x.shape[2], device=device)[None, None, :]
                    span = (positions >= starts) & (positions < starts + channel_dropout_span)
                    drop = drop & span
                batch = batch.masked_fill(drop, 0.0)
            optimizer.zero_grad(set_to_none=True)
            context = torch.autocast("cuda", dtype=torch.bfloat16) if device.type == "cuda" else nullcontext()
            with context:
                logits = model(batch).float()
                loss = classification_loss(logits, y[indices], weights,
                                           hard_negative_alpha)
            if not torch.isfinite(loss):
                raise FloatingPointError(tag)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            loss_sum += float(loss.detach()) * len(indices)
        val_scores = predict(model, validation["x"], device, args.batch_size)
        ap = float(average_precision_score(validation["y"], val_scores))
        row = {"epoch": epoch + 1, "loss": loss_sum / len(x), "validation_ap": ap,
               "seconds": time.monotonic() - started}
        history.append(row)
        print(json.dumps({"candidate": tag, **row}), flush=True)
        if ap > best_ap:
            best_ap, best_epoch, stale = ap, epoch + 1, 0
            torch.save({"architecture": name, "channels": x.shape[1], "seed": seed,
                        "state_dict": {key: value.detach().cpu().clone()
                                       for key, value in model.state_dict().items()}},
                       output / f"{tag}.pt")
            np.save(output / f"{tag}_validation.npy", val_scores)
        else:
            stale += 1
        if stale >= args.patience:
            break
    return {"tag": tag, "architecture": name, "seed": seed,
            "parameters": sum(p.numel() for p in model.parameters()),
            "validation_ap": best_ap, "best_epoch": best_epoch,
            "epochs_run": len(history),
            "elapsed_seconds": time.monotonic() - started, "history": history}


def sweep(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    torch.set_num_threads(min(torch.get_num_threads(), 8))
    device = torch.device(args.device if args.device != "auto" else
                          ("cuda" if torch.cuda.is_available() else
                           "mps" if torch.backends.mps.is_available() else "cpu"))
    train, validation = load_split(args.prepared, "train"), load_split(args.prepared, "validation")
    input_manifest = json.loads((args.prepared / "manifest.json").read_text())
    manifest = {"source_preparation": input_manifest, "device": str(device),
                "torch": torch.__version__, "seeds": args.seeds,
                "architectures": ARCHITECTURES, "epochs": args.epochs,
                "patience": args.patience, "batch_size": args.batch_size,
                "selection": "highest validation average precision across tree, individual neural models, and seed ensembles; threshold chosen on validation macro F1",
                "held_out_status": "test and normal_stress files are not loaded during sweep"}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    tree = ExtraTreesClassifier(n_estimators=500, min_samples_leaf=2,
                                class_weight="balanced", n_jobs=-1, random_state=args.seeds[0])
    tree.fit(window_statistics(train["x"]), train["y"])
    joblib.dump(tree, args.output / "extra_trees.joblib")
    scores = {"extra_trees": tree.predict_proba(window_statistics(validation["x"]))[:, 1]}
    records = []
    for name in ARCHITECTURES:
        for seed in args.seeds:
            result = train_one(name, seed, train, validation, args.output, device, args)
            records.append(result)
            scores[result["tag"]] = np.load(args.output / f"{result['tag']}_validation.npy")
            (args.output / "progress.json").write_text(json.dumps(records, indent=2) + "\n")
        if len(args.seeds) > 1:
            scores[f"{name}_ensemble"] = np.mean(
                [scores[f"{name}_seed{seed}"] for seed in args.seeds], axis=0)
    ranking = {name: float(average_precision_score(validation["y"], score))
               for name, score in scores.items()}
    winner = max(ranking, key=ranking.get)
    threshold = choose_threshold(validation["y"], scores[winner])
    selection = {"validation_ap_by_candidate": ranking, "selected": winner,
                 "selected_members": ([f"{winner.removesuffix('_ensemble')}_seed{seed}" for seed in args.seeds]
                                      if winner.endswith("_ensemble") else [winner]),
                 "validation_metrics": summarize_scores(validation["y"], scores[winner], threshold),
                 "test_status": "not evaluated"}
    (args.output / "selection.json").write_text(json.dumps(selection, indent=2) + "\n")
    print(json.dumps(selection, indent=2), flush=True)


def selected_scores(selection, data, output, device, batch_size):
    members = []
    for tag in selection["selected_members"]:
        if tag == "extra_trees":
            tree = joblib.load(output / "extra_trees.joblib")
            members.append(tree.predict_proba(window_statistics(data["x"]))[:, 1])
        else:
            checkpoint = torch.load(output / f"{tag}.pt", map_location="cpu", weights_only=True)
            model = make_model(checkpoint["architecture"], checkpoint["channels"]).to(device)
            model.load_state_dict(checkpoint["state_dict"])
            members.append(predict(model, data["x"], device, batch_size))
    return np.mean(members, axis=0)


def day_alert_summary(data, scores, threshold):
    days = pd.to_datetime(data["end_time_ns"], utc=True).tz_convert("Indian/Reunion").date
    alert = scores >= threshold
    fault_days = {day for day, target in zip(days, data["y"]) if target == 1}
    detected = {day for day, target, flag in zip(days, data["y"], alert) if target == 1 and flag}
    normal_days = set(days) - fault_days
    false_alarm_days = {day for day, target, flag in zip(days, data["y"], alert)
                        if target == 0 and flag and day in normal_days}
    return {"fault_days": len(fault_days), "fault_days_detected": len(detected),
            "normal_only_days": len(normal_days), "normal_only_days_with_alert": len(false_alarm_days),
            "alerts": int(alert.sum())}


def evaluate(args):
    if any((args.output / name).exists() for name in
           ("evaluation.json", "test_predictions.csv", "normal_stress_predictions.csv")):
        raise FileExistsError("Held-out evaluation already exists; refusing repeated test access")
    selection = json.loads((args.output / "selection.json").read_text())
    device = torch.device(args.device if args.device != "auto" else
                          ("cuda" if torch.cuda.is_available() else
                           "mps" if torch.backends.mps.is_available() else "cpu"))
    report = {"selected": selection["selected"],
              "threshold_from_validation": selection["validation_metrics"]["threshold"]}
    for split in ("test", "normal_stress"):
        data = load_split(args.prepared, split)
        scores = selected_scores(selection, data, args.output, device, args.batch_size)
        report[split] = {**summarize_scores(data["y"], scores, report["threshold_from_validation"]),
                         **day_alert_summary(data, scores, report["threshold_from_validation"])}
        with (args.output / f"{split}_predictions.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["end_time_ns", "label", "fault_probability"])
            writer.writerows(zip(data["end_time_ns"], data["y"], scores))
    (args.output / "evaluation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["sweep", "evaluate"])
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=["auto", "cuda", "mps", "cpu"], default="auto")
    parser.add_argument("--seeds", nargs="+", type=int, default=[2031, 2032, 2033])
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=8e-4)
    args = parser.parse_args()
    if args.phase == "sweep":
        sweep(args)
    else:
        evaluate(args)
