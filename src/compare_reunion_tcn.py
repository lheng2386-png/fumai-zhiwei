"""Validation-only TCN comparison on the fixed Reunion protocol."""

import argparse
import json
import time
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import average_precision_score
from torch.nn import functional as F

from tcn_model import TCN_PV
from train_deep import seed_all
from train_reunion import classification_loss, predict


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    device = torch.device(args.device if args.device != "auto" else
                          ("mps" if torch.backends.mps.is_available() else
                           "cuda" if torch.cuda.is_available() else "cpu"))
    with np.load(args.prepared / "train.npz") as data:
        train = {k: data[k] for k in ("x", "y")}
    with np.load(args.prepared / "validation.npz") as data:
        validation = {k: data[k] for k in ("x", "y")}
    x = torch.from_numpy(train["x"]).to(device)
    y = torch.from_numpy(train["y"]).long().to(device)
    counts = np.bincount(train["y"], minlength=2).astype(np.float32)
    weights = torch.tensor(1 / np.sqrt(np.maximum(counts, 1)), device=device)
    weights /= weights.mean()
    records = []
    for seed in args.seeds:
        seed_all(seed)
        model = TCN_PV(channels=x.shape[1], width=args.width,
                       dilations=tuple(args.dilations), classes=2).to(device)
        optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-3)
        best, best_epoch, stale, history = -1.0, 0, 0, []
        started = time.monotonic()
        tag = f"tcn_seed{seed}"
        for epoch in range(args.epochs):
            model.train()
            total = 0.0
            for ids in torch.randperm(len(x), device=device).split(args.batch_size):
                batch = x[ids] + torch.randn_like(x[ids]) * 0.01
                optimizer.zero_grad(set_to_none=True)
                context = torch.autocast("cuda", dtype=torch.bfloat16) if device.type == "cuda" else nullcontext()
                with context:
                    logits = model(batch).float()
                    loss = classification_loss(logits, y[ids], weights)
                if not torch.isfinite(loss):
                    raise FloatingPointError(tag)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                total += float(loss.detach()) * len(ids)
            scores = predict(model, validation["x"], device, args.batch_size)
            ap = float(average_precision_score(validation["y"], scores))
            row = {"epoch": epoch + 1, "loss": total / len(x),
                   "validation_ap": ap, "seconds": time.monotonic() - started}
            history.append(row)
            print(json.dumps({"candidate": tag, **row}), flush=True)
            if ap > best:
                best, best_epoch, stale = ap, epoch + 1, 0
                torch.save({"architecture": "tcn", "channels": x.shape[1],
                            "seed": seed, "dilations": args.dilations,
                            "state_dict": {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}},
                           args.output / f"{tag}.pt")
            else:
                stale += 1
            if stale >= args.patience:
                break
        records.append({"tag": tag, "seed": seed, "validation_ap": best,
                        "best_epoch": best_epoch, "epochs_run": len(history),
                        "parameters": sum(p.numel() for p in model.parameters()),
                        "elapsed_seconds": time.monotonic() - started,
                        "history": history})
        (args.output / "progress.json").write_text(json.dumps(records, indent=2) + "\n")
    summary = {"device": str(device), "dilations": args.dilations,
               "mean_validation_ap": float(np.mean([r["validation_ap"] for r in records])),
               "records": records,
               "protocol": "same Reunion train/validation split; old test and normal stress not read"}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"mean_validation_ap": summary["mean_validation_ap"],
                      "seeds": [(r["seed"], r["validation_ap"]) for r in records]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, default=Path("data/processed/la_reunion_v1"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda", "mps"], default="auto")
    parser.add_argument("--seeds", type=int, nargs="+", default=[2031, 2032, 2033])
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=8e-4)
    parser.add_argument("--width", type=int, default=64)
    parser.add_argument("--dilations", type=int, nargs="+", default=[1, 2, 4, 8])
    run(parser.parse_args())
