"""Retrospective validation-only image stress and manual-review coverage audit.

Fixed perturbations and thresholds are diagnostics, never a calibration claim.
The date-held-out test archive is not opened.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import f1_score, recall_score
from torchvision.transforms.functional import gaussian_blur

from train_pvf10_transfer import build_model, normalize
from train_multiscale import write_json


CONDITIONS = ('clean', 'contrast_050', 'gain_070', 'blur_sigma_15', 'noise_std_003')
THRESHOLDS = (0.0, 0.7, 0.9, 0.95)


def perturb(images, condition):
    if condition not in CONDITIONS:
        raise ValueError('Unknown condition')
    if images.ndim != 4 or images.shape[1] != 3 or not torch.isfinite(images).all():
        raise ValueError('Expected finite NCHW RGB images')
    if condition == 'contrast_050':
        center = images.mean(dim=(-2, -1), keepdim=True)
        return ((images - center) * 0.5 + center).clamp(0, 1)
    if condition == 'gain_070':
        return images * 0.7
    if condition == 'blur_sigma_15':
        return gaussian_blur(images, [7, 7], [1.5, 1.5])
    if condition == 'noise_std_003':
        generator = torch.Generator(device=images.device).manual_seed(20261020)
        noise = torch.randn(images.shape, device=images.device, generator=generator)
        return (images + noise * 0.03).clamp(0, 1)
    return images


def triage_metrics(labels, probabilities, healthy, threshold):
    y, p = np.asarray(labels), np.asarray(probabilities)
    if (p.ndim != 2 or y.shape != (len(p),) or len(p) == 0
            or not np.isfinite(p).all() or np.any((p < 0) | (p > 1))
            or not np.allclose(p.sum(1), 1, atol=1e-5)
            or not 0 <= threshold <= 1 or not 0 <= healthy < p.shape[1]
            or np.any((y < 0) | (y >= p.shape[1]))):
        raise ValueError('Invalid labels, probabilities or threshold')
    predicted = p.argmax(1)
    accepted = p.max(1) >= threshold
    normal, defect = y == healthy, y != healthy
    n = int(accepted.sum())
    return {
        'threshold': threshold, 'samples': len(y), 'automated_candidates': n,
        'coverage': n / len(y), 'manual_review': int((~accepted).sum()),
        'manual_review_defects': int((~accepted & defect).sum()),
        'conditional_accuracy': float((predicted[accepted] == y[accepted]).mean()) if n else None,
        'healthy_samples': int(normal.sum()), 'defect_samples': int(defect.sum()),
        'healthy_defect_alert_candidates': int((accepted & normal & (predicted != healthy)).sum()),
        'defects_released_as_healthy': int((accepted & defect & (predicted == healthy)).sum()),
        'defect_alert_candidates': int((accepted & defect & (predicted != healthy)).sum()),
        'wrong_defect_type_candidates': int((accepted & defect & (predicted != healthy) & (predicted != y)).sum()),
    }


@torch.no_grad()
def infer(model, images, device, size, batch_size, flip_tta):
    result = []
    model.eval()
    for batch in images.split(batch_size):
        batch = batch.to(device)
        p = model(normalize(batch, size)).float().softmax(-1)
        if flip_tta:
            p = (p + model(normalize(batch.flip(-1), size)).float().softmax(-1)) / 2
        result.append(p.cpu().numpy())
    return np.concatenate(result)


def run(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    manifest = json.loads((args.data / 'manifest.json').read_text())
    checkpoint = torch.load(args.checkpoint, map_location='cpu', weights_only=True)
    with np.load(args.data / 'validation.npz', allow_pickle=False) as data:
        images = torch.from_numpy(data['images'].transpose(0, 3, 1, 2).copy()).float() / 255
        labels, classes, dates = data['labels'], data['classes'].tolist(), data['capture_date']
    if (checkpoint['archive_sha256'] != manifest['archive_sha256']
            or checkpoint['classes'] != classes or manifest['classes'] != classes):
        raise ValueError('Checkpoint/data protocol mismatch')
    device = torch.device(args.device)
    model, _, _ = build_model(checkpoint['architecture'], len(classes), pretrained=False)
    model.load_state_dict(checkpoint['state_dict'], strict=True)
    model.to(device)
    healthy = classes.index('healthy panel')
    args.output.mkdir(parents=True)
    report = {
        'protocol': 'Retrospective validation-only fixed stress/triage audit; no new holdout claim',
        'checkpoint_sha256': hashlib.sha256(args.checkpoint.read_bytes()).hexdigest(),
        'archive_sha256': manifest['archive_sha256'], 'classes': classes,
        'device': str(device), 'torch_version': torch.__version__,
        'threshold_policy': 'Fixed diagnostic grid; no threshold selected or calibrated',
        'test_status': 'not read', 'conditions': {},
        'limitations': ['Validation dates were repeatedly used for training/model selection.',
                        'Synthetic RGB perturbations are not calibrated thermal-sensor physics.',
                        'Softmax confidence is not a calibrated correctness probability.',
                        'All manual-review samples remain unresolved; they are not counted as healthy.',
                        'No verified independent plant/flight or production acceptance evidence.'],
    }
    for condition in CONDITIONS:
        transformed = perturb(images, condition)
        for tta in (False, True):
            key = condition + ('_flip_tta' if tta else '')
            probabilities = infer(model, transformed, device, checkpoint.get('input_size', 112), args.batch_size, tta)
            predicted = probabilities.argmax(1)
            metrics = {
                'macro_f1': float(f1_score(labels, predicted, labels=range(len(classes)), average='macro', zero_division=0)),
                'class_recall': recall_score(labels, predicted, labels=range(len(classes)), average=None, zero_division=0).tolist(),
                'triage': [triage_metrics(labels, probabilities, healthy, threshold) for threshold in THRESHOLDS],
                'by_date': {str(date): triage_metrics(labels[dates == date], probabilities[dates == date], healthy, 0.0)
                            for date in np.unique(dates)},
            }
            report['conditions'][key] = metrics
            np.save(args.output / (key + '_probabilities.npy'), probabilities)
            write_json(args.output / 'results.json', report)
            print(json.dumps({'condition': key, 'macro_f1': metrics['macro_f1'], **metrics['triage'][0]}), flush=True)
    report['status'] = 'complete'
    write_json(args.output / 'results.json', report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=Path('data/processed/pvf10_date_v1'))
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--device', choices=('cpu', 'cuda', 'mps'), default='cuda')
    parser.add_argument('--batch-size', type=int, default=64)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error('batch-size must be positive')
    run(args)
