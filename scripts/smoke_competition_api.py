"""Real HTTP smoke checks against an already running local competition API."""
import argparse
import json
from pathlib import Path
import httpx


def run(args):
    if args.output.exists():raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    def save(name,data):(args.output/(name+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    evidence=[]
    with httpx.Client(base_url=args.url,timeout=120) as client:
        r=client.get('/health');r.raise_for_status();save('health',r.json())
        for task,name,plant,asset in [('timeseries','telemetry.csv','reunion-public','paired-inverter'),
             ('classification','module.png','pvf10-public','unverified-module'),
             ('detection','inspection.png','pvhsd-public','unverified-array')]:
            data={'plant_id':plant,'asset_id':asset,'observation_id':task+'-http-smoke',
                  'data_kind':'public_research' if task=='timeseries' else 'synthetic_demo'}
            if task!='timeseries':data['observed_at']='2026-10-21T10:00:00Z'
            with (args.inputs/name).open('rb') as source:
                r=client.post('/v1/infer/'+task,data=data,files={'file':(name,source)})
            r.raise_for_status();result=r.json();save(task,result);evidence.extend(result['evidence'])
            print(task,r.status_code,[e['decision'] for e in result['evidence']],flush=True)
        r=client.post('/v1/review',json={'evidence':evidence,'plant_id':'pvf10-public',
            'asset_id':'unverified-module','as_of':'2026-10-21T10:10:00Z'})
        r.raise_for_status();result=r.json();save('review',result)
        if result['status'] not in ('single_source_review','normal_observed','insufficient_evidence'):
            raise AssertionError('Different public sources must not be fused as corroborated observations')
        if len(result['excluded'])!=2 or any(e['reason']!='different_asset' for e in result['excluded']):
            raise AssertionError('Public-source identity isolation failed')
        print('review',result['status'],'different assets excluded',flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--url',default='http://127.0.0.1:8770')
    p.add_argument('--inputs',type=Path,default=Path('reports/competition_real_inputs_v1'))
    p.add_argument('--output',type=Path,required=True)
    run(p.parse_args())
