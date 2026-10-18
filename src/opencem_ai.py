"""Local ML baselines: electrical novelty scores and activity text clusters."""
import argparse
import html
import json
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.ensemble import IsolationForest
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.impute import SimpleImputer
from threadpoolctl import threadpool_limits
from opencem import sha256

FEATURES=['battvolt','battcurr','battsoc','pv1power','outsumw','gridpowerw_a','temper1','temper2']


def electrical_novelty(data):
    """Fit only early timestamps; calibrate a threshold on a later interval."""
    d=data.sort_values(['read_ts','inverter']).reset_index(drop=True).copy()
    times=np.sort(d.read_ts.unique())
    if len(times)<100:
        raise ValueError('At least 100 distinct timestamps are required')
    train_end=int(times[int(len(times)*.70)])
    test_start=int(times[int(len(times)*.85)])
    rows=[];bundles={};metrics=[]
    for inverter,g in d.groupby('inverter'):
        g=g.copy()
        train=g.read_ts.lt(train_end)
        calibration=g.read_ts.ge(train_end)&g.read_ts.lt(test_start)
        test=g.read_ts.ge(test_start)
        if min(train.sum(),calibration.sum(),test.sum())<10:
            raise ValueError('Insufficient device coverage in chronological partitions')
        x=g[FEATURES].apply(pd.to_numeric,errors='coerce').replace([np.inf,-np.inf],np.nan)
        features=[c for c in FEATURES if x.loc[train,c].notna().sum()>=10 and x.loc[train,c].nunique()>1]
        if len(features)<2:
            raise ValueError('At least two varying training features are required')
        # Require >=75% observed features. Missingness itself is a quality issue.
        usable=x[features].notna().mean(axis=1).ge(.75)
        masks=[m&usable for m in (train,calibration,test)]
        if min(m.sum() for m in masks)<10:
            raise ValueError('Insufficient usable device coverage')
        imputer=SimpleImputer(strategy='median')
        training=imputer.fit_transform(x.loc[masks[0],features])
        model=IsolationForest(n_estimators=100,max_samples=min(2048,len(training)),
                              contamination='auto',random_state=2026,n_jobs=2)
        model.fit(training)
        score=np.full(len(g),np.nan)
        score[usable.to_numpy()]=-model.score_samples(imputer.transform(x.loc[usable,features]))
        threshold=float(np.quantile(score[masks[1].to_numpy()],.99))
        g['novelty_score']=score
        g['calibration_threshold']=threshold
        g['partition']=np.select([train,calibration],['train','calibration'],default='test')
        g['quality_usable']=usable.to_numpy()
        g['novelty_flag']=g.novelty_score.gt(threshold)&g.quality_usable
        rows.append(g)
        bundles[int(inverter)]={'imputer':imputer,'model':model,'features':features,'threshold':threshold}
        held=g[test&usable]
        metrics.append({'inverter':int(inverter),'train_rows':int(masks[0].sum()),
                        'calibration_rows':int(masks[1].sum()),'test_rows':len(held),
                        'test_candidate_points':int(held.novelty_flag.sum()),
                        'test_candidate_fraction':float(held.novelty_flag.mean()),
                        'excluded_quality_rows':int((~usable).sum()),'features':features})
    return pd.concat(rows,ignore_index=True),{'models':bundles,'train_end_exclusive':train_end,
            'test_start_inclusive':test_start},metrics


def cluster_text(contexts):
    texts=contexts.description.fillna('').astype(str)
    vectorizer=TfidfVectorizer(lowercase=True,stop_words='english',ngram_range=(1,2),
                               min_df=2,max_features=3000)
    matrix=vectorizer.fit_transform(texts)
    if matrix.shape[1]<2:
        raise ValueError('Insufficient repeated vocabulary for text clustering')
    usable=np.asarray(matrix.getnnz(axis=1)>0)
    eligible=matrix[usable]
    distinct=len({(tuple(row.indices),tuple(row.data.round(8))) for row in eligible})
    count=min(8,int(usable.sum()),distinct)
    if count<1:
        raise ValueError('No usable activity texts')
    model=KMeans(n_clusters=count,n_init=10,random_state=2026)
    labels=np.full(len(texts),-1,dtype=int)
    labels[usable]=model.fit_predict(eligible)
    result=contexts.copy();result['text_cluster']=labels
    terms=vectorizer.get_feature_names_out()
    descriptions=[]
    for i in range(count):
        keywords=terms[np.argsort(model.cluster_centers_[i])[-8:][::-1]].tolist()
        descriptions.append({'cluster':i,'records':int((labels==i).sum()),'keywords':keywords})
    return result,{'vectorizer':vectorizer,'model':model},descriptions


def run(replay_dir,model_dir):
    data=pd.read_csv(replay_dir/'measurements_clean.csv')
    contexts=pd.read_csv(replay_dir/'context_normalized.csv')
    scored,power_bundle,power_metrics=electrical_novelty(data)
    clustered,text_bundle,text_summary=cluster_text(contexts)
    candidates=scored[scored.partition.eq('test')&scored.novelty_flag].copy()
    descriptions=[];ids=[]
    for _,point in candidates.iterrows():
        known=contexts[contexts.inverter.eq(point.inverter)&contexts.recorded.le(point.read_ts)
                       &contexts.start.le(point.read_ts)&contexts.end.gt(point.read_ts)]
        ids.append(','.join(str(int(n)) for n in known.context_id))
        descriptions.append(' | '.join(known.description.fillna('').astype(str)))
    candidates['known_context_ids']=ids
    candidates['known_activity_descriptions']=descriptions
    model_dir.mkdir(parents=True,exist_ok=True)
    joblib.dump(power_bundle,model_dir/'electrical_novelty.joblib')
    joblib.dump(text_bundle,model_dir/'activity_text_clusters.joblib')
    scored.to_csv(replay_dir/'electrical_novelty_scores.csv',index=False)
    clustered.to_csv(replay_dir/'activity_text_clusters.csv',index=False)
    candidates.to_csv(replay_dir/'test_novelty_candidates.csv',index=False)
    summary={'electrical_model':'IsolationForest per inverter',
             'train_end_exclusive_utc':pd.to_datetime(power_bundle['train_end_exclusive'],unit='s',utc=True).isoformat(),
             'test_start_inclusive_utc':pd.to_datetime(power_bundle['test_start_inclusive'],unit='s',utc=True).isoformat(),
             'calibration':'99th percentile of novelty score; calibration interval only',
             'electrical_results':power_metrics,'text_model':'TF-IDF word ngrams + KMeans; exploratory full-corpus grouping',
             'text_groups':text_summary,
             'input_sha256':{name:sha256(replay_dir/name) for name in (
                 'measurements_clean.csv','context_normalized.csv','manifest.json')},
             'limitations':['Electrical scores indicate novelty relative to historical observations, not confirmed faults.',
                            'No fault labels: fault precision, recall and accuracy are unavailable.',
                            'Clusters group activity wording, not photovoltaic fault categories.',
                            'This module does not implement a language-model agent.']}
    (replay_dir/'ai_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    render_ai(summary,candidates,replay_dir/'ai.html')
    return summary


def render_ai(summary,candidates,path):
    esc=lambda x:html.escape(str(x),quote=True)
    groups=''.join('<tr>'+''.join(f'<td>{esc(x)}</td>' for x in (
        group['cluster'],group['records'],', '.join(group['keywords'])))+'</tr>' for group in summary['text_groups'])
    rows=[]
    for _,point in candidates.iterrows():
        values=[pd.to_datetime(point.read_ts,unit='s',utc=True).isoformat(),int(point.inverter),
                f'{point.novelty_score:.4f}',f'{point.calibration_threshold:.4f}',
                point.known_context_ids or '无',point.known_activity_descriptions or '无可用活动文本']
        rows.append('<tr>'+''.join(f'<td>{esc(x)}</td>' for x in values)+'</tr>')
    template='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>伏脉智维 · AI 分析</title><style>body{font:15px system-ui;color:#16324a;background:#f5f8fb;margin:24px}h1{font-size:26px}section{background:white;padding:18px;border-radius:12px;margin:18px 0}p{line-height:1.7}.scroll{overflow:auto}table{border-collapse:collapse;width:100%}td,th{padding:10px;text-align:left;border-bottom:1px solid #dce4eb}td{min-width:90px}a{color:#2363af}</style>
<a href="index.html">← 数据与文本关联</a><h1>伏脉智维 · AI 分析</h1>
<section><h2>多变量电气异常筛查</h2><p>每台设备使用 Isolation Forest 学习早期历史电气量的组合。前 70% 时间训练，中间 15% 校准阈值，最后 15% 留出回放。阈值为校准分数的第 99 百分位，分数越高表示偏离历史分布越明显。</p>
<p>本次留出测试共 TEST 条可用测量，筛出 COUNT 个候选点。它们不等于 COUNT 次故障；同一过程可能连续触发多个点，缺少故障真值，不能计算故障准确率。</p>
<a href="test_novelty_candidates.csv" download>下载留出候选点与当时可用文本</a></section>
<section><h2>活动文本自动分组</h2><p>TF-IDF 将文字转成数值特征，KMeans 按用词相似性分组。下列关键词来自模型计算，组号本身没有故障含义。它整理的是工作站和用电活动，不是对光伏故障做八分类。</p><div class="scroll"><table><thead><tr><th>文本组</th><th>记录数</th><th>代表词</th></tr></thead><tbody>GROUPS</tbody></table></div></section>
<section><h2>留出候选点与活动上下文</h2><p>只列出该时刻已经记录、并在适用区间内的同设备活动；没有匹配时明确保留为空，不推断根因。所有时间为 UTC，分数不是概率。</p><div class="scroll"><table><thead><tr><th>时间 UTC</th><th>设备</th><th>异常分数</th><th>阈值</th><th>文本 ID</th><th>可用活动描述</th></tr></thead><tbody>ROWS</tbody></table></div></section>
<p><a href="ai_summary.json">模型、时间划分与输入校验值</a></p></html>'''
    text=template.replace('TEST',str(sum(r['test_rows'] for r in summary['electrical_results'])))
    text=text.replace('COUNT',str(len(candidates))).replace('GROUPS',groups).replace('ROWS','\n'.join(rows))
    path.write_text(text,encoding='utf-8')


if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]
    p=argparse.ArgumentParser()
    p.add_argument('--replay-dir',type=Path,default=root/'reports/opencem')
    p.add_argument('--model-dir',type=Path,default=root/'models/opencem')
    a=p.parse_args()
    with threadpool_limits(limits=2):
        print(json.dumps(run(a.replay_dir,a.model_dir),ensure_ascii=False,indent=2))
