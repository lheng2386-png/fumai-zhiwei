"""Paired resolution fine-tuning from the same frozen tile detector, validation only.

No automatic resume: interrupted YOLO last.pt states are retained for an explicit
resume decision. Output roots and existing branch directories are never reused.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

from train_pv_hsd_tiled_yolo import validate_data


def training_controls(resolution, epochs, seed):
    if resolution not in (640, 960) or epochs < 1:
        raise ValueError('Expected fixed 640/960 resolution comparison')
    return dict(imgsz=resolution, epochs=epochs, patience=epochs,
                batch=8, workers=4, seed=seed, deterministic=True,
                optimizer='AdamW', lr0=1e-4, lrf=1.0, cos_lr=False,
                warmup_epochs=1.0, weight_decay=.0005,
                mosaic=0.0, mixup=0.0, close_mosaic=0,
                cache=False, amp=True, plots=False, save=True, val=True)


def write_json(path, value):
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value, indent=2)+'\n'); temp.replace(path)


def run(args):
    if args.output.exists(): raise FileExistsError(args.output)
    manifest=validate_data(args.source,args.tiles)
    if not args.checkpoint.is_file(): raise FileNotFoundError(args.checkpoint)
    import torch
    import ultralytics
    from ultralytics import YOLO
    from evaluate_pv_hsd_tiled import run as evaluate
    if not torch.cuda.is_available(): raise RuntimeError('CUDA required; no CPU fallback')
    controls={str(size):training_controls(size,args.epochs,args.seed) for size in (640,960)}
    args.output.mkdir(parents=True)
    report={'protocol':'Paired tile fine-tuning and 2x2 train/inference resolution validation',
            'checkpoint_sha256':hashlib.sha256(args.checkpoint.read_bytes()).hexdigest(),
            'source_manifest_sha256':manifest['source_manifest_sha256'],
            'tile_manifest_sha256':hashlib.sha256((args.tiles/'manifest.json').read_bytes()).hexdigest(),
            'torch_version':torch.__version__,'ultralytics_version':ultralytics.__version__,
            'gpu_name':torch.cuda.get_device_name(0),'controls':controls,
            'test_status':'not read','status':'training','branches':{},
            'limitations':['Both branches fine-tune an already validation-selected checkpoint.',
                           'One seed; repeated validation, no fresh flight or production claim.',
                           'Larger input interpolates existing crops; no added sensor detail.',
                           'Validation checkpoint selection uses tile mAP; full-image metrics separate.',
                           'Training and inference size effects are reported with a 2x2 matrix.']}
    write_json(args.output/'protocol.json',report)
    started=time.monotonic()
    for size in (640,960):
        name=f'imgsz{size}'; branch=args.output/name
        model=YOLO(str(args.checkpoint))
        # Explicit initialization of a new optimizer; not resume of old training.
        model.train(data=str((args.tiles/'data.yaml').resolve()),
                    project=str(args.output.resolve()),name=name,exist_ok=False,
                    device=0,pretrained=True,**controls[str(size)])
        checkpoint=branch/'weights'/'best.pt'
        report['branches'][name]={'status':'evaluating','best_sha256':hashlib.sha256(checkpoint.read_bytes()).hexdigest()}
        write_json(args.output/'protocol.json',report)
        del model
        torch.cuda.empty_cache()
        for inference_size in (640,960):
            label=f'eval{inference_size}'
            evaluate(args.source,checkpoint,branch/f'{label}_summary.json','cuda',
                     inference_size,4,.001,.5,'tiled',branch/f'{label}_predictions.json')
        report['branches'][name]['status']='complete'
        write_json(args.output/'protocol.json',report)
    report.update(status='complete',elapsed_seconds=time.monotonic()-started)
    write_json(args.output/'protocol.json',report)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--source',type=Path,default=Path('data/processed/pv_hsd_grouped_v2'))
    p.add_argument('--tiles',type=Path,default=Path('data/processed/pv_hsd_tiles_384_v1'))
    p.add_argument('--checkpoint',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--epochs',type=int,default=20)
    p.add_argument('--seed',type=int,default=2026)
    run(p.parse_args())
