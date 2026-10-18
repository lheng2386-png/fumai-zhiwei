"""Paired normal-only pretraining vs scratch on the Reunion development split.

Only the original train and validation files are opened. The previously viewed
test and normal-stress periods are not evaluated by this experiment.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import torch
from torch.nn import functional as F

from train_deep import seed_all
from train_reunion import (choose_threshold, day_alert_summary, load_split,
                           make_model, summarize_scores, train_one)


def masked_batch(x):
    count, channels, length = x.shape
    span_length = max(2, length // 5)
    start = torch.randint(0, length-span_length+1, (count, 1, 1), device=x.device)
    positions = torch.arange(length, device=x.device)[None, None, :]
    time_mask = (positions >= start) & (positions < start+span_length)
    channel_mask = torch.rand(count, channels, 1, device=x.device) < 0.1
    mask = (time_mask | channel_mask).expand_as(x)
    return x.masked_fill(mask, 0), mask


def normal_pretrain(train, seed, device, epochs, batch_size, lr):
    seed_all(seed)
    normal = torch.from_numpy(train["x"][train["y"] == 0]).to(device)
    model = make_model("multiscale_cnn", normal.shape[1]).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-3)
    history = []
    for epoch in range(epochs):
        model.train()
        total = 0.0
        for indices in torch.randperm(len(normal), device=device).split(batch_size):
            batch = normal[indices]
            corrupted, mask = masked_batch(batch)
            optimizer.zero_grad(set_to_none=True)
            prediction = model(corrupted, reconstruct=True)
            loss = F.smooth_l1_loss(prediction[mask], batch[mask])
            if not torch.isfinite(loss):
                raise FloatingPointError("Non-finite reconstruction loss")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total += loss.detach().item()*len(batch)
        history.append({"epoch": epoch+1, "masked_huber_loss": total/len(normal)})
    return {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}, history


def validation_result(record, validation, folder):
    scores = np.load(folder/f"{record['tag']}_validation.npy")
    threshold = choose_threshold(validation["y"], scores)
    return {"validation_ap": record["validation_ap"], "best_epoch": record["best_epoch"],
            "threshold_chosen_on_validation": threshold,
            "window_metrics": summarize_scores(validation["y"], scores, threshold),
            "day_metrics": day_alert_summary(validation, scores, threshold)}


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    device = torch.device(args.device if args.device != "auto" else
                          "cuda" if torch.cuda.is_available() else
                          "mps" if torch.backends.mps.is_available() else "cpu")
    torch.set_num_threads(min(torch.get_num_threads(), 8))
    train = load_split(args.prepared, "train")
    validation = load_split(args.prepared, "validation")
    report = {"protocol": "paired train-only normal reconstruction, then identical supervised fine-tuning; validation only",
              "prepared_manifest_sha256": hashlib.sha256((args.prepared/"manifest.json").read_bytes()).hexdigest(),
              "device": str(device), "seeds": args.seeds,
              "normal_pretraining_windows": int((train["y"] == 0).sum()),
              "pretrain_epochs": args.pretrain_epochs, "supervised_epochs": args.epochs,
              "limitations": ["Single plant and one development validation period.",
                              "Validation threshold/day statistics are descriptive, not independent tests.",
                              "Old held-out test and normal-stress periods are not opened."],
              "runs": {}}
    for mode in ("scratch", "pretrained"):
        folder = args.output/mode
        folder.mkdir()
        report["runs"][mode] = {}
        for seed in args.seeds:
            initial = None
            history = None
            if mode == "pretrained":
                initial, history = normal_pretrain(train, seed, device,
                                                   args.pretrain_epochs,
                                                   args.batch_size, args.pretrain_lr)
                torch.save({"state_dict": initial, "seed": seed,
                            "epochs": args.pretrain_epochs,
                            "normal_windows": report["normal_pretraining_windows"]},
                           folder/f"normal_ssl_seed{seed}.pt")
            record = train_one("multiscale_cnn", seed, train, validation, folder,
                               device, args, initial_state=initial)
            report["runs"][mode][str(seed)] = {
                "pretrain_history": history,
                "supervised": validation_result(record, validation, folder)}
            (args.output/"progress.json").write_text(json.dumps(report, indent=2)+"\n")
    for mode in ("scratch", "pretrained"):
        values = [entry["supervised"]["validation_ap"] for entry in report["runs"][mode].values()]
        report[mode+"_mean_validation_ap"] = float(np.mean(values))
    report["pretrain_minus_scratch_mean_ap"] = (report["pretrained_mean_validation_ap"]-
                                                report["scratch_mean_validation_ap"])
    (args.output/"results.json").write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps({key: report[key] for key in
                      ("scratch_mean_validation_ap", "pretrained_mean_validation_ap",
                       "pretrain_minus_scratch_mean_ap")}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    parser.add_argument("--seeds", type=int, nargs="+", default=[2031, 2032, 2033])
    parser.add_argument("--pretrain-epochs", type=int, default=20)
    parser.add_argument("--pretrain-lr", type=float, default=1e-3)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=8e-4)
    args = parser.parse_args()
    if min(args.pretrain_epochs, args.pretrain_lr, args.epochs, args.patience,
           args.batch_size, args.lr) <= 0:
        parser.error("Training controls must be positive")
    run(args)
