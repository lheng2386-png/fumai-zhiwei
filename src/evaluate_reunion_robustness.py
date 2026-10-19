"""Validation-only stress probes for the frozen La Reunion Transformer.

Synthetic perturbations expose sensitivity; they do not emulate a certified
sensor-fault distribution or provide a new independent test set.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import average_precision_score

from train_reunion import make_model, predict, summarize_scores


def perturb(x, scenario, seed):
    result = np.array(x, dtype=np.float32, copy=True)
    if scenario == "clean":
        return result
    if scenario == "noise_0p1_std":
        rng = np.random.default_rng(seed)
        result += rng.normal(0.0, 0.1, result.shape).astype(np.float32)
    elif scenario == "last_5min_mean_imputation":
        result[:, :, -5:] = 0.0
    elif scenario == "last_10min_sensor_freeze":
        result[:, :, -10:] = result[:, :, -11:-10]
    elif scenario == "control_channels_last_5min_missing":
        # Control inverter's six electrical channels plus both derived ratios.
        result[:, 6:14, -5:] = 0.0
    else:
        raise ValueError(f"Unknown scenario: {scenario}")
    return result


def run(prepared, run_dir, output, batch_size):
    if output.exists():
        raise FileExistsError(output)
    selection = json.loads((run_dir / "selection.json").read_text())
    tag = selection["selected"]
    if tag != "transformer_seed2032":
        raise ValueError("Expected frozen first-round Transformer selection")
    checkpoint = torch.load(run_dir / f"{tag}.pt", map_location="cpu", weights_only=True)
    model = make_model(checkpoint["architecture"], checkpoint["channels"])
    model.load_state_dict(checkpoint["state_dict"])
    with np.load(prepared / "validation.npz") as data:
        x, y = data["x"], data["y"]
    threshold = float(selection["validation_metrics"]["threshold"])
    seeds = (2031, 2032, 2033)
    results = {}
    for scenario in ("clean", "noise_0p1_std", "last_5min_mean_imputation",
                     "last_10min_sensor_freeze", "control_channels_last_5min_missing"):
        repetitions = seeds if scenario == "noise_0p1_std" else (seeds[0],)
        records = []
        for seed in repetitions:
            scores = predict(model, perturb(x, scenario, seed), torch.device("cpu"), batch_size)
            metrics = summarize_scores(y, scores, threshold)
            records.append({"seed": seed, "average_precision": float(average_precision_score(y, scores)),
                            "fault_recall": metrics["fault_recall"],
                            "fault_precision": metrics["fault_precision"],
                            "false_positive_rate": metrics["false_positive_rate"],
                            "confusion_matrix": metrics["confusion_matrix"]})
        results[scenario] = {"runs": records,
                             "mean_average_precision": float(np.mean([r["average_precision"] for r in records]))}
    report = {"protocol": "frozen model, frozen threshold, original validation partition only",
              "model": tag, "threshold": threshold,
              "input_shape": list(x.shape), "scenarios": results,
              "limitations": ["Same-site validation was previously used for model selection.",
                              "Gaussian noise and mean-imputed missingness are synthetic stressors, not field sensor-failure distributions.",
                              "The model has no explicit missingness indicators or fallback policy."]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: {"ap": v["mean_average_precision"],
                          "recall": [r["fault_recall"] for r in v["runs"]],
                          "fpr": [r["false_positive_rate"] for r in v["runs"]]}
                      for k, v in results.items()}, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, default=Path("data/processed/la_reunion_v1"))
    parser.add_argument("--run", type=Path, default=Path("reports/reunion_v1_mps"))
    parser.add_argument("--output", type=Path, default=Path("reports/reunion_robustness_v1.json"))
    parser.add_argument("--batch-size", type=int, default=256)
    args = parser.parse_args()
    run(args.prepared, args.run, args.output, args.batch_size)


if __name__ == "__main__":
    main()
