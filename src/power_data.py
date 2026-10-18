"""PVDAQ 2107 feature adapter shared by training and replay."""
import json
import numpy as np
import pandas as pd

def load_power_frame(data):
 meta=json.loads((data/'2107_system_metadata.json').read_text())
 header=pd.read_csv(data/'2107_electrical_data.csv',nrows=0).columns
 pc=[c for c in header if '_ac_power_' in c]
 e=pd.read_csv(data/'2107_electrical_data.csv',usecols=['measured_on']+pc)
 ir=pd.read_csv(data/'2107_irradiance_data.csv')
 env=pd.read_csv(data/'2107_environment_data.csv')
 for d in (e,ir,env):d['measured_on']=pd.to_datetime(d.measured_on); d.sort_values('measured_on',inplace=True)
 # Exact irradiance; backward-only weather join with 15 minute maximum age.
 d=e.merge(ir,on='measured_on',how='inner',validate='one_to_one')
 d=pd.merge_asof(d.sort_values('measured_on'),env.sort_values('measured_on'),on='measured_on',direction='backward',tolerance=pd.Timedelta('15min'))
 irr=ir.columns[1]; temp=[c for c in env if 'ambient_temperature' in c][0];wind=[c for c in env if 'wind_speed' in c][0]
 def unit(c):return meta['Metrics'][c]['units']
 assert unit(irr)=='W/m^2'
 d['ambient_c']=(d[temp]-32)*5/9 if unit(temp).lower() in ('f','degf','°f') else d[temp]
 d['wind_ms']=d[wind]*.44704 if unit(wind)=='mph' else d[wind]
 d['hour']=d.measured_on.dt.hour+d.measured_on.dt.minute/60
 doy=d.measured_on.dt.dayofyear
 d['doy_sin']=np.sin(2*np.pi*doy/365.25);d['doy_cos']=np.cos(2*np.pi*doy/365.25)
 d['hour_sin']=np.sin(2*np.pi*d.hour/24);d['hour_cos']=np.cos(2*np.pi*d.hour/24)
 features=[irr,'ambient_c','wind_ms','doy_sin','doy_cos','hour_sin','hour_cos']
 d=d[(d[irr]>=50)&(d[irr]<=1400)&d.ambient_c.between(-30,60)&d.wind_ms.between(0,50)].copy()
 d=d.dropna(subset=features)
 return d,pc,features,meta
