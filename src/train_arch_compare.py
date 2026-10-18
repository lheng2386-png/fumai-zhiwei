"""Validation-only architecture/window comparison on Costa2020."""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from sklearn.metrics import f1_score

from multiscale_model import MultiscalePV
from train_deep import prepare, seed_all
from train_multiscale import probabilities, write_json


class TransformerPV(nn.Module):
    """Small pre-norm Transformer encoder for short multivariate PV windows."""
    def __init__(self, channels=13, width=64, layers=3, max_steps=128, classes=5):
        super().__init__()
        self.input = nn.Linear(channels, width)
        self.position = nn.Parameter(torch.zeros(1, max_steps, width))
        nn.init.trunc_normal_(self.position, std=.02)
        block = nn.TransformerEncoderLayer(width, 4, width*4, .1, activation='gelu',
                                           batch_first=True, norm_first=True)
        self.encoder = nn.TransformerEncoder(block, layers, enable_nested_tensor=False)
        self.query = nn.Parameter(torch.randn(width)*.02)
        self.statistics = nn.Sequential(nn.Linear(channels*3, width), nn.LayerNorm(width), nn.GELU())
        self.classifier = nn.Sequential(nn.Linear(width*3, width*2), nn.GELU(), nn.Dropout(.2),
                                        nn.Linear(width*2, classes))

    def forward(self, x):
        z = self.input(x.transpose(1, 2)) + self.position[:, :x.shape[-1]]
        z = self.encoder(z)
        pooled = (z * self.query).sum(-1).softmax(-1).unsqueeze(-1)
        attended = (z * pooled).sum(1)
        stats = self.statistics(torch.cat([x.mean(-1), x.std(-1, correction=0), x[:, :, -1]], 1))
        return self.classifier(torch.cat([attended, z[:, -1], stats], 1))


def make_model(config):
    if config['model'] == 'transformer':
        return TransformerPV(width=config['width'], layers=config['layers'])
    return MultiscalePV(width=config['width'], depth=config['depth'])


def train_one(x, y, config, seed, output, mean, scale, args):
    seed_all(seed)
    name = config['name']
    tag = f'{name}_seed{seed}'
    model = make_model(config).cuda()
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-3)
    counts = torch.bincount(y[0], minlength=5).float().clamp(min=1)
    weights = counts.rsqrt(); weights /= weights.mean()
    best, best_epoch, stale, history = -1., 0, 0, []
    started = time.time()
    params = sum(p.numel() for p in model.parameters())
    print(json.dumps({'candidate': tag, 'parameters': params}), flush=True)
    for epoch in range(args.epochs):
        model.train()
        lr = args.lr * (.05 + .95*.5*(1+math.cos(math.pi*epoch/max(args.epochs-1, 1))))
        for group in optimizer.param_groups:
            group['lr'] = lr
        order = torch.randperm(len(x[0]), device='cuda')
        total = 0.
        for ids in order.split(args.batch_size):
            batch = x[0][ids]
            # Shared mild sensor-noise augmentation for a fair model comparison.
            batch = batch + torch.randn_like(batch)*.01
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast('cuda', dtype=torch.bfloat16):
                logits = model(batch).float()
                loss = F.cross_entropy(logits, y[0][ids], weight=weights)
            if not torch.isfinite(loss):
                raise FloatingPointError(tag)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.)
            optimizer.step()
            total += float(loss.detach())*len(ids)
        val = probabilities(model, x[1], args.batch_size)
        score = float(f1_score(y[1].cpu().numpy(), val.argmax(1), labels=range(5),
                               average='macro', zero_division=0))
        row = {'candidate': tag, 'epoch': epoch+1, 'train_loss': total/len(x[0]),
               'validation_macro_f1': score, 'seconds': time.time()-started}
        history.append(row); print(json.dumps(row), flush=True)
        if score > best:
            best, best_epoch, stale = score, epoch+1, 0
            torch.save({'state_dict': {k:v.cpu() for k,v in model.state_dict().items()},
                        'config': config, 'seed': seed, 'window': config['window'],
                        'mean': torch.from_numpy(mean), 'scale': torch.from_numpy(scale),
                        'validation_macro_f1': score}, output/f'{tag}.pt')
        else:
            stale += 1
        if stale >= args.patience:
            break
    result = {'tag': tag, 'config': config, 'seed': seed, 'parameters': params,
              'validation_macro_f1': best, 'best_epoch': best_epoch,
              'elapsed_seconds': time.time()-started, 'history': history}
    write_json(output/f'{tag}.json', result)
    return result


def run(args):
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA is required; refusing CPU fallback')
    args.output.mkdir(parents=True, exist_ok=False)
    torch.set_num_threads(8)
    torch.backends.cudnn.benchmark = True
    configs = [
        {'name':'cnn_ms_w24','model':'cnn','window':24,'width':64,'depth':3},
        {'name':'cnn_ms_w48','model':'cnn','window':48,'width':64,'depth':3},
        {'name':'transformer_w24','model':'transformer','window':24,'width':64,'layers':3},
    ]
    manifest = {'protocol':'Costa2020 ordered 75/5/20 split, 100-row purge, nonoverlapping windows, train-only scaling/preprocessing',
                'selection':'validation macro F1 only; test partition deliberately not evaluated',
                'architectures':configs,'seeds':args.seeds,'epochs':args.epochs,
                'patience':args.patience,'batch_size':args.batch_size,
                'gpu':torch.cuda.get_device_name(0),'torch':str(torch.__version__),
                'input_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in args.raw_dir.glob('dataset_*.mat')},
                'limitations':['single plant, 16-day Costa2020 data; no independent plant evaluation',
                               'same temporal split family used in prior model exploration']}
    write_json(args.output/'manifest.json', manifest)
    cache={}
    for window in (24,48):
        raw_x,raw_y,indices,mean,scale=prepare(args.raw_dir, window=window)
        x=[torch.from_numpy(a).cuda() for a in raw_x[:2]]
        y=[torch.from_numpy(a).long().cuda() for a in raw_y[:2]]
        cache[window]=(x,y,mean,scale,indices)
    records=[]
    for seed in args.seeds:
        for config in configs:
            x,y,mean,scale,indices=cache[config['window']]
            records.append(train_one(x,y,config,seed,args.output,mean,scale,args))
            write_json(args.output/'progress.json',records)
    summary={}
    for c in configs:
        members=[r for r in records if r['config']['name']==c['name']]
        summary[c['name']]={'mean_validation_macro_f1':float(np.mean([r['validation_macro_f1'] for r in members])),
                            'std_validation_macro_f1':float(np.std([r['validation_macro_f1'] for r in members])),
                            'mean_seconds':float(np.mean([r['elapsed_seconds'] for r in members])),
                            'best_seed':max(members,key=lambda r:r['validation_macro_f1'])['seed']}
    write_json(args.output/'summary.json',summary)
    print(json.dumps(summary),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--raw-dir',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--seeds',type=int,nargs='+',default=[2031,2032,2033])
    p.add_argument('--epochs',type=int,default=100)
    p.add_argument('--patience',type=int,default=15)
    p.add_argument('--batch-size',type=int,default=512)
    p.add_argument('--lr',type=float,default=8e-4)
    a=p.parse_args()
    run(a)
