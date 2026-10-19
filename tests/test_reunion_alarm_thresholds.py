import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from evaluate_reunion_alarm_thresholds import (
    choose_zero_validation_false_positive_threshold, describe,
)


class ReunionAlarmThresholdTests(unittest.TestCase):
    def test_validation_threshold_rejects_maximum_healthy_score(self):
        y = np.array([0, 0, 1, 1])
        scores = np.array([.1, .6, .55, .8], dtype=np.float32)
        threshold = choose_zero_validation_false_positive_threshold(y, scores)
        self.assertGreater(threshold, .6)
        self.assertEqual(int(np.sum(scores[y == 0] >= threshold)), 0)

    def test_alarm_days_exclude_days_with_a_fault(self):
        times = pd.to_datetime(["2022-02-01 08:00:00+00:00",
                                "2022-02-01 08:05:00+00:00",
                                "2022-02-02 08:00:00+00:00"]).astype("int64")
        result = describe([.8, .9, .8], [0, 1, 0], times, .5)
        self.assertEqual(result["pure_healthy_days"], 1)
        self.assertEqual(result["pure_healthy_days_with_alarm"], 1)
        self.assertEqual(result["fault_days_detected"], 1)


if __name__ == "__main__":
    unittest.main()
