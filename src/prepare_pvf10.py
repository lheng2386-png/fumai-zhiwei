"""Audit PVF-10's class folders and make a capture-date-disjoint research split.

Only one 112x112 rendering per original crop is used. Folder number prefixes
are ignored because published train/test use different numbers for the same
semantic fault. Dates are a proxy grouping, not plant/flight identifiers.
"""

import argparse
from collections import Counter, defaultdict
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

import numpy as np
from PIL import Image


VALIDATION_DATES = {"20230223", "20230407"}
TEST_DATES = {"20231114", "20231121"}
IMAGE_PREFIX = "PVF-10/PVF_10_112x112/"
NAME_PATTERN = re.compile(r"^(DJI_(\d{8})\d{6}_\d+_T)_\d+\.png$")


def classify(path):
    parts = Path(path).parts
    if len(parts) != 5 or parts[0:2] != ("PVF-10", "PVF_10_112x112"):
        raise ValueError(f"Unexpected image path: {path}")
    folder = parts[3]
    match = re.match(r"^\d{2}(.+)$", folder)
    if parts[2] not in ("train", "test") or not match:
        raise ValueError(f"Unexpected source split or class: {path}")
    photo = NAME_PATTERN.match(parts[4])
    if not photo:
        raise ValueError(f"Unexpected DJI filename: {path}")
    date = photo.group(2)
    split = "validation" if date in VALIDATION_DATES else "test" if date in TEST_DATES else "train"
    return parts[2], folder, match.group(1).strip().lower(), photo.group(1), date, split


def prepare(archive, output):
    if output.exists():
        raise FileExistsError(output)
    with zipfile.ZipFile(archive) as source:
        bad = source.testzip()
        if bad:
            raise ValueError(f"ZIP CRC failure: {bad}")
        paths = sorted(path for path in source.namelist()
                       if path.startswith(IMAGE_PREFIX) and path.lower().endswith(".png"))
        if len(paths) != 5579:
            raise ValueError(f"Unexpected PVF-10 crop count: {len(paths)}")
        alternative = {Path(path).stem: Path(path).parts[2]
                       for path in source.namelist()
                       if path.startswith("PVF-10/PVF_10_110x60/") and path.lower().endswith(".png")}
        if len(alternative) != len(paths):
            raise ValueError("Parallel image rendering has different crop identities")
        rows, images, labels, groups, dates, splits = [], [], [], [], [], []
        pixel_groups = defaultdict(set)
        original_groups = defaultdict(set)
        published_class_folders = defaultdict(set)
        for path in paths:
            published, folder, label, group, date, split = classify(path)
            with Image.open(io.BytesIO(source.read(path))) as image:
                if image.size != (112, 112) or image.mode != "RGB":
                    raise ValueError(f"Unexpected image format: {path}")
                pixels = np.asarray(image, dtype=np.uint8).copy()
            digest = hashlib.sha256(pixels.tobytes()).hexdigest()
            rows.append({"path": path, "published_split": published, "published_folder": folder,
                         "semantic_class": label, "source_photo": group, "capture_date": date,
                         "new_split": split, "pixel_sha256": digest})
            images.append(pixels)
            labels.append(label)
            groups.append(group)
            dates.append(date)
            splits.append(split)
            pixel_groups[digest].add(split)
            original_groups[group].add(published)
            published_class_folders[published].add(folder)
    classes = sorted(set(labels))
    if len(classes) != 10:
        raise ValueError(f"Expected ten semantic classes: {classes}")
    if any(len(v) > 1 for v in pixel_groups.values()):
        raise ValueError("Exact decoded-pixel duplicates cross new splits")
    by_split = {split: Counter(label for label, assigned in zip(labels, splits) if assigned == split)
                for split in ("train", "validation", "test")}
    if any(set(counts) != set(classes) for counts in by_split.values()):
        raise ValueError("A new split lacks at least one semantic class")
    by_date = {split: sorted({date for date, assigned in zip(dates, splits) if assigned == split})
               for split in by_split}
    if any(set(by_date[a]) & set(by_date[b]) for a, b in (("train", "validation"),
                                                       ("train", "test"), ("validation", "test"))):
        raise ValueError("Capture-date leakage")
    output.mkdir(parents=True)
    all_images = np.stack(images)
    all_labels = np.array([classes.index(label) for label in labels], dtype=np.int64)
    all_splits = np.array(splits)
    for split in by_split:
        selected = all_splits == split
        np.savez_compressed(output / f"{split}.npz", images=all_images[selected],
                            labels=all_labels[selected], classes=np.array(classes),
                            source_photo=np.array(groups)[selected],
                            capture_date=np.array(dates)[selected])
    report = {"source": "https://github.com/wangbobby1026/PVF-Dataset",
              "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
              "rendering": "PVF_10_112x112 RGB only; other PNG and original TIFF are parallel copies",
              "classes": classes, "images": len(images), "source_photos": len(original_groups),
              "published_train_test_source_photo_overlap": sum(len(v) > 1 for v in original_groups.values()),
              "published_split_disagreement_between_png_renderings": sum(
                  alternative[Path(row["path"]).stem] != row["published_split"] for row in rows),
              "published_folder_names": {key: sorted(value) for key, value in published_class_folders.items()},
              "exact_pixel_duplicate_groups": sum(1 for row in Counter(row["pixel_sha256"] for row in rows).values()
                                                  if row > 1),
              "exact_pixel_duplicates_cross_new_splits": 0,
              "new_split": {split: {"images": sum(by_split[split].values()),
                                    "dates": by_date[split], "class_counts": dict(by_split[split])}
                            for split in by_split},
              "limitations": ["Capture dates are only proxy groups; the archive has no verified plant/flight IDs.",
                              "Date choice used published labels to retain all ten classes in each split.",
                              "This is module-crop classification, not defect-box detection.",
                              "The repository does not state a separate dataset commercial-use license."]}
    (output / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"images": report["images"],
                      "published_train_test_source_photo_overlap": report["published_train_test_source_photo_overlap"],
                      "new_split_counts": {k: v["images"] for k, v in report["new_split"].items()}}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, default=Path("data/raw/pvf10/PVF-10.zip"))
    parser.add_argument("--output", type=Path, default=Path("data/processed/pvf10_date_v1"))
    args = parser.parse_args()
    prepare(args.archive, args.output)
