"""Train-only forward-fold physical tree baseline with frozen inner selection.

Compares last-minute DC/ratio readings to summary statistics. All candidates and
thresholds are chosen on tuning dates before historical outer inference.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

import joblib
import numpy as np
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import average_precision_score

from backtest_reunion import FOLDS, fold_data
from compare_reunion_operating_points import observed_event_metrics
from evaluate_reunion_sensor_drift import CONDITIONS, apply_sensor_gain
from train_reunion import choose_threshold, summarize_scores, day_alert_summary

CHANNELS = (0, 1, 3, 6, 7, 9, 12, 13)


def features(x, mode):
    if x.ndim != 3 or x.shape[1] != 14 or not np.isfinite(x).all():
        raise ValueError('Expected finite 14-channel windows')
    z = x[:, CHANNELS, :]
    if mode == 'latest':
        return z[:, :, -1].copy()
    if mode == 'summary':
        return np.concatenate((z[:, :, -1], z.mean(2), z.std(2),
                               z[:, :, -1] - z[:, :, 0]), axis=1)
    raise ValueError('Unknown feature mode')


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    started = time.monotonic()
    report = {'protocol': 'Train-only physical ExtraTrees forward folds; inner mode/seed/threshold selection',
              'prepared_manifest_sha256': hashlib.sha256((args.prepared / 'manifest.json').read_bytes()).hexdigest(),
              'source_indices': CHANNELS, 'seeds': [2031, 2032, 2033],
              'controls': {'n_estimators': 500, 'min_samples_leaf': 5, 'class_weight': 'balanced',
                           'max_features': 1.0, 'n_jobs': 4},
              'held_out_status': 'Original validation/test/normal_stress never loaded',
              'limitations': ['Historical outer dates already inspected; retrospective only.',
                              'One site with injected shading and few independent normal days.',
                              'Sensor gains are synthetic; calibration and field diagnosis remain unverified.'],
              'folds': {}}
    for name, *dates in FOLDS:
        train, tune, outer, scaler, days = fold_data(args.prepared, *dates)
        folder = args.output / name; folder.mkdir()
        np.savez(folder / 'normalization.npz', **scaler)
        candidates, models = {}, {}
        for mode in ('latest', 'summary'):
            for seed in (2031, 2032, 2033):
                key = f'{mode}_seed{seed}'
                model = ExtraTreesClassifier(n_estimators=500, min_samples_leaf=5,
                    class_weight='balanced', max_features=1.0, n_jobs=4, random_state=seed)
                model.fit(features(train['x'], mode), train['y'])
                scores = model.predict_proba(features(tune['x'], mode))[:, list(model.classes_).index(1)]
                candidates[key] = {'mode': mode, 'seed': seed,
                    'tuning_ap': float(average_precision_score(tune['y'], scores)),
                    'threshold': choose_threshold(tune['y'], scores)}
                models[key] = model
                joblib.dump(model, folder / f'{key}.joblib')
        selected = max(candidates, key=lambda key: (candidates[key]['tuning_ap'], key))
        decision = {'selected': selected, 'candidates': candidates, 'outer_status': 'not scored',
                    'dates': days}
        (folder / 'inner_selection.json').write_text(json.dumps(decision, indent=2)+'\n')
        chosen = candidates[selected]; model = models[selected]
        results = {}
        for condition in CONDITIONS:
            x = apply_sensor_gain(outer['x'], scaler, condition)
            scores = model.predict_proba(features(x, chosen['mode']))[:, list(model.classes_).index(1)]
            threshold = chosen['threshold']
            results[condition] = {'window_metrics': summarize_scores(outer['y'], scores, threshold),
                                 'day_metrics': day_alert_summary(outer, scores, threshold),
                                 'event_metrics': observed_event_metrics(outer['y'], scores >= threshold, outer['end_time_ns'])}
            np.savez_compressed(folder / f'{condition}_outer.npz', scores=scores,
                                y=outer['y'], end_time_ns=outer['end_time_ns'])
        report['folds'][name] = {**decision, 'outer_status': 'scored', 'conditions': results}
        (args.output / 'progress.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps({'fold': name, 'selected': selected, 'clean': results['clean']}), flush=True)
    report['status'] = 'complete';report['elapsed_seconds'] = time.monotonic()-started
    (args.output / 'results.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepared', type=Path, default=Path('data/processed/la_reunion_v1'))
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args())
