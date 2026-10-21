"""Matched validation-only report for thermal SSL vs ImageNet initialization."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from sklearn.metrics import classification_report, f1_score

from evaluate_pvf10_stress import triage_metrics

SEEDS = (2026, 2027, 2028)


def compatible(reference, adapted, archive_sha, seed):
    for run in (reference, adapted):
        if (run['archive_sha256'] != archive_sha or run['seed'] != seed
                or run['test_status'] != 'not read' or run['architecture'] != 'convnext_tiny'
                or run['input_size'] != [224, 224]):
            raise ValueError('Paired run protocol mismatch')
    if reference.get('adapted_backbone_sha256') or not adapted.get('adapted_backbone_sha256'):
        raise ValueError('Expected unadapted reference and adapted candidate')
    for key in ('epochs', 'patience', 'batch_size', 'backbone_lr', 'head_lr', 'ema_decay', 'augmentation', 'binary_loss_weight', 'class_weight_power', 'mixup_alpha'):
        default = 0.0 if key in ('binary_loss_weight', 'mixup_alpha') else .5 if key == 'class_weight_power' else None
        if reference['training_controls'].get(key, default) != adapted['training_controls'].get(key, default):
            raise ValueError(f'Training controls differ: {key}')
    if reference['pretrained_sha256'] != adapted['pretrained_sha256']:
        raise ValueError('ImageNet initial weight sources differ')


def probability(path, rows, classes):
    p = np.load(path, allow_pickle=False)
    if (p.shape != (rows, classes) or not np.isfinite(p).all() or np.any((p < 0) | (p > 1))
            or not np.allclose(p.sum(1), 1, atol=1e-5)):
        raise ValueError('Invalid validation probabilities')
    return p


def describe(y, p, classes, dates):
    predicted = p.argmax(1)
    healthy = classes.index('healthy panel')
    return {'macro_f1': float(f1_score(y, predicted, labels=range(len(classes)), average='macro', zero_division=0)),
            'classification_report': classification_report(y, predicted, labels=range(len(classes)),
                target_names=classes, output_dict=True, zero_division=0),
            'triage': triage_metrics(y, p, healthy, 0.0),
            'by_date': {str(date): triage_metrics(y[dates == date], p[dates == date], healthy, 0.0)
                        for date in np.unique(dates)}}


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    manifest = json.loads((args.data / 'manifest.json').read_text())
    with np.load(args.data / 'validation.npz', allow_pickle=False) as data:
        y, classes, dates = data['labels'], data['classes'].tolist(), data['capture_date']
    if classes != manifest['classes']:
        raise ValueError('Class map mismatch')
    report = {'protocol': 'Matched three-seed validation-only SSL adaptation comparison; no thresholds optimized',
              'source_manifest_sha256': hashlib.sha256((args.data / 'manifest.json').read_bytes()).hexdigest(),
              'seeds': list(SEEDS), 'test_status': 'not read', 'pairs': {},
              'limitations': ['Validation dates repeatedly used for development.',
                              'One SSL pretraining seed followed by three fine-tuning seeds.',
                              'Scene-to-crop transfer and unresolved source provenance limit interpretation.',
                              'No fresh independent site, industrial acceptance, or automatic model promotion.']}
    ref_prob, ada_prob, digests = [], [], set()
    for seed in SEEDS:
        r, a = args.reference / f'seed{seed}', args.adapted / f'seed{seed}'
        baseline, adapted = json.loads((r / 'results.json').read_text()), json.loads((a / 'results.json').read_text())
        compatible(baseline, adapted, manifest['archive_sha256'], seed)
        digests.add(adapted['adapted_backbone_sha256'])
        p, q = probability(r / 'best_validation_probabilities.npy', len(y), len(classes)), probability(a / 'best_validation_probabilities.npy', len(y), len(classes))
        ref_prob.append(p); ada_prob.append(q)
        rm, am = describe(y, p, classes, dates), describe(y, q, classes, dates)
        report['pairs'][str(seed)] = {'reference': rm, 'adapted': am,
                                    'macro_f1_delta': am['macro_f1'] - rm['macro_f1']}
    if len(digests) != 1:
        raise ValueError('Expected the same adaptation backbone for all three fine-tuning seeds')
    report['adapted_backbone_sha256'] = next(iter(digests))
    report['mean_macro_f1'] = {kind: float(np.mean([pair[kind]['macro_f1'] for pair in report['pairs'].values()]))
                               for kind in ('reference', 'adapted')}
    report['paired_positive_seeds'] = sum(pair['macro_f1_delta'] > 0 for pair in report['pairs'].values())
    report['equal_three_seed_ensembles'] = {'reference': describe(y, np.mean(ref_prob, 0), classes, dates),
                                           'adapted': describe(y, np.mean(ada_prob, 0), classes, dates)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'mean_macro_f1': report['mean_macro_f1'],
                      'paired_positive_seeds': report['paired_positive_seeds']}, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=Path('data/processed/pvf10_date_v1'))
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--adapted', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args())
