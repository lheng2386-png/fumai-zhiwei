"""Fine-tune YOLO on grouped PV-HSD tiles; selection uses tile validation.

Full-image validation must be run separately with evaluate_pv_hsd_tiled.py.
The grouped test partition is never opened by this command.
"""

import argparse
import hashlib
import json
from pathlib import Path
import time


def validate_data(source, tiles):
    source_manifest = source / "manifest.json"
    tile_manifest = json.loads((tiles / "manifest.json").read_text())
    actual_hash = hashlib.sha256(source_manifest.read_bytes()).hexdigest()
    if tile_manifest["source_manifest_sha256"] != actual_hash:
        raise ValueError("Tile data were made from a different grouped source")
    if tile_manifest["test_status"] != "untouched, not created":
        raise ValueError("Unexpected tile test status")
    for split in ("train", "val"):
        counts = tile_manifest["splits"][split]
        if counts["source_boxes_without_full_tile"] != 0:
            raise ValueError(f"Uncovered {split} boxes")
        expected = counts["source_images"] * 4
        if counts["tiles"] != expected:
            raise ValueError(f"Unexpected {split} tile count")
    return tile_manifest


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    manifest = validate_data(args.source, args.tiles)
    import torch
    import ultralytics
    from ultralytics import YOLO

    pretrained = Path(args.pretrained)
    pretrained_sha256 = hashlib.sha256(pretrained.read_bytes()).hexdigest() if pretrained.is_file() else None
    model = YOLO(args.pretrained)
    started = time.monotonic()
    model.train(data=str((args.tiles / "data.yaml").resolve()),
                project=str(args.output.parent.resolve()), name=args.output.name,
                exist_ok=False, epochs=args.epochs, patience=args.patience,
                imgsz=args.imgsz, batch=args.batch_size, device=args.device,
                workers=args.workers, seed=args.seed, deterministic=True,
                cache=False, amp=args.device == "cuda", plots=False,
                pretrained=True, save=True, val=True)
    (args.output / "protocol.json").write_text(json.dumps({
        "protocol": "grouped PV-HSD overlapping-tile training and tile-level validation",
        "source_manifest_sha256": manifest["source_manifest_sha256"],
        "tile_manifest_sha256": hashlib.sha256((args.tiles / "manifest.json").read_bytes()).hexdigest(),
        "pretrained": args.pretrained, "pretrained_sha256": pretrained_sha256,
        "torch_version": torch.__version__, "ultralytics_version": ultralytics.__version__,
        "cuda_version": torch.version.cuda,
        "gpu_name": torch.cuda.get_device_name(0) if args.device == "cuda" else None,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "epochs": args.epochs, "patience": args.patience, "imgsz": args.imgsz,
        "batch_size": args.batch_size, "seed": args.seed,
        "full_image_validation_status": "not evaluated",
        "test_status": "not evaluated"
    }, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("data/processed/pv_hsd_grouped_v2"))
    parser.add_argument("--tiles", type=Path, default=Path("data/processed/pv_hsd_tiles_384_v1"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pretrained", default="yolo26s.pt")
    parser.add_argument("--device", choices=("cuda", "mps"), required=True)
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--patience", type=int, default=20)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--seed", type=int, default=2026)
    run(parser.parse_args())
