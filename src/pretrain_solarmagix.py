"""Masked-reconstruction pretraining on station-held-out SolarMagix windows.

Validation selects an epoch; the separate test station is evaluated once.
Reconstruction quality is not a fault-detection metric.
"""

import argparse
import hashlib
import json
from pathlib import Path
import random
import time

import numpy as np
import torch

from multiscale_model import MultiscalePV


def eval_mask(shape, seed):
    count, channels, length = shape
    rng = np.random.default_rng(seed)
    mask = np.zeros(shape, dtype=bool)
    starts = rng.integers(0, length-1, size=count)
    for row, start in enumerate(starts):
        mask[row, :, start:start+2] = True
    mask |= rng.random((count, channels, 1)) < 0.1
    return torch.from_numpy(mask)


def train_mask(shape, device):
    count, channels, length = shape
    starts = torch.randint(length-1, (count, 1, 1), device=device)
    positions = torch.arange(length, device=device)[None, None, :]
    span = (positions >= starts) & (positions < starts+2)
    channels_masked = torch.rand(count, channels, 1, device=device) < 0.1
    return (span | channels_masked).expand(shape)


@torch.no_grad()
def evaluate(model, data, mask, batch_size, device):
    model.eval()
    squared = 0.0
    count = 0
    baseline = 0.0
    for xb, mb in zip(data.split(batch_size), mask.split(batch_size)):
        xb, mb = xb.to(device), mb.to(device)
        predicted = model(xb.masked_fill(mb, 0), reconstruct=True)
        squared += float(((predicted-xb).square()*mb).sum())
        baseline += float((xb.square()*mb).sum())
        count += int(mb.sum())
    return {"masked_mse": squared/count, "zero_fill_mse": baseline/count}


def run(args):
    if args.device == "auto":
        device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    else:
        device = args.device
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    random.seed(args.seed)
    data_bytes = args.data.read_bytes()
    arrays = np.load(args.data)
    train = torch.from_numpy(arrays["train"].copy())
    validation = torch.from_numpy(arrays["validation"].copy())
    test = torch.from_numpy(arrays["test"].copy())
    if any(x.ndim != 3 or x.shape[1:] != train.shape[1:] for x in (train, validation, test)):
        raise ValueError("Inconsistent split tensor shapes")
    manifest = json.loads(args.data.with_suffix(".json").read_text())
    if manifest["splits"]["train"]["windows"] != len(train):
        raise ValueError("Manifest/data mismatch")
    model = MultiscalePV(channels=train.shape[1], width=args.width, depth=args.depth).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-3)
    val_mask = eval_mask(validation.shape, args.seed+1)
    test_mask = eval_mask(test.shape, args.seed+2)
    args.output.mkdir(parents=True, exist_ok=False)
    best = float("inf")
    best_epoch = 0
    history = []
    started = time.time()
    for epoch in range(1, args.epochs+1):
        model.train()
        total = 0.0
        seen = 0
        for indices in torch.randperm(len(train)).split(args.batch_size):
            xb = train[indices].to(device)
            mask = train_mask(xb.shape, device)
            optimizer.zero_grad(set_to_none=True)
            predicted = model(xb.masked_fill(mask, 0), reconstruct=True)
            loss = (predicted-xb).square()[mask].mean()
            if not torch.isfinite(loss):
                raise FloatingPointError("Non-finite pretraining loss")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total += loss.detach().item()*len(xb)
            seen += len(xb)
        val = evaluate(model, validation, val_mask, args.batch_size, device)
        row = {"epoch": epoch, "train_masked_mse": total/seen,
               "validation_masked_mse": val["masked_mse"],
               "seconds": round(time.time()-started, 1)}
        history.append(row)
        print(json.dumps(row), flush=True)
        if val["masked_mse"] < best:
            best = val["masked_mse"]
            best_epoch = epoch
            torch.save({"state_dict": {k: v.cpu() for k, v in model.state_dict().items()},
                        "channels": train.shape[1], "width": args.width, "depth": args.depth,
                        "window": train.shape[2], "features": manifest["scaler"]["features"],
                        "scaler": manifest["scaler"], "seed": args.seed}, args.output/"best.pt")
        if epoch-best_epoch >= args.patience:
            break
    checkpoint = torch.load(args.output/"best.pt", map_location="cpu", weights_only=True)
    model.load_state_dict(checkpoint["state_dict"])
    result = {"source": manifest["source"], "task": "masked reconstruction",
              "data_sha256": hashlib.sha256(data_bytes).hexdigest(),
              "device": device, "seed": args.seed, "parameters": sum(p.numel() for p in model.parameters()),
              "train_windows": len(train), "validation_windows": len(validation),
              "test_windows": len(test), "best_epoch": best_epoch,
              "validation": evaluate(model, validation, val_mask, args.batch_size, device),
              "heldout_station_test": evaluate(model, test, test_mask, args.batch_size, device),
              "history": history,
              "limitations": ["Reconstruction is not fault classification or field-fault detection.",
                              "Only one held-out station; confirm with repeated station folds.",
                              "Input features differ from the La Reunion model; transfer requires an adapter."]}
    (args.output/"results.json").write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps({k: result[k] for k in ("best_epoch", "validation", "heldout_station_test")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("data/processed/solarmagix_8h.npz"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--patience", type=int, default=6)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--width", type=int, default=64)
    parser.add_argument("--depth", type=int, default=3)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    args = parser.parse_args()
    if min(args.epochs, args.patience, args.batch_size, args.lr) <= 0:
        parser.error("Training controls must be positive")
    run(args)
