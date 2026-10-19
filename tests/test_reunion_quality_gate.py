import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from reunion_quality_gate import assess_windows, safe_scores


class ReunionQualityGateTests(unittest.TestCase):
    def test_missing_provenance_is_rejected(self):
        x = np.zeros((1, 14, 30), dtype=np.float32)
        with self.assertRaisesRegex(ValueError, "observed_mask"):
            assess_windows(x, None)

    def test_missing_recent_sensor_never_reaches_model(self):
        x = np.zeros((2, 14, 30), dtype=np.float32)
        observed = np.ones_like(x, dtype=bool)
        observed[0, 6, -3:] = False
        calls = []

        def score_fn(batch):
            calls.append(batch.shape)
            return np.array([.8])

        result = safe_scores(x, observed, score_fn)
        self.assertEqual(result[0], {"status": "abstain_recent_missing",
                                     "fault_probability": None})
        self.assertEqual(result[1], {"status": "ready", "fault_probability": .8})
        self.assertEqual(calls, [(1, 14, 30)])

    def test_nonfinite_and_earlier_missing_abstain(self):
        x = np.zeros((2, 14, 30), dtype=np.float32)
        observed = np.ones_like(x, dtype=bool)
        x[0, 0, 0] = np.nan
        observed[1, 0, 0] = False
        self.assertEqual(assess_windows(x, observed),
                         ["abstain_nonfinite", "abstain_history_missing"])


if __name__ == "__main__":
    unittest.main()
