"""Retrospective sampled-window recovery audit on train-only forward folds.

A normal endpoint can still include prior fault observations in its model input.
This audit never relabels it or removes it from official false-positive counts.
Observed five-minute endpoints are not a full minute-level fault history.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from backtest_reunion import FOLDS, fold_data

MINUTE_NS = 60_000_000_000


def recovery_groups(labels, times, context_minutes):
    y, t = np.asarray(labels), np.asarray(times, dtype=np.int64)
    if (y.ndim != 1 or t.shape != y.shape or len(y) == 0
            or not set(np.unique(y)).issubset({0, 1})
            or np.any(np.diff(t) <= 0) or context_minutes < 1):
        raise ValueError('Expected chronological binary endpoint labels')
    days = np.asarray(pd.to_datetime(t, utc=True).tz_convert('Indian/Reunion').date, dtype=str)
    recent, elapsed = np.zeros(len(y), bool), np.full(len(y), np.nan)
    last_fault, last_day = None, None
    for i in range(len(y)):
        if days[i] != last_day:
            last_fault = None
        if y[i] == 0 and last_fault is not None:
            minutes = float((t[i] - last_fault) / MINUTE_NS)
            elapsed[i] = minutes
            # Inclusive input is [endpoint-context+1, endpoint].
            recent[i] = 0 < minutes < context_minutes
        if y[i] == 1:
            last_fault = t[i]
        last_day = days[i]
    return recent, elapsed


def describe(y, scores, times, threshold, context):
    recent, elapsed = recovery_groups(y, times, context)
    prediction = np.asarray(scores) >= threshold
    result = {}
    for name, mask in (('normal_with_sampled_fault_in_context', (y == 0) & recent),
                       ('other_normal_endpoints', (y == 0) & ~recent),
                       ('fault_endpoints', y == 1)):
        n = int(mask.sum());alerts = int((mask & prediction).sum())
        result[name] = {'windows': n, 'alarms': alerts,
                        'alarm_fraction': alerts / n if n else None}
    normal_alerts = (y == 0) & prediction
    result['all_normal_false_alarms'] = int(normal_alerts.sum())
    result['false_alert_rows'] = [
        {'end_time_ns': int(times[i]), 'fault_probability': float(scores[i]),
         'sampled_fault_inside_context': bool(recent[i]),
         'minutes_since_last_same_day_sampled_fault': float(elapsed[i]) if np.isfinite(elapsed[i]) else None}
        for i in np.flatnonzero(normal_alerts)]
    return result


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    manifest = (args.prepared / 'manifest.json').read_bytes()
    manifest_hash = hashlib.sha256(manifest).hexdigest()
    sources = {name: Path(path) for name, path in args.sources}
    if len(sources) != len(args.sources):
        raise ValueError('Unique source names required')
    reports = {name: json.loads((path / 'results.json').read_text()) for name, path in sources.items()}
    for report in reports.values():
        if report['prepared_manifest_sha256'] != manifest_hash or report['status'] != 'complete':
            raise ValueError('Source run incompatible or incomplete')
    result = {'protocol': 'Sampled endpoint label history vs model context; no relabelling or filtering',
              'prepared_manifest_sha256': manifest_hash, 'source_report_sha256': {
                  name: hashlib.sha256((path / 'results.json').read_bytes()).hexdigest()
                  for name, path in sources.items()}, 'folds': {},
              'held_out_status': 'Only train.npz and training normalization; no raw or validation/test/stress files',
              'limitations': ['Five-minute sampled labels can miss short faults and precise recovery times.',
                              'No preceding sampled fault does not certify wholly normal input.',
                              'A normal endpoint with prior fault remains an official false positive.',
                              'Historical outer dates already inspected; not a new independent evaluation.']}
    for name, *dates in FOLDS:
        _, _, outer, _, _ = fold_data(args.prepared, *dates)
        endpoint_hash = hashlib.sha256(outer['end_time_ns'].tobytes()).hexdigest()
        fold_result = result['folds'][name] = {}
        for label, source in sources.items():
            r = reports[label]; fold = r['folds'][name]
            if fold['endpoint_sha256']['outer'] != endpoint_hash:
                raise ValueError('Fold endpoint mismatch')
            context = fold['selected_context_minutes']; arch = r['architecture']
            runs = {}
            for seed in r['seeds']:
                p = source / name / f'context_{context}' / f'{arch}_seed{seed}_outer_predictions.npz'
                with np.load(p, allow_pickle=False) as cached:
                    if not np.array_equal(cached['y'], outer['y']) or not np.array_equal(cached['end_time_ns'], outer['end_time_ns']):
                        raise ValueError('Cached endpoint identity mismatch')
                    scores = cached['scores']
                if scores.shape != outer['y'].shape or not np.isfinite(scores).all() or np.any((scores < 0) | (scores > 1)):
                    raise ValueError('Invalid cached scores')
                threshold = fold['runs'][str(context)][str(seed)]['tuning_threshold']
                runs[str(seed)] = describe(outer['y'], scores, outer['end_time_ns'], threshold, context)
            fold_result[label] = {'context_minutes': context, 'runs': runs}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({name:{label:[r['normal_with_sampled_fault_in_context']['alarms'] for r in d['runs'].values()]
                            for label,d in fold.items()} for name,fold in result['folds'].items()},indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepared', type=Path, default=Path('data/processed/la_reunion_v1'))
    parser.add_argument('--source', nargs=2, action='append', dest='sources', required=True, metavar=('NAME','RUN_DIR'))
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args())
