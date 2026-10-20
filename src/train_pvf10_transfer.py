"""Fine-tune ImageNet ResNet18 on the repaired PVF-10 capture-date split.

Train/validation only; the date-held-out test NPZ stays unopened until model
choice is frozen. ImageNet weights come from torchvision's official release.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import time
from urllib.parse import urlsplit

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from sklearn.metrics import f1_score
from torchvision.models import (ConvNeXt_Tiny_Weights, EfficientNet_B0_Weights,
                                ResNet18_Weights, convnext_tiny, efficientnet_b0, resnet18)
import torchvision

from train_deep import seed_all
from train_multiscale import write_json


IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


def normalize(x, input_size=112):
    if x.shape[-2:] != (input_size, input_size):
        x = F.interpolate(x, size=(input_size, input_size), mode="bilinear",
                          align_corners=False, antialias=True)
    mean = torch.tensor(IMAGENET_MEAN, device=x.device).view(1, 3, 1, 1)
    std = torch.tensor(IMAGENET_STD, device=x.device).view(1, 3, 1, 1)
    return (x - mean) / std


def augment(x):
    flip = torch.rand((len(x), 1, 1, 1), device=x.device) < 0.5
    x = torch.where(flip, x.flip(-1), x)
    gain = torch.empty((len(x), 1, 1, 1), device=x.device).uniform_(0.9, 1.1)
    shift = torch.empty((len(x), 1, 1, 1), device=x.device).uniform_(-0.03, 0.03)
    return (x * gain + shift).clamp(0, 1)


@torch.no_grad()
def predict(model, images, batch_size, input_size=112):
    model.eval()
    return torch.cat([model(normalize(batch, input_size)).float().softmax(-1).cpu()
                      for batch in images.split(batch_size)]).numpy()


def build_model(architecture, classes):
    if architecture == "resnet18":
        weights = ResNet18_Weights.IMAGENET1K_V1
        model = resnet18(weights=weights)
        model.fc = nn.Linear(model.fc.in_features, classes)
        head_prefix = "fc."
    elif architecture == "efficientnet_b0":
        weights = EfficientNet_B0_Weights.IMAGENET1K_V1
        model = efficientnet_b0(weights=weights)
        model.classifier[1] = nn.Linear(model.classifier[1].in_features, classes)
        head_prefix = "classifier.1."
    elif architecture == "convnext_tiny":
        weights = ConvNeXt_Tiny_Weights.IMAGENET1K_V1
        model = convnext_tiny(weights=weights)
        model.classifier[2] = nn.Linear(model.classifier[2].in_features, classes)
        head_prefix = "classifier.2."
    else:
        raise ValueError(f"Unknown architecture: {architecture}")
    return model, weights, head_prefix


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    if args.device == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError("MPS unavailable")
    manifest = json.loads((args.data / "manifest.json").read_text())
    with np.load(args.data / "train.npz", allow_pickle=False) as train:
        train_images, train_labels, classes = train["images"], train["labels"], train["classes"].tolist()
    with np.load(args.data / "validation.npz", allow_pickle=False) as validation:
        val_images, val_labels = validation["images"], validation["labels"]
        if validation["classes"].tolist() != classes:
            raise ValueError("Class maps disagree")
    if classes != manifest["classes"]:
        raise ValueError("Manifest class map mismatch")
    device = torch.device(args.device)
    seed_all(args.seed)
    model, weights_enum, head_prefix = build_model(args.architecture, len(classes))
    weight_cache = Path(torch.hub.get_dir()) / "checkpoints" / Path(urlsplit(weights_enum.url).path).name
    pretrained_sha256 = hashlib.sha256(weight_cache.read_bytes()).hexdigest()
    model = model.to(device)
    args.output.mkdir(parents=True)
    x_train = torch.from_numpy(train_images.transpose(0, 3, 1, 2).astype(np.float32) / 255).to(device)
    x_val = torch.from_numpy(val_images.transpose(0, 3, 1, 2).astype(np.float32) / 255).to(device)
    y_train = torch.from_numpy(train_labels).long().to(device)
    counts = torch.bincount(y_train, minlength=len(classes)).float().clamp(min=1)
    weights = counts.rsqrt()
    weights /= weights.mean()
    optimizer = torch.optim.AdamW([
        {"params": (p for name, p in model.named_parameters() if not name.startswith(head_prefix)),
         "lr": args.backbone_lr, "base_lr": args.backbone_lr},
        {"params": (p for name, p in model.named_parameters() if name.startswith(head_prefix)),
         "lr": args.head_lr, "base_lr": args.head_lr}],
        weight_decay=1e-3)
    history, best, best_epoch = [], -1.0, 0
    started = time.monotonic()
    for epoch in range(args.epochs):
        model.train()
        factor = 0.1 + 0.9 * (1 + math.cos(math.pi * epoch / max(args.epochs - 1, 1))) / 2
        for group in optimizer.param_groups:
            group["lr"] = group["base_lr"] * factor
        total = 0.0
        for ids in torch.randperm(len(x_train), device=device).split(args.batch_size):
            optimizer.zero_grad(set_to_none=True)
            loss = F.cross_entropy(model(normalize(augment(x_train[ids]), args.input_size)),
                                   y_train[ids], weight=weights)
            if not torch.isfinite(loss):
                raise FloatingPointError("Non-finite loss")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1)
            optimizer.step()
            total += float(loss.detach()) * len(ids)
        probabilities = predict(model, x_val, args.batch_size, args.input_size)
        score = float(f1_score(val_labels, probabilities.argmax(1),
                               labels=range(len(classes)), average="macro", zero_division=0))
        row = {"epoch": epoch + 1, "train_loss": total / len(x_train),
               "validation_macro_f1": score, "elapsed_seconds": time.monotonic() - started}
        history.append(row)
        print(json.dumps(row), flush=True)
        if score > best:
            best, best_epoch = score, epoch + 1
            torch.save({"state_dict": {name: value.cpu().clone() for name, value in model.state_dict().items()},
                        "classes": classes, "seed": args.seed, "validation_macro_f1": best,
                        "archive_sha256": manifest["archive_sha256"],
                        "architecture": args.architecture,
                        "input_size": args.input_size,
                        "pretrained": str(weights_enum)}, args.output / "best.pt")
            np.save(args.output / "best_validation_probabilities.npy", probabilities)
        if epoch + 1 - best_epoch >= args.patience:
            break
    report = {"protocol": "PVF-10 date-disjoint ImageNet transfer; test untouched",
              "archive_sha256": manifest["archive_sha256"], "seed": args.seed,
              "device": str(device), "torch_version": torch.__version__,
              "torchvision_version": torchvision.__version__,
              "architecture": args.architecture, "pretrained": str(weights_enum),
              "input_size": [args.input_size, args.input_size],
              "source_image_size": list(train_images.shape[1:3]),
              "training_controls": {"epochs": args.epochs, "patience": args.patience,
                                    "batch_size": args.batch_size,
                                    "backbone_lr": args.backbone_lr, "head_lr": args.head_lr},
              "pretrained_sha256": pretrained_sha256,
              "model_parameters": sum(p.numel() for p in model.parameters()),
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
    parser.add_argument("--architecture", choices=("resnet18", "efficientnet_b0", "convnext_tiny"),
                        default="resnet18")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("mps", "cuda"), default="mps")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--epochs", type=int, default=25)
    parser.add_argument("--patience", type=int, default=6)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--input-size", type=int, choices=(112, 224), default=112,
                        help="Resize source crops per batch; interpolation adds no measured detail")
    parser.add_argument("--backbone-lr", type=float, default=0.00002)
    parser.add_argument("--head-lr", type=float, default=0.0003)
    args = parser.parse_args()
    if min(args.epochs, args.patience, args.batch_size) < 1 or min(args.backbone_lr, args.head_lr) <= 0:
        parser.error("Invalid training parameters")
    run(args)
