"""Train a Transformer with explicit per-channel observation masks.

The base Reunion files are complete windows. During training, contiguous
channel spans are hidden and the corresponding mask is set to zero. The model
therefore learns that a zero-valued normalized measurement can mean either a
real value or an unobserved value. Selection is based only on clean train and
validation windows; stress metrics are reported after the candidate is frozen.
"""

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
from train_reunion import summarize_scores, choose_threshold


def masked_input(x, mask):
    return torch.cat([x, mask], dim=1)


@torch.no_grad()
def predict(model, x, mask, device, batch_size=256):
    model.eval()
    scores = []
    for start in range(0, len(x), batch_size):
        xb = torch.from_numpy(x[start:start + batch_size]).to(device)
        mb = torch.from_numpy(mask[start:start + batch_size]).to(device)
        scores.append(model(masked_input(xb, mb)).float().softmax(-1)[:, 1].cpu().numpy())
    return np.concatenate(scores)


def corrupt_training_batch(batch, probability, span):
    """Return corrupted values and an observation mask, both on device."""
    n, channels, steps = batch.shape
    mask = torch.ones_like(batch)
    if probability <= 0:
        return batch, mask
    choose = torch.rand((n, channels, 1), device=batch.device) < probability
    starts = torch.randint(0, steps - span + 1, (n, channels, 1), device=batch.device)
    positions = torch.arange(steps, device=batch.device)[None, None, :]
    dropped = choose & (positions >= starts) & (positions < starts + span)
    mask = mask.masked_fill(dropped, 0.0)
    return batch.masked_fill(dropped, 0.0), mask


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    device = torch.device(args.device if args.device != "auto" else
                          ("mps" if torch.backends.mps.is_available() else
                           "cuda" if torch.cuda.is_available() else "cpu"))
    torch.set_num_threads(min(torch.get_num_threads(), 8))
    with np.load(args.prepared / "train.npz") as data:
        train = {k: data[k] for k in ("x", "y")}
    with np.load(args.prepared / "validation.npz") as data:
        validation = {k: data[k] for k in ("x", "y")}
    train_x = torch.from_numpy(train["x"]).to(device)
    train_y = torch.from_numpy(train["y"]).long().to(device)
    clean_mask = np.ones_like(validation["x"], dtype=np.float32)
    counts = np.bincount(train["y"], minlength=2).astype(np.float32)
    weights = torch.tensor(1 / np.sqrt(np.maximum(counts, 1)), device=device)
    weights /= weights.mean()
    records = []
    for seed in args.seeds:
        seed_all(seed)
        model = TransformerPV(channels=train["x"].shape[1] * 2, width=args.width,
                              layers=args.layers, classes=2).to(device)
        optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-3)
        best_ap, best_epoch, stale = -1.0, 0, 0
        history = []
        started = time.monotonic()
        tag = f"mask_aware_seed{seed}"
        for epoch in range(args.epochs):
            model.train()
            order = torch.randperm(len(train_x), device=device)
            loss_sum = 0.0
            for ids in order.split(args.batch_size):
                batch = train_x[ids] + torch.randn_like(train_x[ids]) * 0.01
                batch, mask = corrupt_training_batch(batch, args.dropout, args.span)
                optimizer.zero_grad(set_to_none=True)
                context = torch.autocast("cuda", dtype=torch.bfloat16) if device.type == "cuda" else nullcontext()
                with context:
                    logits = model(masked_input(batch, mask)).float()
                    loss = F.cross_entropy(logits, train_y[ids], weight=weights)
                if not torch.isfinite(loss):
                    raise FloatingPointError(tag)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                loss_sum += float(loss.detach()) * len(ids)
            scores = predict(model, validation["x"], clean_mask, device, args.batch_size)
            ap = float(average_precision_score(validation["y"], scores))
            row = {"epoch": epoch + 1, "loss": loss_sum / len(train_x),
                   "validation_ap": ap, "seconds": time.monotonic() - started}
            history.append(row)
            print(json.dumps({"candidate": tag, **row}), flush=True)
            if ap > best_ap:
                best_ap, best_epoch, stale = ap, epoch + 1, 0
                torch.save({"architecture": "transformer_mask_aware", "channels": train["x"].shape[1] * 2,
                            "base_channels": train["x"].shape[1], "seed": seed,
                            "state_dict": {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}},
                           args.output / f"{tag}.pt")
            else:
                stale += 1
            if stale >= args.patience:
                break
        result = {"tag": tag, "seed": seed, "validation_ap": best_ap,
                  "best_epoch": best_epoch, "epochs_run": len(history),
                  "parameters": sum(p.numel() for p in model.parameters()),
                  "elapsed_seconds": time.monotonic() - started, "history": history}
        records.append(result)
        (args.output / "progress.json").write_text(json.dumps(records, indent=2) + "\n")
    summary = {"device": str(device), "dropout": args.dropout, "span": args.span,
               "mean_validation_ap": float(np.mean([r["validation_ap"] for r in records])),
               "records": records,
               "protocol": "train/validation only; explicit observation mask; stress evaluated after freezing"}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"mean_validation_ap": summary["mean_validation_ap"],
                      "best": max(records, key=lambda r: r["validation_ap"])}, indent=2))


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
    p.add_argument("--dropout", type=float, default=0.15)
    p.add_argument("--span", type=int, default=5)
    p.add_argument("--width", type=int, default=64)
    p.add_argument("--layers", type=int, default=3)
    run(p.parse_args())
