import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from reunion_ensemble import aggregate_alert_events


class ReunionEnsembleTests(unittest.TestCase):
    def test_event_requires_consecutive_ready_windows(self):
        rows = [
            {"status": "ready", "fault_probability": 0.8},
            {"status": "ready", "fault_probability": 0.9},
            {"status": "abstain_recent_missing", "fault_probability": None},
            {"status": "ready", "fault_probability": 0.95},
            {"status": "ready", "fault_probability": 0.96},
        ]
        result = aggregate_alert_events(rows, threshold=0.5, min_consecutive=2)
        self.assertEqual([row["event_alarm"] for row in result],
                         [True, True, False, True, True])
        self.assertEqual([row["event_id"] for row in result], [1, 1, None, 2, 2])

    def test_non_ready_probability_cannot_start_event(self):
        rows = [{"status": "abstain_history_missing", "fault_probability": 0.99},
                {"status": "ready", "fault_probability": 0.99}]
        result = aggregate_alert_events(rows, threshold=0.5, min_consecutive=1)
        self.assertEqual(result[0]["event_id"], None)
        self.assertEqual(result[1]["event_id"], 1)


if __name__ == "__main__":
    unittest.main()
