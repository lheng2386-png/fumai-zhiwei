import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from opencem_ai import electrical_novelty,cluster_text,FEATURES


class AIBaselineTests(unittest.TestCase):
    def test_heldout_extremes_cannot_change_fitted_preprocessing_or_threshold(self):
        rng=np.random.default_rng(3)
        data=pd.DataFrame({c:rng.normal(20,1,200) for c in FEATURES})
        data['read_ts']=np.arange(200)*15+1765843200
        data['inverter']=1
        altered=data.copy()
        altered.loc[170:,FEATURES]=10000
        with threadpool_limits(limits=2):
            a,first,_=electrical_novelty(data)
            b,second,_=electrical_novelty(altered)
        self.assertLess(a[a.partition=='train'].read_ts.max(),a[a.partition=='calibration'].read_ts.min())
        self.assertLess(a[a.partition=='calibration'].read_ts.max(),a[a.partition=='test'].read_ts.min())
        np.testing.assert_array_equal(first['models'][1]['imputer'].statistics_,second['models'][1]['imputer'].statistics_)
        self.assertEqual(first['models'][1]['threshold'],second['models'][1]['threshold'])
        np.testing.assert_array_equal(a[a.partition!='test'].novelty_score,b[b.partition!='test'].novelty_score)

    def test_text_groups_preserve_ids_and_empty_text_is_unassigned(self):
        data=pd.DataFrame({'context_id':range(7),'description':[
            'compile library cpu task','compile library cpu task','compile library cpu task',
            'fit model gpu training','fit model gpu training','fit model gpu training','']})
        grouped,bundle,summary=cluster_text(data)
        self.assertEqual(grouped.context_id.tolist(),list(range(7)))
        self.assertEqual(grouped.iloc[-1].text_cluster,-1)
        self.assertEqual(sum(g['records'] for g in summary),6)
        self.assertNotEqual(grouped.iloc[0].text_cluster,grouped.iloc[3].text_cluster)


if __name__=='__main__':
    unittest.main()
