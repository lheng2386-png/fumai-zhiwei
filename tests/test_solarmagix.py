import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from prepare_solarmagix import FEATURES, fit_scaler, make_windows, prepare


def rows(device, station, start, count, power):
    times = pd.date_range(start, periods=count, freq="h")
    return pd.DataFrame({"device_hash_id": device, "station_hash_id": station,
                         "measured_ts": times, "ac_power": power,
                         "dc_power": power, "device_temperature": 20.0,
                         "temperature_2m": 15.0, "shortwave_radiation": 300.0,
                         "global_tilted_irradiance": 350.0,
                         "perc_state_error": 0.0, "efficiency": 95.0})


class SolarMagixPreparationTests(unittest.TestCase):
    def test_windows_do_not_bridge_nights_or_devices(self):
        data = pd.concat([rows("a", "s", "2025-01-01 08:00", 5, 1.0),
                          rows("a", "s", "2025-01-02 08:00", 5, 2.0),
                          rows("b", "s", "2025-01-01 08:00", 5, 3.0)])
        data = data.sort_values(["device_hash_id", "measured_ts"])
        windows = make_windows(data, fit_scaler(data), window=4, stride=1)
        self.assertEqual(windows.shape, (6, len(FEATURES), 4))
        # Every window has constant power; one crossing a boundary would not.
        self.assertTrue(np.all(np.ptp(windows[:, 0, :], axis=1) == 0))

    def test_holdout_station_cannot_change_training_scaler(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            frames = [rows("train", "train_station", "2025-01-01 08:00", 8, 2.0),
                      rows("val", "val_station", "2025-01-01 08:00", 8, 1000.0),
                      rows("test", "test_station", "2025-01-01 08:00", 8, 2000.0)]
            data = pd.concat(frames).drop(columns="station_hash_id")
            data.to_csv(root / "hourly_pv_weather_inverter.csv", index=False)
            pd.DataFrame({"device_hash_id": ["train", "val", "test"],
                          "station_hash_id": ["train_station", "val_station", "test_station"]}).to_csv(
                              root / "devices.csv", index=False)
            arrays, manifest = prepare(root, 4, 2, "val_station", "test_station")
            self.assertEqual(set(manifest["splits"]["train"]["stations"]), {"train_station"})
            self.assertEqual(manifest["scaler"]["fit_rows"], 8)
            self.assertAlmostEqual(manifest["scaler"]["clip_upper"][0], np.log1p(2.0))
            self.assertEqual({name: len(a) for name, a in arrays.items()},
                             {"train": 3, "validation": 3, "test": 3})


if __name__ == "__main__":
    unittest.main()
