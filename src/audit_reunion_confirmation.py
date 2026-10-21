"""Causal confirmation tradeoffs with frozen inner-selected thresholds/contexts."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from backtest_reunion import FOLDS, fold_data
from compare_reunion_operating_points import observed_event_metrics
from reunion_ensemble import aggregate_alert_events
from train_reunion import day_alert_summary


def evaluate(y, scores, times, threshold, consecutive):
    y,scores,times=np.asarray(y),np.asarray(scores),np.asarray(times)
    if (y.ndim!=1 or len(y)==0 or scores.shape!=y.shape or times.shape!=y.shape
            or not set(np.unique(y)).issubset({0,1}) or not np.isfinite(scores).all()
            or np.any((scores<0)|(scores>1))):
        raise ValueError('Invalid confirmation audit arrays')
    rows=[{'status':'ready','fault_probability':float(p),
           'end_time_utc':pd.Timestamp(int(t),tz='UTC').isoformat()} for p,t in zip(scores,times)]
    alarms=np.array([r['event_alarm'] for r in aggregate_alert_events(rows,threshold,consecutive)])
    healthy=y==0; fault=y==1
    return {'normal_windows':int(healthy.sum()),'fault_windows':int(fault.sum()),
            'normal_false_alarms':int((healthy & alarms).sum()),
            'fault_windows_with_confirmed_alarm':int((fault & alarms).sum()),
            'fault_window_recall':float(alarms[fault].mean()) if fault.any() else None,
            'observed_events':observed_event_metrics(y,alarms,times),
            'day_metrics':day_alert_summary({'y':y,'end_time_ns':times},alarms.astype(float),.5)}


def run(args):
    if args.output.exists():raise FileExistsError(args.output)
    sources={name:Path(path) for name,path in args.sources}
    if len(sources)!=len(args.sources):raise ValueError('Duplicate source names')
    digest=hashlib.sha256((args.prepared/'manifest.json').read_bytes()).hexdigest()
    reports={name:json.loads((path/'results.json').read_text()) for name,path in sources.items()}
    for report in reports.values():
        if report['status']!='complete' or report['prepared_manifest_sha256']!=digest:
            raise ValueError('Source report contract mismatch')
    # All thresholds/contexts are copied from frozen inner selections, before caches open.
    plans={name:{fold:{'context_minutes':r['selected_context_minutes'],
                      'thresholds':{str(seed):r['runs'][str(r['selected_context_minutes'])][str(seed)]['tuning_threshold']
                                    for seed in reports[name]['seeds']}}
                 for fold,r in reports[name]['folds'].items()} for name in sources}
    args.output.mkdir(parents=True)
    (args.output/'frozen_plan.json').write_text(json.dumps({'plans':plans,'consecutive_windows':[1,2,3]},indent=2)+'\n')
    result={'protocol':'Causal 1/2/3 endpoint confirmation with frozen inner-only thresholds and contexts',
            'source_report_sha256':{n:hashlib.sha256((p/'results.json').read_bytes()).hexdigest() for n,p in sources.items()},
            'prepared_manifest_sha256':digest,'folds':{},'prediction_sha256':{},
            'held_out_status':'Only train.npz, training normalization and existing train-fold caches',
            'limitations':['Historical folds already viewed; no fresh holdout.',
                           'Confirmed alarms start only when enough consecutive 5-minute endpoints arrive.',
                           'Window recall decreases during confirmation delay; all fault windows remain in denominator.',
                           'Event times use sampled experimental labels, not work-order onset.',
                           'All rows have complete cached inputs; missing-data coverage not assessed here.',
                           'No confirmation policy or threshold is selected on outer scores.']}
    for fold,*dates in FOLDS:
        _,_,outer,_,_=fold_data(args.prepared,*dates)
        result['folds'][fold]={}
        for name,path in sources.items():
            report=reports[name]; plan=plans[name][fold]; context=plan['context_minutes']; arch=report['architecture']
            if report['folds'][fold]['endpoint_sha256']['outer'] != hashlib.sha256(outer['end_time_ns'].tobytes()).hexdigest():
                raise ValueError('Outer endpoint identity mismatch')
            runs={}
            for seed in report['seeds']:
                p=path/fold/f'context_{context}'/f'{arch}_seed{seed}_outer_predictions.npz'
                with np.load(p,allow_pickle=False) as cached:
                    if not np.array_equal(cached['y'],outer['y']) or not np.array_equal(cached['end_time_ns'],outer['end_time_ns']):
                        raise ValueError('Cached labels/endpoints mismatch')
                    scores=cached['scores']
                if scores.shape!=outer['y'].shape or not np.isfinite(scores).all() or np.any((scores<0)|(scores>1)):
                    raise ValueError('Invalid cached probabilities')
                result['prediction_sha256'][str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
                runs[str(seed)]={str(k):evaluate(outer['y'],scores,outer['end_time_ns'],plan['thresholds'][str(seed)],k) for k in (1,2,3)}
            result['folds'][fold][name]={'context_minutes':context,'runs':runs}
    (args.output/'results.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({fold:{name:{str(k):{'false_alarms':[r[str(k)]['normal_false_alarms'] for r in d['runs'].values()],
                'fault_recall':[r[str(k)]['fault_window_recall'] for r in d['runs'].values()],
                'missed_events':[r[str(k)]['observed_events']['missed_observed_events'] for r in d['runs'].values()]}
                for k in (1,2,3)} for name,d in groups.items()} for fold,groups in result['folds'].items()},indent=2))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--prepared',type=Path,default=Path('data/processed/la_reunion_v1'))
    p.add_argument('--source',nargs=2,action='append',dest='sources',required=True)
    p.add_argument('--output',type=Path,required=True)
    run(p.parse_args())
