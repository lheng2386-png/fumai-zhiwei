import sys
from pathlib import Path
import unittest
import numpy as np
import torch
from torch.nn import functional as F
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from train_pvf10_transfer import mixup_batch, mixup_loss

class MixupTests(unittest.TestCase):
    def test_zero_preserves_tensor_and_rng(self):
        x, y = torch.randn(4, 3, 2, 2), torch.arange(4)
        state = torch.get_rng_state().clone()
        nstate = np.random.get_state()
        mixed, a, b, lam = mixup_batch(x, y, 0)
        self.assertIs(mixed, x)
        self.assertTrue(torch.equal(state, torch.get_rng_state()))
        self.assertTrue(np.array_equal(nstate[1], np.random.get_state()[1]))
        self.assertEqual(lam, 1)

    def test_partner_images_and_labels_share_permutation(self):
        x = torch.arange(8).float().view(8, 1, 1, 1)
        y = torch.arange(8)
        np.random.seed(2026); torch.manual_seed(2026)
        mixed, first, second, lam = mixup_batch(x, y, .2)
        self.assertTrue(0 < lam < 1)
        self.assertTrue(torch.allclose(mixed.flatten(), lam * first + (1-lam) * second))
        self.assertEqual(sorted(second.tolist()), list(range(8)))

    def test_weighted_two_target_objective_and_gradient(self):
        logits = torch.randn(4, 3, requires_grad=True)
        a, b = torch.tensor([0, 1, 2, 1]), torch.tensor([2, 0, 1, 0])
        weights = torch.tensor([1., 2., 3.])
        loss = mixup_loss(logits, a, b, .3, weights, 0)
        expected = .3 * F.cross_entropy(logits, a, weight=weights) + .7 * F.cross_entropy(logits, b, weight=weights)
        self.assertTrue(torch.allclose(loss, expected))
        loss.backward()
        self.assertTrue(torch.isfinite(logits.grad).all())

if __name__ == '__main__': unittest.main()
