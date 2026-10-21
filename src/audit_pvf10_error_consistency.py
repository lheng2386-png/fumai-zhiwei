"""Locate repeatable validation errors across fixed paired-seed classifiers.

No fitting, threshold selection, test reads, or automatic relabelling. Unanimous
errors need inspection; they are not evidence that the published label is wrong.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from sklearn.metrics import confusion_matrix
from compare_pvf10_adaptation import SEEDS, probability
from train_pvf10_transfer import validation_identity


def summarize(y, probabilities, classes, photos, dates):
    y=np.asarray(y); p=np.asarray(probabilities)
    if (y.ndim!=1 or not len(y) or p.shape!=(3,len(y),len(classes))
            or np.asarray(photos).shape!=y.shape or np.asarray(dates).shape!=y.shape
            or len(set(classes))!=len(classes) or not np.isfinite(p).all()
            or np.any((p<0)|(p>1)) or not np.allclose(p.sum(2),1,atol=1e-5)
            or np.any((y<0)|(y>=len(classes))) or y.dtype.kind not in 'iu'):
        raise ValueError('Invalid paired-seed classification inputs')
    predicted=p.argmax(2); mistakes=predicted!=y[None,:]
    unanimous=(predicted==predicted[0:1,:]).all(0)
    persistent=mistakes.all(0); same_wrong=persistent & unanimous
    high_conf=(p.max(2)>=.9).all(0) & same_wrong
    healthy=classes.index('healthy panel')
    def group(mask):
        return {'samples':int(mask.sum()),
                'wrong_in_all_seeds':int((mask & persistent).sum()),
                'same_wrong_type_all_seeds':int((mask & same_wrong).sum()),
                'same_wrong_type_all_seeds_conf_ge_09':int((mask & high_conf).sum()),
                'defect_as_healthy_all_seeds':int((mask & (y!=healthy) & (predicted==healthy).all(0)).sum()),
                'healthy_as_defect_all_seeds':int((mask & (y==healthy) & (predicted!=healthy).all(0)).sum())}
    pairs=[]
    for truth in range(len(classes)):
        for guess in range(len(classes)):
            if truth==guess: continue
            mask=(y==truth)&(predicted==guess).all(0)
            if mask.any():pairs.append({'actual':classes[truth],'predicted':classes[guess],
                                       'samples':int(mask.sum()),'source_photos':len(set(np.asarray(photos)[mask]))})
    return {'overall':group(np.ones(len(y),bool)),
            'by_class':{name:group(y==i) for i,name in enumerate(classes)},
            'by_date':{str(date):group(np.asarray(dates)==date) for date in np.unique(dates)},
            'confusion_matrices':{str(seed):confusion_matrix(y,pred,labels=range(len(classes))).tolist()
                                  for seed,pred in zip(SEEDS,predicted)},
            'persistent_confusions':sorted(pairs,key=lambda row:(-row['samples'],row['actual'],row['predicted'])),
            'review_candidates':[{'validation_row':int(i),'source_photo':str(photos[i]),'date':str(dates[i]),
                                  'actual':classes[y[i]],'predictions':[classes[k] for k in predicted[:,i]],
                                  'maximum_softmax':[float(v) for v in p[:,i].max(1)],
                                  'same_wrong_type':bool(same_wrong[i])}
                                 for i in np.flatnonzero(persistent)]}


def run(args):
    if args.output.exists():raise FileExistsError(args.output)
    manifest=json.loads((args.data/'manifest.json').read_text())
    validation=args.data/'validation.npz'
    with np.load(validation,allow_pickle=False) as d:
        y,classes,photos,dates=d['labels'],d['classes'].tolist(),d['source_photo'],d['capture_date']
    if classes!=manifest['classes']:raise ValueError('Class map mismatch')
    order_hash=validation_identity(y,classes,photos,dates)
    file_hash=hashlib.sha256(validation.read_bytes()).hexdigest()
    probabilities=[]; digests={}; controls=[]; weight_hashes=[]; identity_verified=[]
    for seed in SEEDS:
        folder=args.run/f'seed{seed}'; report_path=folder/'results.json'
        report=json.loads(report_path.read_text())
        if (report['seed']!=seed or report['archive_sha256']!=manifest['archive_sha256']
                or report['test_status']!='not read' or report['architecture']!='convnext_tiny'
                or report['input_size']!=[224,224] or report.get('adapted_backbone_sha256')):
            raise ValueError('Unsupported reference protocol')
        for key,expected in (('validation_order_sha256',order_hash),('validation_npz_sha256',file_hash)):
            if key in report and report[key]!=expected:
                raise ValueError('Prediction cache validation identity mismatch')
        identity_verified.append(all(key in report for key in ('validation_order_sha256','validation_npz_sha256')))
        controls.append(report['training_controls']);weight_hashes.append(report['pretrained_sha256'])
        p=folder/'best_validation_probabilities.npy'
        probabilities.append(probability(p,len(y),len(classes)))
        for path in (p,report_path):digests[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
    if any(c!=controls[0] for c in controls) or len(set(weight_hashes))!=1:
        raise ValueError('Unmatched training controls or pretrained source')
    result={'protocol':'Three-seed validation error persistence audit; no fitting or label changes',
            'validation_sha256':file_hash,'validation_order_sha256':order_hash,
            'source_run_identity_verified':dict(zip(map(str,SEEDS),identity_verified)),
            'source_sha256':digests,'classes':classes,'seeds':list(SEEDS),
            'training_controls':controls[0],**summarize(y,np.asarray(probabilities),classes,photos,dates),
            'limitations':['Repeatedly viewed validation dates; no fresh site/flight generalization evidence.',
                           'Source photo groups are not verified plant or flight identifiers.',
                           'Existing probability caches have no original row identity hash; archive and preparation class map are checked.',
                           'Three seeds are correlated models, not independent evidence of annotation error.',
                           'Maximum softmax .9 is a fixed diagnostic, not calibrated confidence.',
                           'No labels changed and no review candidates used for training.']}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'overall':result['overall'],'by_date':result['by_date'],
                      'top_persistent_confusions':result['persistent_confusions'][:8]},indent=2))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--data',type=Path,default=Path('data/processed/pvf10_date_v1'))
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    run(p.parse_args())
