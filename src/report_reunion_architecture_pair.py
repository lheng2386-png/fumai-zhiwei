"""Matched architecture comparison without refitting or selecting on outer scores."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from sklearn.metrics import average_precision_score
from train_reunion import summarize_scores
from compare_reunion_context import select_context


def compatible(a, b):
    for report in (a, b):
        if report['status'] != 'complete':
            raise ValueError('Incomplete source run')
    for key in ('prepared_manifest_sha256', 'feature_group', 'feature_indices',
                'contexts_minutes', 'seeds', 'epochs', 'patience', 'batch_size', 'lr',
                'hard_negative_alpha', 'sensor_gain_bound', 'normalization', 'held_out_status'):
        default = 0.0 if key in ('hard_negative_alpha', 'sensor_gain_bound') else None
        if default is None and (key not in a or key not in b):
            raise ValueError(f'Missing architecture control: {key}')
        if a.get(key, default) != b.get(key, default):
            raise ValueError(f'Unmatched architecture control: {key}')
    if set(a['folds']) != set(b['folds']):
        raise ValueError('Different folds')
    for name in a['folds']:
        for key in ('dates', 'endpoint_sha256', 'train_windows', 'tuning_windows',
                    'outer_windows', 'outer_normal_windows', 'outer_fault_windows'):
            if a['folds'][name][key] != b['folds'][name][key]:
                raise ValueError(f'Unmatched fold {name}: {key}')


def cached(path, endpoint_hash):
    with np.load(path, allow_pickle=False) as d:
        y, scores, times = d['y'], d['scores'], d['end_time_ns']
    if (y.ndim != 1 or scores.shape != y.shape or times.shape != y.shape
            or not set(np.unique(y)).issubset({0, 1}) or len(np.unique(y)) != 2
            or np.any(np.diff(times) <= 0) or not np.isfinite(scores).all()
            or np.any((scores < 0) | (scores > 1))
            or hashlib.sha256(times.tobytes()).hexdigest() != endpoint_hash):
        raise ValueError('Invalid cached endpoints or scores')
    return y, scores, times


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    sources = {'reference': args.reference, 'candidate': args.candidate}
    reports = {k: json.loads((p/'results.json').read_text()) for k,p in sources.items()}
    compatible(reports['reference'], reports['candidate'])
    result = {'protocol': 'Matched features/controls, fixed-context and internally selected architecture comparison',
              'architectures': {k:r['architecture'] for k,r in reports.items()},
              'source_sha256': {}, 'folds': {},
              'limitations': ['Historical outer dates already viewed; not a fresh holdout.',
                             'Identical optimizer controls do not guarantee equally tuned architectures.',
                             'Parameter counts and random-number consumption differ by architecture.',
                             'No threshold fitting, model selection, or promotion based on outer scores.']}
    for label, source in sources.items():
        p = source/'results.json'
        result['source_sha256'][str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    for name in reports['reference']['folds']:
        # Verify selected length still follows each run's declared inner-only rule.
        for report in reports.values():
            fold = report['folds'][name]
            tuning = {c:[fold['runs'][str(c)][str(seed)]['tuning_ap'] for seed in report['seeds']]
                      for c in report['contexts_minutes']}
            if any(not np.isfinite(values).all() for values in tuning.values()):
                raise ValueError('Nonfinite inner tuning score')
            if select_context(tuning) != fold['selected_context_minutes']:
                raise ValueError('Context selection disagrees with inner scores')
        # Verify saved training-only normalization values really match.
        with np.load(args.reference/name/'normalization.npz', allow_pickle=False) as a, np.load(args.candidate/name/'normalization.npz', allow_pickle=False) as b:
            if set(a.files) != set(b.files) or any(not np.array_equal(a[k], b[k]) for k in a.files):
                raise ValueError('Different fold scalers')
        by_context = {}
        for context in reports['reference']['contexts_minutes']:
            runs, identity = {}, None
            for label, source in sources.items():
                r = reports[label]; fold = r['folds'][name]; arch = r['architecture']
                rows = []
                for seed in r['seeds']:
                    p = source/name/f'context_{context}'/f'{arch}_seed{seed}_outer_predictions.npz'
                    y, scores, times = cached(p, fold['endpoint_sha256']['outer'])
                    if identity is None: identity = (y, times)
                    if not np.array_equal(y, identity[0]) or not np.array_equal(times, identity[1]):
                        raise ValueError('Label/endpoint pairing mismatch')
                    record = fold['runs'][str(context)][str(seed)]
                    metrics = summarize_scores(y, scores, record['tuning_threshold'])
                    if metrics['confusion_matrix'] != record['outer_window_metrics']['confusion_matrix']:
                        raise ValueError('Source metric disagrees with cached predictions')
                    rows.append({'seed': seed, 'outer_ap': float(average_precision_score(y,scores)),
                                 'parameters': record['parameters'], 'threshold': record['tuning_threshold'],
                                 'metrics': metrics})
                    result['source_sha256'][str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
                runs[label] = {'runs': rows,
                              'mean_ap': float(np.mean([row['outer_ap'] for row in rows])),
                              'false_alarms': [row['metrics']['confusion_matrix'][0][1] for row in rows],
                              'fault_recall': [row['metrics']['fault_recall'] for row in rows]}
            by_context[str(context)] = runs
        selected = {label: {'context_minutes':r['folds'][name]['selected_context_minutes'],
                           **by_context[str(r['folds'][name]['selected_context_minutes'])][label]}
                    for label,r in reports.items()}
        result['folds'][name] = {'fixed_contexts':by_context, 'internally_selected':selected}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({name:fold['internally_selected'] for name,fold in result['folds'].items()},indent=2))
    return result


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--reference',type=Path,required=True)
    p.add_argument('--candidate',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    run(p.parse_args())
