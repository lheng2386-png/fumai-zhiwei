import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from audit_curated_pv_pixels import parse_boxes, pixel_fingerprints, run


class PixelAuditTests(unittest.TestCase):
    def test_boxes_reject_nonfinite_unknown_classes_and_outside_image(self):
        boxes, errors = parse_boxes('0 .5 .5 .2 .2\n1 .5 .5 0 .2\n2 .5 .5 .2 .2\n0 nan .5 .2 .2\n0 .9 .5 .5 .2', 2)
        self.assertEqual(boxes, [(0, .5, .5, .2, .2)])
        self.assertEqual(len(errors), 4)
        self.assertEqual(parse_boxes('', 2), ([], []))

    def test_full_audit_proves_duplicates_and_does_not_open_caches(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp); archive = folder / 'sample.zip'
            data = io.BytesIO()
            Image.new('RGB', (12, 9), color=(50, 80, 100)).save(data, format='JPEG')
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('Common_data/data.yaml', 'names: [hotspot]\n')
                for split, suffix in (('train', 'abc'), ('valid', 'def')):
                    name = f'Common_data/{split}/images/p.rf.{suffix}.jpg'
                    z.writestr(name, data.getvalue())
                    z.writestr(name.replace('/images/', '/labels/').replace('.jpg', '.txt'),
                               '0 .5 .5 .2 .2\n' if split == 'train' else '')
                z.writestr('Common_data/train/labels.cache', b'NEVER DESERIALIZE')
            reference = folder / 'reference'
            for split in ('train', 'val', 'test'):
                d = reference / split / 'images'; d.mkdir(parents=True)
                (d / 'same.jpg').write_bytes(data.getvalue() if split != 'test' else b'INVALID MUST NOT BE READ')
            (reference / 'manifest.json').write_text('{"fixture": true}')
            expected = hashlib.md5(archive.read_bytes()).hexdigest()
            read = zipfile.ZipFile.read
            def guarded_read(self, name, *args, **kw):
                self_name = name.filename if isinstance(name, zipfile.ZipInfo) else name
                if str(self_name).endswith('.cache'):
                    raise AssertionError('cache was opened')
                return read(self, name, *args, **kw)
            with patch('zipfile.ZipFile.read', new=guarded_read), patch('builtins.print'):
                result = run(archive, folder / 'audit', expected_md5=expected, reference=reference)
            self.assertEqual(result['exact_pixel_groups_crossing_splits'], 1)
            self.assertEqual(result['exact_pixel_groups_crossing_splits_with_conflicting_labels'], 1)
            self.assertEqual(result['empty_labels_by_split'], {'valid': 1})
            self.assertEqual(result['errors'], [])
            self.assertEqual(result['reference_train_val_images_read'], 2)
            self.assertEqual(len(result['reference_train_val_exact_pixel_matches']), 2)
            self.assertEqual(result['project_test_status'], 'not opened')
            with self.assertRaises(ValueError):
                run(archive, folder / 'bad', expected_md5='bad')
            self.assertFalse((folder / 'bad').exists())

    def test_pixel_hash_has_shape_identity_even_if_rgb_bytes_match(self):
        a, b = io.BytesIO(), io.BytesIO()
        Image.new('RGB', (4, 8)).save(a, format='PNG')
        Image.new('RGB', (8, 4)).save(b, format='PNG')
        self.assertNotEqual(pixel_fingerprints(a.getvalue())[0], pixel_fingerprints(b.getvalue())[0])


if __name__ == '__main__':
    unittest.main()
