import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from prepare_reunion import FEATURES, make_windows, split_for_date
from backtest_reunion import fold_data
from train_arch_compare import TransformerPV


class ReunionProtocolTests(unittest.TestCase):
    def test_date_boundaries_and_gap(self):
        self.assertEqual(split_for_date(pd.Timestamp("2022-03-11").date()), "train")
        self.assertIsNone(split_for_date(pd.Timestamp("2022-03-12").date()))
        self.assertEqual(split_for_date(pd.Timestamp("2022-03-15").date()), "validation")
        self.assertEqual(split_for_date(pd.Timestamp("2022-04-19").date()), "test")

    def test_windows_never_cross_dates_or_partitions(self):
        days = [("2022-03-10", True), ("2022-03-15", True),
                ("2022-04-19", True), ("2022-06-01", False)]
        frames = []
        for day, has_fault in days:
            index = pd.date_range(f"{day} 10:00", periods=90, freq="min", tz="Indian/Reunion")
            block = pd.DataFrame(1.0, index=index, columns=FEATURES)
            block["control_Pg"] = 500.0
            block["fault"] = [0.0] * 45 + ([1.0] * 45 if has_fault else [0.0] * 45)
            frames.append(block)
        windows = make_windows(pd.concat(frames), window=10, stride=5)
        for name in ("train", "validation", "test"):
            self.assertEqual(set(windows[name]["y"]), {0, 1})
            dates = pd.to_datetime(windows[name]["end_time_ns"], utc=True).tz_convert(
                "Indian/Reunion").date
            self.assertEqual(len(set(dates)), 1)
        self.assertTrue(np.all(windows["normal_stress"]["y"] == 0))
        dates = [set(w["end_time_ns"].tolist()) for w in windows.values()]
        for i in range(len(dates)):
            for j in range(i + 1, len(dates)):
                self.assertFalse(dates[i] & dates[j])

    def test_rejects_new_fault_in_purge_gap(self):
        index = pd.date_range("2022-03-12 10:00", periods=30, freq="min", tz="Indian/Reunion")
        data = pd.DataFrame(1.0, index=index, columns=FEATURES)
        data["control_Pg"] = 500.0
        data["fault"] = 1.0
        with self.assertRaisesRegex(ValueError, "outside train/validation/test"):
            make_windows(data, window=10, stride=5)

    def test_transformer_attention_query_receives_gradient(self):
        import torch
        model = TransformerPV(channels=14, classes=2)
        result = model(torch.randn(4, 14, 30))
        result.square().mean().backward()
        self.assertEqual(tuple(result.shape), (4, 2))
        self.assertIsNotNone(model.query.grad)
        self.assertTrue(torch.isfinite(model.query.grad).all().item())

    def test_rolling_backtest_refits_scaler_before_later_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            prepared = Path(directory)
            dates = ["2022-02-08", "2022-02-11", "2022-02-14"]
            times = [pd.Timestamp(f"{day} 10:00", tz="Indian/Reunion").value
                     for day in dates for _ in range(2)]
            raw = np.asarray([2, 4, 100, 110, 1000, 1100], dtype=np.float32)[:, None, None]
            np.savez(prepared / "train.npz", x=raw, y=np.asarray([0, 1] * 3),
                     end_time_ns=np.asarray(times, dtype=np.int64))
            np.savez(prepared / "normalization.npz", mean=np.asarray([0], dtype=np.float32),
                     scale=np.asarray([1], dtype=np.float32))
            fit, tune, outer, scaler, days = fold_data(
                prepared, "2022-02-08", "2022-02-11", "2022-02-11",
                "2022-02-14", "2022-02-14")
            self.assertAlmostEqual(float(scaler["mean"][0]), 3.0)
            self.assertAlmostEqual(float(scaler["scale"][0]), 1.0)
            self.assertEqual([len(data["y"]) for data in (fit, tune, outer)], [2, 2, 2])
            self.assertEqual(len(set(days["train_days"] + days["tuning_days"] +
                                     days["evaluation_days"])), 3)
            self.assertGreater(float(outer["x"].min()), 900.0)


if __name__ == "__main__":
    unittest.main()
