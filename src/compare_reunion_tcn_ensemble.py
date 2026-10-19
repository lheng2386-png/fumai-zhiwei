"""Compare frozen TCN/Transformer blends on the already-used validation set."""

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import average_precision_score

from tcn_model import TCN_PV
from train_arch_compare import TransformerPV
from train_reunion import predict


def run(prepared, transformer_checkpoint, tcn_dir, output, weights):
    if output.exists():
        raise FileExistsError(output)
    with np.load(prepared / "validation.npz") as data:
        x, y = data["x"], data["y"]
    transformer_state = torch.load(transformer_checkpoint, map_location="cpu", weights_only=True)
    transformer = TransformerPV(channels=14, width=64, layers=3, classes=2)
    transformer.load_state_dict(transformer_state["state_dict"])
    transformer_scores = predict(transformer, x, torch.device("cpu"), 256)
    tcn_scores = []
    for seed in (2031, 2032, 2033):
        state = torch.load(tcn_dir / f"tcn_seed{seed}.pt", map_location="cpu", weights_only=True)
        model = TCN_PV(channels=state["channels"], width=64,
                       dilations=tuple(state["dilations"]), classes=2)
        model.load_state_dict(state["state_dict"])
        tcn_scores.append(predict(model, x, torch.device("cpu"), 256))
    tcn_mean = np.mean(tcn_scores, axis=0)
    report = {"protocol": "frozen candidates; previously used validation only",
              "limitations": ["Validation was used during model and checkpoint selection.",
                              "This is not a new blind or external evaluation."],
              "transformer_ap": float(average_precision_score(y, transformer_scores)),
              "tcn_seed_ap": {str(seed): float(average_precision_score(y, score))
                              for seed, score in zip((2031, 2032, 2033), tcn_scores)},
              "tcn_mean_ap": float(average_precision_score(y, tcn_mean)),
              "blends": {str(weight): float(average_precision_score(
                  y, weight * tcn_mean + (1 - weight) * transformer_scores))
                         for weight in weights}}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, default=Path("data/processed/la_reunion_v1"))
    parser.add_argument("--transformer-checkpoint", type=Path,
                        default=Path("reports/reunion_v1_mps/transformer_seed2032.pt"))
    parser.add_argument("--tcn-dir", type=Path, default=Path("reports/reunion_tcn_mps_v1"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--weights", type=float, nargs="+", default=[0.1, 0.25, 0.5, 0.75])
    args = parser.parse_args()
    run(args.prepared, args.transformer_checkpoint, args.tcn_dir, args.output, args.weights)
