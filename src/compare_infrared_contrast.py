"""Paired source-only fine-tuning to probe infrared contrast domain shift.

Uses one previously selected RaptorMaps checkpoint, the same seed and budget
for baseline/contrast-jitter recipes, and RaptorMaps validation for selection.
The already inspected sUAS dataset is used only for retrospective diagnosis.
"""

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np
import torch
from torch.nn import functional as F
from sklearn.metrics import average_precision_score, f1_score

from evaluate_suas_ir_transfer import anomaly_scores, choose_binary_threshold, load_suas
from train_deep import seed_all
from train_infrared import InfraredPV, augment
from train_multiscale import probabilities, write_json


def contrast_jitter(x, mean, std):
    """Broaden image contrast while preserving hotspot morphology."""
    raw = x * std + mean
    center = raw.mean(dim=(-2, -1), keepdim=True)
    gain = torch.exp(torch.empty((len(x), 1, 1, 1), device=x.device).uniform_(
        math.log(0.6), math.log(3.0)))
    shift = torch.empty((len(x), 1, 1, 1), device=x.device).uniform_(-0.08, 0.08)
    jittered = ((raw - center) * gain + center + shift).clamp(0, 1)
    jittered = (jittered + torch.randn_like(jittered) * 0.01).clamp(0, 1)
    return (jittered - mean) / std


def fit_recipe(name, source_checkpoint, x_train, y_train, x_val, y_val, args, device):
    seed_all(args.seed)
    model = InfraredPV(len(source_checkpoint["classes"])).to(device)
    model.load_state_dict(copy.deepcopy(source_checkpoint["state_dict"]))
    mean, std = float(source_checkpoint["mean"]), float(source_checkpoint["std"])
    weights = torch.bincount(y_train, minlength=len(source_checkpoint["classes"])).float().clamp(min=1).rsqrt()
    weights /= weights.mean()
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-3)
    best, best_epoch, history = -1.0, 0, []
    started = time.monotonic()
    for epoch in range(args.epochs):
        model.train()
        lr = args.lr * (0.1 + 0.9 * (1 + math.cos(math.pi * epoch / max(args.epochs - 1, 1))) / 2)
        for group in optimizer.param_groups:
            group["lr"] = lr
        total = 0.0
        for ids in torch.randperm(len(x_train), device=device).split(args.batch_size):
            batch = augment(x_train[ids])
            if name == "contrast_jitter":
                batch = contrast_jitter(batch, mean, std)
            optimizer.zero_grad(set_to_none=True)
            loss = F.cross_entropy(model(batch).float(), y_train[ids], weight=weights)
            if not torch.isfinite(loss):
                raise FloatingPointError(f"Non-finite loss: {name}")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total += float(loss.detach()) * len(ids)
        val = probabilities(model, x_val, args.batch_size)
        score = float(f1_score(y_val.cpu().numpy(), val.argmax(1),
                               labels=range(len(source_checkpoint["classes"])),
                               average="macro", zero_division=0))
        row = {"epoch": epoch + 1, "train_loss": total / len(x_train),
               "validation_macro_f1": score, "elapsed_seconds": time.monotonic() - started}
        history.append(row)
        print(json.dumps({"recipe": name, **row}), flush=True)
        if score > best:
            best, best_epoch = score, epoch + 1
            state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            torch.save({**source_checkpoint, "state_dict": state, "fine_tune_recipe": name,
                        "validation_macro_f1": score}, args.output / f"{name}.pt")
        if epoch + 1 - best_epoch >= args.patience:
            break
    return {"recipe": name, "best_epoch": best_epoch, "validation_macro_f1": best,
            "elapsed_seconds": time.monotonic() - started, "history": history}


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    if args.device == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError("MPS unavailable")
    device = torch.device(args.device)
    source = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    args.output.mkdir(parents=True)
    with np.load(args.data, allow_pickle=False) as data:
        split = data["split"]
        classes = data["classes"].tolist()
        if classes != source["classes"]:
            raise ValueError("Class map mismatch")
        pixels = data["images"]
        labels = data["labels"]
        train_images = pixels[split == "train"]
        val_images = pixels[split == "validation"]
        train_labels = labels[split == "train"]
        val_labels = labels[split == "validation"]
    mean, std = float(source["mean"]), float(source["std"])
    x_train = torch.from_numpy(((train_images.astype(np.float32) / 255 - mean) / std)[:, None]).to(device)
    x_val = torch.from_numpy(((val_images.astype(np.float32) / 255 - mean) / std)[:, None]).to(device)
    y_train = torch.from_numpy(train_labels).long().to(device)
    y_val = torch.from_numpy(val_labels).long().to(device)
    records = [fit_recipe(name, source, x_train, y_train, x_val, y_val, args, device)
               for name in ("original_augment", "contrast_jitter")]
    normal_index = classes.index("No-Anomaly")
    validation_binary = (val_labels != normal_index).astype(np.int64)
    for record in records:
        checkpoint = torch.load(args.output / f"{record['recipe']}.pt", map_location="cpu", weights_only=True)
        val_scores = anomaly_scores(val_images, [checkpoint], args.batch_size, device)
        threshold = choose_binary_threshold(validation_binary, val_scores)
        record["validation_binary_ap"] = float(average_precision_score(validation_binary, val_scores))
        record["validation_binary_threshold"] = threshold
        record["validation_binary_macro_f1"] = float(f1_score(
            validation_binary, val_scores >= threshold, average="macro"))
    selected = max(records, key=lambda r: r["validation_macro_f1"])["recipe"]
    report = {"protocol": "paired source-only fine-tuning; validation chooses recipe; sUAS already inspected",
              "source_checkpoint": str(args.checkpoint), "seed": args.seed, "epochs": args.epochs,
              "source_checkpoint_sha256": hashlib.sha256(args.checkpoint.read_bytes()).hexdigest(),
              "prepared_data_sha256": hashlib.sha256(args.data.read_bytes()).hexdigest(),
              "patience": args.patience, "batch_size": args.batch_size, "device": str(device),
              "train_images": len(train_images), "validation_images": len(val_images),
              "recipes": records, "selected_from_validation": selected,
              "limitations": ["RaptorMaps has no verified site/flight holdout.",
                              "sUAS was previously inspected; any transfer result is retrospective.",
                              "The two datasets differ in labels and capture conditions."]}
    if args.suas_archive:
        images, labels, _, _ = load_suas(args.suas_archive)
        report["suas_archive_md5_verified"] = True
        report["retrospective_suas"] = {}
        for record in records:
            checkpoint = torch.load(args.output / f"{record['recipe']}.pt", map_location="cpu", weights_only=True)
            scores = anomaly_scores(images, [checkpoint], args.batch_size, device)
            predicted = scores >= record["validation_binary_threshold"]
            report["retrospective_suas"][record["recipe"]] = {
                "ap": float(average_precision_score(labels, scores)),
                "macro_f1_at_source_threshold": float(f1_score(labels, predicted, average="macro")),
                "normal_false_positives": int(((labels == 0) & predicted).sum()),
                "normal_images": int((labels == 0).sum()),
                "fault_detected": int(((labels == 1) & predicted).sum()),
                "fault_images": int((labels == 1).sum())}
    write_json(args.output / "results.json", report)
    print(json.dumps({"selected_from_validation": selected,
                      "retrospective_suas": report.get("retrospective_suas")}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("data/raw/infrared_solar_modules/prepared.npz"))
    parser.add_argument("--checkpoint", type=Path, default=Path("reports/infrared_raptor_a10_v1/pretrained_seed2026.pt"))
    parser.add_argument("--suas-archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("mps", "cuda"), default="mps")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--epochs", type=int, default=15)
    parser.add_argument("--patience", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=1e-4)
    args = parser.parse_args()
    if min(args.epochs, args.patience, args.batch_size) < 1 or args.lr <= 0:
        parser.error("Invalid training arguments")
    run(args)
