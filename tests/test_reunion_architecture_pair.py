from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from report_reunion_architecture_pair import compatible, cached

class ArchitecturePairTests(unittest.TestCase):
    def test_rejects_feature_endpoint_and_training_mismatch(self):
        a={'status':'complete','architecture':'rnn','feature_group':'dc_relative',
           'contexts_minutes':[15,30],'seeds':[2031,2032,2033], 'epochs':50,
           'prepared_manifest_sha256':'manifest','feature_indices':[0,1],
           'patience':8,'batch_size':256,'lr':.0008,'normalization':'train only',
           'held_out_status':'not read',
           'folds':{'feb':{'dates':['day'],'endpoint_sha256':{'outer':'hash'},
                         'train_windows':4,'tuning_windows':2,'outer_windows':2,
                         'outer_normal_windows':1,'outer_fault_windows':1}}}
        b=deepcopy(a);b['architecture']='transformer'
        compatible(a,b)
        for key,value in [('feature_group','all'),('epochs',25),('seeds',[2031]),('sensor_gain_bound',.05)]:
            bad=deepcopy(b);bad[key]=value
            with self.assertRaises(ValueError):compatible(a,bad)
        bad=deepcopy(b);del bad['lr']
        with self.assertRaises(ValueError):compatible(a,bad)
        bad=deepcopy(b);bad['folds']['feb']['endpoint_sha256']['outer']='other'
        with self.assertRaises(ValueError):compatible(a,bad)

    def test_cache_integrity_before_comparison(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'scores.npz'
            times=np.array([100,200],dtype=np.int64)
            digest=hashlib.sha256(times.tobytes()).hexdigest()
            np.savez(p,y=[0,1],scores=[.1,.9],end_time_ns=times)
            self.assertEqual(cached(p,digest)[1].tolist(),[.1,.9])
            with self.assertRaises(ValueError):cached(p,'wrong')
            np.savez(p,y=[0,1],scores=[.1,np.nan],end_time_ns=times)
            with self.assertRaises(ValueError):cached(p,digest)

if __name__=='__main__':unittest.main()
