"""Create overlapping PV-HSD training/validation tiles for tiny hot spots.

This creates no test tiles. The original grouped test stays untouched. A tile
is 384x320 in the 640x512 image; 128-pixel overlaps ensure every original
box (max 127x108 pixels in this release) fits inside at least one tile.
Partly visible boxes are clipped and labelled when at least 2x2 pixels remain.
"""

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image


IMAGE_SIZE = (640, 512)
TILE_SIZE = (384, 320)
TILES = ((0, 0), (256, 0), (0, 192), (256, 192))


def read_boxes(path, width=640, height=512):
    boxes = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 5 or parts[0] != "0":
            raise ValueError(f"Unexpected YOLO label: {path}: {line}")
        cx, cy, w, h = map(float, parts[1:])
        if not (0 <= cx <= 1 and 0 <= cy <= 1 and 0 < w <= 1 and 0 < h <= 1):
            raise ValueError(f"Invalid YOLO box: {path}: {line}")
        box = ((cx - w / 2) * width, (cy - h / 2) * height,
               (cx + w / 2) * width, (cy + h / 2) * height)
        if box[0] < -0.01 or box[1] < -0.01 or box[2] > width + 0.01 or box[3] > height + 0.01:
            raise ValueError(f"Box outside image: {path}: {line}")
        boxes.append(tuple(max(0.0, min(value, limit)) for value, limit in
                           zip(box, (width, height, width, height))))
    return boxes


def tile_box(box, left, top, min_side=2.0):
    """Return a clipped normalized YOLO box, or None for a tiny sliver."""
    width, height = TILE_SIZE
    x1 = max(box[0], left)
    y1 = max(box[1], top)
    x2 = min(box[2], left + width)
    y2 = min(box[3], top + height)
    if x2 - x1 < min_side or y2 - y1 < min_side:
        return None
    return ((x1 + x2 - 2 * left) / (2 * width),
            (y1 + y2 - 2 * top) / (2 * height),
            (x2 - x1) / width, (y2 - y1) / height)


def prepare(source, output, jpeg_quality=95):
    if output.exists():
        raise FileExistsError(output)
    manifest = json.loads((source / "manifest.json").read_text())
    source_groups = manifest["groups"]
    if set(source_groups["train"]) & set(source_groups["val"]):
        raise ValueError("Source train/val capture groups overlap")
    output.mkdir(parents=True)
    report = {"source_manifest_sha256": hashlib.sha256(
                  (source / "manifest.json").read_bytes()).hexdigest(),
              "tile_size": list(TILE_SIZE), "tile_origins": [list(tile) for tile in TILES],
              "source_splits": ["train", "val"], "test_status": "untouched, not created",
              "splits": {},
              "limitations": ["Tile-level validation duplicates some source objects and is not full-image mAP.",
                              "Visible box slivers under 2 pixels are ignored; full-image inference needs stitching/NMS.",
                              "One source collection with proxy capture groups, not an independent site."]}
    for split in ("train", "val"):
        records = sorted((source / split / "images").glob("*.JPG"))
        if not records:
            raise ValueError(f"No source images for {split}")
        counts = {"source_images": 0, "tiles": 0, "source_boxes": 0,
                  "tile_box_instances": 0, "source_boxes_with_full_tile": 0,
                  "source_boxes_without_full_tile": 0, "empty_tiles": 0}
        for image_path in records:
            label_path = source / split / "labels" / f"{image_path.stem}.txt"
            boxes = read_boxes(label_path)
            counts["source_images"] += 1
            counts["source_boxes"] += len(boxes)
            for box in boxes:
                fully_covered = any(left <= box[0] and top <= box[1] and
                                    box[2] <= left + TILE_SIZE[0] and
                                    box[3] <= top + TILE_SIZE[1]
                                    for left, top in TILES)
                counts["source_boxes_with_full_tile" if fully_covered else
                       "source_boxes_without_full_tile"] += 1
            with Image.open(image_path) as image:
                if image.size != IMAGE_SIZE:
                    raise ValueError(f"Unexpected source dimensions: {image_path}")
                for tile_id, (left, top) in enumerate(TILES):
                    stem = f"{image_path.stem}_tile{tile_id}"
                    out_image = output / split / "images" / f"{stem}.jpg"
                    out_label = output / split / "labels" / f"{stem}.txt"
                    out_image.parent.mkdir(parents=True, exist_ok=True)
                    out_label.parent.mkdir(parents=True, exist_ok=True)
                    crop = image.crop((left, top, left + TILE_SIZE[0], top + TILE_SIZE[1]))
                    crop.save(out_image, quality=jpeg_quality)
                    normalized = [value for box in boxes
                                  if (value := tile_box(box, left, top)) is not None]
                    out_label.write_text("".join(
                        "0 " + " ".join(f"{coordinate:.8f}" for coordinate in value) + "\n"
                        for value in normalized))
                    counts["tiles"] += 1
                    counts["tile_box_instances"] += len(normalized)
                    counts["empty_tiles"] += int(not normalized)
        report["splits"][split] = counts
        if counts["source_boxes_without_full_tile"]:
            raise ValueError(f"Some {split} objects have no complete tile")
    (output / "data.yaml").write_text(
        f"path: '{output.resolve()}'\ntrain: train/images\nval: val/images\n"
        "nc: 1\nnames: ['hot_spot']\n")
    (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["splits"], indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("data/processed/pv_hsd_grouped_v2"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jpeg-quality", type=int, default=95)
    args = parser.parse_args()
    prepare(args.source, args.output, args.jpeg_quality)
