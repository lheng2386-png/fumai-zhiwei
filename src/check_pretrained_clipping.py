"""Run RDTools' bundled pretrained clipping model on supplied site data."""
import argparse,json
from pathlib import Path
import pandas as pd
import rdtools
from threadpoolctl import threadpool_limits

def main():
 p=argparse.ArgumentParser();p.add_argument('--data-dir',type=Path,default=Path('data/input'));a=p.parse_args()
 root=Path(__file__).resolve().parents[1]
 d=pd.read_csv(a.data_dir/'2107_electrical_data.csv',usecols=['measured_on','inv_01_ac_power_inv_149583']);d['measured_on']=pd.to_datetime(d.measured_on)
 d=d[d.measured_on.between('2023-07-01','2023-07-31 23:59:59')].set_index('measured_on').asfreq('5min')
 d.index=d.index.tz_localize('America/Los_Angeles',ambiguous='raise',nonexistent='raise')
 print('Clipping input',len(d),flush=True)
 with threadpool_limits(limits=2): mask=rdtools.filtering.xgboost_clip_filter(d.iloc[:,0],mounting_type='fixed')
 out=pd.DataFrame({'local_time':d.index.astype(str),'power_kw':d.iloc[:,0].values,'candidate_clipping':(~mask).values})
 out.to_csv(root/'reports/rdtools_clipping_july2023.csv',index=False)
 r={'tool':'rdtools.filtering.xgboost_clip_filter','rdtools_version':rdtools.__version__,'data':'2107 inverter1 July2023 local wall-clock','samples':len(d),'candidate_clipping_samples':int((~mask).sum()),'status':'pretrained model successfully inferred on supplied data','classification_accuracy':None,'limitation':'clipping can be normal rated-power limitation; no supplied clipping ground truth'}
 (root/'reports/pretrained_clipping_check.json').write_text(json.dumps(r,indent=2));print(r)
if __name__=='__main__':main()
