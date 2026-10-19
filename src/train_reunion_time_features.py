"""Compare a Transformer with local time-of-day features on Reunion windows."""

import argparse
import json
import time
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import average_precision_score
from torch.nn import functional as F

from train_arch_compare import TransformerPV
from train_deep import seed_all


def add_time_features(x, end_time_ns):
    """Append sin/cos minute-of-day for every minute in each window.

    Reunion uses UTC+04:00 year-round. The feature is derived only from the
    timestamp and is repeated per window step; no target or future measurement
    is used.
    """
    n, _, steps = x.shape
    end_minutes = end_time_ns.astype(np.int64) // 60_000_000_000
    offsets = np.arange(-(steps - 1), 1, dtype=np.int64)
    minute = (end_minutes[:, None] + offsets[None, :] + 4 * 60) % 1440
    angle = 2 * np.pi * minute / 1440.0
    temporal = np.stack([np.sin(angle), np.cos(angle)], axis=1).astype(np.float32)
    return np.concatenate([x.astype(np.float32), temporal], axis=1)


@torch.no_grad()
def predict(model, x, device, batch_size):
    model.eval()
    out = []
    for start in range(0, len(x), batch_size):
        batch = torch.from_numpy(x[start:start + batch_size]).to(device)
        out.append(model(batch).float().softmax(-1)[:, 1].cpu().numpy())
    return np.concatenate(out)


def train_one(seed, train, validation, args, device):
    seed_all(seed)
    model = TransformerPV(channels=train["x"].shape[1], width=args.width,
                          layers=args.layers, classes=2).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-3)
    counts = np.bincount(train["y"], minlength=2).astype(np.float32)
    weights = torch.tensor(1 / np.sqrt(np.maximum(counts, 1)), device=device)
    weights /= weights.mean()
    train_x = torch.from_numpy(train["x"]).to(device)
    train_y = torch.from_numpy(train["y"]).long().to(device)
    best_ap, best_epoch, stale, history = -1.0, 0, 0, []
    started = time.monotonic()
    tag = f"time_features_seed{seed}"
    for epoch in range(args.epochs):
        model.train()
        total = 0.0
        for ids in torch.randperm(len(train_x), device=device).split(args.batch_size):
            batch = train_x[ids].clone()
            batch[:, :-2] += torch.randn_like(batch[:, :-2]) * 0.01
            optimizer.zero_grad(set_to_none=True)
            context = torch.autocast("cuda", dtype=torch.bfloat16) if device.type == "cuda" else nullcontext()
            with context:
                logits = model(batch).float()
                loss = F.cross_entropy(logits, train_y[ids], weight=weights)
            if not torch.isfinite(loss):
                raise FloatingPointError(tag)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total += float(loss.detach()) * len(ids)
        scores = predict(model, validation["x"], device, args.batch_size)
        ap = float(average_precision_score(validation["y"], scores))
        row = {"epoch": epoch + 1, "loss": total / len(train_x),
               "validation_ap": ap, "seconds": time.monotonic() - started}
        history.append(row)
        print(json.dumps({"candidate": tag, **row}), flush=True)
        if ap > best_ap:
            best_ap, best_epoch, stale = ap, epoch + 1, 0
            torch.save({"architecture": "transformer", "channels": train["x"].shape[1],
                        "seed": seed, "time_features": True,
                        "state_dict": {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}},
                       args.output / f"{tag}.pt")
        else:
            stale += 1
        if stale >= args.patience:
            break
    return {"tag": tag, "seed": seed, "validation_ap": best_ap,
            "best_epoch": best_epoch, "epochs_run": len(history),
            "parameters": sum(p.numel() for p in model.parameters()),
            "elapsed_seconds": time.monotonic() - started, "history": history}


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    device = torch.device(args.device if args.device != "auto" else
                          ("mps" if torch.backends.mps.is_available() else
                           "cuda" if torch.cuda.is_available() else "cpu"))
    with np.load(args.prepared / "train.npz") as data:
        train = {k: data[k] for k in ("x", "y", "end_time_ns")}
    with np.load(args.prepared / "validation.npz") as data:
        validation = {k: data[k] for k in ("x", "y", "end_time_ns")}
    train["x"] = add_time_features(train["x"], train["end_time_ns"])
    validation["x"] = add_time_features(validation["x"], validation["end_time_ns"])
    records = [train_one(seed, train, validation, args, device) for seed in args.seeds]
    summary = {"device": str(device), "feature_channels": train["x"].shape[1],
               "mean_validation_ap": float(np.mean([r["validation_ap"] for r in records])),
               "records": records,
               "protocol": "time-of-day sin/cos appended; train/validation only"}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (args.output / "progress.json").write_text(json.dumps(records, indent=2) + "\n")
    print(json.dumps({"mean_validation_ap": summary["mean_validation_ap"],
                      "seeds": [(r["seed"], r["validation_ap"]) for r in records]}, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--prepared", type=Path, default=Path("data/processed/la_reunion_v1"))
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--device", choices=["auto", "cpu", "cuda", "mps"], default="auto")
    p.add_argument("--seeds", type=int, nargs="+", default=[2031, 2032, 2033])
    p.add_argument("--epochs", type=int, default=50)
    p.add_argument("--patience", type=int, default=8)
    p.add_argument("--batch-size", type=int, default=256)
    p.add_argument("--lr", type=float, default=8e-4)
    p.add_argument("--width", type=int, default=64)
    p.add_argument("--layers", type=int, default=3)
    run(p.parse_args())
