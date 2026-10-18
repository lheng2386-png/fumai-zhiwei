"""Bounded CUDA sweep. Choose architecture/ensemble using validation only.

Uses the existing, previously reported 75/5/20 benchmark; this is not a fresh
external test set. No independent event or plant generalization is asserted.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np
import torch
from torch.nn import functional as F
from sklearn.metrics import f1_score
from deep_model import FEATURES
from multiscale_model import MultiscalePV
from train_deep import prepare, seed_all, metrics


def write_json(path, obj):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(obj, indent=2, ensure_ascii=False))
    temporary.replace(path)


@torch.no_grad()
def probabilities(model, x, batch_size):
    model.eval()
    return torch.cat([model(batch).float().softmax(-1)
                      for batch in x.split(batch_size)]).cpu().numpy()


def masked_input(x):
    # Contiguous missing spans and missing channels; no unrealistic label augmentation.
    start = torch.randint(x.shape[-1], (len(x), 1, 1), device=x.device)
    position = torch.arange(x.shape[-1], device=x.device)[None, None, :]
    span = ((position-start) % x.shape[-1]) < max(1, x.shape[-1]//4)
    channels = torch.rand(len(x), x.shape[1], 1, device=x.device) < .1
    mask = (span | channels).expand_as(x)
    return x.masked_fill(mask, 0), mask


def train_candidate(x, y, config, seed, output, mean, scale, args):
    seed_all(seed)
    tag = f"{config['name']}_seed{seed}"
    model = MultiscalePV(width=config['width'], depth=config['depth']).cuda()
    parameter_count = sum(p.numel() for p in model.parameters())
    print(json.dumps({'candidate': tag, 'parameters': parameter_count}), flush=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-3)
    history = []
    started = time.time()
    counts = torch.bincount(y[0], minlength=5).float()
    weights = counts.clamp(min=1).rsqrt()
    weights /= weights.mean()
    best = -1.
    best_epoch = 0
    checkpoint = output/f'{tag}.pt'
    best_val = None
    for stage, epochs in [('pretrain', args.pretrain_epochs if config['pretrain'] else 0),
                          ('finetune', args.epochs)]:
        if epochs == 0:
            continue
        optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-3)
        for epoch in range(epochs):
            model.train()
            lr = 1e-5 + .5*(1e-3-1e-5)*(1+math.cos(math.pi*epoch/max(epochs-1, 1)))
            for group in optimizer.param_groups:
                group['lr'] = lr
            permutation = torch.randperm(len(x[0]), device='cuda')
            total = torch.zeros((), device='cuda')
            for indices in permutation.split(args.batch_size):
                batch = x[0][indices]
                optimizer.zero_grad(set_to_none=True)
                with torch.autocast('cuda', dtype=torch.bfloat16):
                    if stage == 'pretrain':
                        corrupted, mask = masked_input(batch)
                        error = F.smooth_l1_loss(model(corrupted, reconstruct=True), batch, reduction='none')
                        loss = error[mask].mean()
                    else:
                        logits = model(batch)
                        target = y[0][indices]
                        loss_vector = F.cross_entropy(logits.float(), target, weight=weights, reduction='none')
                        if config['focal']:
                            pt = logits.float().softmax(-1).gather(1, target[:, None]).squeeze(1)
                            loss_vector = loss_vector*(1-pt)
                        loss = loss_vector.mean()
                if not torch.isfinite(loss):
                    raise FloatingPointError(f'Non-finite loss in {tag}')
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.)
                optimizer.step()
                total += loss.detach()*len(indices)
            row = {'candidate': tag, 'stage': stage, 'epoch': epoch+1,
                   'loss': float(total/len(x[0])), 'seconds': time.time()-started}
            if stage == 'finetune':
                val = probabilities(model, x[1], args.batch_size)
                score = float(f1_score(y[1].cpu().numpy(), val.argmax(1), labels=range(5), average='macro', zero_division=0))
                row['validation_macro_f1'] = score
                if score > best:
                    best = score
                    best_epoch = epoch+1
                    best_val = val
                    torch.save({'state_dict': {k: v.cpu() for k, v in model.state_dict().items()},
                                'config': config, 'seed': seed, 'features': FEATURES, 'window': 24,
                                'mean': torch.from_numpy(mean), 'scale': torch.from_numpy(scale),
                                'validation_macro_f1': best}, checkpoint)
                if epoch+1-best_epoch >= args.patience:
                    history.append(row)
                    print(json.dumps(row), flush=True)
                    break
            history.append(row)
            print(json.dumps(row), flush=True)
    np.save(output/f'{tag}_validation.npy', best_val)
    record = {'tag': tag, 'config': config, 'seed': seed, 'parameters': parameter_count,
              'validation_macro_f1': best, 'best_epoch': best_epoch,
              'elapsed_seconds': time.time()-started, 'history': history}
    write_json(output/f'{tag}.json', record)
    return record, best_val


def run(args):
    if not torch.cuda.is_available():
        raise RuntimeError('This GPU experiment requires CUDA; refusing CPU fallback')
    args.output.mkdir(parents=True, exist_ok=False)
    torch.set_num_threads(8)
    torch.backends.cudnn.benchmark = True
    raw_x, raw_y, indices, mean, scale = prepare(args.raw_dir)
    # Held-out windows remain on CPU until all model selection is complete.
    x = [torch.from_numpy(z).cuda() for z in raw_x[:2]]
    y = [torch.from_numpy(z).long().cuda() for z in raw_y[:2]]
    configs = [
        {'name': 'ms64_scratch', 'width': 64, 'depth': 3, 'pretrain': False, 'focal': False},
        {'name': 'ms64_pretrained', 'width': 64, 'depth': 3, 'pretrain': True, 'focal': False},
        {'name': 'ms128_pretrained_focal', 'width': 128, 'depth': 4, 'pretrain': True, 'focal': True},
    ]
    manifest = {'protocol': '75/5/20 original ordered rows, 100-row purge, nonoverlapping 24-row windows; train-only scaler and pretraining',
                'test_status': 'Previously reported benchmark; not a new untouched external test set.',
                'selection': 'Best mean validation macro F1 recipe across all three seeds, then validation-only choice of single seed or equal-weight three-seed ensemble.',
                'configs': configs, 'seeds': args.seeds, 'epochs': args.epochs,
                'pretrain_epochs': args.pretrain_epochs, 'patience': args.patience,
                'batch_size': args.batch_size, 'torch': str(torch.__version__), 'cuda': torch.version.cuda,
                'gpu': torch.cuda.get_device_name(0), 'split_windows': [len(z) for z in raw_x],
                'input_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.raw_dir.glob('dataset_*.mat')},
                'limitations': ['No verified independent event/day identifiers or cross-plant holdout.',
                                'Only 51 short-circuit training windows; validation has very few independent faults.',
                                'More parameters or GPU time do not establish better generalization or SOTA.']}
    write_json(args.output/'manifest.json', manifest)
    np.savez_compressed(args.output/'split_endpoints.npz', **{name: idx[:, [0, -1]] for name, idx in zip(['train', 'validation', 'test'], indices)})
    records = []
    vals = {}
    for seed in args.seeds:
        for config in configs:
            record, val = train_candidate(x, y, config, seed, args.output, mean, scale, args)
            records.append(record)
            vals[record['tag']] = val
            write_json(args.output/'progress.json', records)
    recipe_scores = {c['name']: float(np.mean([r['validation_macro_f1'] for r in records if r['config']['name'] == c['name']])) for c in configs}
    recipe = max(recipe_scores, key=recipe_scores.get)
    eligible = [r for r in records if r['config']['name'] == recipe]
    single = max(eligible, key=lambda r: r['validation_macro_f1'])
    tags = [r['tag'] for r in eligible]
    ensemble_val = np.mean([vals[tag] for tag in tags], axis=0)
    ensemble_score = float(f1_score(raw_y[1], ensemble_val.argmax(1), labels=range(5), average='macro', zero_division=0))
    selected = tags if ensemble_score > single['validation_macro_f1'] else [single['tag']]
    selection = {'recipe_mean_validation_macro_f1': recipe_scores, 'selected_recipe': recipe,
                 'ensemble_validation_macro_f1': ensemble_score, 'selected_members': selected}
    # Persist selection before moving test features to GPU or scoring test labels.
    write_json(args.output/'selection.json', selection)
    test_x = torch.from_numpy(raw_x[2]).cuda()
    outputs = []
    for tag in selected:
        checkpoint = torch.load(args.output/f'{tag}.pt', map_location='cpu', weights_only=True)
        config = checkpoint['config']
        model = MultiscalePV(width=config['width'], depth=config['depth']).cuda()
        model.load_state_dict(checkpoint['state_dict'])
        outputs.append(probabilities(model, test_x, args.batch_size))
    result = {**selection, 'test_results': metrics(raw_y[2], np.mean(outputs, axis=0).argmax(1))}
    write_json(args.output/'results.json', result)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--raw-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--epochs', type=int, default=100)
    parser.add_argument('--pretrain-epochs', type=int, default=40)
    parser.add_argument('--patience', type=int, default=15)
    parser.add_argument('--batch-size', type=int, default=512)
    parser.add_argument('--seeds', type=int, nargs='+', default=[2026, 2027, 2028])
    args = parser.parse_args()
    if min(args.epochs, args.pretrain_epochs, args.patience, args.batch_size) < 1:
        parser.error('Training counts must be positive')
    run(args)
