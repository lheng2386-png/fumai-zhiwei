"""Fixed DC-input architecture ensembles; inner-only thresholds and contexts.

Uses matching completed runs. No outer-fitted weights, threshold, member or
confirmation-count selection. Historical folds are development diagnostics.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from sklearn.metrics import average_precision_score
from backtest_reunion import FOLDS, fold_data
from compare_reunion_context import select_context
from report_reunion_architecture_pair import compatible, cached
from compare_reunion_operating_points import constrained_threshold, observed_event_metrics
from train_reunion import choose_threshold, summarize_scores, day_alert_summary


def mean_scores(values):
    p=np.asarray(values)
    if p.ndim!=2 or len(p)==0 or not np.isfinite(p).all() or np.any((p<0)|(p>1)):
        raise ValueError('Expected finite member probabilities')
    return p.mean(0)


def inner_plan(y, scores):
    """No outer scores accepted: freeze both F1 and exploratory constrained thresholds."""
    return {'threshold':choose_threshold(y,scores),
            'tuning_ap':float(average_precision_score(y,scores)),
            'constrained':constrained_threshold(y,scores,.01,.9)}


def run(args):
    if args.output.exists():raise FileExistsError(args.output)
    sources={'rnn':args.rnn,'transformer':args.transformer}
    reports={k:json.loads((p/'results.json').read_text()) for k,p in sources.items()}
    compatible(reports['rnn'],reports['transformer'])
    manifest_hash=hashlib.sha256((args.prepared/'manifest.json').read_bytes()).hexdigest()
    for arch,report in reports.items():
        if (report['architecture']!=arch or report['feature_group']!='dc_relative'
                or report['prepared_manifest_sha256']!=manifest_hash or report['seeds']!=[2031,2032,2033]):raise ValueError('Unexpected source contract')
    args.output.mkdir(parents=True)
    result={'protocol':'Fixed RNN mean3, Transformer mean3 and equal mean6; inner-only thresholds',
            'source_sha256':{str(p/'results.json'):hashlib.sha256((p/'results.json').read_bytes()).hexdigest() for p in sources.values()},
            'prepared_manifest_sha256':manifest_hash,'folds':{},'prediction_sha256':{},
            'limitations':['Historical dates repeatedly viewed; no fresh independent evaluation.',
                           'Averaged softmax outputs are not calibrated probabilities.',
                           'FPR .01 and recall .9 are exploratory tuning constraints, not industrial requirements.',
                           'Six models increase inference cost; no new latency measurement.',
                           'No ensemble or threshold selected using outer results.']}
    for fold,*dates in FOLDS:
        _,tune,outer,scaler,_=fold_data(args.prepared,*dates)
        tuning={}; members={}
        for arch,source in sources.items():
            report=reports[arch]; f=report['folds'][fold]
            with np.load(source/fold/'normalization.npz',allow_pickle=False) as stored:
                if any(not np.array_equal(stored[k],scaler[k]) for k in ('mean','scale')):
                    raise ValueError('Fold scaler mismatch')
            for split,data in (('tuning',tune),('outer',outer)):
                if f['endpoint_sha256'][split]!=hashlib.sha256(data['end_time_ns'].tobytes()).hexdigest():
                    raise ValueError('Fold endpoint mismatch')
            scores={c:[f['runs'][str(c)][str(seed)]['tuning_ap'] for seed in report['seeds']]
                    for c in report['contexts_minutes']}
            context=select_context(scores)
            if context!=f['selected_context_minutes']:raise ValueError('Invalid inner context selection')
            member=[];values=[]
            for seed in report['seeds']:
                member.append((arch,context,seed))
                p=source/fold/f'context_{context}'/f'{arch}_seed{seed}_validation.npy'
                v=np.load(p,allow_pickle=False)
                if v.shape!=tune['y'].shape:raise ValueError('Tuning cache shape mismatch')
                values.append(v);result['prediction_sha256'][str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
            members[arch+'_mean3']=member;tuning[arch+'_mean3']=mean_scores(values)
        members['equal_mean6']=members['rnn_mean3']+members['transformer_mean3']
        tuning['equal_mean6']=mean_scores([tuning['rnn_mean3'],tuning['transformer_mean3']])
        plans={name:{'members':members[name],**inner_plan(tune['y'],score)} for name,score in tuning.items()}
        folder=args.output/fold;folder.mkdir()
        (folder/'inner_plan.json').write_text(json.dumps(plans,indent=2)+'\n')
        # No outer prediction files opened until every plan is persisted.
        evaluated={}
        for name,plan in plans.items():
            probabilities=[]
            for arch,context,seed in plan['members']:
                p=sources[arch]/fold/f'context_{context}'/f'{arch}_seed{seed}_outer_predictions.npz'
                y,v,t=cached(p,hashlib.sha256(outer['end_time_ns'].tobytes()).hexdigest())
                if not np.array_equal(y,outer['y']):raise ValueError('Outer labels mismatch')
                probabilities.append(v);result['prediction_sha256'][str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
            score=mean_scores(probabilities)
            evaluated[name]={'members':plan['members'],'tuning_ap':plan['tuning_ap'],'operating_points':{}}
            for mode,threshold in (('macro_f1',plan['threshold']),('constrained',plan['constrained']['threshold'] if plan['constrained'] else None)):
                if threshold is None:
                    evaluated[name]['operating_points'][mode]={'status':'infeasible_on_tuning'};continue
                evaluated[name]['operating_points'][mode]={'status':'scored','threshold':threshold,
                    'outer_ap':float(average_precision_score(y,score)),
                    'window_metrics':summarize_scores(y,score,threshold),
                    'day_metrics':day_alert_summary(outer,score,threshold),
                    'event_metrics':observed_event_metrics(y,score>=threshold,t)}
            np.savez_compressed(folder/(name+'_outer.npz'),scores=score,y=y,end_time_ns=t)
        result['folds'][fold]=evaluated
    (args.output/'results.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result['folds'],indent=2))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--prepared',type=Path,default=Path('data/processed/la_reunion_v1'))
    p.add_argument('--rnn',type=Path,required=True)
    p.add_argument('--transformer',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    run(p.parse_args())
