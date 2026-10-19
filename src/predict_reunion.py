"""Quality-gated inference for the frozen La Reunion research checkpoint.

Input is one-minute, timezone-aware, raw feature data from paired inverters.
Absent measurements remain NaN until the gate has seen their provenance.
This command is for shadow evaluation, not automatic production alarms.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from prepare_reunion import FEATURES
from reunion_quality_gate import safe_scores
from train_reunion import make_model, predict


WINDOW = 30
STRIDE = 5


def make_window_inputs(frame, mean, scale):
    required = {"time", *FEATURES}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    if not frame["time"].astype(str).str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$").all():
        raise ValueError("time must include a UTC offset or Z")
    times = pd.to_datetime(frame["time"], utc=True, errors="raise")
    if times.isna().any() or not times.is_monotonic_increasing or times.duplicated().any():
        raise ValueError("time must be unique, complete and chronological")
    if not (times.dt.second.eq(0) & times.dt.microsecond.eq(0)).all():
        raise ValueError("time must be aligned to whole-minute samples")
    raw = frame.loc[:, FEATURES].apply(pd.to_numeric, errors="coerce").to_numpy(np.float32)
    observed = np.isfinite(raw)
    x, masks, outputs = [], [], []
    for end in range(WINDOW, len(frame) + 1, STRIDE):
        start = end - WINDOW
        segment = raw[start:end]
        mask = observed[start:end]
        elapsed_ns = times.iloc[end - 1].value - times.iloc[start].value
        if elapsed_ns != (WINDOW - 1) * 60_000_000_000:
            status = "abstain_time_gap"
        elif mask[:, 6].all() and float(np.min(segment[:, 6])) < 100.0:
            status = "abstain_low_control_power"
        else:
            status = None
        filled = np.where(mask, segment, mean[None, :])
        normalized = ((filled - mean[None, :]) / scale[None, :]).T.astype(np.float32)
        x.append(normalized)
        masks.append(mask.T)
        outputs.append({"end_time_utc": times.iloc[end - 1].isoformat(),
                        "status": status, "fault_probability": None,
                        "observed_fraction": float(mask.mean())})
    if not x:
        raise ValueError("At least 30 minute rows are required")
    return np.stack(x), np.stack(masks), outputs


def run(input_path, prepared, run_dir, output_path, batch_size=256):
    selection = json.loads((run_dir / "selection.json").read_text())
    if selection["selected"] != "transformer_seed2032":
        raise ValueError("Expected the frozen La Reunion Transformer checkpoint")
    with np.load(prepared / "normalization.npz") as normalization:
        mean = normalization["mean"].astype(np.float32)
        scale = normalization["scale"].astype(np.float32)
        stored_features = normalization["features"].tolist()
    if stored_features != list(FEATURES) or mean.shape != (14,) or scale.shape != (14,):
        raise ValueError("Preparation features or normalization shape differ from model contract")
    checkpoint = torch.load(run_dir / "transformer_seed2032.pt", map_location="cpu",
                            weights_only=True)
    if checkpoint["architecture"] != "transformer" or checkpoint["channels"] != 14:
        raise ValueError("Checkpoint architecture differs from model contract")
    model = make_model("transformer", 14)
    model.load_state_dict(checkpoint["state_dict"])
    frame = pd.read_csv(input_path)
    x, masks, rows = make_window_inputs(frame, mean, scale)
    eligible = np.asarray([row["status"] is None for row in rows])

    def score_fn(batch):
        return predict(model, batch, torch.device("cpu"), batch_size)

    if eligible.any():
        predictions = safe_scores(x[eligible], masks[eligible], score_fn)
        for index, item in zip(np.flatnonzero(eligible), predictions):
            rows[int(index)].update(item)
    threshold = float(selection["validation_metrics"]["threshold"])
    for row in rows:
        row["research_threshold"] = threshold
        row["research_alarm"] = (row["fault_probability"] >= threshold
                                 if row["fault_probability"] is not None else None)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise FileExistsError(output_path)
    pd.DataFrame(rows).to_csv(output_path, index=False)
    return {"windows": len(rows), "ready": sum(row["status"] == "ready" for row in rows),
            "abstained": sum(row["status"] != "ready" for row in rows),
            "output": str(output_path)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--prepared", type=Path, default=Path("data/processed/la_reunion_v1"))
    parser.add_argument("--run", type=Path, default=Path("reports/reunion_v1_mps"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=256)
    args = parser.parse_args()
    print(json.dumps(run(args.input, args.prepared, args.run, args.output,
                         args.batch_size), indent=2))
