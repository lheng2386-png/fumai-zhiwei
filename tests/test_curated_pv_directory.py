from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from audit_curated_pv_directory import audit


class DirectoryAuditTests(unittest.TestCase):
    def test_hash_derivatives_across_splits_are_flags_not_pixel_proof(self):
        rows = [{'path': f'Common_data/{split}/images/panel_jpg.rf.{digest}.jpg', 'bytes': 100}
                for split, digest in [('train', 'abc'), ('train', 'def'), ('valid', '123')]]
        r = audit(rows)
        self.assertEqual(r['image_count'], 3)
        self.assertEqual(r['unique_names_after_rf_hash_removal'], 1)
        self.assertEqual(r['normalized_names_crossing_splits'], 1)
        self.assertIn('unverified', r['status'])

    def test_different_prefixes_are_not_silently_merged_and_cache_not_loaded(self):
        rows = [{'path': f'Common_data/{split}/images/{prefix}_panel_jpg.rf.abc.jpg', 'bytes': 100}
                for split, prefix in [('train', 'source1'), ('valid', 'source2')]]
        rows.append({'path': 'Common_data/train/labels.cache', 'bytes': 999})
        r = audit(rows)
        self.assertEqual(r['normalized_names_crossing_splits'], 0)
        self.assertEqual(r['label_count'], 0)

    def test_zero_byte_label_count_is_separate_from_image_label_claims(self):
        rows = [{'path': 'Common_data/test/labels/p.txt', 'bytes': 0},
                {'path': 'Common_data/train/labels/q.txt', 'bytes': 12}]
        r = audit(rows)
        self.assertEqual(r['zero_byte_label_files_by_split'], {'test': 1})
        self.assertEqual(r['label_count'], 2)
        self.assertEqual(r['image_count'], 0)

if __name__ == '__main__':
    unittest.main()
