"""Audit the public TECNALIA release before any representation/fault modelling.

Reads only explicitly named CSV members through bsdtar stdout, never extracts
paths or loads executable/pickled content. No fault labels are invented.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import numpy as np
import pandas as pd

EXPECTED_MD5='15daed06b32f5debc9f3d81f0afe31d4'
MODULES=('Atersa','JaSolar3','NingboSolar','Photowatt','TrinaSolar')


def describe(frame):
    if 'Fecha' not in frame:raise ValueError('Missing timestamp column')
    times=pd.to_datetime(frame['Fecha'],errors='coerce')
    numeric=frame.drop(columns='Fecha').apply(pd.to_numeric,errors='coerce')
    sentinel=numeric.eq(-9999.)
    cleaned=numeric.mask(sentinel)
    steps=times.diff().dt.total_seconds().div(60)
    positive=steps[steps>0]
    finite={column:int(np.isfinite(cleaned[column]).sum()) for column in cleaned}
    nulls={column:int(cleaned[column].isna().sum()) for column in cleaned}
    result={'rows':len(frame),'columns':list(frame.columns),
            'first_timestamp':str(times.min()),'last_timestamp':str(times.max()),
            'timestamp_timezone':str(times.dt.tz) if times.dt.tz is not None else 'not specified; not inferred',
            'invalid_timestamps':int(times.isna().sum()),
            'duplicate_timestamps':int(times.dropna().duplicated().sum()),
            'nonpositive_steps':int((steps<=0).sum()),
            'positive_step_minutes_counts':{str(k):int(v) for k,v in positive.value_counts().head(10).items()},
            'median_positive_step_minutes':float(positive.median()) if len(positive) else None,
            'sentinel_minus9999_by_column':{k:int(v) for k,v in sentinel.sum().items() if v},
            'finite_by_column':finite,'missing_after_sentinel_by_column':nulls,
            'entirely_missing_columns':[k for k,v in finite.items() if not v],
            'available_label_columns':[k for k in frame if any(w in k.lower() for w in ('fault','label','failure','class'))]}
    required=('Vmpp(V)','Impp(A)','Pmpp (W)','Front GPOA (W/m²)')
    if all(k in cleaned for k in required):
        v,i,p,g=(cleaned[k] for k in required)
        valid=np.isfinite(v)&np.isfinite(i)&np.isfinite(p)
        result['power_consistency']={'finite_rows':int(valid.sum()),
            'rows_abs_P_minus_VI_above_1W':int((valid & ((p-v*i).abs()>1.)).sum()),
            'daylight_proxy_front_poa_above_200Wm2':int((g>200).sum()),
            'daylight_proxy_nonpositive_power':int(((g>200)&(p<=0)).sum())}
    return result


def run(args):
    if args.output.exists():raise FileExistsError(args.output)
    raw=args.archive.read_bytes()
    if hashlib.md5(raw).hexdigest()!=EXPECTED_MD5:raise ValueError('Archive differs from pinned public release')
    def member(path):
        return subprocess.check_output([args.bsdtar,'-xOf',str(args.archive),path])
    result={'source':'https://zenodo.org/records/18306410','license':'CC BY 4.0, per publisher metadata',
            'archive_md5':EXPECTED_MD5,'archive_sha256':hashlib.sha256(raw).hexdigest(),'modules':{},
            'limitations':['No timezone or fault onset inferred from filenames or model predictions.',
                           'Module identity/capacity can confound fault classification.',
                           'Published description includes both naturally degraded and induced-fault modules.',
                           'No split, labels, imputation, preprocessing fit or model training performed.']}
    for name in MODULES:
        path=f'TECNALIA/data_{name}/data_{name}.csv'
        content=member(path)
        frame=pd.read_csv(io.BytesIO(content),sep=';',encoding='utf-8-sig')
        result['modules'][name]={'member_sha256':hashlib.sha256(content).hexdigest(),**describe(frame)}
    content=member('TECNALIA/Electric_information_TECNALIA.csv')
    info=pd.read_csv(io.BytesIO(content),sep=';',encoding='utf-8-sig')
    result['electrical_metadata']={'rows':len(info),'module_names':info['Name'].tolist(),
        'declared_interval_minutes':info['Time Interval (min)'].tolist(),
        'declared_first_timestamps':info['First Timestamp (DD/MM/AAAA)'].tolist(),
        'declared_last_timestamps':info['Last Timestamp (DD/MM/AAAA)'].tolist()}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:{key:value for key,value in v.items() if key in ('rows','first_timestamp','last_timestamp',
        'median_positive_step_minutes','duplicate_timestamps','available_label_columns','sentinel_minus9999_by_column')}
        for k,v in result['modules'].items()},indent=2))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--bsdtar',default='bsdtar')
    run(p.parse_args())
