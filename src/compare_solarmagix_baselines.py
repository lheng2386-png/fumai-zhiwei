"""Compare a completed SSL run with simple imputation on identical masks."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from pretrain_solarmagix import eval_mask


def linear_interpolation_mse(data: np.ndarray, mask: np.ndarray) -> float:
    squared = 0.0
    masked_count = 0
    for window, hidden in zip(data, mask):
        for values, positions in zip(window, hidden):
            observed = np.flatnonzero(~positions)
            estimate = (np.interp(np.arange(len(values)), observed, values[observed])
                        if len(observed) else np.zeros_like(values))
            squared += float(np.square(estimate[positions]-values[positions]).sum())
            masked_count += int(positions.sum())
    return squared/masked_count


def compare(data_path: Path, results_path: Path) -> dict:
    results = json.loads(results_path.read_text())
    digest = hashlib.sha256(data_path.read_bytes()).hexdigest()
    with np.load(data_path) as data:
        if "array_sha256" in results:
            hashes = {name: hashlib.sha256(data[name].tobytes()).hexdigest()
                      for name in results["array_sha256"]}
            if hashes != results["array_sha256"]:
                raise ValueError("Prepared arrays changed since model training")
        elif digest != results["data_sha256"]:
            raise ValueError("Prepared NPZ bytes changed since model training")
    output = {"data_sha256": digest, "seed": results["seed"], "splits": {}}
    with np.load(data_path) as data:
        for split, key, offset in (("validation", "validation", 1),
                                   ("test", "heldout_station_test", 2)):
            if key not in results:
                continue
            values = data[split]
            mask = eval_mask(values.shape, results["seed"]+offset).numpy()
            linear = linear_interpolation_mse(values, mask)
            output["splits"][split] = {
                "model_masked_mse": results[key]["masked_mse"],
                "zero_fill_mse": results[key]["zero_fill_mse"],
                "linear_interpolation_mse": linear,
                "model_vs_linear_mse_ratio": results[key]["masked_mse"]/linear,
                "masked_points": int(mask.sum())}
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("data/processed/solarmagix_8h.npz"))
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = compare(args.data, args.results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(report, indent=2))
