"""Offline evidence pages and a deterministic, stratified review queue."""
import argparse
import html
from pathlib import Path
import re
import numpy as np
import pandas as pd
from event_report import render_report

LABELS = {'device_specific_candidate':'单机偏离同群',
          'shared_or_model_candidate':'同群共性或模型偏差',
          'insufficient_peers':'同群证据不足'}


def safe_id(value):
    if not re.fullmatch(r'[a-f0-9]{16}', str(value)):
        raise ValueError('Event ID must be a 16-character hex digest')
    return str(value)


def chart(d, series, start, end, title, unit):
    """SVG with real elapsed-time spacing; missing samples break each line."""
    width, height, left, top = 780, 250, 55, 28
    plot_w, plot_h = 700, 175
    times = pd.to_datetime(d.time)
    lo, hi = times.min(), times.max()
    seconds = max((hi-lo).total_seconds(), 300)
    values = np.concatenate([pd.to_numeric(d[col],errors='coerce').to_numpy() for col,_,_ in series])
    finite = values[np.isfinite(values)]
    ymin = min(0.,float(finite.min())) if len(finite) else 0.
    ymax = max(1.,float(finite.max())*1.08) if len(finite) else 1.
    x = lambda t: left + (pd.Timestamp(t)-lo).total_seconds()/seconds*plot_w
    y = lambda v: top + plot_h*(ymax-v)/(ymax-ymin)
    esc = html.escape
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title,quote=True)}">',
             f'<title>{esc(title)}</title>', '<rect width="100%" height="100%" fill="white"/>',
             f'<rect x="{x(start):.2f}" y="{top}" width="{max(x(end)-x(start),1):.2f}" height="{plot_h}" fill="#fff0cf"/>']
    for tick in np.linspace(ymin,ymax,5):
        parts.append(f'<path d="M {left} {y(tick):.2f} h {plot_w}" stroke="#e1e8ee"/><text x="{left-8}" y="{y(tick)+4:.2f}" text-anchor="end">{tick:.1f}</text>')
    for idx in np.linspace(0,len(d)-1,min(5,len(d)),dtype=int):
        t = times.iloc[idx]
        parts.append(f'<text x="{x(t):.2f}" y="226" text-anchor="middle">{t:%m-%d %H:%M}</text>')
    for col, label, color in series:
        segments, segment, previous = [], [], None
        for t,v in zip(times,pd.to_numeric(d[col],errors='coerce')):
            if not np.isfinite(v) or (previous is not None and t-previous!=pd.Timedelta('5min')):
                if segment: segments.append(segment)
                segment=[]
            if np.isfinite(v):
                segment.append(f'{x(t):.2f},{y(v):.2f}')
            previous=t
        if segment: segments.append(segment)
        for points in segments:
            parts.append(f'<polyline points="{" ".join(points)}" stroke="{color}" stroke-width="2" fill="none"/>')
        # Mark isolated samples too; a single point is not a connecting segment.
        for t,v in zip(times,pd.to_numeric(d[col],errors='coerce')):
            if np.isfinite(v):
                parts.append(f'<circle cx="{x(t):.2f}" cy="{y(v):.2f}" r="1.5" fill="{color}"/>')
    parts.append(f'<text x="{left}" y="16">{esc(unit)}</text></svg>')
    legend = ' · '.join(f'<span style="color:{c}">{esc(label)}</span>' for _,label,c in series)
    return f'<h2>{esc(title)}</h2><div class="chart">'+''.join(parts)+f'</div><p>{legend} · 浅黄色为候选区间</p>'


def review_queue(events, limit=60):
    """Round robin across evidence/device strata, then descending shortfall.

    For workload triage only; selection is not a representative evaluation set.
    """
    eligible = events[events.review_status.isin(['unreviewed','needs_investigation'])].copy()
    eligible = eligible.sort_values(['reference_shortfall_kwh','event_id'],ascending=[False,True])
    buckets = [g.index.tolist() for _,g in eligible.groupby(['evidence','inverter'],sort=True)]
    picked=[]
    while len(picked)<limit and any(buckets):
        for bucket in buckets:
            if bucket and len(picked)<limit:
                picked.append(bucket.pop(0))
    queue = eligible.loc[picked].copy()
    queue.insert(0,'review_order',range(1,len(queue)+1))
    queue['review_note'] = ''
    queue['evidence_source'] = ''
    return queue


def build_details(events, samples, output, queue_limit=60):
    output = Path(output)
    output.mkdir(parents=True,exist_ok=True)
    directory = output/'details'
    directory.mkdir(exist_ok=True)
    if events.event_id.duplicated().any():
        raise ValueError('Duplicate event IDs')
    if samples.duplicated(['time','inverter']).any():
        raise ValueError('Duplicate evidence keys')
    samples = samples.copy()
    samples['time'] = pd.to_datetime(samples.time,errors='raise')
    groups = {key: group.sort_values('time').reset_index(drop=True)
              for key,group in samples.groupby('inverter')}
    esc = lambda x: html.escape(str(x),quote=True)
    for _,event in events.iterrows():
        key = safe_id(event.event_id)
        start,end = pd.Timestamp(event.start_local),pd.Timestamp(event.last_sample_local)
        full = groups[event.inverter]
        # Searchsorted avoids rescanning an entire year for every event.
        a = full.time.searchsorted(start-pd.Timedelta('60min'))
        b = full.time.searchsorted(end+pd.Timedelta('60min'),side='right')
        d = full.iloc[a:b].copy()
        inside = d[d.time.between(start,end)]
        if (len(inside)!=event.samples or inside.empty or not inside.residual_flag.all()
                or inside.time.iloc[0]!=start or inside.time.iloc[-1]!=end
                or not inside.time.diff().iloc[1:].eq(pd.Timedelta('5min')).all()):
            raise ValueError(f'Event/evidence mismatch for {key}')
        d['lower_reference_kw'] = d.expected_kw-d.threshold_kw
        power = chart(d,[('actual_kw','实测功率','#007d82'),('expected_kw','参考功率','#3568d4'),
                         ('lower_reference_kw','参考功率减残差阈值','#b66017')],start,end,'功率与残差依据','kW')
        ratio = chart(d,[('ratio','本机实测/参考','#007d82'),('peer_ratio','其他同群设备比值中位数','#8556b5')],
                      start,end,'同群相对表现','比值')
        table_cols = ['time','actual_kw','expected_kw','threshold_kw','residual_kw','ratio',
                      'peer_ratio','peer_count','peer_gap','residual_flag','peer_flag']
        table = inside[table_cols].rename(columns={
            'time':'时间','actual_kw':'实测 kW','expected_kw':'参考 kW','threshold_kw':'残差阈值 kW',
            'residual_kw':'残差 kW','ratio':'本机比值','peer_ratio':'其他同群中位数','peer_count':'有效同群数量',
            'peer_gap':'同群比值差','residual_flag':'残差超限','peer_flag':'偏离同群'}).to_html(index=False,float_format=lambda x:f'{x:.3f}',escape=True)
        content = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>事件 {key} · 伏脉智维</title>
<style>body{{font:15px system-ui;color:#16324a;background:#f5f8fb;margin:24px;max-width:1100px}}a{{color:#2363af}}section{{background:white;border-radius:12px;padding:18px;margin:20px 0}}h1{{font-size:25px}}h2{{font-size:19px}}svg{{width:100%;min-width:620px;font:11px system-ui}}.chart,.table{{overflow:auto}}td,th{{padding:9px;white-space:nowrap;text-align:left;border-bottom:1px solid #dce4eb}}table{{border-collapse:collapse}}code{{word-break:break-all}}p{{line-height:1.7}}</style>
<a href="../index.html">← 返回事件列表</a><h1>{esc(event.inverter)} · {esc(LABELS.get(event.evidence,event.evidence))}</h1>
<p>事件 <code>{key}</code><br>{esc(start)} 至 {esc(end)} · 本地时间 PST8PDT<br>
{int(event.samples)} 个采样点 · 观测跨度 {event.observed_span_minutes:g} 分钟</p>
<section><strong>触发依据</strong><p>功率残差连续至少 3 点超过该设备 2022 年验证阈值；
本事件 {int(event.peer_flag_samples)} / {int(event.samples)} 点的本机比值低于其他同群中位数超过 0.20。
事件内其他有效同群设备最少 {int(event.min_peer_count)} 台。</p>
<p>图中显示候选区间及其前后各最多 60 分钟的已保留日间观测。缺测处曲线断开；未通过前置工况过滤的原始点不在此图内。</p></section>
<section>{power}{ratio}</section><section><h2>逐点证据</h2><div class="table">{table}</div></section>
<section><h2>复核指引</h2><p>核对同一时段的限发计划、检修工单、通信状态及辐照传感器。零功率本身不能区分设备停机与测量问题。
参考电量差 {event.reference_shortfall_kwh:.2f} kWh 仅是观测区间内的积分。证据类别不代表确认故障或确定根因。</p>
<p>当前复核状态：{esc(event.review_status)}<br>复核说明：{esc(event.get('note',''))}</p>
<p>可复制事件 ID 到本地复核命令；仅有现场或工单等实际依据时，才应标记已确认故障。</p></section></html>'''
        (directory/f'{key}.html').write_text(content,encoding='utf-8')
    queue = review_queue(events,queue_limit)
    queue.to_csv(output/'review_queue.csv',index=False)
    return len(events),len(queue)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--replay-dir',type=Path,default=Path(__file__).resolve().parents[1]/'reports/replay')
    p.add_argument('--queue-limit',type=int,default=60)
    a = p.parse_args()
    if a.queue_limit<1:
        p.error('--queue-limit must be positive')
    events = pd.read_csv(a.replay_dir/'events.csv',keep_default_na=False)
    samples = pd.read_csv(a.replay_dir/'evidence.csv')
    count,queue_count = build_details(events,samples,a.replay_dir,a.queue_limit)
    render_report(events,{'设备':samples.inverter.nunique(),'日间时刻':samples.time.nunique(),
                         '候选事件':count,'待复核清单':queue_count},a.replay_dir/'index.html',details=True)
    print(f'Built {count} evidence pages; queued {queue_count} events.')
