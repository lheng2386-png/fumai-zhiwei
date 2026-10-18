"""Summarize every predeclared seed; never select the best test seed."""
import argparse
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def summarize(paths,output):
    runs=[json.loads(Path(path).read_text()) for path in paths]
    if len({r['seed'] for r in runs})!=len(runs):
        raise ValueError('Duplicate seeds')
    first=runs[0]
    for run in runs[1:]:
        if any(run[k]!=first[k] for k in ('protocol','split_windows','class_counts','input_sha256')):
            raise ValueError('Runs do not share the same data and protocol')
    names=list(first['test_results'])
    summary={}
    for name in names:
        values={key:[run['test_results'][name][key] for run in runs] for key in ('accuracy','macro_f1')}
        summary[name]={key:{'per_seed':v,'mean':float(np.mean(v)),
                           'sample_std':float(np.std(v,ddof=1)) if len(v)>1 else None} for key,v in values.items()}
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    result={'seeds':[run['seed'] for run in runs],'models':summary,
            'validation_selected_models':[run['selected_by_validation'] for run in runs],
            'protocol':first['protocol'],'test_windows':first['split_windows']['test'],
            'limitations':first['limitations'],'selection':'All specified seeds included; no test-based seed selection.'}
    (output/'multi_seed_summary.json').write_text(json.dumps(result,indent=2,ensure_ascii=False))
    fig,axes=plt.subplots(1,2,figsize=(10,4.5))
    labels=['Matched trees','CNN scratch','Masked pretrain + CNN']
    for ax,key,title in zip(axes,('accuracy','macro_f1'),('Test accuracy','Test macro F1')):
        for i,name in enumerate(names):
            values=np.array(summary[name][key]['per_seed'])*100
            ax.bar(i,values.mean(),color=['#7792a6','#338694','#8055bd'][i],alpha=.75,width=.6)
            ax.scatter(np.full(len(values),i),values,color='#172d42',s=25,zorder=3)
            ax.text(i,values.mean()+.4,f'{values.mean():.2f}%',ha='center',fontsize=9)
        ax.set_xticks(range(3),labels,rotation=15,ha='right');ax.set_ylim(85,101)
        ax.set_title(title);ax.set_ylabel('%');ax.grid(axis='y',alpha=.2)
    fig.suptitle(f'Costa DC-side task | {len(runs)} seeds | same held-out windows',fontsize=12)
    fig.tight_layout();fig.savefig(output/'model_comparison.png',dpi=180);plt.close(fig)
    lines=['# 自主深度模型多种子实验','',f'随机种子：{result["seeds"]}；测试窗口：{result["test_windows"]}。',
           '','所有模型使用相同数据划分。列出全部指定种子，不按测试成绩挑选种子。','',
           '| 模型 | 测试准确率均值 ± 样本标准差 | 测试宏平均 F1 均值 ± 样本标准差 |',
           '| --- | --- | --- |']
    for name in names:
        fields=[]
        for key in ('accuracy','macro_f1'):
            m=summary[name][key];std=m['sample_std']
            fields.append(f'{100*m["mean"]:.2f}% ± {100*std:.2f} 个百分点' if std is not None else f'{100*m["mean"]:.2f}%')
        lines.append(f'| {name} | {fields[0]} | {fields[1]} |')
    lines+=['','![同协议模型比较](model_comparison.png)','','这是单站有序行留出评估，不代表跨站泛化或已超过 SOTA。',
            '3 个种子的标准差描述本轮随机性，不能替代独立场站/独立事件的误差范围。']
    (output/'实验结果.md').write_text('\n'.join(lines),encoding='utf-8')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('results',type=Path,nargs='+')
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    print(json.dumps(summarize(a.results,a.output),ensure_ascii=False,indent=2))
