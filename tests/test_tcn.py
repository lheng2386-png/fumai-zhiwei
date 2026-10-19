import sys
import unittest
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tcn_model import TCN_PV


class TCNTests(unittest.TestCase):
    def test_causal_tcn_preserves_window_shape_and_gradients(self):
        model = TCN_PV(channels=14, width=16, dilations=(1, 2, 4), classes=2)
        x = torch.randn(3, 14, 30, requires_grad=True)
        output = model(x)
        self.assertEqual(tuple(output.shape), (3, 2))
        output.square().mean().backward()
        self.assertTrue(torch.isfinite(x.grad).all().item())


if __name__ == "__main__":
    unittest.main()
