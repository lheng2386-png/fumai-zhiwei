"""Compare full-image and tiled detectors on original PV-HSD validation images.

Predictions from four overlapping crops are mapped back to full-image pixels,
merged with image-level NMS, and scored with COCOeval. This script reads the
grouped validation split only; the one-shot test protocol remains separate.
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

from prepare_pv_hsd_tiles import IMAGE_SIZE, TILE_SIZE, TILES


def iou_with(box, others):
    intersection_left = np.maximum(box[0], others[:, 0])
    intersection_top = np.maximum(box[1], others[:, 1])
    intersection_right = np.minimum(box[2], others[:, 2])
    intersection_bottom = np.minimum(box[3], others[:, 3])
    inter = np.maximum(intersection_right - intersection_left, 0) * np.maximum(
        intersection_bottom - intersection_top, 0)
    area_a = max(box[2] - box[0], 0) * max(box[3] - box[1], 0)
    area_b = np.maximum(others[:, 2] - others[:, 0], 0) * np.maximum(
        others[:, 3] - others[:, 1], 0)
    return inter / np.maximum(area_a + area_b - inter, 1e-9)


def nms(boxes, scores, threshold=0.5, max_detections=300):
    """Return indices of score-sorted boxes after full-image NMS."""
    boxes = np.asarray(boxes, dtype=np.float64).reshape(-1, 4)
    scores = np.asarray(scores, dtype=np.float64)
    if len(boxes) != len(scores) or not np.isfinite(boxes).all() or not np.isfinite(scores).all():
        raise ValueError("Invalid boxes or scores")
    order = np.argsort(-scores, kind="stable")
    keep = []
    while len(order) and len(keep) < max_detections:
        current = int(order[0])
        keep.append(current)
        order = order[1:]
        if len(order):
            order = order[iou_with(boxes[current], boxes[order]) <= threshold]
    return keep


def restore_box(box, origin):
    left, top = origin
    x1, y1, x2, y2 = map(float, box)
    return [max(0.0, min(x1 + left, IMAGE_SIZE[0])),
            max(0.0, min(y1 + top, IMAGE_SIZE[1])),
            max(0.0, min(x2 + left, IMAGE_SIZE[0])),
            max(0.0, min(y2 + top, IMAGE_SIZE[1]))]


def touches_internal_edge(box, origin, margin=2.0):
    """Discard likely clipped detections at an interior crop boundary."""
    left, top = origin
    x1, y1, x2, y2 = map(float, box)
    return ((left > 0 and x1 <= margin) or
            (top > 0 and y1 <= margin) or
            (left + TILE_SIZE[0] < IMAGE_SIZE[0] and x2 >= TILE_SIZE[0] - margin) or
            (top + TILE_SIZE[1] < IMAGE_SIZE[1] and y2 >= TILE_SIZE[1] - margin))


def inference_views(original, mode):
    """Return RGB PIL views and their original-image origins."""
    if mode == "full":
        return [original.copy()], ((0, 0),)
    if mode == "tiled":
        return ([original.crop((left, top, left + TILE_SIZE[0], top + TILE_SIZE[1]))
                 for left, top in TILES], TILES)
    raise ValueError(f"Unknown inference mode: {mode}")


def run(source, checkpoint, output, device, imgsz, batch_size, conf, nms_iou, mode="tiled"):
    if output.exists():
        raise FileExistsError(output)
    if not 0 < conf < 1 or not 0 < nms_iou < 1:
        raise ValueError("conf and NMS IoU must be in (0, 1)")
    if mode not in ("full", "tiled"):
        raise ValueError(f"Unknown inference mode: {mode}")
    from pycocotools.coco import COCO
    from pycocotools.cocoeval import COCOeval
    from ultralytics import YOLO

    annotation_path = source / "coco" / "instances_val.json"
    coco = COCO(str(annotation_path))
    model = YOLO(str(checkpoint))
    predictions = []
    empty_images = {image["id"] for image in coco.dataset["images"]}
    empty_images -= {item["image_id"] for item in coco.dataset["annotations"]}
    empty_image_detections = 0
    started = time.monotonic()
    for image in coco.dataset["images"]:
        path = source / "val" / "images" / image["file_name"]
        with Image.open(path) as original:
            if original.size != IMAGE_SIZE:
                raise ValueError(f"Unexpected image size: {path}")
            crops, origins = inference_views(original, mode)
        results = model.predict(crops, device=device, imgsz=imgsz, conf=conf,
                                iou=0.7, max_det=300, batch=batch_size, verbose=False)
        boxes, scores = [], []
        if len(results) != len(origins):
            raise ValueError("Detector returned a different number of predictions than views")
        for origin, result in zip(origins, results):
            for box, score in zip(result.boxes.xyxy.cpu().numpy(),
                                  result.boxes.conf.cpu().numpy()):
                if mode == "tiled" and touches_internal_edge(box, origin):
                    continue
                full_box = restore_box(box, origin)
                if full_box[2] <= full_box[0] or full_box[3] <= full_box[1]:
                    continue
                boxes.append(full_box)
                scores.append(float(score))
        for index in nms(boxes, scores, threshold=nms_iou):
            x1, y1, x2, y2 = boxes[index]
            predictions.append({"image_id": image["id"], "category_id": 0,
                                "bbox": [x1, y1, x2 - x1, y2 - y1],
                                "score": scores[index]})
            empty_image_detections += image["id"] in empty_images
    if not predictions:
        raise ValueError("Detector produced no validation predictions")
    inference_seconds = time.monotonic() - started
    detected = coco.loadRes(predictions)
    evaluation = COCOeval(coco, detected, "bbox")
    evaluation.evaluate()
    evaluation.accumulate()
    evaluation.summarize()
    report = {"protocol": "grouped validation original-image COCOeval with common final NMS",
              "source": str(source), "checkpoint": str(checkpoint),
              "mode": mode, "imgsz": imgsz,
              "inference_view_size": list(IMAGE_SIZE if mode == "full" else TILE_SIZE),
              "view_origins": [list(v) for v in (((0, 0),) if mode == "full" else TILES)],
              "confidence_threshold": conf, "final_nms_iou": nms_iou,
              "validation_images": len(coco.dataset["images"]),
              "validation_boxes": len(coco.dataset["annotations"]),
              "map50_95": float(evaluation.stats[0]), "map50": float(evaluation.stats[1]),
              "map_small": float(evaluation.stats[3]), "recall_100": float(evaluation.stats[8]),
              "empty_label_images": len(empty_images),
              "detections_on_empty_label_images": empty_image_detections,
              "seconds_per_original_image": inference_seconds / len(coco.dataset["images"]),
              "limitations": ["Validation is from one collection with proxy capture groups.",
                              "Empty labels are not certified healthy images.",
                              "No test images are read by this script."]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("data/processed/pv_hsd_grouped_v2"))
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--conf", type=float, default=0.001)
    parser.add_argument("--nms-iou", type=float, default=0.5)
    parser.add_argument("--mode", choices=("full", "tiled"), default="tiled")
    args = parser.parse_args()
    run(args.source, args.checkpoint, args.output, args.device, args.imgsz,
        args.batch_size, args.conf, args.nms_iou, args.mode)
