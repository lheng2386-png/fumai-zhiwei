from pathlib import Path
import sys
import io
import json
import tempfile
import zipfile
from unittest.mock import patch
import unittest

import numpy as np
from PIL import Image
import torch
from torch import nn
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from pretrain_pv_simsiam import PackedJPEGs, SiameseEncoder, TwoViews, siamese_loss
from train_pvf10_transfer import load_backbone
from prepare_curated_pv_ssl import prepare
from audit_curated_pv_pixels import PUBLISHED_MD5


class SelfSupervisedTests(unittest.TestCase):
    def test_stop_gradient_is_only_on_targets(self):
        p1 = torch.randn(4, 8, requires_grad=True)
        p2 = torch.randn(4, 8, requires_grad=True)
        z1 = torch.randn(4, 8, requires_grad=True)
        z2 = torch.randn(4, 8, requires_grad=True)
        loss = siamese_loss(p1, p2, z1, z2)
        loss.backward()
        self.assertIsNone(z1.grad)
        self.assertIsNone(z2.grad)
        self.assertTrue(torch.isfinite(p1.grad).all())
        self.assertGreater(float(p1.grad.abs().sum()), 0)
        self.assertGreater(float(p2.grad.abs().sum()), 0)

    def test_toy_two_view_training_has_encoder_gradient_and_updates_weights(self):
        torch.manual_seed(1)
        model = SiameseEncoder(nn.Linear(4, 8), 8, projection=16, hidden=4)
        original = model.encoder.weight.detach().clone()
        optimizer = torch.optim.SGD(model.parameters(), lr=.1)
        p1, p2, z1, z2 = model(torch.randn(8, 4), torch.randn(8, 4))
        loss = siamese_loss(p1, p2, z1, z2)
        loss.backward(); optimizer.step()
        self.assertFalse(torch.equal(original, model.encoder.weight))
        self.assertTrue(torch.isfinite(model.encoder.weight.grad).all())

    def test_views_have_valid_normalized_tensor_shape_without_labels(self):
        images = np.random.default_rng(2).integers(0, 256, size=(2, 256, 256, 3), dtype=np.uint8)
        dataset = TwoViews(images)
        a, b = dataset[0]
        self.assertEqual(a.shape, (3, 224, 224))
        self.assertEqual(b.shape, a.shape)
        self.assertTrue(torch.isfinite(a).all())
        self.assertFalse(torch.equal(a, b))

    def test_packed_jpeg_roundtrip_and_offset_validation(self):
        stream = io.BytesIO()
        Image.new("RGB", (256, 256), color=(120, 80, 40)).save(stream, format="JPEG", quality=95, subsampling=0)
        raw = stream.getvalue()
        data = np.frombuffer(raw, dtype=np.uint8)
        images = PackedJPEGs(data, np.array([0, len(data)], dtype=np.int64), 256)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].shape, (256, 256, 3))
        a, b = TwoViews(images)[0]
        self.assertEqual(a.shape, (3, 224, 224))
        for offsets in ([1, len(data)], [0, len(data) + 1], [0, 0, len(data)]):
            with self.assertRaises(ValueError):
                PackedJPEGs(data, np.array(offsets, dtype=np.int64), 256)

    def test_preparation_stores_no_labels_and_rejects_heldout_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "source.zip"
            plan = root / "plan"; plan.mkdir()
            buffer = io.BytesIO()
            Image.new("RGB", (320, 320), color=(50, 80, 100)).save(buffer, format="JPEG")
            with zipfile.ZipFile(archive, "w") as source:
                source.writestr("Common_data/train/images/a.jpg", buffer.getvalue())
                source.writestr("Common_data/train/labels/a.txt", "MUST NOT BE READ")
                source.writestr("Common_data/test/images/b.jpg", b"MUST NOT BE READ")
            record = {"path": "Common_data/train/images/a.jpg", "component": "group-a",
                      "status": "candidate", "reasons": [], "representative": "Common_data/train/images/a.jpg"}
            (plan / "plan.jsonl").write_text(json.dumps(record) + "\n")
            (plan / "summary.json").write_text("{}")
            with patch("prepare_curated_pv_ssl.file_md5", return_value=PUBLISHED_MD5), patch("builtins.print"):
                result = prepare(archive, plan, root / "out", size=256, jpeg_quality=95)
            self.assertEqual(result["images"], 1)
            with np.load(root / "out/unlabeled.npz", allow_pickle=False) as cached:
                self.assertNotIn("labels", cached.files)
                images = PackedJPEGs(cached["jpeg_data"], cached["jpeg_offsets"], 256)
                self.assertEqual(images[0].shape, (256, 256, 3))
            record.update(path="Common_data/test/images/b.jpg", representative="Common_data/test/images/b.jpg")
            (plan / "plan.jsonl").write_text(json.dumps(record) + "\n")
            with patch("prepare_curated_pv_ssl.file_md5", return_value=PUBLISHED_MD5):
                with self.assertRaises(ValueError):
                    prepare(archive, plan, root / "bad", size=256, jpeg_quality=95)
            self.assertFalse((root / "bad").exists())

    def test_transfer_replaces_only_backbone_and_rejects_partial_or_head_weights(self):
        model = nn.Sequential(nn.Linear(4, 8), nn.Linear(8, 2))
        head = model[1].weight.detach().clone()
        checkpoint = {'architecture': 'fixture', 'backbone_state_dict': {
            '0.weight': torch.ones_like(model[0].weight), '0.bias': torch.zeros_like(model[0].bias)}}
        load_backbone(model, checkpoint, 'fixture', '1.')
        torch.testing.assert_close(model[0].weight, torch.ones_like(model[0].weight))
        torch.testing.assert_close(model[1].weight, head)
        for bad in ({'0.weight': torch.zeros_like(model[0].weight)},
                    {**checkpoint['backbone_state_dict'], '1.bias': model[1].bias}):
            with self.assertRaises(ValueError):
                load_backbone(model, {'architecture': 'fixture', 'backbone_state_dict': bad}, 'fixture', '1.')

if __name__ == '__main__':
    unittest.main()
