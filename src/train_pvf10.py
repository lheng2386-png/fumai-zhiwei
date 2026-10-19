"""Train a source-only PVF-10 crop classifier on capture-date-separated data.

The test NPZ is never opened here. Results are research-only because dates are
proxy groups and no independent plant/flight identifiers or rights are known.
"""

import argparse
import json
import math
from pathlib import Path
import time

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from sklearn.metrics import f1_score

from train_deep import seed_all
from train_multiscale import write_json


class ResidualBlock(nn.Module):
    def __init__(self, channels):
        super().__init__()
        self.net = nn.Sequential(nn.Conv2d(channels, channels, 3, padding=1),
                                 nn.GroupNorm(8, channels), nn.GELU(),
                                 nn.Conv2d(channels, channels, 3, padding=1),
                                 nn.GroupNorm(8, channels))

    def forward(self, x):
        return F.gelu(x + self.net(x))


class PVF10Net(nn.Module):
    def __init__(self, classes=10):
        super().__init__()
        layers = []
        previous = 3
        for width in (32, 64, 128, 256):
            layers.extend((nn.Conv2d(previous, width, 3, stride=2, padding=1),
                           nn.GroupNorm(8, width), nn.GELU(), ResidualBlock(width)))
            previous = width
        self.encoder = nn.Sequential(*layers)
        self.classifier = nn.Sequential(nn.AvgPool2d(2), nn.Flatten(),
                                        nn.Linear(256 * 9, 256), nn.GELU(),
                                        nn.Dropout(0.3), nn.Linear(256, classes))

    def forward(self, x):
        return self.classifier(self.encoder(x))


def augment(x):
    flipped = torch.rand((len(x), 1, 1, 1), device=x.device) < 0.5
    x = torch.where(flipped, x.flip(-1), x)
    gain = torch.empty((len(x), 1, 1, 1), device=x.device).uniform_(0.9, 1.1)
    shift = torch.empty((len(x), 1, 1, 1), device=x.device).uniform_(-0.04, 0.04)
    return (x * gain + shift).clamp(0, 1)


@torch.no_grad()
def predict(model, images, batch_size):
    model.eval()
    return torch.cat([model(batch).float().softmax(-1).cpu()
                      for batch in images.split(batch_size)]).numpy()


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    if args.device == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError("MPS unavailable")
    device = torch.device(args.device)
    manifest = json.loads((args.data / "manifest.json").read_text())
    with np.load(args.data / "train.npz", allow_pickle=False) as train:
        train_images, train_labels, classes = train["images"], train["labels"], train["classes"].tolist()
    with np.load(args.data / "validation.npz", allow_pickle=False) as validation:
        val_images, val_labels = validation["images"], validation["labels"]
        if validation["classes"].tolist() != classes:
            raise ValueError("Class maps disagree")
    if len(classes) != 10 or classes != manifest["classes"]:
        raise ValueError("Unexpected class map")
    if len(train_images) != manifest["new_split"]["train"]["images"] or len(val_images) != manifest["new_split"]["validation"]["images"]:
        raise ValueError("Manifest count mismatch")
    seed_all(args.seed)
    args.output.mkdir(parents=True)
    x_train = torch.from_numpy(train_images.transpose(0, 3, 1, 2).astype(np.float32) / 255).to(device)
    x_val = torch.from_numpy(val_images.transpose(0, 3, 1, 2).astype(np.float32) / 255).to(device)
    y_train = torch.from_numpy(train_labels).long().to(device)
    model = PVF10Net(len(classes)).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-3)
    counts = torch.bincount(y_train, minlength=len(classes)).float().clamp(min=1)
    weights = counts.rsqrt()
    weights /= weights.mean()
    history, best, best_epoch = [], -1.0, 0
    started = time.monotonic()
    for epoch in range(args.epochs):
        model.train()
        lr = args.lr * (0.05 + 0.95 * (1 + math.cos(math.pi * epoch / max(args.epochs - 1, 1))) / 2)
        for group in optimizer.param_groups:
            group["lr"] = lr
        total = 0.0
        for indices in torch.randperm(len(x_train), device=device).split(args.batch_size):
            batch = augment(x_train[indices])
            optimizer.zero_grad(set_to_none=True)
            loss = F.cross_entropy(model(batch), y_train[indices], weight=weights)
            if not torch.isfinite(loss):
                raise FloatingPointError("Non-finite training loss")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1)
            optimizer.step()
            total += float(loss.detach()) * len(indices)
        probabilities = predict(model, x_val, args.batch_size)
        macro_f1 = float(f1_score(val_labels, probabilities.argmax(1),
                                  labels=range(len(classes)), average="macro", zero_division=0))
        row = {"epoch": epoch + 1, "train_loss": total / len(x_train),
               "validation_macro_f1": macro_f1, "elapsed_seconds": time.monotonic() - started}
        history.append(row)
        print(json.dumps(row), flush=True)
        if macro_f1 > best:
            best, best_epoch = macro_f1, epoch + 1
            torch.save({"state_dict": {key: value.cpu().clone() for key, value in model.state_dict().items()},
                        "classes": classes, "seed": args.seed, "validation_macro_f1": best,
                        "data_archive_sha256": manifest["archive_sha256"]}, args.output / "best.pt")
            np.save(args.output / "best_validation_probabilities.npy", probabilities)
        if epoch + 1 - best_epoch >= args.patience:
            break
    report = {"protocol": "PVF-10 date-disjoint 10-class research baseline; test untouched",
              "data_manifest": manifest["archive_sha256"], "seed": args.seed,
              "device": str(device), "model_parameters": sum(p.numel() for p in model.parameters()),
              "train_images": len(train_images), "validation_images": len(val_images),
              "best_epoch": best_epoch, "validation_macro_f1": best,
              "elapsed_seconds": time.monotonic() - started, "history": history,
              "test_status": "not read", "limitations": manifest["limitations"]}
    write_json(args.output / "results.json", report)
    print(json.dumps({"best_epoch": best_epoch, "validation_macro_f1": best,
                      "elapsed_seconds": report["elapsed_seconds"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("data/processed/pvf10_date_v1"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("mps", "cuda"), default="mps")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=0.0004)
    args = parser.parse_args()
    if min(args.epochs, args.patience, args.batch_size) < 1 or args.lr <= 0:
        parser.error("Invalid training parameters")
    run(args)
