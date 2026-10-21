from pathlib import Path
import sys
import unittest
import tempfile
import json
import argparse
from unittest.mock import patch
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from compare_reunion_physical_trees import features, CHANNELS, run


class PhysicalFeatureTests(unittest.TestCase):
    def test_latest_and_summary_use_only_declared_channels_and_keep_order(self):
        x = np.arange(2*14*30,dtype=np.float32).reshape(2,14,30)
        z = x[:, CHANNELS, :]
        np.testing.assert_array_equal(features(x,'latest'),z[:,:,-1])
        expected=np.concatenate((z[:,:,-1],z.mean(2),z.std(2),z[:,:,-1]-z[:,:,0]),axis=1)
        np.testing.assert_allclose(features(x,'summary'),expected)
        altered=x.copy();altered[:,2]=1e6
        np.testing.assert_array_equal(features(altered,'summary'),features(x,'summary'))

    def test_inner_plan_persisted_before_outer_predict_and_tests_not_read(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'manifest.json').write_text('{}')
            args=argparse.Namespace(prepared=root,output=root/'result')
            train={'x':np.zeros((4,14,30),np.float32),'y':np.array([0,0,1,1]),'end_time_ns':np.arange(4)*300_000_000_000}
            tuning={**train,'x':np.ones((4,14,30),np.float32)}
            outer={**train,'x':np.full((4,14,30),2,np.float32)}
            class Tree:
                classes_=np.array([0,1])
                def __init__(self,**kwargs):pass
                def fit(self,x,y):return self
                def predict_proba(self,x):
                    if x[0,0] > 1.5:
                        plan=json.loads((args.output/'fold/inner_selection.json').read_text())
                        assert plan['outer_status']=='not scored'
                    return np.array([[.9,.1],[.8,.2],[.2,.8],[.1,.9]])
            with (patch('compare_reunion_physical_trees.FOLDS',[('fold',)]),
                  patch('compare_reunion_physical_trees.fold_data',return_value=(train,tuning,outer,{'mean':np.zeros(14),'scale':np.ones(14)},{})),
                  patch('compare_reunion_physical_trees.ExtraTreesClassifier',Tree),
                  patch('compare_reunion_physical_trees.joblib.dump'),
                  patch('compare_reunion_physical_trees.CONDITIONS',{'clean':{}}),patch('builtins.print')):
                r=run(args)
            self.assertEqual(r['status'],'complete')
            self.assertEqual(r['folds']['fold']['conditions']['clean']['window_metrics']['fault_recall'],1.)

    def test_nonfinite_and_unknown_mode_are_rejected(self):
        x=np.zeros((1,14,30),np.float32)
        with self.assertRaises(ValueError):features(x,'bad')
        x[0,0,0]=np.nan
        with self.assertRaises(ValueError):features(x,'latest')

if __name__=='__main__':unittest.main()
