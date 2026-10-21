"""Competition prototype: asset/time-aligned evidence review, not a fault oracle.

Upstream adapters decide model outcomes using their own versioned thresholds.
This policy never averages incompatible class/detection/time-series scores.
Only exact plant+asset identity is matched; no guessed panel/inverter mapping.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

POLICY_VERSION='competition-evidence-v1'
TASK_MODALITY={'timeseries':'telemetry','classification':'vision','detection':'vision'}


def utc_time(value):
    if not isinstance(value,str):raise ValueError('Expected ISO timestamp with timezone')
    try:stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError as error:raise ValueError('Invalid ISO timestamp') from error
    if stamp.tzinfo is None or stamp.utcoffset() is None:raise ValueError('Timestamp must include timezone')
    return stamp.astimezone(timezone.utc)


def validate(record):
    required=('evidence_id','plant_id','asset_id','observed_at','task','observation_id',
              'model_version','threshold_version','quality','decision','data_kind')
    if not isinstance(record,dict) or any(not isinstance(record.get(k),str) or not record[k].strip() for k in required):
        raise ValueError('Evidence requires nonempty provenance, identity, quality and decision fields')
    if record['data_kind'] not in ('public_research','site_observation','synthetic_demo'):
        raise ValueError('Unknown evidence data origin')
    if record['task'] not in TASK_MODALITY or record['quality'] not in ('ready','blocked'):
        raise ValueError('Unknown task/quality status')
    if record['decision'] not in ('anomaly','normal','uncertain'):
        raise ValueError('Unknown model decision')
    if record['quality']=='blocked' and not record.get('quality_reason'):
        raise ValueError('Blocked evidence needs an explanation')
    if 'score' in record and (isinstance(record['score'],bool) or not isinstance(record['score'],(int,float))
                              or not math.isfinite(record['score']) or not 0<=record['score']<=1):
        raise ValueError('Optional source model score must be finite in [0,1]')
    utc_time(record['observed_at'])


def assess(records,plant_id,asset_id,as_of,max_age_minutes=30,max_pair_gap_minutes=15):
    if (not isinstance(plant_id,str) or not plant_id.strip() or not isinstance(asset_id,str) or not asset_id.strip()
            or not math.isfinite(max_age_minutes) or max_age_minutes<=0
            or not math.isfinite(max_pair_gap_minutes) or not 0<=max_pair_gap_minutes<=max_age_minutes):
        raise ValueError('Invalid asset/time policy')
    now=utc_time(as_of);ids=set();eligible=[];excluded=[]
    for record in records:
        validate(record)
        identifier=record['evidence_id']
        if identifier in ids:raise ValueError('Duplicate evidence_id')
        ids.add(identifier);stamp=utc_time(record['observed_at'])
        age=(now-stamp).total_seconds()/60
        reason=None
        if (record['plant_id'],record['asset_id'])!=(plant_id,asset_id):reason='different_asset'
        elif age<0:reason='future_observation'
        elif age>max_age_minutes:reason='stale_observation'
        elif record['quality']!='ready':reason='quality_blocked'
        if reason:excluded.append({'evidence_id':identifier,'reason':reason})
        else:eligible.append(record)
    # Keep only the latest result for each model task; old predictions cannot
    # accumulate votes. Same-time differing records are ambiguous, not resolved by order.
    active=[]
    for task in TASK_MODALITY:
        candidates=[r for r in eligible if r['task']==task]
        if not candidates:continue
        latest=max(utc_time(r['observed_at']) for r in candidates)
        current=[r for r in candidates if utc_time(r['observed_at'])==latest]
        if len(current)>1:raise ValueError('Ambiguous latest evidence for a task')
        active.extend(current)
        excluded.extend({'evidence_id':r['evidence_id'],'reason':'superseded_same_task'}
                        for r in candidates if r is not current[0])
    comparable=[]
    if active:
        newest=max(utc_time(r['observed_at']) for r in active)
        for record in active:
            gap=(newest-utc_time(record['observed_at'])).total_seconds()/60
            if gap>max_pair_gap_minutes:excluded.append({'evidence_id':record['evidence_id'],'reason':'outside_pair_time_window'})
            else:comparable.append(record)
    anomalies=[r for r in comparable if r['decision']=='anomaly']
    normals=[r for r in comparable if r['decision']=='normal']
    uncertain=[r for r in comparable if r['decision']=='uncertain']
    paired=[(a,b) for a in anomalies for b in anomalies
            if TASK_MODALITY[a['task']]!=TASK_MODALITY[b['task']] and a['observation_id']!=b['observation_id']]
    if anomalies and normals:
        status,priority='conflict_review','high'
        reason='Usable model observations disagree; inspect time, device mapping and model evidence.'
    elif paired:
        status,priority='corroborated_review','high'
        reason='Separate telemetry and visual observations both indicate anomaly within the configured time window.'
    elif anomalies:
        status,priority='single_source_review','medium'
        reason='Anomaly evidence is available, but independent telemetry/vision support is incomplete.'
    elif uncertain or not comparable:
        status,priority='insufficient_evidence','data_check'
        reason='Evidence is unavailable, blocked or uncertain; do not label the asset healthy.'
    else:
        status,priority='normal_observed','routine'
        reason='Usable observations indicate normal; this does not rule out unobserved faults.'
    ordered=sorted(records,key=lambda r:r['evidence_id'])
    payload=json.dumps(ordered,sort_keys=True,ensure_ascii=False,separators=(',',':'))
    return {'policy_version':POLICY_VERSION,'plant_id':plant_id,'asset_id':asset_id,'as_of':as_of,
            'max_age_minutes':max_age_minutes,'max_pair_gap_minutes':max_pair_gap_minutes,
            'status':status,'review_priority':priority,'reason':reason,
            'demo_only':any(r['data_kind']=='synthetic_demo' for r in records),
            'supporting_evidence_ids':sorted(r['evidence_id'] for r in comparable),
            'anomaly_evidence_ids':sorted(r['evidence_id'] for r in anomalies),
            'normal_evidence_ids':sorted(r['evidence_id'] for r in normals),
            'uncertain_evidence_ids':sorted(r['evidence_id'] for r in uncertain),
            'excluded':sorted(excluded,key=lambda r:r['evidence_id']),
            'input_sha256':hashlib.sha256(payload.encode()).hexdigest(),
            'source_evidence':ordered,'automatic_control_action':None,
            'limitations':['Competition review policy, not calibrated fault probability or causal diagnosis.',
                           'Classification and detection of one photo are not independent modalities.',
                           'Cross-task corroboration needs verified same-asset mapping and timestamps.',
                           'No autonomous equipment control or automatic fault confirmation.']}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--input',type=Path,nargs='+',required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--plant-id',required=True);p.add_argument('--asset-id',required=True)
    p.add_argument('--as-of',required=True)
    p.add_argument('--max-age-minutes',type=float,default=30)
    p.add_argument('--max-pair-gap-minutes',type=float,default=15)
    args=p.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    records=[]
    for path in args.input:
        payload=json.loads(path.read_text())
        if isinstance(payload,dict) and payload.get('schema_version')=='competition-inference-v1':
            payload=payload['evidence']
        if not isinstance(payload,list):raise ValueError('Input must be evidence list or inference-v1 report')
        records.extend(payload)
    result=assess(records,args.plant_id,args.asset_id,args.as_of,args.max_age_minutes,args.max_pair_gap_minutes)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({k:result[k] for k in ('status','review_priority','supporting_evidence_ids')},ensure_ascii=False))

if __name__=='__main__':main()
