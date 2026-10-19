"""Paired SolarMagix encoder transfer versus scratch on Reunion development data.

The two sources have different sensor channels and sampling rates. Transfer
only the channel-independent convolutional blocks; train the Reunion stem,
statistics branch and fault head from scratch. Test/normal-stress are unopened.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import torch

from train_deep import seed_all
from train_reunion import (choose_threshold, day_alert_summary, load_split,
                           make_model, summarize_scores, train_one)


def transferred_state(checkpoint_path, seed, target_channels):
    checkpoint_bytes = checkpoint_path.read_bytes()
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    if (checkpoint.get("channels"), checkpoint.get("width"), checkpoint.get("depth")) != (6, 64, 3):
        raise ValueError("Expected SolarMagix six-channel, width-64, depth-3 checkpoint")
    source = checkpoint["state_dict"]
    seed_all(seed)
    target = make_model("multiscale_cnn", target_channels)
    state = target.state_dict()
    block_names = [name for name in state if name.startswith("blocks.")]
    if not block_names or {name for name in source if name.startswith("blocks.")} != set(block_names):
        raise ValueError("Pretrained and target convolutional block keys differ")
    for name in block_names:
        if source[name].shape != state[name].shape:
            raise ValueError(f"Pretrained block shape differs: {name}")
        state[name] = source[name].clone()
    return state, {"path": str(checkpoint_path),
                   "sha256": hashlib.sha256(checkpoint_bytes).hexdigest(),
                   "pretraining_seed": checkpoint.get("seed"),
                   "transferred_tensors": len(block_names),
                   "transferred_parameters": sum(state[name].numel() for name in block_names)}


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    if len(args.seeds) != len(args.pretrained):
        raise ValueError("Provide one pretrained checkpoint for each supervised seed")
    if len(set(args.seeds)) != len(args.seeds):
        raise ValueError("Supervised seeds must be unique")
    device = torch.device(args.device if args.device != "auto" else
                          "cuda" if torch.cuda.is_available() else
                          "mps" if torch.backends.mps.is_available() else "cpu")
    torch.set_num_threads(min(torch.get_num_threads(), 8))
    train = load_split(args.prepared, "train")
    validation = load_split(args.prepared, "validation")
    if train["x"].shape[1] != 14 or validation["x"].shape[1] != 14:
        raise ValueError("Expected the 14-channel Reunion electrical split")
    manifest_bytes = (args.prepared / "manifest.json").read_bytes()
    if not np.array_equal(np.unique(train["y"]), [0, 1]):
        raise ValueError("Training labels must include both classes")
    report = {"protocol": "Paired SolarMagix block-only transfer versus scratch; Reunion train/validation only",
              "prepared_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
              "device": str(device), "torch_version": torch.__version__,
              "seeds": args.seeds, "epochs": args.epochs, "patience": args.patience,
              "batch_size": args.batch_size, "lr": args.lr,
              "splits": {"train": int(len(train["y"])), "validation": int(len(validation["y"]))},
              "test_status": "not loaded", "normal_stress_status": "not loaded",
              "limitations": ["SolarMagix uses hourly data; Reunion uses minute data.",
                              "Only convolutional blocks transfer; source sensors do not match target sensors.",
                              "One Reunion plant and one repeatedly used validation period; not production evidence."],
              "runs": {}}
    args.output.mkdir(parents=True)
    for seed, source in zip(args.seeds, args.pretrained):
        report["runs"][str(seed)] = {}
        for mode in ("scratch", "transferred"):
            folder = args.output / mode
            folder.mkdir(exist_ok=True)
            initial, provenance = (None, None) if mode == "scratch" else transferred_state(
                source, seed, train["x"].shape[1])
            record = train_one("multiscale_cnn", seed, train, validation, folder,
                               device, args, initial_state=initial)
            scores = np.load(folder / f"{record['tag']}_validation.npy")
            threshold = choose_threshold(validation["y"], scores)
            report["runs"][str(seed)][mode] = {
                "source": provenance, "validation_ap": record["validation_ap"],
                "best_epoch": record["best_epoch"], "elapsed_seconds": record["elapsed_seconds"],
                "threshold_chosen_on_validation": threshold,
                "window_metrics": summarize_scores(validation["y"], scores, threshold),
                "day_metrics": day_alert_summary(validation, scores, threshold)}
            (args.output / "progress.json").write_text(json.dumps(report, indent=2) + "\n")
    for mode in ("scratch", "transferred"):
        report[mode + "_mean_validation_ap"] = float(np.mean([
            record[mode]["validation_ap"] for record in report["runs"].values()]))
    report["transfer_minus_scratch_mean_ap"] = (report["transferred_mean_validation_ap"] -
                                                report["scratch_mean_validation_ap"])
    (args.output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in (
        "scratch_mean_validation_ap", "transferred_mean_validation_ap",
        "transfer_minus_scratch_mean_ap")}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, default=Path("data/processed/la_reunion_v1"))
    parser.add_argument("--pretrained", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=[2031, 2032, 2033])
    parser.add_argument("--device", choices=("auto", "cuda", "mps", "cpu"), default="auto")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=8e-4)
    args = parser.parse_args()
    if min(args.epochs, args.patience, args.batch_size, args.lr) <= 0:
        parser.error("Training controls must be positive")
    run(args)
