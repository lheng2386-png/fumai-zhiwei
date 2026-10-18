import sys
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
try:
    import torch
except ImportError:
    torch=None


@unittest.skipIf(torch is None,'Optional torch dependency not installed')
class DeepTests(unittest.TestCase):
    def test_no_window_overlap_between_partitions(self):
        from train_deep import window_indices
        partitions=window_indices(10000)
        for first,second in zip(partitions,partitions[1:]):
            self.assertLess(first.max()+100,second.min())
        for idx in partitions:
            self.assertTrue((idx[:,1:]-idx[:,:-1]==1).all())
            self.assertEqual(len(np.unique(idx)),idx.size)
        with self.assertRaises(ValueError):
            window_indices(10000,stride=12)

    def test_finite_physical_features_and_masked_backward(self):
        from deep_model import make_features,TemporalEncoder
        raw=np.array([[300,300,2,2,800,40],[300,0,2,0,800,40]],dtype=np.float32)
        x=make_features(raw)
        self.assertEqual(x.shape,(2,13))
        self.assertEqual(x[0,6],600)
        self.assertEqual(x[0,8],1200)
        self.assertTrue(np.isfinite(x).all())
        with self.assertRaises(ValueError):make_features(raw*np.nan)
        torch.set_num_threads(2)
        model=TemporalEncoder()
        tensor=torch.randn(2,13,24)
        self.assertEqual(model(tensor).shape,(2,5))
        reconstructed=model(tensor,reconstruct=True)
        self.assertEqual(reconstructed.shape,tensor.shape)
        loss=(reconstructed-tensor).square().mean();loss.backward()
        self.assertTrue(torch.isfinite(loss))


if __name__=='__main__':
    unittest.main()
