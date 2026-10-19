"""Train our own 12-class IR models on audited Raptor Maps images.

Compare random initialization and train-only masked image pretraining. The
grouped image test is not an independent-site deployment evaluation.
"""
import argparse
from contextlib import nullcontext
import json
import math
from pathlib import Path
import time
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
from train_deep import seed_all
from train_multiscale import probabilities, write_json


class InfraredPV(nn.Module):
    def __init__(self, classes=12):
        super().__init__()
        layers = []
        previous = 1
        for width in [32, 64, 128]:
            layers.extend([nn.Conv2d(previous, width, 3, padding=1), nn.GroupNorm(8, width), nn.GELU(),
                           nn.Conv2d(width, width, 3, padding=1), nn.GroupNorm(8, width), nn.GELU(), nn.AvgPool2d(2)])
            previous = width
        self.encoder = nn.Sequential(*layers)
        self.decoder = nn.Sequential(nn.Conv2d(128, 64, 3, padding=1), nn.GELU(), nn.Conv2d(64, 1, 1))
        self.classifier = nn.Sequential(nn.Flatten(), nn.Linear(128*5*3, 256), nn.GELU(),
                                        nn.Dropout(.3), nn.Linear(256, classes))

    def forward(self, x, reconstruct=False):
        z = self.encoder(x)
        if reconstruct:
            return F.interpolate(self.decoder(z), size=x.shape[-2:], mode='bilinear', align_corners=False)
        return self.classifier(z)


def augment(x):
    horizontal = torch.rand(len(x), 1, 1, 1, device=x.device) < .5
    vertical = torch.rand(len(x), 1, 1, 1, device=x.device) < .5
    return torch.where(vertical, torch.where(horizontal, x.flip(-1), x).flip(-2),
                       torch.where(horizontal, x.flip(-1), x))


def train(args):
    device = torch.device(args.device if args.device != 'auto' else
                          'cuda' if torch.cuda.is_available() else
                          'mps' if torch.backends.mps.is_available() else 'cpu')
    if device.type == 'cpu':
        raise RuntimeError('A GPU is required for full infrared training')
    args.output.mkdir(parents=True, exist_ok=False)
    torch.set_num_threads(8)
    if device.type == 'cuda':
        torch.backends.cudnn.benchmark = True
    data = np.load(args.data, allow_pickle=False)
    classes = data['classes'].tolist()
    split = data['split']
    train_pixels = data['images'][split == 'train'].astype(np.float32)/255
    mean = float(train_pixels.mean())
    std = max(float(train_pixels.std()), 1e-5)
    x = [torch.from_numpy((data['images'][split == s].astype(np.float32)/255-mean)/std)[:, None].to(device)
         for s in ['train', 'validation']]
    y = [torch.from_numpy(data['labels'][split == s]).long().to(device) for s in ['train', 'validation']]
    manifest = {'source': 'https://github.com/RaptorMaps/InfraredSolarModules',
                'task': '12-class image classification, not detection or temperature estimation',
                'architecture': 'InfraredPV, trained from random initialization with optional own-domain masked pretraining',
                'parameters': sum(p.numel() for p in InfraredPV(len(classes)).parameters()),
                'classes': classes, 'normalization': {'mean': mean, 'std': std, 'fit': 'train only'},
                'seeds': args.seeds, 'epochs': args.epochs, 'pretrain_epochs': args.pretrain_epochs,
                'validation_only': args.validation_only,
                'patience': args.patience, 'batch_size': args.batch_size,
                'selection': 'Mean validation macro F1 chooses scratch/pretrained; validation selects single seed or three-seed mean.',
                'torch': str(torch.__version__), 'device': str(device),
                'gpu': torch.cuda.get_device_name(0) if device.type == 'cuda' else 'Apple MPS',
                'limitations': ['Exact pixel duplicates grouped; near duplicates not fully ruled out.',
                                'No independent plant/module/flight identifiers verified.',
                                'Low-resolution grayscale crops do not represent calibrated thermography.']}
    write_json(args.output/'manifest.json', manifest)
    records = []
    val_predictions = {}
    counts = torch.bincount(y[0], minlength=len(classes)).float()
    weights = counts.clamp(min=1).rsqrt()
    weights /= weights.mean()
    for seed in args.seeds:
        for pretrained in [False, True]:
            tag = f"{'pretrained' if pretrained else 'scratch'}_seed{seed}"
            seed_all(seed)
            model = InfraredPV(len(classes)).to(device)
            best, best_epoch = -1., 0
            history = []
            started = time.time()
            for stage, epochs in [('pretrain', args.pretrain_epochs if pretrained else 0), ('finetune', args.epochs)]:
                optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-3)
                for epoch in range(epochs):
                    model.train()
                    lr = 1e-5 + .5*(1e-3-1e-5)*(1+math.cos(math.pi*epoch/max(epochs-1, 1)))
                    for group in optimizer.param_groups:
                        group['lr'] = lr
                    loss_sum = torch.zeros((), device=device)
                    for indices in torch.randperm(len(x[0]), device=device).split(args.batch_size):
                        batch = augment(x[0][indices])
                        optimizer.zero_grad(set_to_none=True)
                        context = (torch.autocast('cuda', dtype=torch.bfloat16)
                                   if device.type == 'cuda' else nullcontext())
                        with context:
                            if stage == 'pretrain':
                                mask = F.interpolate((torch.rand(len(batch), 1, 8, 6, device=device) < .3).float(),
                                                     size=batch.shape[-2:], mode='nearest').bool()
                                prediction = model(batch.masked_fill(mask, 0), reconstruct=True)
                                loss = F.smooth_l1_loss(prediction[mask], batch[mask])
                            else:
                                loss = F.cross_entropy(model(batch).float(), y[0][indices], weight=weights)
                        if not torch.isfinite(loss):
                            raise FloatingPointError(tag)
                        loss.backward()
                        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.)
                        optimizer.step()
                        loss_sum += loss.detach()*len(indices)
                    row = {'candidate': tag, 'stage': stage, 'epoch': epoch+1,
                           'loss': float(loss_sum/len(x[0])), 'seconds': time.time()-started}
                    if stage == 'finetune':
                        val = probabilities(model, x[1], args.batch_size)
                        score = float(f1_score(y[1].cpu().numpy(), val.argmax(1), labels=range(len(classes)), average='macro', zero_division=0))
                        row['validation_macro_f1'] = score
                        if score > best:
                            best, best_epoch = score, epoch+1
                            val_predictions[tag] = val
                            torch.save({'state_dict': {k: v.cpu() for k, v in model.state_dict().items()},
                                        'classes': classes, 'mean': mean, 'std': std, 'seed': seed,
                                        'pretrained': pretrained, 'validation_macro_f1': score}, args.output/f'{tag}.pt')
                        if epoch+1-best_epoch >= args.patience:
                            history.append(row)
                            print(json.dumps(row), flush=True)
                            break
                    history.append(row)
                    print(json.dumps(row), flush=True)
            record = {'tag': tag, 'pretrained': pretrained, 'seed': seed,
                      'validation_macro_f1': best, 'best_epoch': best_epoch, 'history': history}
            write_json(args.output/f'{tag}.json', record)
            records.append(record)
            write_json(args.output/'progress.json', records)
    recipe_scores = {str(flag): float(np.mean([r['validation_macro_f1'] for r in records if r['pretrained'] == flag]))
                     for flag in [False, True]}
    recipe = max([False, True], key=lambda flag: recipe_scores[str(flag)])
    eligible = [r for r in records if r['pretrained'] == recipe]
    single = max(eligible, key=lambda r: r['validation_macro_f1'])
    tags = [r['tag'] for r in eligible]
    score = float(f1_score(y[1].cpu().numpy(), np.mean([val_predictions[tag] for tag in tags], axis=0).argmax(1),
                          labels=range(len(classes)), average='macro', zero_division=0))
    selected = tags if score > single['validation_macro_f1'] else [single['tag']]
    selection = {'recipe_validation_macro_f1': recipe_scores, 'pretrained': recipe, 'members': selected,
                 'ensemble_validation_macro_f1': score}
    write_json(args.output/'selection.json', selection)
    if args.validation_only:
        print(json.dumps(selection), flush=True)
        return selection
    test_x = torch.from_numpy((data['images'][split == 'test'].astype(np.float32)/255-mean)/std)[:, None].to(device)
    outputs = []
    for tag in selected:
        model = InfraredPV(len(classes)).to(device)
        model.load_state_dict(torch.load(args.output/f'{tag}.pt', map_location='cpu', weights_only=True)['state_dict'])
        outputs.append(probabilities(model, test_x, args.batch_size))
    predicted = np.mean(outputs, axis=0).argmax(1)
    truth = data['labels'][split == 'test']
    result = {**selection, 'accuracy': float(accuracy_score(truth, predicted)),
              'macro_f1': float(f1_score(truth, predicted, labels=range(len(classes)), average='macro', zero_division=0)),
              'classification_report': classification_report(truth, predicted, labels=range(len(classes)), target_names=classes, output_dict=True, zero_division=0),
              'confusion_matrix': confusion_matrix(truth, predicted, labels=range(len(classes))).tolist()}
    write_json(args.output/'results.json', result)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--epochs', type=int, default=80)
    parser.add_argument('--pretrain-epochs', type=int, default=40)
    parser.add_argument('--patience', type=int, default=12)
    parser.add_argument('--batch-size', type=int, default=256)
    parser.add_argument('--device', choices=('auto', 'cuda', 'mps'), default='auto')
    parser.add_argument('--validation-only', action='store_true')
    parser.add_argument('--seeds', type=int, nargs='+', default=[2026, 2027, 2028])
    args = parser.parse_args()
    if min(args.epochs, args.pretrain_epochs, args.patience, args.batch_size) < 1:
        parser.error('Training counts must be positive')
    train(args)
