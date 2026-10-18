"""Audit downloaded SolarMagix hourly tables before representation learning.

This dataset has operating-state codes, not independently verified field-fault
events. The report deliberately does not calculate a fault-detection score.
"""

import argparse
import json
from pathlib import Path

import pandas as pd


def audit(root: Path) -> dict:
    inverter = pd.read_csv(root / "hourly_pv_weather_inverter.csv")
    station = pd.read_csv(root / "hourly_pv_weather_station.csv")
    devices = pd.read_csv(root / "devices.csv")
    stations = pd.read_csv(root / "stations.csv")
    states = pd.read_csv(root / "inverter_state_canonical.csv")
    times = pd.to_datetime(inverter["measured_ts"], errors="raise")
    station_times = pd.to_datetime(station["measured_ts"], errors="raise")
    if inverter.duplicated(["device_hash_id", "measured_ts"]).any():
        raise ValueError("Duplicate inverter-hour keys")
    if station.duplicated(["station_hash_id", "measured_ts"]).any():
        raise ValueError("Duplicate station-hour keys")
    state_cols = ["perc_state_on", "perc_state_off", "perc_state_error"]
    if not (inverter[state_cols].sum(axis=1).sub(1).abs() < 0.01).all():
        raise ValueError("Operating-state fractions do not sum to one")
    return {
        "source": "https://doi.org/10.17632/4zgsckxpdy.2",
        "license": "CC BY 4.0",
        "inverter_hour_rows": len(inverter),
        "station_hour_rows": len(station),
        "device_metadata_rows": len(devices),
        "unique_inverters": int(inverter.device_hash_id.nunique()),
        "unique_stations": int(station.station_hash_id.nunique()),
        "station_metadata_rows": len(stations),
        "source_counts": {str(k): int(v) for k, v in inverter.source.value_counts().items()},
        "first_inverter_timestamp": times.min().isoformat(),
        "last_inverter_timestamp": times.max().isoformat(),
        "inverter_calendar_days": int(times.dt.date.nunique()),
        "first_station_timestamp": station_times.min().isoformat(),
        "last_station_timestamp": station_times.max().isoformat(),
        "inverter_rows_with_error_fraction": int((inverter.perc_state_error > 0).sum()),
        "inverter_rows_with_negative_ac_power": int((inverter.ac_power < 0).sum()),
        "inverter_rows_with_negative_dc_power": int((inverter.dc_power < 0).sum()),
        "inverter_rows_with_efficiency_over_110": int((inverter.efficiency > 110).sum()),
        "shortwave_radiation_instant_min": float(inverter.shortwave_radiation_instant.min()),
        "shortwave_radiation_instant_max": float(inverter.shortwave_radiation_instant.max()),
        "canonical_states": {str(row.canonical_state_id): row.description
                             for row in states.itertuples(index=False)},
        "modeling_notes": [
            "Operating-state codes do not establish independently verified fault events.",
            "Drop or physically validate shortwave_radiation_instant: its values remain near 950-1032, even when radiation is zero.",
            "Filter gross power/efficiency outliers using training-only thresholds and report exclusions.",
            "Evaluate chronology and held-out-station transfer separately; for the latter, keep every hour of each test station out of training.",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/solarmagix"))
    parser.add_argument("--output", type=Path, default=Path("data/raw/solarmagix/audit.json"))
    args = parser.parse_args()
    report = audit(args.raw_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
