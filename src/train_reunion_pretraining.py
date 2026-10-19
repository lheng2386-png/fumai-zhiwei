"""Masked reconstruction pretraining followed by Transformer fine-tuning.

Pretraining uses only normal windows from the training partition. Validation
is used for supervised early stopping after pretraining; the old test and
normal-stress partitions are never loaded here.
"""

import argparse
import json
import math
import time
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import average_precision_score
from torch.nn import functional as F

from train_arch_compare import TransformerPV
from train_deep import seed_all


class ReconstructableTransformer(TransformerPV):
    def __init__(self, channels, width=64, layers=3):
        super().__init__(channels=channels, width=width, layers=layers, classes=2)
        self.reconstruction = torch.nn.Linear(width, channels)

    def encode(self, x):
        return self.encoder(self.input(x.transpose(1, 2)) + self.position[:, :x.shape[-1]])

    def reconstruct(self, x):
        return self.reconstruction(self.encode(x)).transpose(1, 2)


@torch.no_grad()
def predict(model, x, device, batch_size=256):
    model.eval()
    scores = []
    for start in range(0, len(x), batch_size):
        batch = torch.from_numpy(x[start:start + batch_size]).to(device)
        scores.append(model(batch).float().softmax(-1)[:, 1].cpu().numpy())
    return np.concatenate(scores)


def pretrain(model, normal_x, args, device, seed):
    model.train()
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.pretrain_lr, weight_decay=1e-3)
    for epoch in range(args.pretrain_epochs):
        order = torch.randperm(len(normal_x), device=device)
        total = 0.0
        for ids in order.split(args.batch_size):
            original = normal_x[ids]
            mask = torch.rand_like(original) < args.mask_probability
            # At least one masked point per sample keeps the reconstruction task meaningful.
            mask[:, :, 0] = True
            corrupted = original.masked_fill(mask, 0.0)
            optimizer.zero_grad(set_to_none=True)
            context = torch.autocast("cuda", dtype=torch.bfloat16) if device.type == "cuda" else nullcontext()
            with context:
                reconstruction = model.reconstruct(corrupted).float()
                loss = F.smooth_l1_loss(reconstruction[mask], original[mask])
            if not torch.isfinite(loss):
                raise FloatingPointError(f"pretrain seed {seed}")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total += float(loss.detach()) * len(ids)
        print(json.dumps({"phase": "pretrain", "seed": seed, "epoch": epoch + 1,
                          "loss": total / len(normal_x)}), flush=True)


def finetune(model, train_x, train_y, validation_x, validation_y, args, device, seed):
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-3)
    counts = np.bincount(train_y.cpu().numpy(), minlength=2).astype(np.float32)
    weights = torch.tensor(1 / np.sqrt(np.maximum(counts, 1)), device=device)
    weights /= weights.mean()
    best_ap, best_epoch, stale, history = -1.0, 0, 0, []
    started = time.monotonic()
    for epoch in range(args.epochs):
        model.train()
        order = torch.randperm(len(train_x), device=device)
        total = 0.0
        for ids in order.split(args.batch_size):
            batch = train_x[ids] + torch.randn_like(train_x[ids]) * 0.01
            optimizer.zero_grad(set_to_none=True)
            context = torch.autocast("cuda", dtype=torch.bfloat16) if device.type == "cuda" else nullcontext()
            with context:
                logits = model(batch).float()
                loss = F.cross_entropy(logits, train_y[ids], weight=weights)
            if not torch.isfinite(loss):
                raise FloatingPointError(f"finetune seed {seed}")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total += float(loss.detach()) * len(ids)
        scores = predict(model, validation_x, device, args.batch_size)
        ap = float(average_precision_score(np.asarray(validation_y), scores))
        row = {"epoch": epoch + 1, "loss": total / len(train_x),
               "validation_ap": ap, "seconds": time.monotonic() - started}
        history.append(row)
        print(json.dumps({"phase": "finetune", "seed": seed, **row}), flush=True)
        if ap > best_ap:
            best_ap, best_epoch, stale = ap, epoch + 1, 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()
                          if k != "reconstruction.weight" and k != "reconstruction.bias"}
        else:
            stale += 1
        if stale >= args.patience:
            break
    model.load_state_dict(best_state, strict=False)
    return {"validation_ap": best_ap, "best_epoch": best_epoch,
            "epochs_run": len(history), "history": history,
            "elapsed_seconds": time.monotonic() - started}


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    device = torch.device(args.device if args.device != "auto" else
                          ("mps" if torch.backends.mps.is_available() else
                           "cuda" if torch.cuda.is_available() else "cpu"))
    with np.load(args.prepared / "train.npz") as data:
        train_x_np, train_y_np = data["x"], data["y"]
    with np.load(args.prepared / "validation.npz") as data:
        validation_x, validation_y = data["x"], data["y"]
    train_x = torch.from_numpy(train_x_np).to(device)
    train_y = torch.from_numpy(train_y_np).long().to(device)
    normal_x = train_x[train_y == 0]
    records = []
    for seed in args.seeds:
        seed_all(seed)
        model = ReconstructableTransformer(train_x.shape[1], args.width, args.layers).to(device)
        started = time.monotonic()
        pretrain(model, normal_x, args, device, seed)
        result = finetune(model, train_x, train_y, validation_x, validation_y, args, device, seed)
        tag = f"pretrained_transformer_seed{seed}"
        torch.save({"architecture": "transformer", "channels": train_x.shape[1],
                    "seed": seed, "pretrained": True,
                    "state_dict": {k: v.cpu() for k, v in model.state_dict().items()
                                   if not k.startswith("reconstruction.")}},
                   args.output / f"{tag}.pt")
        record = {"tag": tag, "seed": seed, "pretrained": True,
                  "parameters": sum(p.numel() for p in model.parameters()),
                  **{k: v for k, v in result.items() if k != "history"},
                  "history": result["history"],
                  "total_elapsed_seconds": time.monotonic() - started}
        records.append(record)
        (args.output / "progress.json").write_text(json.dumps(records, indent=2) + "\n")
    summary = {"device": str(device), "normal_pretrain_windows": len(normal_x),
               "mean_validation_ap": float(np.mean([r["validation_ap"] for r in records])),
               "records": records,
               "protocol": "normal training windows only for pretraining; validation for fine-tuning early stopping"}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"mean_validation_ap": summary["mean_validation_ap"],
                      "seeds": [(r["seed"], r["validation_ap"]) for r in records]}, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--prepared", type=Path, default=Path("data/processed/la_reunion_v1"))
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--device", choices=["auto", "cpu", "cuda", "mps"], default="auto")
    p.add_argument("--seeds", type=int, nargs="+", default=[2031, 2032, 2033])
    p.add_argument("--pretrain-epochs", type=int, default=20)
    p.add_argument("--epochs", type=int, default=50)
    p.add_argument("--patience", type=int, default=8)
    p.add_argument("--batch-size", type=int, default=256)
    p.add_argument("--pretrain-lr", type=float, default=8e-4)
    p.add_argument("--lr", type=float, default=8e-4)
    p.add_argument("--mask-probability", type=float, default=0.15)
    p.add_argument("--width", type=int, default=64)
    p.add_argument("--layers", type=int, default=3)
    run(p.parse_args())
