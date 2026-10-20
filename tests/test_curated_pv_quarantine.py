from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from plan_curated_pv_quarantine import plan


def row(name, split='train', pixel=None, normalized=None, signature='label'):
    return {'path': f'Common_data/{split}/images/{name}.jpg', 'split': split,
            'pixel_sha256': pixel or name, 'normalized_name': normalized or name,
            'label_signature': signature, 'box_count': 1, 'empty_label': False}


class QuarantineTests(unittest.TestCase):
    def test_transitive_pixel_and_name_components_cannot_leak_publisher_holdouts(self):
        rows = [row('a', pixel='same'), row('b', pixel='same', normalized='group'),
                row('c', split='valid', normalized='group'), row('clean')]
        records, report = plan(rows, {'errors': []})
        self.assertEqual(report['status_counts'], {'quarantined': 3, 'candidate': 1})
        self.assertEqual(len({r['component'] for r in records[:3]}), 1)

    def test_conflicting_pixels_quarantine_whole_training_component(self):
        rows = [row('a', pixel='same'), row('b', pixel='same', signature='different')]
        records, _ = plan(rows, {'errors': []})
        self.assertTrue(all('exact_pixel_annotation_conflict' in r['reasons'] for r in records))

    def test_invalid_box_is_not_fixed_or_silently_dropped(self):
        rows = [row('a'), row('b', normalized='a')]
        records, _ = plan(rows, {'errors': [{'path': 'Common_data/train/labels/a.txt'}]})
        self.assertTrue(all(r['status'] == 'quarantined' for r in records))

    def test_training_derivatives_get_one_deterministic_representative(self):
        rows = [row('z', normalized='group'), row('a', normalized='group')]
        records, summary = plan(rows, {'errors': []})
        self.assertEqual(summary['status_counts'], {'redundant_derivative': 1, 'candidate': 1})
        self.assertEqual(records[1]['status'], 'candidate')

    def test_truncated_reference_candidates_require_full_list(self):
        with self.assertRaises(ValueError):
            plan([row('a')], {'errors': [], 'reference_train_val_same_dhash_candidate_count': 1})

if __name__ == '__main__':
    unittest.main()
