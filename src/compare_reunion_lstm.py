"""Validation-only recurrent comparison on the frozen La Reunion train/validation split.

The held-out test and normal-stress files are never opened. This is exploratory
because the development validation period has already informed prior research.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import average_precision_score

from train_reunion import load_split, train_one


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    torch.set_num_threads(min(torch.get_num_threads(), 8))
    device = torch.device(args.device if args.device != "auto" else
                          "cuda" if torch.cuda.is_available() else
                          "mps" if torch.backends.mps.is_available() else "cpu")
    if device.type == "cpu":
        raise RuntimeError("GPU required for the LSTM comparison")
    train = load_split(args.prepared, "train")
    validation = load_split(args.prepared, "validation")
    manifest = {"protocol": "Original La Reunion train/validation only; no held-out test or stress access",
                "architecture": f"bidirectional two-layer {args.architecture.upper()} with statistics branch",
                "device": str(device), "seeds": args.seeds, "epochs": args.epochs,
                "patience": args.patience, "batch_size": args.batch_size,
                "lr": args.lr, "selection": "validation AP only; exploratory reused validation period"}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    records = []
    for seed in args.seeds:
        records.append(train_one(args.architecture, seed, train, validation, args.output, device, args))
        (args.output / "progress.json").write_text(json.dumps(records, indent=2) + "\n")
    ensemble = np.mean([np.load(args.output / f"{args.architecture}_seed{seed}_validation.npy")
                        for seed in args.seeds], axis=0)
    summary = {"mean_single_seed_validation_ap": float(np.mean([r["validation_ap"] for r in records])),
               "individual_validation_ap": {str(r["seed"]): r["validation_ap"] for r in records},
               "ensemble_validation_ap": float(average_precision_score(validation["y"], ensemble)),
               "mean_seconds": float(np.mean([r["elapsed_seconds"] for r in records])),
               "held_out_status": "not evaluated"}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("auto", "cuda", "mps"), default="auto")
    parser.add_argument("--architecture", choices=("lstm", "rnn"), default="lstm")
    parser.add_argument("--seeds", type=int, nargs="+", default=[2031, 2032, 2033])
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=8e-4)
    run(parser.parse_args())
