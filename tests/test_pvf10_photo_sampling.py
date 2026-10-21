import sys
from pathlib import Path
import unittest
import numpy as np
import torch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from pvf10_photo_sampling import photo_balanced_weights,epoch_indices

class PhotoSamplingTests(unittest.TestCase):
    def test_preserves_class_mass_but_equalizes_each_photo_inside_class(self):
        y=np.array([0,0,0,0,1,1,1]);photos=np.array(['a','a','a','b','a','c','c'])
        w=photo_balanced_weights(y,photos)
        self.assertAlmostEqual(w[y==0].sum(),4.)
        self.assertAlmostEqual(w[y==1].sum(),3.)
        self.assertAlmostEqual(w[(y==0)&(photos=='a')].sum(),w[(y==0)&(photos=='b')].sum())
        self.assertAlmostEqual(w[(y==1)&(photos=='a')].sum(),w[(y==1)&(photos=='c')].sum())
        self.assertAlmostEqual(w.sum(),len(y))

    def test_default_shuffle_preserves_previous_rng_path(self):
        torch.manual_seed(2031);expected=torch.randperm(20)
        torch.manual_seed(2031);actual=epoch_indices(20,torch.device('cpu'))
        self.assertTrue(torch.equal(expected,actual))
        torch.manual_seed(2031);a=epoch_indices(50,torch.device('cpu'),torch.ones(50))
        self.assertEqual(a.shape,(50,));self.assertTrue(((a>=0)&(a<50)).all())
        # Replacement must be disclosed; unlike shuffle, it may omit/repeat images.
        self.assertLess(len(a.unique()),50)

    def test_invalid_groups_or_weights_rejected(self):
        for y,groups in [(np.array([0,1]),['a']), (np.array([0,1]),['a','']),
                         (np.array([0.,1.]),['a','b']), (np.array([-1,0]),['a','b'])]:
            with self.assertRaises(ValueError):photo_balanced_weights(y,groups)
        with self.assertRaises(ValueError):epoch_indices(2,torch.device('cpu'),torch.tensor([1.,float('nan')]))

if __name__=='__main__':unittest.main()
