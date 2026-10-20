import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from compare_reunion_operating_points import (ARCHITECTURES, SEEDS, STEP_NS,
    candidate_members, constrained_threshold, observed_event_metrics, run)


class OperatingPointTests(unittest.TestCase):
    def test_threshold_honors_ties_and_reports_infeasible(self):
        labels = [0, 0, 1, 1]
        self.assertIsNone(constrained_threshold(labels, [.9, .9, .8, .95], 0, 1))
        result = constrained_threshold(labels, [.2, .2, .8, .9], 0, 1)
        self.assertEqual(result['threshold'], .8)
        self.assertEqual(result['false_positive_rate'], 0)
        self.assertEqual(result['fault_recall'], 1)
        # Tied normal/fault scores cannot be split by a fabricated threshold.
        self.assertIsNone(constrained_threshold(labels, [.8, .1, .8, .9], 0, 1))

    def test_bad_scores_and_one_class_cannot_calibrate_threshold(self):
        for y, p in (([0, 0], [.1, .2]), ([0, 1], [0, float('nan')]),
                     ([0, 1], [-.1, 1.1])):
            with self.assertRaises(ValueError):
                constrained_threshold(y, p, .01, .9)

    def test_event_gap_separates_events_and_preserves_missed_event(self):
        y = np.array([0, 1, 1, 1, 0, 0, 1])
        times = np.array([0, 1, 2, 5, 6, 7, 8], dtype=np.int64) * STEP_NS
        alarms = [True, False, True, False, True, True, True]
        r = observed_event_metrics(y, alarms, times)
        self.assertEqual(r['observed_fault_events'], 3)
        self.assertEqual(r['events_with_true_alarm'], 2)
        self.assertEqual(r['missed_observed_events'], 1)
        self.assertEqual(r['false_alarm_episodes'], 2)
        self.assertEqual(r['first_observed_fault_window_to_alarm_minutes'], [5., 0.])

    def test_uniform_members_do_not_repeat_models(self):
        for members in candidate_members().values():
            self.assertEqual(len(members), len(set(members)))
            self.assertIn(len(members), (1, 3, 6))

    def test_outer_files_open_only_after_inner_plan_is_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepared = root / 'prepared'; prepared.mkdir()
            manifest = b'{"fixture": true}'
            (prepared / 'manifest.json').write_bytes(manifest)
            for name in ('validation', 'test', 'normal_stress'):
                (prepared / (name + '.npz')).write_bytes(b'forbidden')
            labels = np.array([0, 0, 1, 1])
            times = np.arange(4, dtype=np.int64) * STEP_NS
            data = {'y': labels, 'end_time_ns': times}
            digest = hashlib.sha256(times.tobytes()).hexdigest()
            runs = root / 'runs'
            for arch in ARCHITECTURES:
                folder = runs / arch; folder.mkdir(parents=True)
                report = {'prepared_manifest_sha256': hashlib.sha256(manifest).hexdigest(),
                          'architecture': arch, 'status': 'complete', 'seeds': list(SEEDS),
                          'folds': {'fold': {'endpoint_sha256': {'tuning': digest, 'outer': digest}}}}
                (folder / 'results.json').write_text(json.dumps(report))
                for context in (15, 30):
                    d = folder / 'fold' / f'context_{context}'; d.mkdir(parents=True)
                    for seed in SEEDS:
                        np.save(d / f'{arch}_seed{seed}_validation.npy', [.1, .2, .8, .9])
                        np.savez(d / f'{arch}_seed{seed}_outer_predictions.npz',
                                 scores=[.1, .2, .8, .9], y=labels, end_time_ns=times)
            args = argparse.Namespace(prepared=prepared, runs=runs, output=root / 'out',
                                      max_tuning_fpr=.01, min_tuning_recall=.9)
            load = np.load
            def guard(path, *a, **kw):
                self.assertNotIn(Path(path).name, ('test.npz', 'validation.npz', 'normal_stress.npz'))
                if 'outer_predictions' in str(path):
                    plan = json.loads((args.output / 'fold' / 'inner_selection.json').read_text())
                    self.assertEqual(plan['outer_status'], 'not scored')
                return load(path, *a, **kw)
            with (patch('compare_reunion_operating_points.FOLDS', [('fold',)]),
                  patch('compare_reunion_operating_points.fold_data', return_value=(None, data, data, None, None)),
                  patch('numpy.load', side_effect=guard), patch('builtins.print')):
                r = run(args)
            self.assertEqual(r['status'], 'complete')
            self.assertEqual(r['folds']['fold']['constrained']['window_metrics']['fault_recall'], 1)


if __name__ == '__main__':
    unittest.main()
