import sys
import tempfile
import unittest
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from events import screen_events
from event_details import chart, build_details, review_queue, safe_id
from event_report import render_report


class DetailTests(unittest.TestCase):
    def fixture(self):
        return pd.DataFrame([dict(time=t,inverter=f'inv_{i}',peer_group='one',
            actual_kw=2 if i==0 else 10,expected_kw=10,threshold_kw=3)
            for t in pd.date_range('2023-07-01 11:00',periods=8,freq='5min') for i in range(7)])

    def test_chart_breaks_at_missing_points_and_elapsed_gaps(self):
        d=pd.DataFrame({'time':pd.to_datetime(['2023-01-01 12:00','2023-01-01 12:05',
            '2023-01-01 12:15','2023-01-01 12:20']), 'power':[2.,np.nan,3.,4.]})
        s=chart(d,[('power','power','#000')],d.time.min(),d.time.max(),'test','kW')
        self.assertEqual(s.count('<polyline'),2)
        self.assertIn('cx="580.00"',s)  # 15 minutes into a 20-minute window
        self.assertNotIn('nan',s)

    def test_build_consistent_pages_links_and_reject_bad_boundaries(self):
        points,events=screen_events(self.fixture())
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            events['note']='<script>bad</script>'
            n,q=build_details(events,points,root)
            self.assertEqual((n,q),(1,1))
            key=events.event_id.iloc[0]
            page=(root/'details'/f'{key}.html').read_text()
            self.assertIn('&lt;script&gt;bad',page)
            self.assertIn('逐点证据',page)
            self.assertNotIn('<script>bad',page)
            render_report(events,{},root/'index.html',details=True)
            self.assertIn(f'details/{key}.html',(root/'index.html').read_text())
            events.loc[0,'start_local']='2023-07-01 10:59:00'
            with self.assertRaises(ValueError):
                build_details(events,points,root)
        with self.assertRaises(ValueError):
            safe_id('../../secret')

    def test_queue_device_coverage_reproducibility_and_review_exclusion(self):
        rows=[]
        for i in range(2):
            for j in range(4):
                rows.append(dict(event_id=f'{i}{j}',evidence='candidate',inverter=f'inv_{i}',
                    reference_shortfall_kwh=100-j if i==0 else 10-j,review_status='unreviewed'))
        e=pd.DataFrame(rows)
        q=review_queue(e,2)
        self.assertEqual(set(q.inverter),{'inv_0','inv_1'})
        self.assertEqual(q.event_id.tolist(),review_queue(e.sample(frac=1,random_state=4),2).event_id.tolist())
        e['review_status']='confirmed_fault'
        self.assertTrue(review_queue(e).empty)


if __name__=='__main__':
    unittest.main()
