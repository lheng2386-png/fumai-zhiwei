"""Reproducible CPU baselines. Raw files are read only. No random time splitting."""
from pathlib import Path
import argparse, json, hashlib, re, time
import numpy as np
import pandas as pd
from scipy.io import loadmat
from sklearn.ensemble import HistGradientBoostingRegressor, ExtraTreesClassifier
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score, classification_report, confusion_matrix, accuracy_score, f1_score
from threadpoolctl import threadpool_limits
import joblib
from power_data import load_power_frame

ROOT=Path(__file__).resolve().parents[1]
REPORTS=ROOT/'reports'; MODELS=ROOT/'models'
for p in (REPORTS,MODELS): p.mkdir(exist_ok=True,parents=True)

def save(name,obj):
 (REPORTS/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str))

def audit(data):
 info=[]
 for p in sorted(data.glob('*')):
  if p.suffix not in ('.csv','.json','.docx'): continue
  h=hashlib.sha256()
  with p.open('rb') as f:
   for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
  item={'file':str(p),'bytes':p.stat().st_size,'sha256':h.hexdigest()}
  if p.suffix=='.csv':
   d=pd.read_csv(p,low_memory=False)
   item.update(rows=len(d),columns=len(d.columns),column_names=list(d.columns),missing_fraction=d.isna().mean().round(5).to_dict())
   t='measured_on' if 'measured_on' in d else 'read_ts' if 'read_ts' in d else 'recorded' if 'recorded' in d else None
   if t:
    item.update(time_column=t,start=str(d[t].min()),end=str(d[t].max()))
    keys=['read_ts','inverter'] if 'read_ts' in d and 'inverter' in d else [t]
    item['duplicate_key']=int(d.duplicated(keys).sum())
   if 'opencem_measurements' in p.name:
    full_duplicates=int(d.duplicated().sum())
    unique=d.drop_duplicates(); conflict=unique[unique.duplicated(['read_ts','inverter'],keep=False)]
    item.update(duplicate_full_rows=full_duplicates,conflicting_key_rows_after_exact_dedup=len(conflict),conflicting_key_count=len(conflict[['read_ts','inverter']].drop_duplicates()))
    item['timestamp_utc_start']=str(pd.to_datetime(d.read_ts,unit='s',utc=True).min())
    item['timestamp_utc_end']=str(pd.to_datetime(d.read_ts,unit='s',utc=True).max())
   if 'opencem_context' in p.name:
    parsed=[]
    for v in d.value:
     try: parsed.append(json.loads(v))
     except (ValueError,TypeError): parsed.append({})
    item['context_source_counts']=pd.Series([x.get('source','unknown') for x in parsed]).value_counts().to_dict()
    item['time_semantics']='recorded is availability time; start/end can be future schedules; never join on start alone for forecasting'
  info.append(item)
 save('input_audit.json',info)
 print('AUDIT complete',flush=True)

def train_power(data):
 d,pc,features,meta=load_power_frame(data)
 train=d.measured_on<'2022-01-01';val=d.measured_on.between('2022-01-01','2022-12-31 23:59:59');test=d.measured_on>='2023-01-01'
 assert d.loc[train,'measured_on'].max()<d.loc[val,'measured_on'].min()<d.loc[test,'measured_on'].min()
 rows=[];bundles={};events=[];sample=[]
 for c in pc:
  target=d[c]; valid=target.between(0,35)&target.notna()
  tr=np.flatnonzero((train&valid).to_numpy());v=np.flatnonzero((val&valid).to_numpy());te=np.flatnonzero((test&valid).to_numpy())
  if min(len(tr),len(v),len(te))<100:continue
  tr=tr[::max(1,int(np.ceil(len(tr)/40000)))]
  model=HistGradientBoostingRegressor(max_iter=70,max_leaf_nodes=15,l2_regularization=1,early_stopping=False,random_state=2026)
  model.fit(d.iloc[tr][features],target.iloc[tr]);pv=model.predict(d.iloc[v][features]);pt=model.predict(d.iloc[te][features])
  # Unlabelled, empirical underproduction reference; no healthy-state claim.
  threshold=float(max(3,np.quantile(pv-target.iloc[v].to_numpy(),.99)))
  loss=pt-target.iloc[te].to_numpy(); mask=(loss>threshold)&(pt>5)
  view=pd.DataFrame({'time':d.iloc[te].measured_on.to_numpy(),'actual_kw':target.iloc[te].to_numpy(),'expected_kw':pt,'flag':mask})
  run=(view.flag.ne(view.flag.shift())|view.time.diff().ne(pd.Timedelta('5min'))).cumsum()
  n_events=0
  for _,g in view.groupby(run):
   if bool(g.flag.iloc[0]) and len(g)>=3:
    events.append({'inverter':c.split('_ac_power')[0],'start_local':str(g.time.iloc[0]),'last_sample_local':str(g.time.iloc[-1]),'flagged_samples':len(g),'mean_observed_kw':float(g.actual_kw.mean()),'mean_reference_kw':float(g.expected_kw.mean()),'status':'unverified_underproduction','cause':'requires operator confirmation'})
    n_events+=1
  row={'channel':c,'train_rows':len(tr),'validation_rows':len(v),'test_rows':len(te),'test_mae_kw':float(mean_absolute_error(target.iloc[te],pt)),'test_rmse_kw':float(np.sqrt(mean_squared_error(target.iloc[te],pt))),'test_r2':float(r2_score(target.iloc[te],pt)),'threshold_kw':threshold,'candidate_events_min_3_samples':n_events}
  rows.append(row);bundles[c]={'model':model,'threshold_kw':threshold}
  if len(rows)==1:
   for k in range(min(2000,len(view))):sample.append(dict(view.iloc[k],time=str(view.time.iloc[k])))
  print('POWER',c,'MAE',round(row['test_mae_kw'],3),flush=True)
 joblib.dump({'models':bundles,'features':features,'task':'same-time conditional AC power reference, not future forecast','timezone':meta['System']['timezone_code'],'preprocessing':'see train_power; Celsius and m/s; weather backward asof <=15min; POA exact; daylight POA>=50','units':'kW','labelled_faults':False},MODELS/'power_reference_2107.joblib')
 pd.DataFrame(events).to_csv(REPORTS/'candidate_underproduction_events.csv',index=False)
 pd.DataFrame(sample).to_csv(REPORTS/'power_replay_sample.csv',index=False)
 save('power_metrics.json',{'task':'unlabelled historical power-reference baseline','train_before':'2022-01-01','validation_year':2022,'test_from':'2023-01-01','matched_daylight_rows':len(d),'inverters':rows,'event_count':len(events),'fault_precision_recall':'unavailable: no confirmed fault labels','healthy_training_data':'not established; observed historical operating states may include faults','warning':'threshold and runs are screening rules, not validated fault decisions','missing_power_channels':[f'inv_{i:02d}' for i in range(1,25) if not any(c.startswith(f'inv_{i:02d}_') for c in pc)]})

def train_classifier():
 raw=ROOT/'data/raw/costa2020'
 amb=loadmat(raw/'dataset_amb.mat');elec=loadmat(raw/'dataset_elec.mat')
 names=['irr','pvt','idc1','idc2','vdc1','vdc2']
 arr={k:(amb[k] if k in amb else elec[k]).ravel() for k in names}
 x=pd.DataFrame(arr);y=amb['f_nv'].ravel();n=len(y);boundary=int(.8*n);purge=100
 train=np.arange(0,boundary-purge,10);test=np.arange(boundary+purge,n,10)
 # Index positions are retained; invalid rows cannot move the chronological boundary.
 valid=np.isfinite(x).all(axis=1).to_numpy()&np.isin(y,[0,1,2,3,4])
 train=train[valid[train]];test=test[valid[test]]
 assert set(y[train])==set(range(5))
 model=ExtraTreesClassifier(n_estimators=100,max_depth=18,min_samples_leaf=5,class_weight='balanced',n_jobs=2,random_state=2026)
 model.fit(x.iloc[train],y[train]);pred=model.predict(x.iloc[test])
 labels={0:'正常',1:'组串短路',2:'模拟退化',3:'组串开路',4:'遮挡'}
 save('fault_metrics.json',{'dataset':'Costa/Lazzaretti 2020, two strings, 5kW, 16 operating days','protocol':'first 80% ordered rows for training; last20% test; 100-row purge each side; every10th sample; no timestamp or external-plant test','original_rows':n,'train_rows':len(train),'test_rows':len(test),'labels':labels,'accuracy':accuracy_score(y[test],pred),'macro_f1':f1_score(y[test],pred,average='macro'),'classification_report':classification_report(y[test],pred,labels=list(labels),target_names=list(labels.values()),output_dict=True,zero_division=0),'confusion_matrix':confusion_matrix(y[test],pred,labels=list(labels)).tolist(),'test_indices_start':int(test[0]),'train_indices_end':int(train[-1]),'limitations':['DC side faults, not inverter-internal fault codes','one plant; chronological rows do not establish new event or cross-plant generalization','artificially induced short/open/degradation; natural building shade','does not validate future fault prediction'],'features':names})
 joblib.dump({'model':model,'features':names,'labels':labels,'dataset':'Costa2020','task':'DC-side contemporaneous fault classification'},MODELS/'dc_fault_classifier.joblib')
 sample=x.iloc[test[:25]].copy();sample['row_index']=test[:25];sample['true_label']=y[test[:25]];sample.to_csv(REPORTS/'fault_inference_sample.csv',index=False)
 # A saved-model round trip and schema/finite value contract are material checks.
 reloaded=joblib.load(MODELS/'dc_fault_classifier.joblib')
 assert np.array_equal(reloaded['model'].predict(x.iloc[test[:25]]),pred[:25])
 print('FAULT',accuracy_score(y[test],pred),'macroF1',f1_score(y[test],pred,average='macro'),flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--data-dir',type=Path,default=Path('data/input'));a=p.parse_args()
 started=time.time()
 with threadpool_limits(limits=2):
  audit(a.data_dir);train_power(a.data_dir);train_classifier()
 print('DONE seconds',round(time.time()-started,1),flush=True)
