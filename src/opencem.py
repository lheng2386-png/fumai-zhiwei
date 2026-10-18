"""Strict OpenCEM ingestion and availability-aware context association.

No commands from context JSON are executed. Text is evidence, not fault labels.
"""
import argparse
import csv
import hashlib
import html
import json
from pathlib import Path
import numpy as np
import pandas as pd

SIGNALS = ['battvolt','battcurr','battsoc','pv1power','outw_a','outsumw',
           'gridpowerw_a','temper1','temper2']
MEAS_COLUMNS = ['read_ts','inverter',*SIGNALS,'source_record']
CONTEXT_COLUMNS = ['context_id','recorded','start','end','inverter','source',
                   'context_kind','description','source_record']


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def integer(value):
    number=float(value)
    if not np.isfinite(number) or number!=int(number):
        raise ValueError('Expected integer')
    return int(number)


def load_measurements(path):
    accepted,rejected=[],[]
    widths={}
    with Path(path).open(newline='',encoding='utf-8-sig') as stream:
        reader=csv.reader(stream)
        header=next(reader)
        if len(header)!=len(set(header)) or set(MEAS_COLUMNS[:-1])-set(header):
            raise ValueError('Missing or duplicate measurement header fields')
        for source_record,row in enumerate(reader,2):
            widths[len(row)]=widths.get(len(row),0)+1
            # Pandas silently pads short rows; reject before column assignment.
            if len(row)!=len(header):
                rejected.append(dict(source_record=source_record,reason='row_width_mismatch',
                                     observed_columns=len(row),expected_columns=len(header)))
                continue
            item=dict(zip(header,row))
            try:
                timestamp=integer(item['read_ts'])
                inverter=integer(item['inverter'])
                if inverter not in (1,2):
                    raise ValueError('Unsupported inverter')
                pd.to_datetime(timestamp,unit='s',utc=True)
                clean={'read_ts':timestamp,'inverter':inverter,'source_record':source_record}
                for name in SIGNALS:
                    value=float(item[name]) if item[name].strip() else np.nan
                    if not np.isfinite(value) and item[name].strip():
                        raise ValueError('Nonfinite metric')
                    clean[name]=value
            except (ValueError,TypeError,OverflowError):
                rejected.append(dict(source_record=source_record,reason='invalid_key_or_numeric_value'))
                continue
            # Compare entire original schema for exact duplicates/conflicts.
            clean['_row_hash']=hashlib.sha256(json.dumps(row).encode()).hexdigest()
            accepted.append(clean)
    d=pd.DataFrame(accepted,columns=[*MEAS_COLUMNS,'_row_hash'])
    exact=d.duplicated(['read_ts','inverter','_row_hash'])
    for n in d.loc[exact,'source_record']:
        rejected.append(dict(source_record=int(n),reason='exact_duplicate'))
    d=d.loc[~exact].copy()
    conflicts=d.duplicated(['read_ts','inverter'],keep=False)
    for n in d.loc[conflicts,'source_record']:
        rejected.append(dict(source_record=int(n),reason='conflicting_valid_width_key'))
    d=d.loc[~conflicts].drop(columns='_row_hash').sort_values(['inverter','read_ts']).reset_index(drop=True)
    return d,pd.DataFrame(rejected,columns=['source_record','reason','observed_columns','expected_columns']),widths


def load_context(path):
    rows,rejects=[],[]
    with Path(path).open(newline='',encoding='utf-8-sig') as stream:
        reader=csv.DictReader(stream)
        if not {'id','recorded','start','end','value'}.issubset(reader.fieldnames or []):
            raise ValueError('Missing context header fields')
        for record,row in enumerate(reader,2):
            try:
                if None in row or any(v is None for v in row.values()):
                    raise ValueError('Invalid context row width')
                value=json.loads(row['value'])
                if not isinstance(value,dict):
                    raise ValueError('Context must be an object')
                inverter=integer(value.get('inverter'))
                start,end,recorded=(integer(row[c]) for c in ['start','end','recorded'])
                for t in (start,end,recorded):
                    pd.to_datetime(t,unit='s',utc=True)
                if inverter not in (1,2) or end<start:
                    raise ValueError('Invalid inverter or interval')
                source=str(value.get('source','unknown'))
                kind=('user_schedule_or_activity' if source=='manual entry' else
                      'workstation_activity' if source.startswith('workstation') else 'other_context')
                rows.append(dict(context_id=integer(row['id']),recorded=recorded,start=start,end=end,
                    inverter=inverter,source=source,context_kind=kind,
                    description=str(value.get('textual_description','')),source_record=record))
            except (ValueError,TypeError,OverflowError):
                rejects.append(dict(source_record=record,reason='invalid_context'))
    d=pd.DataFrame(rows,columns=CONTEXT_COLUMNS)
    duplicated=d.context_id.duplicated(keep=False)
    for n in d.loc[duplicated,'source_record']:
        rejects.append(dict(source_record=int(n),reason='duplicate_context_id'))
    return d.loc[~duplicated].reset_index(drop=True),pd.DataFrame(rejects,columns=['source_record','reason'])


def associate(measurements,contexts):
    groups={key:g.sort_values('read_ts') for key,g in measurements.groupby('inverter')}
    rows=[]
    for _,context in contexts.iterrows():
        g=groups.get(context.inverter,pd.DataFrame(columns=MEAS_COLUMNS))
        # Only associate records that are already available at a measurement.
        available_start=max(context.start,context.recorded)
        point=context.start==context.end
        endpoint=context.start+30 if point else context.end
        left=g.read_ts.searchsorted(available_start)
        right=g.read_ts.searchsorted(endpoint,side='right' if point else 'left')
        during=g.iloc[left:max(left,right)]
        if point:
            during=during.iloc[:1]  # One observation, within explicit 30-second tolerance.
        before=g.iloc[g.read_ts.searchsorted(context.start-900):g.read_ts.searchsorted(context.start)]
        finite_during=during.outsumw.dropna()
        finite_before=before.outsumw.dropna()
        during_median=float(finite_during.median()) if len(finite_during) else None
        before_median=float(finite_before.median()) if len(finite_before) else None
        delta=(during_median-before_median if len(finite_during)>=3 and len(finite_before)>=3 else None)
        rows.append({**context.to_dict(), 'available_start':available_start,
            'association_type':'point_next_sample_30s' if point else 'known_interval',
            'known_samples':len(during),'load_samples':len(finite_during),
            'first_sample_ts':int(during.read_ts.iloc[0]) if len(during) else None,
            'last_sample_ts':int(during.read_ts.iloc[-1]) if len(during) else None,
            'before_15m_samples':len(finite_before),
            'before_load_median_w':before_median,'known_load_median_w':during_median,
            'descriptive_load_delta_w':delta,
            'interpretation':'activity_context_not_fault_ground_truth'})
    columns=[*CONTEXT_COLUMNS,'available_start','association_type','known_samples','load_samples',
             'first_sample_ts','last_sample_ts','before_15m_samples','before_load_median_w',
             'known_load_median_w','descriptive_load_delta_w','interpretation']
    return pd.DataFrame(rows,columns=columns)


def report(links,summary,path):
    esc=lambda x:html.escape(str(x),quote=True)
    rows=[]
    headers=['文本 ID','逆变器','活动开始 UTC','来源','活动描述','可用观测数','负荷中位数 W']
    for _,r in links.iterrows():
        when=pd.to_datetime(r.start,unit='s',utc=True).isoformat()
        watts='—' if pd.isna(r.known_load_median_w) else f'{r.known_load_median_w:.1f}'
        values=[r.context_id,r.inverter,when,r.source,r.description,r.known_samples,watts]
        rows.append('<tr>'+''.join(f'<td data-label="{esc(label)}">{esc(value)}</td>'
                                  for label,value in zip(headers,values))+'</tr>')
    cards=' · '.join(f'{esc(k)}：{esc(v)}' for k,v in summary.items())
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>伏脉智维 · 数据与文本关联</title>
<style>body{font:15px system-ui;margin:24px;color:#16324a;background:#f5f8fb}h1{font-size:26px}.card{padding:18px;background:white;border-radius:12px}p{line-height:1.7}input{padding:12px;width:min(90%,650px)}.scroll{overflow:auto}table{border-collapse:collapse;background:white;width:100%}td,th{padding:12px;text-align:left;border-bottom:1px solid #dce4eb}td:nth-child(5){min-width:280px}td:nth-child(3){white-space:nowrap}
@media(max-width:760px){thead{display:none}table,tbody,tr{display:block}table{background:transparent}tr{background:white;border-radius:10px;padding:12px;margin:16px 0}td{display:flex;justify-content:space-between;gap:14px;padding:10px 0;font-size:13px}td:before{content:attr(data-label);color:#52697c;flex-shrink:0}td:nth-child(5){min-width:0}td:nth-child(3){white-space:normal}.scroll{overflow:visible}}
</style>
<h1>伏脉智维 · OpenCEM 数据与文本关联</h1><div class="card">CARDS</div>
<p>已隔离与表头列数不符的记录，保留原始文件校验值和来源行号。这里只展示活动文本与测量的时间关联，不代表故障告警或维修工单。负荷单位为 W，时间为 UTC。</p>
<p>区间记录只关联同时满足“活动已开始”和“文本已记录”的观测，截止活动结束（不含）；瞬时活动匹配之后 30 秒内的首个有效观测。前 15 分钟的负荷用于回顾比较，差值不代表活动造成的因果影响。</p>
<p><a href="context_associations.csv" download>下载关联表</a> · <a href="measurements_rejected.csv" download>查看隔离记录</a> · <a href="manifest.json">处理清单</a></p>
<input id="filter" placeholder="筛选设备、来源或活动描述" aria-label="筛选文本关联"><p id="count"></p>
<div class="scroll"><table><thead><tr><th>文本 ID</th><th>逆变器</th><th>活动开始 UTC</th><th>来源</th><th>活动描述</th><th>可用观测数</th><th>负荷中位数 W</th></tr></thead><tbody>ROWS</tbody></table></div>
<script>const field=document.getElementById('filter'),rows=[...document.querySelectorAll('tbody tr')];function update(){let n=0;for(const row of rows){row.hidden=!row.textContent.toLowerCase().includes(field.value.toLowerCase());if(!row.hidden)n++;}document.getElementById('count').textContent=`显示 ${n} / ${rows.length} 条文本`;};field.addEventListener('input',update);update();</script></html>'''
    path.write_text(page.replace('CARDS',cards).replace('ROWS','\n'.join(rows)),encoding='utf-8')


def run(measurement_path,context_path,output):
    measurements,rejected,widths=load_measurements(measurement_path)
    contexts,context_rejected=load_context(context_path)
    links=associate(measurements,contexts)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    measurements.to_csv(output/'measurements_clean.csv',index=False)
    rejected.to_csv(output/'measurements_rejected.csv',index=False)
    contexts.to_csv(output/'context_normalized.csv',index=False)
    context_rejected.to_csv(output/'context_rejected.csv',index=False)
    links.to_csv(output/'context_associations.csv',index=False)
    summary={'accepted_measurements':len(measurements),'rejected_measurements':len(rejected),
             'width_counts':widths,'rejection_reasons':rejected.reason.value_counts().to_dict(),
             'contexts':len(contexts),'rejected_contexts':len(context_rejected),
             'contexts_with_observations':int(links.known_samples.gt(0).sum()),
             'context_sources':contexts.source.value_counts().to_dict(),
             'input_sha256':{'measurements':sha256(measurement_path),'context':sha256(context_path)},
             'rules':{'row_width':'must equal CSV header width','duplicate_key':'remove exact duplicates; quarantine all remaining conflicts',
                      'association':'start >= max(recorded,start); end exclusive; point-next <=30s',
                      'units':'OpenCEM outsumw in W; timestamps UTC',
                      'source_record':'1-based CSV logical record number including header; not physical text line'},
             'limitations':['Contexts are activities, not confirmed faults or repair work orders.',
                            'Quarantined malformed rows are not repaired or used for training.',
                            'Median load differences are descriptive, not causal or model accuracy.']}
    (output/'manifest.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    report(links,{'有效测量':len(measurements),'隔离测量':len(rejected),'文本记录':len(contexts),
                  '关联到观测':summary['contexts_with_observations']},output/'index.html')
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--measurements',type=Path,required=True)
    p.add_argument('--context',type=Path,required=True)
    p.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'reports/opencem')
    a=p.parse_args()
    print(json.dumps(run(a.measurements,a.context,a.output),ensure_ascii=False,indent=2))
