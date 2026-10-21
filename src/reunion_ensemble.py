"""Quality-gated ensemble and event aggregation for Reunion inference.

The ensemble scores only complete windows. Any missing or non-finite window is
returned as an abstention before model inference. Event alerts require a
configurable number of consecutive eligible windows, so a sensor gap cannot
silently become a healthy prediction or bridge two alarm events.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from reunion_quality_gate import safe_scores
from train_reunion import make_model, predict
from predict_reunion import make_window_inputs


def load_models(checkpoints, device):
    models = []
    for checkpoint_path in checkpoints:
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
        if checkpoint.get("architecture") != "transformer" or checkpoint.get("channels") != 14:
            raise ValueError(f"Unsupported checkpoint contract: {checkpoint_path}")
        model = make_model("transformer", 14).to(device)
        model.load_state_dict(checkpoint["state_dict"])
        model.eval()
        models.append(model)
    if not models:
        raise ValueError("At least one checkpoint is required")
    return models


def ensemble_scores(models, x, device, batch_size=256, weights=None):
    if weights is None:
        weights = np.ones(len(models), dtype=np.float64)
    weights = np.asarray(weights, dtype=np.float64)
    if weights.shape != (len(models),) or not np.isfinite(weights).all() or (weights < 0).any():
        raise ValueError("weights must be finite, nonnegative, and match checkpoints")
    if weights.sum() <= 0:
        raise ValueError("at least one ensemble weight must be positive")
    scores = np.stack([predict(model, x, device, batch_size) for model in models])
    return np.average(scores, axis=0, weights=weights)


def aggregate_alert_events(rows, threshold, min_consecutive=2):
    """Causal confirmation at 5-minute endpoints; never backfill earlier alarms.

    A gap, abstention or low score resets confirmation. Timezone-aware, strictly
    chronological end_time_utc values are mandatory, so separated samples cannot
    silently qualify as consecutive observations. Inputs are not mutated.
    """
    if (not np.isfinite(threshold) or not 0 <= threshold <= 1
            or not isinstance(min_consecutive, int) or isinstance(min_consecutive, bool)
            or min_consecutive < 1):
        raise ValueError("Invalid event confirmation controls")
    result = [dict(row) for row in rows]
    streak, event_number, previous_time = 0, 0, None
    for row in result:
        if "end_time_utc" not in row:
            raise ValueError("Event confirmation requires end_time_utc")
        timestamp = pd.Timestamp(row["end_time_utc"])
        if pd.isna(timestamp) or timestamp.tzinfo is None:
            raise ValueError("Event time must include a timezone")
        current_time = timestamp.value
        if previous_time is not None:
            if current_time <= previous_time:
                raise ValueError("Event endpoints must be strictly chronological")
            if current_time - previous_time != 300_000_000_000:
                streak = 0
        previous_time = current_time
        probability = row.get("fault_probability")
        ready = row.get("status") == "ready"
        if ready and (probability is None or not np.isfinite(probability) or not 0 <= probability <= 1):
            raise ValueError("Ready rows require a finite fault probability in [0,1]")
        alarm = bool(ready and probability >= threshold)
        streak = streak + 1 if alarm else 0
        row["candidate_alarm"] = alarm
        row["confirmation_streak"] = streak
        row["event_alarm"] = streak >= min_consecutive
        row["event_id"] = None
        if row["event_alarm"]:
            if streak == min_consecutive:
                event_number += 1
            row["event_id"] = event_number
    return result


def run(input_path, prepared, checkpoints, output_path, threshold, min_consecutive=2,
        batch_size=256, weights=None):
    with np.load(prepared / "normalization.npz") as normalization:
        mean = normalization["mean"].astype(np.float32)
        scale = normalization["scale"].astype(np.float32)
    models = load_models(checkpoints, torch.device("cpu"))
    frame = pd.read_csv(input_path)
    x, masks, rows = make_window_inputs(frame, mean, scale)
    eligible = np.asarray([row["status"] is None for row in rows])

    def score_fn(batch):
        return ensemble_scores(models, batch, torch.device("cpu"), batch_size, weights)

    if eligible.any():
        predictions = safe_scores(x[eligible], masks[eligible], score_fn)
        for index, item in zip(np.flatnonzero(eligible), predictions):
            rows[int(index)].update(item)
    for row in rows:
        if row["status"] is None:
            row["status"] = "ready"
        row["research_threshold"] = threshold
    rows = aggregate_alert_events(rows, threshold, min_consecutive)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise FileExistsError(output_path)
    pd.DataFrame(rows).to_csv(output_path, index=False)
    return {"windows": len(rows), "ready": sum(r["status"] == "ready" for r in rows),
            "abstained": sum(r["status"] != "ready" for r in rows),
            "event_count": len({r["event_id"] for r in rows if r["event_id"] is not None}),
            "checkpoints": [str(p) for p in checkpoints], "output": str(output_path)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--prepared", type=Path, default=Path("data/processed/la_reunion_v1"))
    parser.add_argument("--checkpoint", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threshold", type=float, required=True)
    parser.add_argument("--min-consecutive", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=256)
    args = parser.parse_args()
    print(json.dumps(run(args.input, args.prepared, args.checkpoint, args.output,
                         args.threshold, args.min_consecutive, args.batch_size), indent=2))
