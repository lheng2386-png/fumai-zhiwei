"""Make a deterministic capture-group split for PV-HSD-2025 detection.

The published train/valid split shares many capture-group prefixes. This
preparation combines the two and splits by original folder plus numeric image
prefix. It does not create an independent-plant test set.
"""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

from PIL import Image

from audit_pv_hsd import group_for, sha256


def assignment(group, seed):
    digest = hashlib.sha256(f"{seed}:{group}".encode()).digest()
    fraction = int.from_bytes(digest[:8], "big") / 2**64
    return "train" if fraction < .70 else "val" if fraction < .85 else "test"


def prepare(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    manifest = {"source": "https://huggingface.co/datasets/MisakaMikoto128/PV-HSD-2025",
                "archive_sha256": sha256(args.archive), "seed": args.seed,
                "split_rule": "SHA-256(seed + original folder + numeric filename prefix); 70/15/15",
                "limitations": ["Group prefix is a proxy for capture sequence, not a verified plant/flight ID.",
                                "Original archive only describes check1 source paths; none of these splits is an independent-site evaluation.",
                                "Empty hotspot labels do not certify a healthy module."],
                "source_images": 0, "excluded_non_thermal_size": [],
                "splits": {}, "groups": {}, "files": []}
    group_sets = defaultdict(set)
    counts = defaultdict(Counter)
    with zipfile.ZipFile(args.archive) as source:
        raw_paths = json.loads(source.read("train/raw_images_files_dict.json"))
        for original_split in ("train", "valid"):
            images = sorted(name for name in source.namelist()
                            if name.startswith(f"{original_split}/images/")
                            and name.lower().endswith(".jpg"))
            for image_path in images:
                manifest["source_images"] += 1
                filename = Path(image_path).name
                with source.open(image_path) as stream, Image.open(stream) as image:
                    size = image.size
                if size != (640, 512):
                    manifest["excluded_non_thermal_size"].append(
                        {"image": image_path, "size": list(size)})
                    continue
                group = group_for(filename, raw_paths)
                split = assignment(group, args.seed)
                label_path = f"{original_split}/labels/{Path(filename).stem}.txt"
                target_image = args.output / split / "images" / filename
                target_label = args.output / split / "labels" / f"{Path(filename).stem}.txt"
                target_image.parent.mkdir(parents=True, exist_ok=True)
                target_label.parent.mkdir(parents=True, exist_ok=True)
                with source.open(image_path) as stream, target_image.open("wb") as target:
                    shutil.copyfileobj(stream, target, length=4 * 1024 * 1024)
                with source.open(label_path) as stream, target_label.open("wb") as target:
                    shutil.copyfileobj(stream, target)
                lines = [line for line in target_label.read_text().splitlines() if line.strip()]
                group_sets[split].add(group)
                counts[split]["images"] += 1
                counts[split]["boxes"] += len(lines)
                counts[split]["empty_label_images"] += int(not lines)
                manifest["files"].append({"image": f"{split}/images/{filename}",
                                          "original_split": original_split, "group": group,
                                          "boxes": len(lines)})
    if (group_sets["train"] & group_sets["val"] or group_sets["train"] & group_sets["test"]
            or group_sets["val"] & group_sets["test"]):
        raise AssertionError("Capture group split leakage")
    for split in ("train", "val", "test"):
        if counts[split]["images"] == 0 or counts[split]["boxes"] == 0:
            raise ValueError(f"Empty detection split: {split}")
        manifest["splits"][split] = dict(counts[split])
        manifest["splits"][split]["groups"] = len(group_sets[split])
        manifest["groups"][split] = sorted(group_sets[split])
    (args.output / "data.yaml").write_text(
        f"path: '{args.output.resolve()}'\ntrain: train/images\nval: val/images\n"
        "test: test/images\nnc: 1\nnames: ['hot_spot']\n")
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"splits": manifest["splits"],
                      "archive_sha256": manifest["archive_sha256"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=2026)
    prepare(parser.parse_args())
