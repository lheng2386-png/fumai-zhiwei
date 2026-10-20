"""Freeze model/ensemble/threshold on inner folds before scoring later dates.

Uses existing training-only cached predictions. Historical outer dates have
already been inspected; this is a retrospective operating-point diagnostic.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from sklearn.metrics import average_precision_score, roc_curve

from backtest_reunion import FOLDS, fold_data
from train_reunion import choose_threshold, day_alert_summary, summarize_scores

ARCHITECTURES = ('transformer', 'rnn', 'lstm', 'multiscale_cnn')
SEEDS = (2031, 2032, 2033)
STEP_NS = 300_000_000_000


def constrained_threshold(labels, scores, max_fpr, min_recall):
    y, p = np.asarray(labels), np.asarray(scores)
    if (p.shape != y.shape or y.ndim != 1 or not np.isfinite(p).all()
            or np.any((p < 0) | (p > 1)) or set(np.unique(y)) != {0, 1}
            or not 0 <= max_fpr <= 1 or not 0 <= min_recall <= 1):
        raise ValueError('Invalid operating-point inputs')
    fpr, recall, thresholds = roc_curve(y, p, drop_intermediate=False)
    feasible = np.flatnonzero((fpr <= max_fpr) & (recall >= min_recall)
                              & np.isfinite(thresholds) & (thresholds <= 1))
    if not len(feasible):
        return None
    index = max(feasible, key=lambda i: (recall[i], -fpr[i], thresholds[i]))
    return {'threshold': float(thresholds[index]), 'false_positive_rate': float(fpr[index]),
            'fault_recall': float(recall[index])}


def observed_event_metrics(labels, alarms, timestamps):
    """Events are contiguous labelled 5-min windows, not confirmed work orders."""
    y, a, t = np.asarray(labels), np.asarray(alarms, dtype=bool), np.asarray(timestamps)
    if (y.ndim != 1 or len(y) == 0 or a.shape != y.shape or t.shape != y.shape
            or not set(np.unique(y)).issubset({0, 1}) or np.any(np.diff(t) <= 0)):
        raise ValueError('Invalid chronological event inputs')
    starts = np.flatnonzero((y == 1) & np.r_[True, (y[:-1] != 1) | (np.diff(t) != STEP_NS)])
    detected, delays = 0, []
    for start in starts:
        stop = start + 1
        while stop < len(y) and y[stop] == 1 and t[stop] - t[stop - 1] == STEP_NS:
            stop += 1
        hits = np.flatnonzero(a[start:stop])
        if len(hits):
            detected += 1
            delays.append(float((t[start + hits[0]] - t[start]) / 60_000_000_000))
    false_hits = a & (y == 0)
    false_starts = false_hits & np.r_[True, (~false_hits[:-1]) | (np.diff(t) != STEP_NS)]
    return {'observed_fault_events': len(starts), 'events_with_true_alarm': detected,
            'missed_observed_events': len(starts) - detected,
            'false_alarm_episodes': int(false_starts.sum()),
            'first_observed_fault_window_to_alarm_minutes': delays,
            'timing_note': 'Measured from first observed labelled fault window; not physical onset latency'}


def candidate_members():
    result = {}
    for context in (15, 30):
        for arch in ARCHITECTURES:
            members = [(arch, context, seed) for seed in SEEDS]
            for member in members:
                result[f'{arch}_{context}_seed{member[2]}'] = [member]
            result[f'{arch}_{context}_mean3'] = members
        for other in ('lstm', 'transformer'):
            result[f'rnn_{other}_{context}_mean6'] = [
                (arch, context, seed) for arch in ('rnn', other) for seed in SEEDS]
    return result


def select_plans(labels, tuning, members, max_fpr, min_recall):
    """Outer scores are deliberately absent from this function's contract."""
    records = {}
    for name, scores in tuning.items():
        threshold = choose_threshold(labels, scores)
        records[name] = {
            'members': members[name], 'model_count': len(members[name]),
            'tuning_ap': float(average_precision_score(labels, scores)),
            'macro_f1_threshold': threshold,
            'macro_f1_metrics': summarize_scores(labels, scores, threshold),
            'constrained': constrained_threshold(labels, scores, max_fpr, min_recall)}
    baseline = max(records, key=lambda name: (records[name]['tuning_ap'],
                                             -records[name]['model_count'], name))
    feasible = [name for name, r in records.items() if r['constrained'] is not None]
    constrained = (max(feasible, key=lambda name: (
        records[name]['constrained']['fault_recall'],
        -records[name]['constrained']['false_positive_rate'],
        records[name]['tuning_ap'], -records[name]['model_count'], name)) if feasible else None)
    return {'reference': {'candidate': baseline, 'threshold': records[baseline]['macro_f1_threshold']},
            'constrained': ({'candidate': constrained,
                             'threshold': records[constrained]['constrained']['threshold']}
                            if constrained else None),
            'candidates': records, 'outer_status': 'not scored'}


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    if not 0 <= args.max_tuning_fpr <= 1 or not 0 <= args.min_tuning_recall <= 1:
        raise ValueError('Invalid constraints')
    manifest_hash = hashlib.sha256((args.prepared / 'manifest.json').read_bytes()).hexdigest()
    sources = {}
    for arch in ARCHITECTURES:
        p = args.runs / arch / 'results.json'
        source = json.loads(p.read_text())
        if (source['prepared_manifest_sha256'] != manifest_hash or source['architecture'] != arch
                or source['status'] != 'complete' or source['seeds'] != list(SEEDS)):
            raise ValueError(f'Incompatible cached run: {arch}')
        sources[arch] = source
    args.output.mkdir(parents=True)
    report = {'protocol': 'Inner-only selection of cached architecture/context/ensemble and thresholds',
              'prepared_manifest_sha256': manifest_hash,
              'source_run_reports_sha256': {arch: hashlib.sha256((args.runs / arch / 'results.json').read_bytes()).hexdigest() for arch in ARCHITECTURES},
              'max_tuning_fpr': args.max_tuning_fpr, 'min_tuning_recall': args.min_tuning_recall,
              'constraints_note': 'Exploratory operating point, not agreed industrial acceptance limits',
              'held_out_status': 'Original validation/test/normal_stress not opened',
              'limitations': ['Historical outer dates were previously inspected.',
                              'One plant with controlled shading; few independent normal days and events.',
                              'Tuning false-positive bound is not an outer or production guarantee.',
                              'Event boundaries use sampled labels, not maintenance-confirmed faults.'],
              'folds': {}}
    source_digests = {}
    for fold_name, *dates in FOLDS:
        _, tune, outer, _, _ = fold_data(args.prepared, *dates)
        for arch in ARCHITECTURES:
            for split, data in (('tuning', tune), ('outer', outer)):
                digest = hashlib.sha256(data['end_time_ns'].tobytes()).hexdigest()
                if sources[arch]['folds'][fold_name]['endpoint_sha256'][split] != digest:
                    raise ValueError('Cached predictions do not match fold endpoints')
        members = candidate_members()
        cached = {}
        for arch in ARCHITECTURES:
            for context in (15, 30):
                for seed in SEEDS:
                    path = args.runs / arch / fold_name / f'context_{context}' / f'{arch}_seed{seed}_validation.npy'
                    p = np.load(path, allow_pickle=False)
                    if p.shape != tune['y'].shape:
                        raise ValueError('Tuning cache shape mismatch')
                    cached[(arch, context, seed)] = p
                    source_digests[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        tuning = {name: np.mean([cached[tuple(member)] for member in m], axis=0)
                  for name, m in members.items()}
        decision = select_plans(tune['y'], tuning, members, args.max_tuning_fpr, args.min_tuning_recall)
        folder = args.output / fold_name
        folder.mkdir()
        # Persist all choices before opening any outer prediction file.
        (folder / 'inner_selection.json').write_text(json.dumps(decision, indent=2) + '\n')
        evaluated = {}
        for mode in ('reference', 'constrained'):
            plan = decision[mode]
            if plan is None:
                evaluated[mode] = {'status': 'infeasible_on_tuning', 'outer_not_scored': True}
                continue
            predictions = []
            for arch, context, seed in members[plan['candidate']]:
                path = args.runs / arch / fold_name / f'context_{context}' / f'{arch}_seed{seed}_outer_predictions.npz'
                with np.load(path, allow_pickle=False) as data:
                    if not np.array_equal(data['y'], outer['y']) or not np.array_equal(data['end_time_ns'], outer['end_time_ns']):
                        raise ValueError('Outer cache identity mismatch')
                    predictions.append(data['scores'])
                source_digests[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            scores = np.mean(predictions, axis=0)
            if (scores.shape != outer['y'].shape or not np.isfinite(scores).all()
                    or np.any((scores < 0) | (scores > 1))):
                raise ValueError('Invalid outer scores')
            threshold = plan['threshold']
            evaluated[mode] = {**plan, 'status': 'scored',
                               'model_count': len(predictions),
                               'window_metrics': summarize_scores(outer['y'], scores, threshold),
                               'day_metrics': day_alert_summary(outer, scores, threshold),
                               'observed_event_metrics': observed_event_metrics(
                                   outer['y'], scores >= threshold, outer['end_time_ns'])}
            np.savez_compressed(folder / f'{mode}_outer_predictions.npz', scores=scores,
                                y=outer['y'], end_time_ns=outer['end_time_ns'])
        report['folds'][fold_name] = evaluated
    report['source_predictions_sha256'] = source_digests
    report['status'] = 'complete'
    (args.output / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report['folds'], indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepared', type=Path, default=Path('data/processed/la_reunion_v1'))
    parser.add_argument('--runs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-tuning-fpr', type=float, default=.01)
    parser.add_argument('--min-tuning-recall', type=float, default=.9)
    run(parser.parse_args())
