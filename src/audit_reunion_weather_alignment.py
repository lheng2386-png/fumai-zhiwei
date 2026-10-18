"""Audit one-minute weather/electrical timing using train windows only."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


MINUTE_NS = 60_000_000_000


def minute_series(prepared):
    with np.load(prepared / "train.npz") as source:
        x, end_time = source["x"], source["end_time_ns"]
    with np.load(prepared / "normalization.npz") as source:
        mean, scale = source["mean"], source["scale"]
    if x.shape[1] != 18:
        raise ValueError("Expected 18 weather-prepared features")
    length = x.shape[-1]
    times = end_time[:, None] - np.arange(length - 1, -1, -1)[None, :] * MINUTE_NS
    positions = np.unique(times.ravel(), return_index=True)[1]
    timestamp = times.ravel()[positions]
    control_power = (x[:, 6, :] * scale[6] + mean[6]).ravel()[positions]
    irradiance = (x[:, 15, :] * scale[15] + mean[15]).ravel()[positions]
    order = np.argsort(timestamp)
    return timestamp[order], control_power[order], irradiance[order]


def audit(timestamp, power, irradiance, max_lag=10):
    lookup = {int(time): i for i, time in enumerate(timestamp)}
    result = []
    for lag in range(-max_lag, max_lag + 1):
        level_power, level_sun, delta_power, delta_sun = [], [], [], []
        for i, time in enumerate(timestamp):
            j = lookup.get(int(time) + lag * MINUTE_NS)
            if j is None or power[i] < 100 or irradiance[j] < 100:
                continue
            level_power.append(power[i])
            level_sun.append(irradiance[j])
            k = lookup.get(int(time) - MINUTE_NS)
            m = lookup.get(int(time) + (lag - 1) * MINUTE_NS)
            if k is not None and m is not None:
                delta_power.append(power[i] - power[k])
                delta_sun.append(irradiance[j] - irradiance[m])
        if len(delta_power) < 100:
            raise ValueError("Too few matched consecutive minutes")
        result.append({"weather_lag_minutes_relative_to_electrical": lag,
                       "matched_daylight_minutes": len(level_power),
                       "consecutive_pairs": len(delta_power),
                       "power_irradiance_correlation": float(np.corrcoef(level_power, level_sun)[0, 1]),
                       "first_difference_correlation": float(np.corrcoef(delta_power, delta_sun)[0, 1])})
    return result


def run(prepared, output, max_lag):
    if output.exists():
        raise FileExistsError(output)
    timestamp, power, irradiance = minute_series(prepared)
    rows = audit(timestamp, power, irradiance, max_lag)
    best = max(rows, key=lambda row: row["first_difference_correlation"])
    report = {"protocol": "Train-window minutes only; deduplicated exact timestamps; control AC power versus GTI; no test/validation opened",
              "source_manifest_sha256": hashlib.sha256((prepared / "manifest.json").read_bytes()).hexdigest(),
              "unique_minutes": len(timestamp),
              "best_lag_by_first_difference_correlation": best["weather_lag_minutes_relative_to_electrical"],
              "limitations": ["Correlation is not a calibrated sensor synchronization test.",
                              "Only windows surviving the model preparation filters are audited."],
              "lags": rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"unique_minutes": len(timestamp), "best_lag_minutes":
                      report["best_lag_by_first_difference_correlation"],
                      "best_first_difference_correlation": best["first_difference_correlation"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-lag", type=int, default=10)
    args = parser.parse_args()
    if args.max_lag < 0:
        parser.error("max-lag must be nonnegative")
    run(args.prepared, args.output, args.max_lag)
