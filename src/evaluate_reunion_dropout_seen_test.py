"""Retrospective comparison of dropout candidates on already-seen partitions.

This script is deliberately not used for model selection: La Reunion test and
normal-stress predictions were inspected in earlier work. It gives a practical
signal about whether the development candidate is worth a future blind test.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import torch

from train_reunion import make_model, predict, summarize_scores


def load_model(path):
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    model = make_model(checkpoint["architecture"], checkpoint["channels"])
    model.load_state_dict(checkpoint["state_dict"])
    return model


def run(prepared, run_dir, dropout_dir, output):
    if output.exists():
        raise FileExistsError(output)
    with np.load(prepared / "test.npz") as data:
        test = {key: data[key] for key in ("x", "y", "end_time_ns")}
    with np.load(prepared / "normal_stress.npz") as data:
        stress = {key: data[key] for key in ("x", "y", "end_time_ns")}
    candidates = {}
    for name, checkpoint, threshold in (
            ("clean_seed2032", run_dir / "transformer_seed2032.pt", 0.05),
            ("dropout_seed2032", dropout_dir / "transformer_seed2032.pt", 0.05)):
        model = load_model(checkpoint)
        candidates[name] = {"threshold_from_clean_validation": threshold,
                            "test": summarize_scores(test["y"], predict(model, test["x"], torch.device("cpu"), 256), threshold),
                            "normal_stress": summarize_scores(stress["y"], predict(model, stress["x"], torch.device("cpu"), 256), threshold)}
    result = {"protocol": "retrospective frozen-candidate comparison on previously inspected test/stress partitions",
              "selection_status": "not used for model or threshold selection",
              "limitations": ["The partitions were already inspected by earlier experiments.",
                              "Same rooftop plant; this is not a new blind or external evaluation.",
                              "Threshold 0.05 is retained from the original clean validation selection for a comparable diagnostic."],
              "candidates": candidates}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({name: {part: {key: value for key, value in metrics.items()
                                    if key in ("average_precision", "fault_recall", "fault_precision", "false_positive_rate")}
                           for part, metrics in item.items() if part != "threshold_from_clean_validation"}
                      for name, item in candidates.items()}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, default=Path("data/processed/la_reunion_v1"))
    parser.add_argument("--run", type=Path, default=Path("reports/reunion_v1_mps"))
    parser.add_argument("--dropout-run", type=Path, default=Path("reports/reunion_channel_dropout_mps_v1/channel_dropout"))
    parser.add_argument("--output", type=Path, default=Path("reports/reunion_dropout_seen_test_v1.json"))
    args = parser.parse_args()
    run(args.prepared, args.run, args.dropout_run, args.output)
