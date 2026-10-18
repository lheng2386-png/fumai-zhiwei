"""Build leakage-controlled SolarMagix windows for masked reconstruction.

The hourly table contains daylight-only runs. Windows must not bridge the
overnight gaps or cross devices. This is self-supervised pretraining data,
not a verified fault-detection benchmark.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


FEATURES = ("ac_power", "dc_power", "device_temperature", "temperature_2m",
            "shortwave_radiation", "global_tilted_irradiance")
LOG_FEATURES = ("ac_power", "dc_power", "shortwave_radiation",
                "global_tilted_irradiance")
VALIDATION_STATION = "7534daaee746a48bc5b99132810f3e69"
# The e2afd... station was inspected in the initial pipeline smoke run. Reserve
# another entire station for the subsequent experiment's test measurement.
TEST_STATION = "b59685487a1fc5712e03c264f70cb656"


def load_clean(root: Path) -> tuple[pd.DataFrame, dict]:
    data = pd.read_csv(root / "hourly_pv_weather_inverter.csv")
    devices = pd.read_csv(root / "devices.csv",
                          usecols=["device_hash_id", "station_hash_id"])
    if devices.device_hash_id.duplicated().any():
        raise ValueError("Device metadata contains duplicate IDs")
    data = data.merge(devices, on="device_hash_id", how="left", validate="many_to_one")
    if data.station_hash_id.isna().any():
        raise ValueError("Some inverter rows have no station metadata")
    data["measured_ts"] = pd.to_datetime(data.measured_ts, errors="raise")
    if data.duplicated(["device_hash_id", "measured_ts"]).any():
        raise ValueError("Duplicate device-hour keys")
    finite = np.isfinite(data[list(FEATURES)].to_numpy()).all(axis=1)
    clean = (finite & data.perc_state_error.eq(0) &
             data.efficiency.between(0, 110) &
             data.ac_power.ge(0) & data.dc_power.ge(0) &
             data.shortwave_radiation.ge(0) &
             data.global_tilted_irradiance.ge(0))
    excluded = {"total": len(data), "kept": int(clean.sum()),
                "nonfinite_features": int((~finite).sum()),
                "error_state": int(data.perc_state_error.gt(0).sum()),
                "efficiency_outside_0_110": int((~data.efficiency.between(0, 110)).sum()),
                "negative_power": int((data.ac_power.lt(0) | data.dc_power.lt(0)).sum()),
                "negative_radiation": int((data.shortwave_radiation.lt(0) |
                                           data.global_tilted_irradiance.lt(0)).sum())}
    return data.loc[clean].sort_values(["device_hash_id", "measured_ts"]), excluded


def fit_scaler(train: pd.DataFrame) -> dict:
    values = train[list(FEATURES)].to_numpy(dtype=np.float64).copy()
    for feature in LOG_FEATURES:
        values[:, FEATURES.index(feature)] = np.log1p(values[:, FEATURES.index(feature)])
    lower, upper = np.quantile(values, [0.001, 0.999], axis=0)
    values = np.clip(values, lower, upper)
    median = np.median(values, axis=0)
    q1, q3 = np.quantile(values, [0.25, 0.75], axis=0)
    scale = np.maximum(q3-q1, 1e-6)
    return {"features": list(FEATURES), "log1p_features": list(LOG_FEATURES),
            "clip_lower": lower.tolist(), "clip_upper": upper.tolist(),
            "median": median.tolist(), "scale_iqr": scale.tolist(),
            "fit_rows": len(train)}


def transform(data: pd.DataFrame, scaler: dict) -> np.ndarray:
    values = data[list(FEATURES)].to_numpy(dtype=np.float64).copy()
    for feature in LOG_FEATURES:
        values[:, FEATURES.index(feature)] = np.log1p(values[:, FEATURES.index(feature)])
    values = np.clip(values, scaler["clip_lower"], scaler["clip_upper"])
    return ((values-scaler["median"])/scaler["scale_iqr"]).astype(np.float32)


def make_windows(data: pd.DataFrame, scaler: dict, window: int, stride: int) -> np.ndarray:
    batches = []
    for _, group in data.groupby("device_hash_id", sort=False):
        times = group.measured_ts.to_numpy(dtype="datetime64[h]")
        values = transform(group, scaler)
        run_start = np.r_[0, np.flatnonzero(np.diff(times) != np.timedelta64(1, "h"))+1]
        run_end = np.r_[run_start[1:], len(group)]
        for start, end in zip(run_start, run_end):
            for index in range(start, end-window+1, stride):
                batches.append(values[index:index+window].T)
    if not batches:
        return np.empty((0, len(FEATURES), window), dtype=np.float32)
    return np.stack(batches)


def prepare(root: Path, window: int, stride: int,
            validation_station: str, test_station: str) -> tuple[dict, dict]:
    if window < 2 or stride < 1 or validation_station == test_station:
        raise ValueError("Invalid window, stride, or station assignment")
    data, excluded = load_clean(root)
    stations = set(data.station_hash_id)
    if not {validation_station, test_station}.issubset(stations):
        raise ValueError("Requested holdout station absent from clean data")
    splits = {"train": data[~data.station_hash_id.isin([validation_station, test_station])],
              "validation": data[data.station_hash_id.eq(validation_station)],
              "test": data[data.station_hash_id.eq(test_station)]}
    scaler = fit_scaler(splits["train"])
    arrays = {name: make_windows(frame, scaler, window, stride)
              for name, frame in splits.items()}
    if any(len(values) == 0 for values in arrays.values()):
        raise ValueError("At least one station split has no contiguous windows")
    manifest = {"source": "https://doi.org/10.17632/4zgsckxpdy.2",
                "purpose": "masked reconstruction pretraining; no fault-truth claim",
                "window_hours": window, "stride_hours": stride,
                "excluded_rows": excluded, "scaler": scaler,
                "splits": {name: {"stations": sorted(frame.station_hash_id.unique().tolist()),
                                  "devices": int(frame.device_hash_id.nunique()),
                                  "rows": len(frame), "windows": len(arrays[name]),
                                  "first_timestamp": frame.measured_ts.min().isoformat(),
                                  "last_timestamp": frame.measured_ts.max().isoformat()}
                           for name, frame in splits.items()},
                "notes": ["Holdout stations have no rows in the scaler fit or training windows.",
                          "Windows contain consecutive observed hours from one device only.",
                          "No 24-hour windows exist in this daylight-only hourly table."]}
    return arrays, manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/solarmagix"))
    parser.add_argument("--output", type=Path, default=Path("data/processed/solarmagix_8h.npz"))
    parser.add_argument("--window", type=int, default=8)
    parser.add_argument("--stride", type=int, default=4)
    parser.add_argument("--validation-station", default=VALIDATION_STATION)
    parser.add_argument("--test-station", default=TEST_STATION)
    args = parser.parse_args()
    windows, manifest = prepare(args.raw_dir, args.window, args.stride,
                                args.validation_station, args.test_station)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.output, **windows)
    args.output.with_suffix(".json").write_text(json.dumps(manifest, indent=2)+"\n")
    print(json.dumps({k: v["windows"] for k, v in manifest["splits"].items()}))
