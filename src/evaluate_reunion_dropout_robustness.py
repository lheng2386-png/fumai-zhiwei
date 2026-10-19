"""Compare clean and channel-dropout Transformer checkpoints on stress inputs."""

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import average_precision_score

from evaluate_reunion_robustness import perturb
from train_reunion import choose_threshold, make_model, predict, summarize_scores


def load_model(path):
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    model = make_model(checkpoint["architecture"], checkpoint["channels"])
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    return model


def evaluate(model, x, y, scenario, threshold, seed):
    scores = predict(model, perturb(x, scenario, seed), torch.device("cpu"), 256)
    metrics = summarize_scores(y, scores, threshold)
    return {"average_precision": float(average_precision_score(y, scores)),
            "fault_recall": metrics["fault_recall"],
            "fault_precision": metrics["fault_precision"],
            "false_positive_rate": metrics["false_positive_rate"],
            "confusion_matrix": metrics["confusion_matrix"]}


def run(prepared, clean_checkpoint, dropout_checkpoint, output):
    if output.exists():
        raise FileExistsError(output)
    with np.load(prepared / "validation.npz") as data:
        x, y = data["x"], data["y"]
    models = {"clean": load_model(clean_checkpoint),
              "channel_dropout": load_model(dropout_checkpoint)}
    report = {"protocol": "frozen checkpoints, original validation only; same stressors",
              "checkpoints": {name: str(path) for name, path in
                              (("clean", clean_checkpoint), ("channel_dropout", dropout_checkpoint))},
              "scenarios": {}}
    for name, model in models.items():
        clean_scores = predict(model, x, torch.device("cpu"), 256)
        threshold = choose_threshold(y, clean_scores)
        report["scenarios"][name] = {
            "clean_validation_threshold": threshold,
            "runs": {scenario: evaluate(model, x, y, scenario, threshold, 2031)
                     for scenario in ("clean", "noise_0p1_std",
                                      "last_5min_mean_imputation",
                                      "last_10min_sensor_freeze",
                                      "control_channels_last_5min_missing")}}
    report["limitations"] = ["Same-site validation was used for checkpoint selection.",
                             "Synthetic missingness is not an independent field test.",
                             "This report does not use the old test or normal-stress partitions."]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({model: {scenario: {key: value for key, value in metrics.items()
                                         if key in ("average_precision", "fault_recall", "false_positive_rate")}
                           for scenario, metrics in data["runs"].items()}
                      for model, data in report["scenarios"].items()}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, default=Path("data/processed/la_reunion_v1"))
    parser.add_argument("--clean-checkpoint", type=Path, required=True)
    parser.add_argument("--dropout-checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.prepared, args.clean_checkpoint, args.dropout_checkpoint, args.output)
