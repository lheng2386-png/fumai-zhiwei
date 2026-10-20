"""Frozen train-period fold sensor-drift audit; no fitting or threshold tuning.

Perturb raw measured channels, then recompute both derived ratios before applying
that fold's training-only scaler. Synthetic gains are diagnostics, not calibrated
field errors or independent validation.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import torch

from backtest_reunion import FOLDS, fold_data
from compare_reunion_context import FEATURE_GROUPS
from compare_reunion_operating_points import observed_event_metrics
from train_reunion import day_alert_summary, make_model, predict, summarize_scores

CONDITIONS = {'clean': {}, 'faulted_power_gain_105': {0: 1.05},
              'faulted_power_gain_095': {0: .95}, 'control_power_gain_105': {6: 1.05},
              'control_power_gain_095': {6: .95}, 'faulted_dc_current_gain_105': {1: 1.05},
              'control_dc_current_gain_105': {7: 1.05}}


def apply_sensor_gain(x, scaler, condition):
    if condition not in CONDITIONS or x.ndim != 3 or x.shape[1] != 14:
        raise ValueError('Expected supported condition and 14 source channels')
    mean, scale = np.asarray(scaler['mean']), np.asarray(scaler['scale'])
    if (mean.shape != (14,) or scale.shape != (14,) or np.any(scale <= 0)
            or not np.isfinite(mean).all() or not np.isfinite(scale).all() or not np.isfinite(x).all()):
        raise ValueError('Invalid normalized data or scaler')
    if condition == 'clean':
        return x.copy()
    raw = x * scale[None, :, None] + mean[None, :, None]
    for channel, gain in CONDITIONS[condition].items():
        raw[:, channel] *= gain
    with np.errstate(divide='raise', invalid='raise'):
        raw[:, 12] = raw[:, 0] / (raw[:, 6] + 50.)
        raw[:, 13] = raw[:, 1] / (raw[:, 7] + .2)
    result = (raw - mean[None, :, None]) / scale[None, :, None]
    if not np.isfinite(result).all():
        raise ValueError('Sensor perturbation produced nonfinite values')
    return result.astype(np.float32)


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    device = torch.device(args.device)
    reports = {group: json.loads((getattr(args, group) / 'results.json').read_text()) for group in FEATURE_GROUPS}
    manifest_hash = hashlib.sha256((args.prepared / 'manifest.json').read_bytes()).hexdigest()
    for group, report in reports.items():
        if (report['prepared_manifest_sha256'] != manifest_hash or report['architecture'] != 'rnn'
                or report['status'] != 'complete' or report.get('feature_group', 'all') != group):
            raise ValueError('Source run contract mismatch')
    args.output.mkdir(parents=True)
    result = {'protocol': 'Frozen internally selected RNN contexts/thresholds; synthetic raw-sensor gain audit',
              'prepared_manifest_sha256': manifest_hash, 'device': str(device),
              'source_report_sha256': {g: hashlib.sha256((getattr(args, g) / 'results.json').read_bytes()).hexdigest() for g in FEATURE_GROUPS},
              'conditions': CONDITIONS, 'folds': {}, 'checkpoint_sha256': {},
              'test_status': 'Original validation/test/normal_stress not opened',
              'limitations': ['Historical outer dates already inspected; no new holdout or production guarantee.',
                              'Five percent gains are fixed synthetic diagnostics, not calibrated sensor specifications.',
                              'Labelled faults are experimental shading at one plant with a paired reference inverter.',
                              'No input-quality rejection is simulated: coverage is 100 percent for finite drift inputs.']}
    for name, *dates in FOLDS:
        _, _, outer, scaler, _ = fold_data(args.prepared, *dates)
        digest = hashlib.sha256(outer['end_time_ns'].tobytes()).hexdigest()
        fold_result = result['folds'][name] = {}
        for group, source in reports.items():
            fold = source['folds'][name]
            if fold['endpoint_sha256']['outer'] != digest:
                raise ValueError('Cached fold endpoints mismatch')
            context = fold['selected_context_minutes']
            run_dir = getattr(args, group) / name / f'context_{context}'
            group_result = fold_result[group] = {'context_minutes': context, 'seeds': {}}
            for seed in source['seeds']:
                p = run_dir / f'rnn_seed{seed}.pt'
                checkpoint = torch.load(p, map_location='cpu', weights_only=True)
                channels = FEATURE_GROUPS[group]
                if checkpoint['architecture'] != 'rnn' or checkpoint['channels'] != len(channels):
                    raise ValueError('Model architecture mismatch')
                model = make_model('rnn', len(channels)).to(device)
                model.load_state_dict(checkpoint['state_dict'], strict=True)
                result['checkpoint_sha256'][str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
                threshold = fold['runs'][str(context)][str(seed)]['tuning_threshold']
                metrics = group_result['seeds'][str(seed)] = {}
                for condition in CONDITIONS:
                    x = apply_sensor_gain(outer['x'], scaler, condition)[:, channels, -context:]
                    scores = predict(model, np.ascontiguousarray(x), device, args.batch_size)
                    metrics[condition] = {'window_metrics': summarize_scores(outer['y'], scores, threshold),
                        'day_metrics': day_alert_summary(outer, scores, threshold),
                        'event_metrics': observed_event_metrics(outer['y'], scores >= threshold, outer['end_time_ns']),
                        'coverage': 1.0}
                    if condition == 'clean':
                        with np.load(run_dir / f'rnn_seed{seed}_outer_predictions.npz', allow_pickle=False) as saved:
                            if not np.array_equal(saved['y'], outer['y']) or not np.array_equal(saved['end_time_ns'], outer['end_time_ns']):
                                raise ValueError('Clean cached identity mismatch')
                            if not np.allclose(scores, saved['scores'], atol=1e-5, rtol=1e-4):
                                raise ValueError('Clean predictions fail reproduction check')
                del model
                print(json.dumps({'fold': name, 'group': group, 'seed': seed, 'status': 'scored'}), flush=True)
        (args.output / 'progress.json').write_text(json.dumps(result, indent=2) + '\n')
    result['status'] = 'complete'
    (args.output / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepared', type=Path, default=Path('data/processed/la_reunion_v1'))
    parser.add_argument('--all', type=Path, required=True)
    parser.add_argument('--dc-relative', type=Path, dest='dc_relative', required=True)
    parser.add_argument('--ratios-only', type=Path, dest='ratios_only', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--device', choices=('cuda', 'mps', 'cpu'), default='cuda')
    parser.add_argument('--batch-size', type=int, default=256)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error('batch-size must be positive')
    run(args)
