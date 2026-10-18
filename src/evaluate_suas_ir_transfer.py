"""One-shot binary transfer check from RaptorMaps IR to sUAS IR crops.

Model selection and binary threshold use RaptorMaps validation only. The
separate sUAS archive is opened only after both are frozen. It is one
orthomosaic, not a multi-site production validation.
"""

import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import zipfile

import numpy as np
from PIL import Image
from sklearn.metrics import (average_precision_score, confusion_matrix, f1_score,
                             precision_score, recall_score, roc_auc_score)
import torch

from train_infrared import InfraredPV


EXPECTED_SUAS_MD5 = "9f8a4dc88a5e1474cdd30697079286bf"


def md5_file(path):
    digest = hashlib.md5()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_suas(archive):
    if md5_file(archive) != EXPECTED_SUAS_MD5:
        raise ValueError("sUAS archive checksum mismatch")
    images, labels, class_ids, counts = [], [], [], Counter()
    with zipfile.ZipFile(archive) as source:
        for name in sorted(source.namelist()):
            if not name.lower().endswith(".jpg"):
                continue
            pieces = Path(name).parts
            if len(pieces) != 3 or pieces[1] not in {str(i) for i in range(1, 9)}:
                raise ValueError(f"Unexpected image path: {name}")
            with Image.open(io.BytesIO(source.read(name))) as image:
                resized = image.convert("L").resize((24, 40), Image.Resampling.BILINEAR)
                images.append(np.asarray(resized, dtype=np.uint8))
            label = int(pieces[1])
            labels.append(int(label != 8))
            class_ids.append(label)
            counts[label] += 1
    if len(images) != 3675 or set(counts) != set(range(1, 9)):
        raise ValueError("sUAS image inventory differs from published record")
    return (np.stack(images), np.asarray(labels, dtype=np.int64),
            np.asarray(class_ids, dtype=np.int64), counts)


@torch.no_grad()
def anomaly_scores(images, members, batch_size, device):
    probabilities = []
    for checkpoint in members:
        model = InfraredPV(len(checkpoint["classes"])).to(device)
        model.load_state_dict(checkpoint["state_dict"])
        model.eval()
        normal_index = checkpoint["classes"].index("No-Anomaly")
        mean, std = float(checkpoint["mean"]), float(checkpoint["std"])
        result = []
        for start in range(0, len(images), batch_size):
            values = (images[start:start + batch_size].astype(np.float32) / 255.0 - mean) / std
            batch = torch.from_numpy(values[:, None]).to(device)
            result.append((1.0 - model(batch).float().softmax(-1)[:, normal_index]).cpu().numpy())
        probabilities.append(np.concatenate(result))
    return np.mean(probabilities, axis=0)


def choose_binary_threshold(labels, scores):
    candidates = np.unique(np.r_[0.0, scores, 1.0])
    return float(max(candidates, key=lambda threshold:
                     (f1_score(labels, scores >= threshold, average="macro", zero_division=0),
                      threshold)))


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    selection = json.loads((args.training_output / "selection.json").read_text())
    tags = selection["members"]
    members = [torch.load(args.training_output / f"{tag}.pt", map_location="cpu", weights_only=True)
               for tag in tags]
    classes = members[0]["classes"]
    if any(member["classes"] != classes for member in members):
        raise ValueError("Ensemble class maps disagree")
    if "No-Anomaly" not in classes:
        raise ValueError("RaptorMaps normal class missing")
    with np.load(args.raptor_prepared, allow_pickle=False) as source:
        valid = source["split"] == "validation"
        validation_images = source["images"][valid]
        validation_labels = (source["classes"][source["labels"][valid]] != "No-Anomaly").astype(np.int64)
    device = torch.device(args.device if args.device != "auto" else
                          "cuda" if torch.cuda.is_available() else
                          "mps" if torch.backends.mps.is_available() else "cpu")
    validation_scores = anomaly_scores(validation_images, members, args.batch_size, device)
    threshold = choose_binary_threshold(validation_labels, validation_scores)
    images, labels, class_ids, counts = load_suas(args.suas_archive)
    scores = anomaly_scores(images, members, args.batch_size, device)
    predicted = scores >= threshold
    report = {"protocol": "RaptorMaps-selected model and validation-selected binary threshold, one-shot sUAS transfer",
              "selected_members": tags, "threshold_from_raptor_validation": threshold,
              "raptor_validation_binary_ap": float(average_precision_score(validation_labels, validation_scores)),
              "raptor_validation_binary_macro_f1": float(f1_score(validation_labels,
                                                                     validation_scores >= threshold,
                                                                     average="macro")),
              "suas_source": "https://zenodo.org/records/20860870",
              "suas_archive_md5": EXPECTED_SUAS_MD5,
              "suas_class_counts": {str(key): value for key, value in sorted(counts.items())},
              "suas_by_class": {str(label): {
                  "samples": int((class_ids == label).sum()),
                  "mean_anomaly_score": float(scores[class_ids == label].mean()),
                  "predicted_anomalous": int(predicted[class_ids == label].sum())}
                  for label in sorted(counts)},
              "suas_binary_ap": float(average_precision_score(labels, scores)),
              "suas_binary_roc_auc": float(roc_auc_score(labels, scores)),
              "suas_binary_macro_f1_at_raptor_threshold": float(f1_score(labels, predicted, average="macro")),
              "suas_fault_recall_at_raptor_threshold": float(recall_score(labels, predicted)),
              "suas_fault_precision_at_raptor_threshold": float(precision_score(labels, predicted,
                                                                                 zero_division=0)),
              "suas_confusion_matrix": confusion_matrix(labels, predicted, labels=[0, 1]).tolist(),
              "limitations": ["RaptorMaps normal-versus-all-other labels differ from sUAS hardware-fault-versus-normal labels.",
                              "The sUAS images are crops from one orthomosaic and not independent sites.",
                              "Resizing 36x60-ish sUAS crops to 24x40 changes image scale; images are not radiometric.",
                              "No sUAS image or label is used to select the model or threshold."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--training-output", type=Path, required=True)
    parser.add_argument("--raptor-prepared", type=Path, required=True)
    parser.add_argument("--suas-archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("auto", "cpu", "mps", "cuda"), default="auto")
    parser.add_argument("--batch-size", type=int, default=256)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("batch-size must be positive")
    run(args)
