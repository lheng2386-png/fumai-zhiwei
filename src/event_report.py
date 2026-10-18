"""Self-contained HTML report: escaped data, offline filters, review notes."""
import html
import json
import math


def render_report(events, summary, path, details=False):
    escaped = lambda value: html.escape(str(value), quote=True)
    labels = {'device_specific_candidate': '单机偏离同群',
              'shared_or_model_candidate': '同群共性或模型偏差',
              'insufficient_peers': '同群证据不足'}
    statuses = {'unreviewed':'待复核', 'confirmed_fault':'已确认故障',
                'expected_operation':'预期运行', 'data_issue':'数据问题',
                'needs_investigation':'待进一步调查'}
    headers = ['事件 ID','设备','开始时间','采样数','实测 / 参考 kW',
               '平均同群比值差','偏离同群采样数','证据','参考电量差 kWh','复核状态','复核说明']
    rows = []
    for _, e in events.iterrows():
        gap = ('—' if e.mean_peer_gap is None or e.mean_peer_gap == ''
               or not math.isfinite(float(e.mean_peer_gap)) else f'{float(e.mean_peer_gap):.3f}')
        cells = [e.event_id, e.inverter, e.start_local, e.samples,
                 f'{e.mean_actual_kw:.2f} / {e.mean_reference_kw:.2f}', gap, e.peer_flag_samples,
                 labels.get(e.evidence, e.evidence),
                 f'{e.reference_shortfall_kwh:.2f}', statuses.get(e.review_status,e.review_status), e.get('note', '')]
        rendered = [escaped(v) for v in cells]
        if details:
            from event_details import safe_id
            rendered[0] = f'<a href="details/{safe_id(e.event_id)}.html">{escaped(e.event_id)} · 查看详情</a>'
        rows.append('<tr>'+''.join(f'<td data-label="{escaped(h)}">{v}</td>'
                                  for h,v in zip(headers,rendered))+'</tr>')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>伏脉智维 · 运维事件回放</title><style>
body{font:15px system-ui;margin:36px;color:#16324a;background:#f5f8fb}
h1{margin-bottom:8px} .card{background:white;padding:20px;border-radius:12px;margin:18px 0}
input{padding:12px;width:min(600px,90%);border:1px solid #bdcbd7;border-radius:7px}
table{border-collapse:collapse;width:100%;background:white}td,th{padding:12px;text-align:left;border-bottom:1px solid #dce4eb}
td:first-child{font-family:monospace}thead{background:#dfebf3} .scroll{overflow:auto}small{color:#52697c}
td{white-space:nowrap}td:last-child{white-space:normal;min-width:100px}
@media(max-width:760px){body{margin:20px}h1{font-size:25px}.scroll{overflow:visible}
thead{display:none}table,tbody,tr{display:block}table{background:transparent}
tr{background:white;border-radius:10px;padding:10px;margin:14px 0}
td{display:flex;justify-content:space-between;gap:12px;white-space:normal;padding:8px 4px;font-size:13px}
td:before{content:attr(data-label);color:#52697c;flex-shrink:0}td:first-child{font-family:inherit}}
</style><h1>伏脉智维 · 运维事件回放</h1>
<small>PVDAQ 2107 · 本地历史时间（PST8PDT） · 规则筛查与人工复核</small>
<div class="card">SUMMARY</div>
<p>候选事件未经故障确认；“单机偏离同群”是证据分类，不是故障根因。参考电量差按相邻有效观测点积分，不代表可挽回损失。共享辐照传感器误差、限发及设备差异均可能影响结果。</p>
<input id="filter" placeholder="筛选设备、事件、证据或复核状态" aria-label="筛选事件">
<p id="count"></p><div class="scroll"><table><thead><tr>
<th>事件 ID</th><th>设备</th><th>开始时间</th><th>采样数</th><th>实测 / 参考 kW</th><th>平均同群比值差</th><th>偏离同群采样数</th><th>证据</th><th>参考电量差 kWh</th><th>复核状态</th><th>复核说明</th>
</tr></thead><tbody>ROWS</tbody></table></div>
<script>const input=document.getElementById('filter'),rows=[...document.querySelectorAll('tbody tr')];
function update(){let n=0;const q=input.value.toLowerCase();for(const row of rows){row.hidden=!row.textContent.toLowerCase().includes(q);if(!row.hidden)n++;}document.getElementById('count').textContent=`显示 ${n} / ${rows.length} 条事件`;}
input.addEventListener('input',update);update();</script></html>'''
    info = ' · '.join(f'{escaped(k)}：{escaped(v)}' for k,v in summary.items())
    if details:
        info += '<p><a href="review_queue.csv" download>下载待复核清单 CSV</a> · 按证据和设备轮转选取，供复核排程使用，不能作为无偏准确率评估集。</p>'
    path.write_text(page.replace('SUMMARY', info).replace('ROWS', '\n'.join(rows)), encoding='utf-8')
