import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from opencem import SIGNALS,load_measurements,load_context,associate,report


class OpenCEMTests(unittest.TestCase):
    def test_short_rows_are_rejected_before_padding_and_valid_conflicts_quarantined(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'measurements.csv'
            base=[100,1,*([10]*len(SIGNALS))]
            conflict=base.copy();conflict[0]=200
            changed=conflict.copy();changed[2]=99
            with path.open('w',newline='') as stream:
                writer=csv.writer(stream)
                writer.writerow(['read_ts','inverter',*SIGNALS])
                writer.writerows([base,base,base[:-1],conflict,changed])
            good,rejected,widths=load_measurements(path)
            self.assertEqual(good.read_ts.tolist(),[100])
            self.assertEqual(good.source_record.tolist(),[2])
            self.assertEqual(rejected.reason.value_counts().to_dict(),{
                'conflicting_valid_width_key':2,'row_width_mismatch':1,'exact_duplicate':1})

    def test_no_cross_device_or_future_context_leakage(self):
        measurements=pd.DataFrame({'read_ts':[100,110,120,130,140,150,120],
            'inverter':[1,1,1,1,1,1,2], 'outsumw':[5,5,10,10,20,999,900]})
        # The interval begins at 100 but the text is not available until 120.
        context=pd.DataFrame([dict(context_id=1,recorded=120,start=100,end=150,inverter=1),
                              dict(context_id=2,recorded=180,start=100,end=150,inverter=1),
                              dict(context_id=3,recorded=125,start=125,end=125,inverter=1)])
        rows=associate(measurements,context)
        self.assertEqual(rows.iloc[0].known_samples,3)
        self.assertEqual(rows.iloc[0].known_load_median_w,10)
        self.assertEqual(rows.iloc[0].first_sample_ts,120)
        self.assertEqual(rows.iloc[0].last_sample_ts,140)
        self.assertEqual(rows.iloc[1].known_samples,0)
        self.assertEqual(rows.iloc[2].known_samples,1)
        self.assertEqual(rows.iloc[2].first_sample_ts,130)
        self.assertTrue(pd.isna(rows.iloc[2].descriptive_load_delta_w))

    def test_context_rejection_text_escape_and_commands_are_only_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'context.csv';marker=root/'should_not_exist'
            value=json.dumps({'inverter':1,'source':'manual entry',
                'textual_description':'<script>alert(1)</script>', 'command':f'touch {marker}'})
            with path.open('w',newline='') as stream:
                writer=csv.writer(stream)
                writer.writerow(['id','recorded','start','end','value'])
                writer.writerows([[1,100,100,200,value],[2,100,200,100,value],[3,100,100,200,'[]']])
            context,rejected=load_context(path)
            self.assertEqual(len(context),1)
            self.assertEqual(len(rejected),2)
            self.assertFalse(marker.exists())
            m=pd.DataFrame({'read_ts':[110],'inverter':[1],'outsumw':[10]})
            links=associate(m,context)
            report(links,{},root/'index.html')
            text=(root/'index.html').read_text()
            self.assertIn('&lt;script&gt;alert(1)',text)
            self.assertNotIn('<script>alert(1)',text)
            self.assertNotIn(str(marker),text)


if __name__=='__main__':
    unittest.main()
