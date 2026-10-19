import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from evaluate_reunion_alarm_persistence import persistent_alarms


class AlarmPersistenceTests(unittest.TestCase):
    def test_three_hits_alert_only_from_third_window(self):
        times = pd.date_range("2022-02-20 10:00", periods=4, freq="5min", tz="Indian/Reunion").asi8
        np.testing.assert_array_equal(persistent_alarms([.8, .9, .95, .1], times, .5, 3),
                                      [False, False, True, False])

    def test_gap_and_local_midnight_reset_streak(self):
        times = pd.DatetimeIndex(["2022-02-20 23:55", "2022-02-21 00:00",
                                  "2022-02-21 00:05", "2022-02-21 00:15",
                                  "2022-02-21 00:20", "2022-02-21 00:25"],
                                 tz="Indian/Reunion").asi8
        np.testing.assert_array_equal(persistent_alarms([.9] * 6, times, .5, 3),
                                      [False, False, False, False, False, True])

    def test_rejects_unsorted_times(self):
        with self.assertRaises(ValueError):
            persistent_alarms([.9, .9], [600, 300], .5, 2)


if __name__ == "__main__":
    unittest.main()
