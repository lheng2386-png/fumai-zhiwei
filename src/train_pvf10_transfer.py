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
from torch.optim.swa_utils import AveragedModel, get_ema_multi_avg_fn
from torch.nn import functional as F
from sklearn.metrics import f1_score
from torchvision.models import (ConvNeXt_Tiny_Weights, EfficientNet_B0_Weights,
                                ResNet18_Weights, Swin_T_Weights, convnext_tiny, efficientnet_b0, resnet18, swin_t)
import torchvision
from torchvision.transforms.functional import gaussian_blur

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


def augment(x, recipe="baseline"):
    if recipe not in ("baseline", "robust"):
        raise ValueError("Unknown augmentation recipe")
    flip = torch.rand((len(x), 1, 1, 1), device=x.device) < 0.5
    x = torch.where(flip, x.flip(-1), x)
    gain = torch.empty((len(x), 1, 1, 1), device=x.device).uniform_(0.9, 1.1)
    shift = torch.empty((len(x), 1, 1, 1), device=x.device).uniform_(-0.03, 0.03)
    x = (x * gain + shift).clamp(0, 1)
    if recipe == "robust":
        center = x.mean(dim=(-2, -1), keepdim=True)
        contrast = torch.empty((len(x), 1, 1, 1), device=x.device).uniform_(0.5, 1.2)
        x = (x - center) * contrast + center
        blurred = gaussian_blur(x, [5, 5], [1.0, 1.0])
        apply_blur = torch.rand((len(x), 1, 1, 1), device=x.device) < 0.2
        x = torch.where(apply_blur, blurred, x)
        sigma = torch.empty((len(x), 1, 1, 1), device=x.device).uniform_(0.0, 0.02)
        x = x + torch.randn_like(x) * sigma
    return x.clamp(0, 1)


@torch.no_grad()
def predict(model, images, batch_size, input_size=112):
    model.eval()
    return torch.cat([model(normalize(batch, input_size)).float().softmax(-1).cpu()
                      for batch in images.split(batch_size)]).numpy()


def build_model(architecture, classes, pretrained=True):
    if architecture == "resnet18":
        weights = ResNet18_Weights.IMAGENET1K_V1
        model = resnet18(weights=weights if pretrained else None)
        model.fc = nn.Linear(model.fc.in_features, classes)
        head_prefix = "fc."
    elif architecture == "efficientnet_b0":
        weights = EfficientNet_B0_Weights.IMAGENET1K_V1
        model = efficientnet_b0(weights=weights if pretrained else None)
        model.classifier[1] = nn.Linear(model.classifier[1].in_features, classes)
        head_prefix = "classifier.1."
    elif architecture == "convnext_tiny":
        weights = ConvNeXt_Tiny_Weights.IMAGENET1K_V1
        model = convnext_tiny(weights=weights if pretrained else None)
        model.classifier[2] = nn.Linear(model.classifier[2].in_features, classes)
        head_prefix = "classifier.2."
    elif architecture == "swin_t":
        weights = Swin_T_Weights.IMAGENET1K_V1
        model = swin_t(weights=weights if pretrained else None)
        model.head = nn.Linear(model.head.in_features, classes)
        head_prefix = "head."
    else:
        raise ValueError(f"Unknown architecture: {architecture}")
    return model, weights, head_prefix



def defect_aware_loss(logits, labels, weights, healthy_index, binary_weight=0.0):
    """Multiclass weighted CE plus unweighted healthy/any-defect marginal CE."""
    if not math.isfinite(binary_weight) or binary_weight < 0 or not 0 <= healthy_index < logits.shape[1]:
        raise ValueError("Invalid binary loss controls")
    loss = F.cross_entropy(logits, labels, weight=weights)
    if binary_weight:
        defect_indices = [index for index in range(logits.shape[1]) if index != healthy_index]
        binary_logits = torch.stack((logits[:, healthy_index],
                                     torch.logsumexp(logits[:, defect_indices], dim=1)), dim=1)
        loss = loss + binary_weight * F.cross_entropy(binary_logits, (labels != healthy_index).long())
    return loss


def load_backbone(model, checkpoint, architecture, head_prefix):
    if checkpoint.get("architecture") != architecture or "backbone_state_dict" not in checkpoint:
        raise ValueError("Pretrained backbone architecture/format mismatch")
    state = checkpoint["backbone_state_dict"]
    expected = {key for key in model.state_dict() if not key.startswith(head_prefix)}
    if set(state) != expected:
        raise ValueError("Backbone keys must exactly cover all non-head parameters and buffers")
    target = model.state_dict()
    if any(value.shape != target[key].shape for key, value in state.items()):
        raise ValueError("Backbone tensor shape mismatch")
    model.load_state_dict({**target, **state}, strict=True)


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
    backbone_init = getattr(args, "backbone_init", None)
    backbone_sha256 = None
    if backbone_init is not None:
        checkpoint = torch.load(backbone_init, map_location="cpu", weights_only=True)
        load_backbone(model, checkpoint, args.architecture, head_prefix)
        backbone_sha256 = hashlib.sha256(backbone_init.read_bytes()).hexdigest()
    model = model.to(device)
    ema = (AveragedModel(model, multi_avg_fn=get_ema_multi_avg_fn(args.ema_decay),
                         use_buffers=True) if args.ema_decay else None)
    args.output.mkdir(parents=True)
    x_train = torch.from_numpy(train_images.transpose(0, 3, 1, 2).astype(np.float32) / 255).to(device)
    x_val = torch.from_numpy(val_images.transpose(0, 3, 1, 2).astype(np.float32) / 255).to(device)
    y_train = torch.from_numpy(train_labels).long().to(device)
    counts = torch.bincount(y_train, minlength=len(classes)).float().clamp(min=1)
    weights = counts.pow(-args.class_weight_power)
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
            logits = model(normalize(augment(x_train[ids], args.augmentation), args.input_size))
            loss = defect_aware_loss(logits, y_train[ids], weights,
                                     classes.index("healthy panel"), args.binary_loss_weight)
            if not torch.isfinite(loss):
                raise FloatingPointError("Non-finite loss")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1)
            optimizer.step()
            if ema is not None:
                ema.update_parameters(model)
            total += float(loss.detach()) * len(ids)
        evaluation_model = ema.module if ema is not None else model
        probabilities = predict(evaluation_model, x_val, args.batch_size, args.input_size)
        score = float(f1_score(val_labels, probabilities.argmax(1),
                               labels=range(len(classes)), average="macro", zero_division=0))
        row = {"epoch": epoch + 1, "train_loss": total / len(x_train),
               "validation_macro_f1": score, "elapsed_seconds": time.monotonic() - started}
        history.append(row)
        print(json.dumps(row), flush=True)
        if score > best:
            best, best_epoch = score, epoch + 1
            torch.save({"state_dict": {name: value.cpu().clone() for name, value in evaluation_model.state_dict().items()},
                        "classes": classes, "seed": args.seed, "validation_macro_f1": best,
                        "archive_sha256": manifest["archive_sha256"],
                        "architecture": args.architecture,
                        "input_size": args.input_size,
                        "ema_decay": args.ema_decay,
                        "augmentation": args.augmentation,
                        "binary_loss_weight": args.binary_loss_weight,
                        "class_weight_power": args.class_weight_power,
                        "adapted_backbone_sha256": backbone_sha256,
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
                                    "backbone_lr": args.backbone_lr, "head_lr": args.head_lr,
                                    "ema_decay": args.ema_decay, "augmentation": args.augmentation,
                                    "binary_loss_weight": args.binary_loss_weight,
                                    "class_weight_power": args.class_weight_power},
              "pretrained_sha256": pretrained_sha256,
              "adapted_backbone_sha256": backbone_sha256,
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
    parser.add_argument("--backbone-init", type=Path,
                        help="Optional strict self-supervised backbone checkpoint; head stays seed-initialized")
    parser.add_argument("--architecture", choices=("resnet18", "efficientnet_b0", "convnext_tiny", "swin_t"),
                        default="resnet18")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("mps", "cuda"), default="mps")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--epochs", type=int, default=25)
    parser.add_argument("--patience", type=int, default=6)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--input-size", type=int, choices=(112, 224), default=112,
                        help="Resize source crops per batch; interpolation adds no measured detail")
    parser.add_argument("--class-weight-power", type=float, default=0.5,
                        help="Inverse training class-count power; 0 is uniform loss, .5 preserves baseline")
    parser.add_argument("--binary-loss-weight", type=float, default=0.0,
                        help="Auxiliary healthy/any-defect marginal CE; does not require new labels")
    parser.add_argument("--augmentation", choices=("baseline", "robust"), default="baseline")
    parser.add_argument("--ema-decay", type=float, default=0.0,
                        help="0 disables EMA; otherwise evaluate and save EMA weights")
    parser.add_argument("--backbone-lr", type=float, default=0.00002)
    parser.add_argument("--head-lr", type=float, default=0.0003)
    args = parser.parse_args()
    if min(args.epochs, args.patience, args.batch_size) < 1 or min(args.backbone_lr, args.head_lr) <= 0:
        parser.error("Invalid training parameters")
    if not math.isfinite(args.class_weight_power) or not 0 <= args.class_weight_power <= 1:
        parser.error("class-weight-power must be in [0, 1]")
    if not math.isfinite(args.binary_loss_weight) or args.binary_loss_weight < 0:
        parser.error("binary-loss-weight must be finite and nonnegative")
    if not 0 <= args.ema_decay < 1:
        parser.error("EMA decay must be in [0, 1)")
    run(args)
