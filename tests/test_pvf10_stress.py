import sys
import unittest
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.optim.swa_utils import AveragedModel, get_ema_multi_avg_fn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from evaluate_pvf10_stress import CONDITIONS, perturb, triage_metrics
from train_pvf10_transfer import augment


class PVFStressTests(unittest.TestCase):
    def test_review_is_unresolved_and_never_silently_healthy(self):
        labels = np.array([0, 0, 1, 1, 2])
        probabilities = np.array([[.95, .03, .02], [.1, .85, .05],
                                  [.92, .04, .04], [.4, .5, .1], [.01, .98, .01]])
        r = triage_metrics(labels, probabilities, healthy=0, threshold=.9)
        self.assertEqual(r['automated_candidates'], 3)
        self.assertEqual(r['manual_review'], 2)
        self.assertEqual(r['manual_review_defects'], 1)
        self.assertEqual(r['defects_released_as_healthy'], 1)
        self.assertEqual(r['wrong_defect_type_candidates'], 1)
        self.assertEqual(r['healthy_defect_alert_candidates'], 0)
        self.assertAlmostEqual(r['conditional_accuracy'], 1 / 3)
        self.assertEqual(r['samples'], r['manual_review'] + r['automated_candidates'])
        r = triage_metrics(labels, probabilities, healthy=0, threshold=1)
        self.assertIsNone(r['conditional_accuracy'])
        self.assertEqual(r['coverage'], 0)

    def test_reject_invalid_probabilities(self):
        for p in ([[float('nan'), 0]], [[.1, .2]], [[1.1, -.1]]):
            with self.assertRaises(ValueError):
                triage_metrics([0], p, 0, .9)

    def test_stress_is_reproducible_and_preserves_source(self):
        torch.manual_seed(42)
        x = torch.rand(2, 3, 16, 16)
        original = x.clone()
        for condition in CONDITIONS:
            a, b = perturb(x, condition), perturb(x, condition)
            self.assertEqual(a.shape, x.shape)
            torch.testing.assert_close(a, b)
            self.assertTrue(torch.isfinite(a).all())
            self.assertTrue(((a >= 0) & (a <= 1)).all())
        torch.testing.assert_close(x, original)
        with self.assertRaises(ValueError):
            perturb(torch.full_like(x, float('nan')), 'clean')

    def test_robust_training_augmentation_is_finite_and_bounded(self):
        x = torch.rand(4, 3, 16, 16)
        source = x.clone()
        for recipe in ("baseline", "robust"):
            torch.manual_seed(7)
            a = augment(x, recipe)
            torch.manual_seed(7)
            b = augment(x, recipe)
            torch.testing.assert_close(a, b)
            self.assertTrue(torch.isfinite(a).all())
            self.assertTrue(((a >= 0) & (a <= 1)).all())
        torch.testing.assert_close(x, source)
        with self.assertRaises(ValueError):
            augment(x, "unknown")

    def test_ema_tracks_weights_without_gradient_or_alias(self):
        model = nn.Linear(1, 1, bias=False)
        ema = AveragedModel(model, multi_avg_fn=get_ema_multi_avg_fn(.9), use_buffers=True)
        with torch.no_grad():
            model.weight.fill_(2)
        ema.update_parameters(model)
        with torch.no_grad():
            model.weight.fill_(4)
        ema.update_parameters(model)
        self.assertAlmostEqual(ema.module.weight.item(), 2.2, places=5)
        self.assertIsNone(ema.module.weight.grad)
        self.assertEqual(model.weight.item(), 4)


if __name__ == '__main__':
    unittest.main()
