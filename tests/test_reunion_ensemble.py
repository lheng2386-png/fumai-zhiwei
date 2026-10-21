import sys
import unittest
import pandas as pd
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from reunion_ensemble import aggregate_alert_events


def timed(rows, minutes=None):
    if minutes is None: minutes=range(0,len(rows)*5,5)
    return [{**row,"end_time_utc":(pd.Timestamp("2026-10-21T10:00:00Z")+pd.Timedelta(minutes=int(m))).isoformat()}
            for row,m in zip(rows,minutes)]


class ReunionEnsembleTests(unittest.TestCase):
    def test_event_requires_consecutive_ready_windows(self):
        rows = [
            {"status": "ready", "fault_probability": 0.8},
            {"status": "ready", "fault_probability": 0.9},
            {"status": "abstain_recent_missing", "fault_probability": None},
            {"status": "ready", "fault_probability": 0.95},
            {"status": "ready", "fault_probability": 0.96},
        ]
        result = aggregate_alert_events(timed(rows), threshold=0.5, min_consecutive=2)
        self.assertEqual([row["event_alarm"] for row in result],
                         [False, True, False, False, True])
        self.assertEqual([row["event_id"] for row in result], [None, 1, None, None, 2])

    def test_non_ready_probability_cannot_start_event(self):
        rows = [{"status": "abstain_history_missing", "fault_probability": 0.99},
                {"status": "ready", "fault_probability": 0.99}]
        result = aggregate_alert_events(timed(rows), threshold=0.5, min_consecutive=1)
        self.assertEqual(result[0]["event_id"], None)
        self.assertEqual(result[1]["event_id"], 1)

    def test_future_samples_cannot_change_prior_output(self):
        rows=timed([{"status":"ready","fault_probability":.9} for _ in range(5)])
        original=deepcopy(rows)
        whole=aggregate_alert_events(rows,.5,3)
        for stop in range(1,len(rows)+1):
            self.assertEqual(aggregate_alert_events(rows[:stop],.5,3),whole[:stop])
        self.assertEqual(rows,original)
        self.assertEqual([r['event_alarm'] for r in whole],[False,False,True,True,True])

    def test_gap_and_midnight_long_gap_reset_streak(self):
        rows=timed([{"status":"ready","fault_probability":.9} for _ in range(5)], [0,5,20,25,1440])
        result=aggregate_alert_events(rows,.5,2)
        self.assertEqual([r['event_id'] for r in result],[None,1,None,2,None])

    def test_invalid_time_or_probability_is_not_healthy(self):
        valid=timed([{"status":"ready","fault_probability":.9}])[0]
        for change in ({'end_time_utc':'2026-10-21 10:00'}, {'fault_probability':float('nan')},
                       {'fault_probability':None},{'fault_probability':1.1}):
            with self.assertRaises(ValueError):aggregate_alert_events([{**valid,**change}],.5)
        with self.assertRaises(ValueError):aggregate_alert_events([valid,valid],.5)
        with self.assertRaises(ValueError):aggregate_alert_events([{'status':'ready','fault_probability':.9}],.5)

    def test_confirmation_delay_and_short_event_are_counted(self):
        import numpy as np
        from audit_reunion_confirmation import evaluate
        # One single-window fault must not be reported detected by future samples.
        y=np.array([0,1,0,1,1,0]);scores=np.array([.1,.9,.1,.9,.9,.1])
        times=np.arange(6,dtype=np.int64)*300_000_000_000
        raw=evaluate(y,scores,times,.5,1)
        confirmed=evaluate(y,scores,times,.5,2)
        self.assertEqual(raw['observed_events']['missed_observed_events'],0)
        self.assertEqual(confirmed['observed_events']['missed_observed_events'],1)
        self.assertEqual(confirmed['observed_events']['first_observed_fault_window_to_alarm_minutes'],[5.])
        self.assertEqual(confirmed['fault_windows'],3)
        self.assertEqual(confirmed['fault_window_recall'],1/3)
        with self.assertRaises(ValueError):evaluate(y,scores[:-1],times,.5,2)


if __name__ == "__main__":
    unittest.main()
