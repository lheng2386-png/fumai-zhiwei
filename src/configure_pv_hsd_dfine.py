"""Render a reproducible single-GPU D-FINE-S config for grouped PV-HSD data."""

import argparse
import json
from pathlib import Path

import yaml


def load_upstream(path):
    config = yaml.safe_load(path.read_text())
    result = {}
    for include in config.pop("__include__", []):
        child = Path(include)
        if not child.is_absolute():
            child = path.parent / child
        merge(result, load_upstream(child.resolve()))
    merge(result, config)
    return result


def merge(target, source):
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge(target[key], value)
        else:
            target[key] = value


def main(args):
    manifest = json.loads((args.data / "manifest.json").read_text())
    for split in ("train", "val"):
        annotation = args.data / "coco" / f"instances_{split}.json"
        dataset = json.loads(annotation.read_text())
        if (len(dataset["images"]) != manifest["splits"][split]["images"]
                or len(dataset["annotations"]) != manifest["splits"][split]["boxes"]):
            raise ValueError(f"COCO annotation counts differ for {split}")
    if args.output.exists():
        raise FileExistsError(args.output)
    upstream = (args.dfine / "configs/dfine/custom/dfine_hgnetv2_s_custom.yml").resolve()
    config = load_upstream(upstream)
    config["output_dir"] = str(args.training_output.resolve())
    config["num_classes"] = 1
    config["remap_mscoco_category"] = False
    config["epochs"] = 80
    config["checkpoint_freq"] = 5
    config["train_dataloader"]["total_batch_size"] = 8
    config["val_dataloader"]["total_batch_size"] = 8
    config["train_dataloader"]["dataset"]["img_folder"] = str((args.data / "train/images").resolve())
    config["train_dataloader"]["dataset"]["ann_file"] = str((args.data / "coco/instances_train.json").resolve())
    config["val_dataloader"]["dataset"]["img_folder"] = str((args.data / "val/images").resolve())
    config["val_dataloader"]["dataset"]["ann_file"] = str((args.data / "coco/instances_val.json").resolve())
    config["train_dataloader"]["dataset"]["transforms"]["policy"]["epoch"] = 72
    config["train_dataloader"]["collate_fn"]["stop_epoch"] = 72
    config["optimizer"]["lr"] = 0.0001
    config["optimizer"]["params"][0]["lr"] = 0.00005
    config["optimizer"]["params"][1]["lr"] = 0.00005
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(yaml.safe_dump(config, sort_keys=False))
    print(args.output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--dfine", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--training-output", type=Path, required=True)
    main(parser.parse_args())
