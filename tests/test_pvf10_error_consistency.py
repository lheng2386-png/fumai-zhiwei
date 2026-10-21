import sys
from pathlib import Path
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from audit_pvf10_error_consistency import summarize
from train_pvf10_transfer import validation_identity

class ErrorConsistencyTests(unittest.TestCase):
    def test_distinguishes_persistent_wrong_type_and_seed_disagreement(self):
        y=np.array([0,1,1,2]); classes=['healthy panel','hot','shadow']
        prediction=np.array([[1,0,2,2],[2,0,0,2],[1,0,2,2]])
        p=np.full((3,4,3),.01)
        for seed in range(3):p[seed,np.arange(4),prediction[seed]]=.98
        result=summarize(y,p,classes,np.array(['a','b','c','d']),np.array(['day1','day1','day2','day2']))
        self.assertEqual(result['overall']['wrong_in_all_seeds'],3)
        self.assertEqual(result['overall']['same_wrong_type_all_seeds'],1)
        self.assertEqual(result['overall']['defect_as_healthy_all_seeds'],1)
        self.assertEqual(result['overall']['healthy_as_defect_all_seeds'],1)
        self.assertEqual(result['by_date']['day1']['wrong_in_all_seeds'],2)
        self.assertEqual(len(result['review_candidates']),3)
        self.assertEqual(result['persistent_confusions'],[{'actual':'hot','predicted':'healthy panel','samples':1,'source_photos':1}])
        with self.assertRaises(ValueError):summarize(y,p[:2],classes,['a']*4,['d']*4)
        with self.assertRaises(ValueError):summarize(y,p*.5,classes,['a']*4,['d']*4)

    def test_validation_hash_tracks_order_labels_and_class_map(self):
        y=np.array([0,1]);classes=['healthy panel','hot'];photos=['a','b'];dates=['d1','d2']
        original=validation_identity(y,classes,photos,dates)
        self.assertEqual(original,validation_identity(y.copy(),classes.copy(),photos.copy(),dates.copy()))
        for changed in ((y[::-1],classes,photos,dates),(y,classes,photos[::-1],dates),
                        (y,classes[::-1],photos,dates),(y,classes,photos,dates[::-1])):
            self.assertNotEqual(original,validation_identity(*changed))
        with self.assertRaises(ValueError):validation_identity(np.array([0,2]),classes,photos,dates)

if __name__=='__main__':unittest.main()
