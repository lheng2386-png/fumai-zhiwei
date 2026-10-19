"""Convert prepared PV-HSD YOLO labels to COCO for D-FINE.

The default writes train and validation annotations only. Reading the grouped
test split requires an explicit --include-test after model selection is frozen.
"""

import argparse
import json
from pathlib import Path

from PIL import Image


def convert_split(data, split):
    image_dir = data / split / "images"
    label_dir = data / split / "labels"
    images = []
    annotations = []
    annotation_id = 1
    for image_id, path in enumerate(sorted(image_dir.glob("*.JPG")), start=1):
        with Image.open(path) as image:
            width, height = image.size
        images.append({"id": image_id, "file_name": path.name,
                       "width": width, "height": height})
        label_path = label_dir / f"{path.stem}.txt"
        for line in label_path.read_text().splitlines():
            if not line.strip():
                continue
            label, cx, cy, bw, bh = map(float, line.split())
            if label != 0:
                raise ValueError(f"Unexpected class in {label_path}")
            x = (cx - bw / 2) * width
            y = (cy - bh / 2) * height
            box_width, box_height = bw * width, bh * height
            if (x < -1e-4 or y < -1e-4 or x + box_width > width + 1e-4
                    or y + box_height > height + 1e-4 or box_width <= 0 or box_height <= 0):
                raise ValueError(f"Invalid box in {label_path}")
            annotations.append({"id": annotation_id, "image_id": image_id,
                                "category_id": 0, "bbox": [x, y, box_width, box_height],
                                "area": box_width * box_height, "iscrowd": 0})
            annotation_id += 1
    return {"images": images, "annotations": annotations,
            "categories": [{"id": 0, "name": "hot_spot", "supercategory": "defect"}],
            "info": {"description": "PV-HSD-2025 grouped research split"}, "licenses": []}


def main(args):
    manifest = json.loads((args.data / "manifest.json").read_text())
    output = args.data / "coco"
    output.mkdir(exist_ok=True)
    splits = ("train", "val", "test") if args.include_test else ("train", "val")
    for split in splits:
        path = output / f"instances_{split}.json"
        if path.exists():
            raise FileExistsError(path)
        converted = convert_split(args.data, split)
        expected = manifest["splits"][split]
        if len(converted["images"]) != expected["images"]:
            raise ValueError(f"Image count mismatch for {split}")
        if len(converted["annotations"]) != expected["boxes"]:
            raise ValueError(f"Box count mismatch for {split}")
        path.write_text(json.dumps(converted, separators=(",", ":")) + "\n")
        print(split, len(converted["images"]), len(converted["annotations"]), path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--include-test", action="store_true")
    main(parser.parse_args())
