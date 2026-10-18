"""Re-evaluate saved SSL checkpoints on the same validation mask only."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import torch

from multiscale_model import MultiscalePV
from pretrain_solarmagix import VALIDATION_MASK_SEED, eval_mask, evaluate


def run(data_path: Path, candidate_dirs: list[Path], device: str, batch_size: int) -> dict:
    with np.load(data_path) as prepared:
        validation_np = prepared["validation"].copy()
    validation = torch.from_numpy(validation_np)
    mask = eval_mask(validation.shape, VALIDATION_MASK_SEED)
    validation_hash = hashlib.sha256(validation_np.tobytes()).hexdigest()
    result = {"split": "validation only", "mask_seed": VALIDATION_MASK_SEED,
              "validation_array_sha256": validation_hash, "candidates": {},
              "ensembles": {}}
    groups = {}
    for folder in candidate_dirs:
        record = json.loads((folder/"results.json").read_text())
        hashes = record.get("array_sha256", {})
        if hashes and hashes["validation"] != validation_hash:
            raise ValueError(f"Validation data changed: {folder}")
        checkpoint = torch.load(folder/"best.pt", map_location="cpu", weights_only=True)
        model = MultiscalePV(channels=checkpoint["channels"], width=checkpoint["width"],
                             depth=checkpoint["depth"]).to(device)
        model.load_state_dict(checkpoint["state_dict"])
        scores = evaluate(model, validation, mask, batch_size, device)
        groups.setdefault((checkpoint["width"], checkpoint["depth"]), []).append(model)
        result["candidates"][folder.name] = {
            "width": checkpoint["width"], "depth": checkpoint["depth"],
            "seed": checkpoint["seed"], "best_epoch_from_original_validation": record["best_epoch"],
            "masked_mse_common_mask": scores["masked_mse"],
            "zero_fill_mse_common_mask": scores["zero_fill_mse"]}
    for (width, depth), models in groups.items():
        if len(models) < 2:
            continue
        for model in models:
            model.eval()
        squared = 0.0
        count = 0
        with torch.no_grad():
            for xb, mb in zip(validation.split(batch_size), mask.split(batch_size)):
                xb, mb = xb.to(device), mb.to(device)
                hidden = xb.masked_fill(mb, 0)
                mean_prediction = torch.stack([model(hidden, reconstruct=True)
                                               for model in models]).mean(0)
                squared += float(((mean_prediction-xb).square()*mb).sum())
                count += int(mb.sum())
        result["ensembles"][f"width{width}_depth{depth}"] = {
            "members": len(models), "masked_mse_common_mask": squared/count}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=["cpu", "mps", "cuda"], default="cpu")
    parser.add_argument("--batch-size", type=int, default=256)
    args = parser.parse_args()
    report = run(args.data, args.candidate, args.device, args.batch_size)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(report, indent=2))
