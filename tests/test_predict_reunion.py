import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from predict_reunion import make_window_inputs
from prepare_reunion import FEATURES


class ReunionInferenceTests(unittest.TestCase):
    def example(self):
        data = {name: np.full(35, 250, dtype=np.float32) for name in FEATURES}
        data["time"] = pd.date_range("2022-03-01", periods=35, freq="min", tz="UTC").astype(str)
        return pd.DataFrame(data)

    def test_normal_input_yields_two_windows(self):
        x, masks, rows = make_window_inputs(self.example(), np.zeros(14), np.ones(14))
        self.assertEqual(x.shape, (2, 14, 30))
        self.assertTrue(masks.all())
        self.assertEqual([row["status"] for row in rows], [None, None])

    def test_gap_abstains_and_missing_is_preserved(self):
        frame = self.example()
        frame.loc[10, "time"] = "2022-03-01 00:10:30+00:00"
        with self.assertRaisesRegex(ValueError, "whole-minute"):
            make_window_inputs(frame, np.zeros(14), np.ones(14))
        frame = self.example()
        frame.loc[10:, "time"] = pd.date_range("2022-03-01 00:11", periods=25,
                                                 freq="min", tz="UTC").astype(str)
        frame.loc[34, "control_Pg"] = np.nan
        _, masks, rows = make_window_inputs(frame, np.zeros(14), np.ones(14))
        self.assertEqual(rows[0]["status"], "abstain_time_gap")
        self.assertEqual(rows[1]["status"], "abstain_time_gap")
        self.assertFalse(masks[1, 6, -1])

    def test_naive_timestamps_are_rejected(self):
        frame = self.example()
        frame["time"] = pd.date_range("2022-03-01", periods=35, freq="min").astype(str)
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            make_window_inputs(frame, np.zeros(14), np.ones(14))


if __name__ == "__main__":
    unittest.main()
