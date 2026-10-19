"""Train a YOLO detection baseline on capture-grouped PV-HSD-2025 data.

Training uses train+validation only. `test` is a separate, explicit one-shot
entrypoint after architecture and weights are frozen.
"""

import argparse
import json
from pathlib import Path


def validate_grouping(data):
    manifest = json.loads((data / "manifest.json").read_text())
    groups = [set(manifest["groups"][split]) for split in ("train", "val", "test")]
    if any(groups[i] & groups[j] for i in range(3) for j in range(i + 1, 3)):
        raise ValueError("Capture group leakage in prepared data")
    if sum(manifest["splits"][split]["images"] for split in ("train", "val", "test")) != 4025:
        raise ValueError("Unexpected prepared dataset size")
    return manifest


def run(args):
    from ultralytics import YOLO

    manifest = validate_grouping(args.data)
    if args.mode == "train":
        if args.output.exists():
            raise FileExistsError(args.output)
        model = YOLO(args.pretrained)
        model.train(data=str((args.data / "data.yaml").resolve()),
                    project=str(args.output.parent.resolve()), name=args.output.name,
                    exist_ok=False, epochs=args.epochs, patience=args.patience,
                    imgsz=args.imgsz, batch=args.batch_size, device=args.device,
                    workers=args.workers, seed=args.seed, deterministic=True,
                    cache=False, amp=args.device == "cuda", plots=False,
                    pretrained=True, save=True, val=True)
        (args.output / "protocol.json").write_text(json.dumps({
            "source_archive_sha256": manifest["archive_sha256"],
            "prepared_data": str(args.data.resolve()),
            "model": args.pretrained, "seed": args.seed,
            "epochs": args.epochs, "patience": args.patience,
            "imgsz": args.imgsz, "batch_size": args.batch_size,
            "device": args.device, "test_status": "not evaluated",
            "limits": ["One collection with proxy capture groups, not independent sites.",
                       "Few empty-label images; normal false-alarm behavior remains poorly measured."]
        }, indent=2) + "\n")
    else:
        if args.output.exists():
            raise FileExistsError(args.output)
        checkpoint = args.training_output / "weights" / "best.pt"
        if not checkpoint.exists():
            raise FileNotFoundError(checkpoint)
        model = YOLO(str(checkpoint))
        metrics = model.val(data=str((args.data / "data.yaml").resolve()),
                            split="test", device=args.device, imgsz=args.imgsz,
                            batch=args.batch_size, workers=args.workers,
                            project=str(args.output.parent.resolve()), name=args.output.name,
                            exist_ok=False, plots=False)
        report = {"checkpoint": str(checkpoint),
                  "source_archive_sha256": manifest["archive_sha256"],
                  "split": "grouped test, one collection",
                  "map50": float(metrics.box.map50),
                  "map50_95": float(metrics.box.map),
                  "precision": float(metrics.box.mp),
                  "recall": float(metrics.box.mr)}
        (args.output / "one_shot_test.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("train", "test"))
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--training-output", type=Path)
    parser.add_argument("--pretrained", default="yolo26s.pt")
    parser.add_argument("--device", choices=("mps", "cuda"), required=True)
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--patience", type=int, default=20)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    if args.mode == "test" and args.training_output is None:
        parser.error("test mode requires --training-output")
    run(args)
