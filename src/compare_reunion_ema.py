"""Validation-only comparison of ordinary and EMA Transformer weights."""

import argparse
import copy
import json
import time
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import average_precision_score
from torch.nn import functional as F

from train_deep import seed_all
from train_reunion import classification_loss, make_model, predict


def update_ema(ema, model, decay):
    with torch.no_grad():
        for ema_param, param in zip(ema.parameters(), model.parameters()):
            ema_param.mul_(decay).add_(param, alpha=1.0 - decay)
        for ema_buffer, buffer in zip(ema.buffers(), model.buffers()):
            ema_buffer.copy_(buffer)


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
        model = make_model("transformer", x.shape[1]).to(device)
        ema = copy.deepcopy(model).eval()
        optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-3)
        best = {"raw": -1.0, "ema": -1.0}
        best_epoch = {"raw": 0, "ema": 0}
        stale = {"raw": 0, "ema": 0}
        history = []
        started = time.monotonic()
        tag = f"ema_transformer_seed{seed}"
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
                update_ema(ema, model, args.decay)
                total += float(loss.detach()) * len(ids)
            raw_scores = predict(model, validation["x"], device, args.batch_size)
            ema_scores = predict(ema, validation["x"], device, args.batch_size)
            aps = {"raw": float(average_precision_score(validation["y"], raw_scores)),
                   "ema": float(average_precision_score(validation["y"], ema_scores))}
            history.append({"epoch": epoch + 1, "loss": total / len(x), **aps,
                            "seconds": time.monotonic() - started})
            print(json.dumps({"candidate": tag, **history[-1]}), flush=True)
            for name, score in aps.items():
                if score > best[name]:
                    best[name], best_epoch[name], stale[name] = score, epoch + 1, 0
                    source = model if name == "raw" else ema
                    torch.save({"architecture": "transformer", "channels": x.shape[1],
                                "seed": seed, "weight_type": name,
                                "state_dict": {k: v.detach().cpu().clone()
                                               for k, v in source.state_dict().items()}},
                               args.output / f"{tag}_{name}.pt")
                else:
                    stale[name] += 1
            if all(value >= args.patience for value in stale.values()):
                break
        records.append({"seed": seed, "tag": tag, "best_validation_ap": best,
                        "best_epoch": best_epoch, "epochs_run": len(history),
                        "parameters": sum(p.numel() for p in model.parameters()),
                        "history": history})
        (args.output / "progress.json").write_text(json.dumps(records, indent=2) + "\n")
    summary = {"device": str(device), "ema_decay": args.decay,
               "mean_best_ap": {name: float(np.mean([r["best_validation_ap"][name] for r in records]))
                                for name in ("raw", "ema")},
               "records": records,
               "protocol": "same train/validation protocol; EMA selected by validation only"}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary["mean_best_ap"], indent=2))


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
    parser.add_argument("--decay", type=float, default=0.995)
    run(parser.parse_args())
