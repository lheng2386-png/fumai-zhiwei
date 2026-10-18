"""Audit the published PV-Fault-DS226 raw and derived-label CSVs.

This is a data-quality audit, not a model evaluation. The published labels are
assigned using rules involving electrical inputs, so this source cannot serve
as independent evidence of field-fault detection performance.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


MEASUREMENTS = ("I1", "I2", "V1", "V2", "Irr", "T1", "T2", "P1", "P2")
LABEL_NAMES = {1: "Normal", 2: "Short Circuit", 3: "Open Circuit",
               4: "Degradation", 5: "Shading"}


def audit(root):
    raw = pd.read_csv(root / "df_ia_raw_acquisition.csv", sep=";", encoding="utf-8-sig")
    labeled = pd.read_csv(root / "PV_Diagnostic_Final_Labels.csv", sep=";", encoding="utf-8-sig")
    expected_raw = ["timestamp", *MEASUREMENTS]
    expected_labeled = [*expected_raw, "Label_Num_S1", "Label_Num_S2",
                        "Label_S1", "Label_S2"]
    if raw.columns.tolist() != expected_raw or labeled.columns.tolist() != expected_labeled:
        raise ValueError("Unexpected columns or order in DS226 CSVs")
    if len(raw) != len(labeled) or not raw.equals(labeled[expected_raw]):
        raise ValueError("Raw and labeled measurement rows differ")
    timestamps = pd.to_datetime(raw.timestamp, format="%Y-%m-%d %H:%M:%S.%f",
                                errors="raise")
    if timestamps.duplicated().any() or not timestamps.is_monotonic_increasing:
        raise ValueError("Timestamps must be unique and chronological")
    if raw.isna().any().any() or labeled.isna().any().any():
        raise ValueError("Unexpected missing values")
    result = {"source": "https://doi.org/10.5281/zenodo.19259504",
              "license": "CC BY 4.0", "rows": len(raw),
              "first_timestamp": timestamps.iloc[0].isoformat(),
              "last_timestamp": timestamps.iloc[-1].isoformat(),
              "unique_dates": int(timestamps.dt.date.nunique()),
              "day_counts": {str(day): int(count) for day, count in
                             timestamps.dt.date.value_counts(sort=False).sort_index().items()},
              "measurement_files_identical": True, "modules": {},
              "label_origin": "Published labels use voltage/current/irradiance rules applied to these measurements; physical conditions were induced on an experimental setup, not confirmed natural field failures.",
              "limitations": ["Two 120 W modules at one experimental site over 12 calendar dates.",
                              "Rule-derived labels can make models using the same input variables appear unrealistically strong.",
                              "P1/P2 have sign disagreements with V*I for a substantial subset; inspect sensor polarity before modeling."]}
    for index in (1, 2):
        numeric = labeled[f"Label_Num_S{index}"]
        names = labeled[f"Label_S{index}"]
        if not numeric.isin(LABEL_NAMES).all():
            raise ValueError(f"Unknown numeric labels for S{index}")
        if not names.equals(numeric.map(LABEL_NAMES)):
            raise ValueError(f"Numeric/text label disagreement for S{index}")
        voltage = labeled[f"V{index}"].to_numpy()
        current = labeled[f"I{index}"].to_numpy()
        power = labeled[f"P{index}"].to_numpy()
        expected = voltage * current
        same_sign = np.isclose(power, expected, atol=0.01, rtol=1e-4)
        reversed_sign = np.isclose(power, -expected, atol=0.01, rtol=1e-4)
        result["modules"][f"S{index}"] = {
            "class_counts": {LABEL_NAMES[code]: int((numeric == code).sum())
                             for code in LABEL_NAMES},
            "class_counts_by_day": {str(day): {LABEL_NAMES[code]: int((group == code).sum())
                                          for code in LABEL_NAMES}
                                    for day, group in numeric.groupby(timestamps.dt.date)},
            "power_matches_v_times_i": int(same_sign.sum()),
            "power_disagrees_v_times_i": int((~same_sign).sum()),
            "power_matches_negative_v_times_i": int(reversed_sign.sum()),
            "negative_power_rows": int((power < 0).sum()),
        }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path,
                        default=Path("data/raw/pv_fault_ds226"))
    parser.add_argument("--output", type=Path,
                        default=Path("data/raw/pv_fault_ds226/audit.json"))
    args = parser.parse_args()
    result = audit(args.raw_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"rows": result["rows"], "unique_dates": result["unique_dates"],
                      "modules": {key: {"class_counts": value["class_counts"],
                                         "power_disagrees_v_times_i": value["power_disagrees_v_times_i"]}
                                  for key, value in result["modules"].items()}}, indent=2))
