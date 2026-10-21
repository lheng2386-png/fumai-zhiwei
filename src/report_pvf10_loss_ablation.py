"""Report paired class-weight loss ablation on development validation only."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np

from compare_pvf10_adaptation import describe, probability, SEEDS


def check_pair(reference, candidate, archive, seed, ablation="class_weight"):
    if ablation not in ("class_weight", "mixup", "sampling"):
        raise ValueError("Unknown ablation")
    for r in (reference,candidate):
        if (r['archive_sha256']!=archive or r['seed']!=seed or r['test_status']!='not read'
                or r['architecture']!='convnext_tiny' or r['input_size']!=[224,224]
                or r.get('adapted_backbone_sha256')):
            raise ValueError('Incompatible loss-ablation source')
    allowed = {'class_weight':'class_weight_power','mixup':'mixup_alpha','sampling':'sampling'}[ablation]
    for key in ('epochs','patience','batch_size','backbone_lr','head_lr','ema_decay','augmentation','binary_loss_weight','mixup_alpha','class_weight_power','sampling'):
        if key == allowed: continue
        default=0. if key in ('binary_loss_weight','mixup_alpha') else .5 if key=='class_weight_power' else 'image_shuffle' if key=='sampling' else None
        if reference['training_controls'].get(key,default)!=candidate['training_controls'].get(key,default):
            raise ValueError(f'Training controls differ: {key}')
    expected = {'class_weight':(.5,0.),'mixup':(0.,.2),'sampling':('image_shuffle','photo_balanced')}[ablation]
    default = .5 if allowed == 'class_weight_power' else 'image_shuffle' if allowed=='sampling' else 0.
    if (reference['training_controls'].get(allowed, default) != expected[0]
            or candidate['training_controls'].get(allowed, default) != expected[1]
            or reference['pretrained_sha256'] != candidate['pretrained_sha256']):
        raise ValueError('Expected matched fixed ablation controls')


def run(args):
    if args.output.exists():raise FileExistsError(args.output)
    ablation=getattr(args,'ablation','class_weight')
    labels={'class_weight':('weighted','uniform'),'mixup':('unmixed','mixup'),'sampling':('image_shuffle','photo_balanced')}[ablation]
    manifest=json.loads((args.data/'manifest.json').read_text())
    with np.load(args.data/'validation.npz',allow_pickle=False) as d:
        y,classes,dates=d['labels'],d['classes'].tolist(),d['capture_date']
    if classes!=manifest['classes']:raise ValueError('Class-map mismatch')
    report={'protocol':f'Paired {ablation} ablation, fixed seeds and validation only',
            'archive_sha256':manifest['archive_sha256'],'test_status':'not read','pairs':{},'source_sha256':{},
            'limitations':['Validation dates repeatedly used in model development; no fresh holdout.',
                           'Class macro F1, healthy errors and defect recall must be considered together.',
                           'No automatic model promotion, industrial or SOTA claim.']}
    for seed in SEEDS:
        ref,can=args.reference/f'seed{seed}',args.candidate/f'seed{seed}'
        a,b=json.loads((ref/'results.json').read_text()),json.loads((can/'results.json').read_text())
        check_pair(a,b,manifest['archive_sha256'],seed,ablation)
        pair={}
        for label,path in zip(labels,(ref,can)):
            f=path/'best_validation_probabilities.npy'
            pair[label]=describe(y,probability(f,len(y),len(classes)),classes,dates)
            report['source_sha256'][str(f)]=hashlib.sha256(f.read_bytes()).hexdigest()
            f=path/'results.json';report['source_sha256'][str(f)]=hashlib.sha256(f.read_bytes()).hexdigest()
        pair['macro_f1_delta']=pair[labels[1]]['macro_f1']-pair[labels[0]]['macro_f1']
        report['pairs'][str(seed)]=pair
    report['mean_macro_f1']={name:float(np.mean([p[name]['macro_f1'] for p in report['pairs'].values()]))
                             for name in labels}
    report['positive_seeds']=sum(p['macro_f1_delta']>0 for p in report['pairs'].values())
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'mean_macro_f1':report['mean_macro_f1'],'positive_seeds':report['positive_seeds']},indent=2))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--ablation',choices=('class_weight','mixup','sampling'),default='class_weight')
    p.add_argument('--data',type=Path,default=Path('data/processed/pvf10_date_v1'))
    p.add_argument('--reference',type=Path,required=True)
    p.add_argument('--candidate',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    run(p.parse_args())
