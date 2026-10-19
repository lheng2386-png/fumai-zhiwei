"""Audit the released PV SCADA tables before using their status codes as labels."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


EXPECTED = ("Inverter SCADA.csv", "Meteorological data.csv", "Grid export data.csv")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_lengths(values, target):
    mask = np.asarray(values) == target
    boundaries = np.flatnonzero(np.diff(np.r_[False, mask, False]))
    return (boundaries[1::2] - boundaries[::2]).tolist()


def audit(directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    for name in EXPECTED:
        path = directory / name
        expected = manifest["files"][name]
        if path.stat().st_size != expected["bytes"] or sha256(path) != expected["sha256"]:
            raise ValueError(f"Source file differs from manifest: {name}")
    scada = pd.read_csv(directory / EXPECTED[0])
    meteo = pd.read_csv(directory / EXPECTED[1])
    grid = pd.read_csv(directory / EXPECTED[2])
    scada["timestamp"] = pd.to_datetime(scada["timestamp"], utc=True)
    meteo["reading_time"] = pd.to_datetime(meteo["reading_time"], utc=True)
    grid["export_timestamp"] = pd.to_datetime(grid["export_timestamp"], utc=True)
    scada = scada.sort_values(["inverter_id", "timestamp"])
    grouped = scada.groupby("inverter_id", sort=True)
    status = scada.status_code.value_counts().to_dict()
    episodes = {}
    for code in sorted(status):
        lengths = [length for _, group in grouped
                   for length in run_lengths(group.status_code.to_numpy(), code)]
        episodes[code] = {"episodes": len(lengths),
                          "single_row_episodes": sum(n == 1 for n in lengths),
                          "maximum_rows": max(lengths)}
    actual_dc = scada.dc_voltage_v * scada.dc_current_a
    nonzero = scada.dc_power_kw > 0
    ratio = scada.loc[nonzero, "dc_power_kw"] / actual_dc[nonzero].replace(0, np.nan)
    fault = scada.status_code.eq("FAULT")
    report = {
        "source": manifest["source"], "license": manifest["license"],
        "source_files": manifest["files"],
        "rows": {"inverter_scada": len(scada), "meteorology": len(meteo),
                 "grid_export": len(grid)},
        "inverter_count": int(scada.inverter_id.nunique()),
        "date_range_utc": [str(scada.timestamp.min()), str(scada.timestamp.max())],
        "rows_per_inverter": grouped.size().to_dict(),
        "status_counts": status, "status_episode_lengths": episodes,
        "fault_rows_with_zero_dc_power": int((fault & scada.dc_power_kw.eq(0)).sum()),
        "fault_rows_with_zero_ac_power": int((fault & scada.ac_power_kw.eq(0)).sum()),
        "fault_rows_total": int(fault.sum()),
        "dc_power_max_kw": float(scada.dc_power_kw.max()),
        "ac_power_max_kw": float(scada.ac_power_kw.max()),
        "rows_dc_power_above_170_kw": int(scada.dc_power_kw.gt(170).sum()),
        "dc_power_divided_by_voltage_times_current_quantiles": {
            str(q): float(ratio.quantile(q)) for q in (.01, .5, .99)},
        "nulls_by_scada_column": scada.isna().sum().astype(int).to_dict(),
        "status_rows_between_midnight_and_0459_utc": scada.loc[
            scada.timestamp.dt.hour.between(0, 4), "status_code"].value_counts().to_dict(),
        "unique_scada_timestamps": int(scada.timestamp.nunique()),
        "duplicate_inverter_timestamps": int(scada.duplicated(["inverter_id", "timestamp"]).sum()),
        "qualifying_decision": "Do not use status_code as verified fault ground truth or train a supervised industrial fault detector on this release without source clarification.",
        "limitations": [
            "Status code provenance and maintenance confirmation are not supplied in the released tables.",
            "Single-row status episodes cannot establish event duration or maintenance outcome.",
            "The released files are one plant and approximately three months, not independent-site evaluation.",
            "Power/current/unit relationships require clarification before model feature engineering.",
        ],
    }
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("data/raw/pv_scada_mendeley"))
    parser.add_argument("--output", type=Path,
                        default=Path("data/raw/pv_scada_mendeley/audit.json"))
    args = parser.parse_args()
    result = audit(args.data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in
                      ("rows", "status_counts", "status_episode_lengths",
                       "fault_rows_with_zero_dc_power", "dc_power_max_kw",
                       "dc_power_divided_by_voltage_times_current_quantiles")}, indent=2))


if __name__ == "__main__":
    main()
