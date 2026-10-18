"""Build leakage-aware binary fault windows from the La Reunion inverter data.

The fault events end in May 2022, although the newest release extends into
April 2023. A naive 70/15/15 chronological split therefore leaves no faults
for validation or test. This protocol uses whole, disjoint date ranges around
the known events and reserves later normal data for a false-alarm stress set.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


SPLIT_DATES = {
    "train": ("2021-10-01", "2022-03-11"),
    "validation": ("2022-03-15", "2022-04-03"),
    "test": ("2022-04-19", "2022-05-19"),
    "normal_stress": ("2022-06-01", "2023-04-13"),
}
RAW_COLUMNS = ("Pg", "Ia", "Ig", "Va", "Vg", "Fg")
FEATURES = tuple(f"{side}_{col}" for side in ("faulted", "control") for col in RAW_COLUMNS) + (
    "power_ratio", "dc_current_ratio")
WEATHER_FEATURES = ("weather_DTI", "weather_GTI", "weather_TA", "weather_TPV")


def split_for_date(date):
    day = date.isoformat()
    for name, (first, last) in SPLIT_DATES.items():
        if first <= day <= last:
            return name
    return None


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_minutes(raw_dir):
    fault_path = raw_dir / "dt2_electrical_production_inverter_1_with_faults.csv"
    control_path = raw_dir / "dt3_electrical_production_inverter_2.csv"
    dtype = {name: "float32" for name in ("Fault", *RAW_COLUMNS)}
    faulted = pd.read_csv(fault_path, usecols=["time", "Fault", *RAW_COLUMNS], dtype=dtype)
    control = pd.read_csv(control_path, usecols=["time", *RAW_COLUMNS], dtype=dtype)
    if len(faulted) != len(control) or not faulted.time.equals(control.time):
        raise ValueError("The two inverter streams are not row-aligned; join by time before use")
    times = pd.to_datetime(faulted.pop("time"), utc=True, errors="raise").dt.floor("min")
    control.pop("time")
    values = {f"faulted_{column}": faulted[column].to_numpy() for column in RAW_COLUMNS}
    values.update({f"control_{column}": control[column].to_numpy() for column in RAW_COLUMNS})
    frame = pd.DataFrame(values, index=pd.DatetimeIndex(times))
    fault_values = faulted["Fault"].to_numpy()
    labels = pd.Series(np.where(np.isfinite(fault_values), (fault_values > 0).astype(float), np.nan),
                       index=frame.index)
    # Median suppresses isolated sensor spikes; a single faulty 5-second reading
    # makes the transition minute positive rather than silently losing onset.
    minute = frame.resample("1min").median()
    minute["fault"] = labels.resample("1min").max()
    minute.index = minute.index.tz_convert("Indian/Reunion")
    minute["power_ratio"] = minute["faulted_Pg"] / (minute["control_Pg"] + 50.0)
    minute["dc_current_ratio"] = minute["faulted_Ia"] / (minute["control_Ia"] + 0.2)
    return minute, {fault_path.name: file_hash(fault_path), control_path.name: file_hash(control_path)}


def read_weather_minutes(path, chunksize=200_000):
    """Aggregate high-rate weather to exact minutes without loading the 2.2 GB CSV.

    Carry the last minute across chunks so its median uses every source row.
    The source must be chronological; out-of-order timestamps fail loudly.
    """
    columns = ("DTI", "GTI", "TA", "TPV")
    dtype = {column: "float32" for column in columns}
    parts = []
    carry = None
    previous = None
    for chunk in pd.read_csv(path, usecols=["time", *columns], dtype=dtype,
                             chunksize=chunksize):
        times = pd.to_datetime(chunk.pop("time"), utc=True, errors="raise")
        if not times.is_monotonic_increasing or (previous is not None and times.iloc[0] < previous):
            raise ValueError("Weather timestamps are not chronological")
        previous = times.iloc[-1]
        chunk.index = pd.DatetimeIndex(times.dt.floor("min"))
        if carry is not None:
            chunk = pd.concat([carry, chunk])
        last_minute = chunk.index[-1]
        carry = chunk.loc[chunk.index == last_minute]
        complete = chunk.loc[chunk.index != last_minute]
        if not complete.empty:
            parts.append(complete.groupby(level=0).median())
    if carry is None:
        raise ValueError("Empty meteorological CSV")
    parts.append(carry.groupby(level=0).median())
    weather = pd.concat(parts)
    if weather.index.has_duplicates:
        raise ValueError("Weather chunking created duplicate minutes")
    weather.index = weather.index.tz_convert("Indian/Reunion")
    weather.columns = WEATHER_FEATURES
    return weather


def make_windows(minute, window=30, stride=5, min_control_power=100.0,
                 features=FEATURES):
    if window < 2 or stride < 1:
        raise ValueError("window must be >= 2 and stride must be positive")
    records = {name: {"x": [], "y": [], "end_time_ns": []} for name in SPLIT_DATES}
    unseen_fault_days = []
    for day, block in minute.groupby(minute.index.date, sort=True):
        split = split_for_date(day)
        if split is None:
            if block["fault"].gt(0).any():
                unseen_fault_days.append(day.isoformat())
            continue
        values = block.loc[:, features].to_numpy(dtype=np.float32)
        labels = block["fault"].to_numpy(dtype=np.float32)
        control = block["control_Pg"].to_numpy(dtype=np.float32)
        time_ns = block.index.asi8
        if split == "normal_stress" and np.nanmax(labels, initial=0) > 0:
            unseen_fault_days.append(day.isoformat())
        target = records[split]
        for start in range(0, len(block) - window + 1, stride):
            end = start + window
            segment = values[start:end]
            if not np.isfinite(segment).all() or not np.isfinite(labels[start:end]).all():
                continue
            if control[start:end].min() < min_control_power:
                continue
            # resample produces a continuous minute index; NaN rows above remove gaps.
            target["x"].append(segment.T.copy())
            target["y"].append(int(labels[end - 1] > 0))
            target["end_time_ns"].append(int(time_ns[end - 1]))
    if unseen_fault_days:
        raise ValueError(f"Fault labels occur outside train/validation/test: {unseen_fault_days}")
    arrays = {}
    for split, items in records.items():
        if not items["x"]:
            raise ValueError(f"No usable windows for {split}")
        arrays[split] = {"x": np.stack(items["x"]),
                         "y": np.asarray(items["y"], dtype=np.int64),
                         "end_time_ns": np.asarray(items["end_time_ns"], dtype=np.int64)}
    for split in ("train", "validation", "test"):
        if len(np.unique(arrays[split]["y"])) != 2:
            raise ValueError(f"{split} must contain both normal and faulty windows")
    if arrays["normal_stress"]["y"].any():
        raise ValueError("Normal stress contains faulty windows")
    return arrays


def prepare(raw_dir, output, window=30, stride=5, min_control_power=100.0,
            include_weather=False):
    if output.exists():
        raise FileExistsError(output)
    minute, hashes = read_minutes(raw_dir)
    features = FEATURES
    if include_weather:
        weather_path = raw_dir / "dt1_solar_and_meteorological_measurement.csv"
        minute = minute.join(read_weather_minutes(weather_path), how="left")
        features += WEATHER_FEATURES
        hashes[weather_path.name] = file_hash(weather_path)
    arrays = make_windows(minute, window, stride, min_control_power, features)
    train = arrays["train"]["x"]
    mean = train.mean(axis=(0, 2), dtype=np.float64).astype(np.float32)
    scale = train.std(axis=(0, 2), dtype=np.float64).astype(np.float32)
    scale = np.maximum(scale, 1e-4)
    output.mkdir(parents=True)
    np.savez(output / "normalization.npz", mean=mean, scale=scale, features=np.asarray(features))
    stats = {}
    for split, items in arrays.items():
        normalized = ((items["x"] - mean[None, :, None]) / scale[None, :, None]).astype(np.float32)
        np.savez_compressed(output / f"{split}.npz", x=normalized, y=items["y"],
                            end_time_ns=items["end_time_ns"])
        stats[split] = {"windows": int(len(items["y"])),
                        "normal": int((items["y"] == 0).sum()),
                        "fault": int((items["y"] == 1).sum()),
                        "first_end_time": pd.Timestamp(items["end_time_ns"].min(), tz="UTC").isoformat(),
                        "last_end_time": pd.Timestamp(items["end_time_ns"].max(), tz="UTC").isoformat()}
    manifest = {"source": "https://zenodo.org/records/7828879", "raw_sha256": hashes,
                "protocol": "30-minute windows at 1-minute median cadence, 5-minute stride; whole-date, forward chronological train/validation/test; day boundaries never crossed; fixed gaps between partitions; last-minute binary label",
                "split_dates_local": SPLIT_DATES, "features": features,
                "window": window, "stride": stride, "min_control_power_w": min_control_power,
                "normalization": "train windows only", "splits": stats,
                "weather": ("same-minute median of DTI, GTI, TA, TPV; exact minute join; missing minutes reject windows"
                            if include_weather else "not included"),
                "limitations": ["One 4 kW rooftop plant, not cross-plant validation.",
                                "All faults are shading experiments; some subtypes have only one episode.",
                                "The 2022-05-10 and later 3.1 shading subtype is absent from training.",
                                "Weather is unavailable at some dates/minutes; windows with missing values are excluded."
                                if include_weather else
                                "Meteorological stream is not yet included; the second inverter is used as a peer reference."]}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--window", type=int, default=30)
    parser.add_argument("--stride", type=int, default=5)
    parser.add_argument("--min-control-power", type=float, default=100.0)
    parser.add_argument("--include-weather", action="store_true")
    args = parser.parse_args()
    print(json.dumps(prepare(args.raw_dir, args.output, args.window, args.stride,
                             args.min_control_power, args.include_weather), indent=2), flush=True)
