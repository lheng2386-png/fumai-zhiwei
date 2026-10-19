"""Audit PV-HSD-2025 YOLO boxes and capture-group leakage in its ZIP archive."""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import zipfile


SOURCE = "https://huggingface.co/datasets/MisakaMikoto128/PV-HSD-2025"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def group_for(filename, raw_paths):
    raw = raw_paths[filename].replace("\\", "/")
    folder = "/".join(raw.split("/")[-3:-1])
    prefix = filename.split("_", 1)[0]
    return f"{folder}/{prefix}"


def audit(archive):
    report = {"source": SOURCE, "archive_sha256": sha256(archive),
              "archive_bytes": archive.stat().st_size, "splits": {}}
    with zipfile.ZipFile(archive) as source:
        bad = source.testzip()
        if bad:
            raise ValueError(f"ZIP CRC failure: {bad}")
        raw_paths = json.loads(source.read("train/raw_images_files_dict.json"))
        if raw_paths != json.loads(source.read("valid/raw_images_files_dict.json")):
            raise ValueError("Raw image mappings differ between splits")
        groups = {}
        file_signatures = defaultdict(set)
        for split in ("train", "valid"):
            images = sorted((info for info in source.infolist()
                             if info.filename.startswith(f"{split}/images/")
                             and info.filename.lower().endswith(".jpg")),
                            key=lambda info: info.filename)
            group_ids = []
            box_count = 0
            empty_images = 0
            invalid = []
            pack_counts = Counter()
            for info in images:
                filename = Path(info.filename).name
                if filename not in raw_paths:
                    raise ValueError(f"No raw path for {filename}")
                group = group_for(filename, raw_paths)
                group_ids.append(group)
                pack_counts[group.rsplit("/", 1)[0]] += 1
                file_signatures[(info.CRC, info.file_size)].add(split)
                label = f"{split}/labels/{Path(filename).stem}.txt"
                lines = source.read(label).decode("utf-8-sig").splitlines()
                if not lines:
                    empty_images += 1
                for line in lines:
                    pieces = line.split()
                    try:
                        class_id = int(pieces[0])
                        coordinates = [float(value) for value in pieces[1:]]
                        good = (len(pieces) == 5 and class_id == 0
                                and all(0 <= value <= 1 for value in coordinates)
                                and coordinates[2] > 0 and coordinates[3] > 0
                                and coordinates[0] - coordinates[2] / 2 >= -1e-6
                                and coordinates[0] + coordinates[2] / 2 <= 1 + 1e-6
                                and coordinates[1] - coordinates[3] / 2 >= -1e-6
                                and coordinates[1] + coordinates[3] / 2 <= 1 + 1e-6)
                    except (ValueError, IndexError):
                        good = False
                    if not good:
                        invalid.append({"file": label, "row": line})
                    box_count += 1
            groups[split] = group_ids
            report["splits"][split] = {"images": len(images), "boxes": box_count,
                                        "empty_label_images": empty_images,
                                        "capture_groups": len(set(group_ids)),
                                        "source_pack_counts": dict(sorted(pack_counts.items())),
                                        "invalid_boxes": invalid[:10],
                                        "invalid_box_count": len(invalid)}
        overlap = set(groups["train"]) & set(groups["valid"])
        report["cross_split"] = {
            "shared_capture_groups": len(overlap),
            "train_images_in_shared_groups": sum(item in overlap for item in groups["train"]),
            "valid_images_in_shared_groups": sum(item in overlap for item in groups["valid"]),
            "identical_crc_size_groups": sum(len(value) > 1 for value in file_signatures.values()),
        }
        report["limitations"] = [
            "Capture group is original folder plus numeric filename prefix; verify it against flight metadata.",
            "CRC+size audit finds exact files, not near-duplicate frames.",
            "All mapped source paths are under check1; no independent plant labels are published in this archive.",
            "Only empty YOLO labels proxy normal images; a missing hotspot annotation does not prove healthy hardware.",
        ]
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    report = audit(args.archive)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
