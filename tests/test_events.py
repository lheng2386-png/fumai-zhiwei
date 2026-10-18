import sys
import tempfile
import unittest
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from events import screen_events
from review import record, latest
from event_report import render_report


def cohort(times=None):
    times = times if times is not None else pd.date_range('2023-07-01 12:00', periods=3, freq='5min')
    return pd.DataFrame([dict(time=t, inverter=f'inv_{i}', peer_group='same',
        actual_kw=10 if i else 2, expected_kw=10, threshold_kw=3)
        for t in times for i in range(7)])


class ScreeningTests(unittest.TestCase):
    def test_individual_and_shared_drops(self):
        points, events = screen_events(cohort())
        self.assertEqual(len(events), 1)
        self.assertEqual(events.iloc[0].evidence, 'device_specific_candidate')
        self.assertEqual(events.iloc[0].min_peer_count, 6)
        self.assertAlmostEqual(events.iloc[0].reference_shortfall_kwh, 8*10/60)
        common = cohort().assign(actual_kw=2)
        _, shared = screen_events(common)
        self.assertEqual(len(shared), 7)
        self.assertTrue(shared.evidence.eq('shared_or_model_candidate').all())

    def test_gaps_invalid_and_missing_peers(self):
        d = cohort(pd.to_datetime(['2023-07-01 12:00','2023-07-01 12:05','2023-07-01 12:15']))
        self.assertTrue(screen_events(d)[1].empty)
        d = cohort()
        d.loc[(d.inverter=='inv_0') & (d.time==d.time.unique()[1]), 'actual_kw'] = np.nan
        self.assertTrue(screen_events(d)[1].empty)
        d = cohort()
        d.loc[d.inverter!='inv_0', 'expected_kw'] = 0
        points, events = screen_events(d)
        self.assertEqual(events.iloc[0].evidence, 'insufficient_peers')
        self.assertEqual(events.iloc[0].min_peer_count, 0)

    def test_duplicate_rejection_and_stable_ids(self):
        d = cohort()
        with self.assertRaises(ValueError):
            screen_events(pd.concat([d,d.iloc[[0]]]))
        _, a = screen_events(d)
        _, b = screen_events(d.sample(frac=1,random_state=9))
        self.assertEqual(a.event_id.tolist(), b.event_id.tolist())

    def test_review_history_and_escaped_html(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = screen_events(cohort())[1]
            path = root/'events.csv'
            events.to_csv(path,index=False)
            key = events.iloc[0].event_id
            with self.assertRaises(ValueError):
                record(root/'r.sqlite',path,'unknown','data_issue','bad sensor')
            record(root/'r.sqlite',path,key,'needs_investigation','check')
            record(root/'r.sqlite',path,key,'data_issue','<script>alert(1)</script>')
            view = latest(root/'r.sqlite')
            self.assertEqual(view.iloc[0].status,'data_issue')
            import sqlite3
            with sqlite3.connect(root/'r.sqlite') as db:
                self.assertEqual(db.execute('select count(*) from reviews').fetchone()[0],2)
            events['note'] = view.iloc[0].note
            render_report(events,{},root/'report.html')
            self.assertIn('&lt;script&gt;', (root/'report.html').read_text())
            self.assertNotIn('<script>alert(1)', (root/'report.html').read_text())


if __name__ == '__main__':
    unittest.main()
