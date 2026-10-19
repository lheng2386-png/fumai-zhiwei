"""Choose a PVF-10 transfer checkpoint using validation only.

Compares the strongest single seed with the equal-weight three-seed ensemble.
The date-held-out test split is not loaded.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.metrics import classification_report, f1_score

from train_multiscale import write_json


def run(data, runs, baseline, output):
    if output.exists():
        raise FileExistsError(output)
    manifest = json.loads((data / "manifest.json").read_text())
    with np.load(data / "validation.npz", allow_pickle=False) as validation:
        truth, classes = validation["labels"], validation["classes"].tolist()
    candidates, probabilities = {}, []
    for path in runs:
        report = json.loads((path / "results.json").read_text())
        if report["archive_sha256"] != manifest["archive_sha256"] or report["test_status"] != "not read":
            raise ValueError(f"Run protocol mismatch: {path}")
        prediction = np.load(path / "best_validation_probabilities.npy")
        if prediction.shape != (len(truth), len(classes)):
            raise ValueError(f"Validation prediction shape mismatch: {path}")
        score = float(f1_score(truth, prediction.argmax(1), labels=range(len(classes)),
                               average="macro", zero_division=0))
        candidates[path.name] = score
        probabilities.append(prediction)
    ensemble = np.mean(probabilities, axis=0)
    ensemble_score = float(f1_score(truth, ensemble.argmax(1), labels=range(len(classes)),
                                    average="macro", zero_division=0))
    best_name = max(candidates, key=candidates.get)
    selected = "equal_three_seed_ensemble" if ensemble_score > candidates[best_name] else best_name
    chosen = ensemble if selected == "equal_three_seed_ensemble" else probabilities[list(candidates).index(best_name)]
    baseline_report = json.loads((baseline / "results.json").read_text())
    if baseline_report["data_manifest"] != manifest["archive_sha256"]:
        raise ValueError("Baseline data differ")
    report = {"protocol": "date-separated validation-only choice: best single vs all-seed mean",
              "archive_sha256": manifest["archive_sha256"],
              "baseline_scratch_validation_macro_f1": baseline_report["validation_macro_f1"],
              "single_seed_validation_macro_f1": candidates,
              "equal_three_seed_validation_macro_f1": ensemble_score,
              "selected": selected,
              "selected_validation_macro_f1": float(f1_score(
                  truth, chosen.argmax(1), labels=range(len(classes)), average="macro", zero_division=0)),
              "selected_classification_report": classification_report(
                  truth, chosen.argmax(1), labels=range(len(classes)), target_names=classes,
                  output_dict=True, zero_division=0),
              "test_status": "not read",
              "limitations": manifest["limitations"]}
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, report)
    print(json.dumps({key: report[key] for key in ("baseline_scratch_validation_macro_f1",
                                                 "single_seed_validation_macro_f1",
                                                 "equal_three_seed_validation_macro_f1",
                                                 "selected")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("data/processed/pvf10_date_v1"))
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if len(args.runs) != 3:
        parser.error("Expected three seeded transfer runs")
    run(args.data, args.runs, args.baseline, args.output)
