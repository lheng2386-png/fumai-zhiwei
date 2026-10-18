import sys
import unittest
from pathlib import Path
import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from multiscale_model import MultiscalePV
from train_multiscale import masked_input
from prepare_infrared import partition
from train_infrared import InfraredPV, augment


class MultiscaleTests(unittest.TestCase):
    def test_masked_reconstruction_and_classification_gradients(self):
        torch.set_num_threads(2)
        model = MultiscalePV(width=32, depth=2)
        batch = torch.randn(4, 13, 24)
        corrupted, mask = masked_input(batch)
        self.assertTrue(mask.any().item())
        self.assertTrue(torch.equal(batch[~mask], corrupted[~mask]))
        self.assertTrue((corrupted[mask] == 0).all().item())
        reconstruction = model(corrupted, reconstruct=True)
        loss = (reconstruction[mask]-batch[mask]).square().mean()
        loss.backward()
        self.assertTrue(torch.isfinite(model.stem.weight.grad).all().item())
        model.zero_grad()
        logits = model(batch)
        self.assertEqual(tuple(logits.shape), (4, 5))
        torch.nn.functional.cross_entropy(logits, torch.arange(4)).backward()
        self.assertTrue(torch.isfinite(model.classifier[-1].weight.grad).all().item())

    def test_image_duplicates_share_partition(self):
        labels = np.repeat(np.arange(3), 100)
        groups = np.repeat(np.arange(150), 2)
        splits = partition(labels, groups, 2026)
        self.assertEqual(set(splits), {'train', 'validation', 'test'})
        for group in np.unique(groups):
            self.assertEqual(len(set(splits[groups == group])), 1)
        for split in set(splits):
            self.assertEqual(set(labels[splits == split]), {0, 1, 2})

    def test_infrared_model_shapes_match_published_dataset(self):
        torch.set_num_threads(2)
        model = InfraredPV(classes=12)
        batch = torch.randn(2, 1, 40, 24)
        self.assertEqual(tuple(model(batch).shape), (2, 12))
        self.assertEqual(tuple(model(batch, reconstruct=True).shape), (2, 1, 40, 24))

    def test_infrared_augmentation_preserves_batch_shape_and_values(self):
        batch = torch.rand(4, 1, 40, 24)
        transformed = augment(batch)
        self.assertEqual(tuple(transformed.shape), tuple(batch.shape))
        self.assertTrue(torch.equal(torch.sort(transformed.flatten()).values,
                                   torch.sort(batch.flatten()).values))


if __name__ == '__main__':
    unittest.main()
