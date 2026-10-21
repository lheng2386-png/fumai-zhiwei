"""Reproduce three real-data smoke inputs; these are not new test samples."""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
from PIL import Image
from backtest_reunion import FOLDS,fold_data
from prepare_reunion import FEATURES


def run(root,output):
    if output.exists():raise FileExistsError(output)
    # Read existing development/training sources only, never original held-out tests.
    with np.load(root/'data/processed/pvf10_date_v1/validation.npz',allow_pickle=False) as d:
        index=int(np.flatnonzero(d['labels']==d['classes'].tolist().index('hot cell'))[0])
        module=Image.fromarray(d['images'][index])
        provenance={'classification':{'source':'PVF-10 development validation','row':index,
            'source_photo':str(d['source_photo'][index]),'date':str(d['capture_date'][index]),
            'capture_time':'Unknown; any exact demo timestamp must be synthetic.'}}
    coco=json.loads((root/'data/processed/pv_hsd_grouped_v2/coco/instances_val.json').read_text())
    item=coco['images'][0]
    with Image.open(root/'data/processed/pv_hsd_grouped_v2/val/images'/item['file_name']) as im:
        inspection=im.convert('RGB')
    provenance['detection']={'source':'PV-HSD development validation','image_id':item['id'],
        'file_name':item['file_name'],'capture_time':'Unknown; any exact demo timestamp must be synthetic.'}
    name,*dates=FOLDS[0]
    training,_,_,scaler,_=fold_data(root/'data/processed/la_reunion_v1',*dates)
    index=int(np.flatnonzero(training['y']==1)[0])
    raw=training['x'][index]*scaler['scale'][:,None]+scaler['mean'][:,None]
    end=pd.Timestamp(int(training['end_time_ns'][index]),tz='UTC')
    frame=pd.DataFrame(raw.T,columns=FEATURES)
    frame['power_ratio']=frame['faulted_Pg']/(frame['control_Pg']+50.)
    frame['dc_current_ratio']=frame['faulted_Ia']/(frame['control_Ia']+.2)
    frame.insert(0,'time',pd.date_range(end=end,periods=30,freq='min').map(lambda t:t.isoformat()))
    provenance['timeseries']={'source':'La Reunion earlier training fold','row':index,'end_time':end.isoformat(),
        'note':'Reconstructed from prepared training window; not an evaluation of generalization.'}
    output.mkdir(parents=True)
    module.save(output/'module.png');inspection.save(output/'inspection.png')
    frame.to_csv(output/'telemetry.csv',index=False)
    (output/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('.'))
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();run(args.root,args.output)
