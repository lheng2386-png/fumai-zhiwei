import {TYPES,assetEvents,pendingEvents} from './scene-data.js';
import {equipmentRecipe} from './scene-equipment-data.js';

const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = value => Number.isFinite(value) ? value.toFixed(1) : '—';
const reviewNames = {unreviewed:'未复核',needs_investigation:'待现场核查',expected_operation:'预期运行示例',data_issue:'数据问题示例'};
const evidenceNames = {device_specific_candidate:'单机偏离候选',shared_or_model_candidate:'共享因素 / 模型候选',insufficient_peers:'同群证据不足'};
const pair = (label,value) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`;

/** All values come from the supplied fixtures/layout; absent telemetry is never synthesized. */
export function renderAssetDetails({asset,manifest,devices,events,modelState}) {
    if (!asset) return '<p class="scene-empty">选择设备，查看档案、监测参数与关联证据。</p>';
    const demo = manifest.source === 'constructed_demo';
    const device = demo && devices.find(d => d.id === asset.id);
    const linked = assetEvents(asset,events,manifest.source);
    const pending = pendingEvents(asset,events,manifest.source);
    const children = manifest.assets.filter(a => a.group === asset.id && a.id !== asset.id);
    const upstream = manifest.assets.find(a => a.id === asset.group && a.id !== asset.id);
    const dimensions = !asset.model && equipmentRecipe.models.find(m => m.id === asset.type)?.dimensions_m;
    const measured = device && Number.isFinite(device.actual_kw) && Number.isFinite(device.expected_kw);
    const difference = measured ? device.actual_kw - device.expected_kw : null;
    const ratio = measured && device.expected_kw > 0 ? difference / device.expected_kw * 100 : null;
    const fields = asset.type === 'panel'
        ? [['直流电压','V'],['直流电流','A'],['组件温度','°C'],['辐照强度','W/m²'],['绝缘电阻','MΩ']]
        : [['A 相电流','A'],['B 相电流','A'],['C 相电流','A'],['线电压 Uab','V'],['线电压 Ubc','V'],['线电压 Uca','V'],['无功功率','kvar'],['功率因数','—'],['电网频率','Hz'],['机内温度','°C'],['直流电压','V'],['直流电流','A']];
    const state = pending.length ? `${pending.length} 条候选线索待核查` : demo ? '未挂接待核查线索' : '业务状态未接入';
    return `<header class="scene-detail-title"><small>${esc(TYPES[asset.type])} / ${demo?'示意资产':'导入资产'}</small><h3>${esc(asset.name)}</h3><p>${esc(asset.id)} <span class="scene-state ${pending.length?'attention':''}">${esc(state)}</span></p><button data-scene-locate>定位设备 ↗</button></header>
        <section class="scene-detail-section"><h4>运行概况 <small>${measured?'构造样例':'尚无设备遥测'}</small></h4>
        ${measured?`<div class="scene-power-cards"><div><span>样例实测功率</span><strong>${number(device.actual_kw)}<small> kW</small></strong></div><div><span>样例参考功率</span><strong>${number(device.expected_kw)}<small> kW</small></strong></div><div><span>功率差值</span><strong>${difference>0?'+':''}${number(difference)}<small> kW</small></strong></div><div><span>相对参考偏差</span><strong>${ratio>0?'+':''}${number(ratio)}<small>${ratio===null?'':' %'}</small></strong></div></div><p>差值 = 样例实测 − 样例参考；仅描述偏离，不等同于故障。当前构造读数未附采集时刻，不代表实时值。</p>`:'<p class="scene-data-gap">当前资产未接入监测数据，不能据此判断设备正常或异常。</p>'}
        </section>
        <section class="scene-detail-section"><h4>设备档案</h4><dl class="scene-kv">
        ${pair('设备编号',asset.id)}${pair('所在分区',asset.zone)}${pair('场景坐标 / m',asset.position.map(number).join(' / '))}${pair('姿态 XYZ / °',asset.rotation.map(number).join(' / '))}${pair('空间来源',demo?'构造示意 · 非测绘':'用户导入 · 待验收')}${pair('设备类型',TYPES[asset.type])}
        ${dimensions?pair('示意尺寸 XYZ / m',dimensions.map(number).join(' × ')):pair('模型尺寸','待工程模型验收')}
        ${pair('采集时间 / 通信状态','未接入')}${pair('负责人 / 维保记录','现场台账未接入')}</dl>
        <p class="scene-model-state">${esc(modelState || (asset.model?'模型等待加载':'精细化示意模型 · 尺寸待核对'))}</p></section>
        <section class="scene-detail-section"><h4>电气与环境参数 <small>待接入 ${fields.length} 项</small></h4><table class="scene-telemetry"><thead><tr><th>监测参数</th><th>数值</th><th>单位</th></tr></thead><tbody>${fields.map(([name,unit])=>`<tr><td>${esc(name)}</td><td><span>未接入</span></td><td>${esc(unit)}</td></tr>`).join('')}</tbody></table><p>暂无测点数据，保留空状态；日照演示不会生成电气读数。</p></section>
        <section class="scene-detail-section"><h4>空间关联 <small>待工程接线确认</small></h4>
        ${upstream?`<p>上级示意设备 <button class="scene-related" data-scene-select="${esc(upstream.id)}">${esc(upstream.name)} · ${esc(upstream.id)} ↗</button></p>`:''}
        ${children.length?`<p>关联 ${esc(TYPES[children[0].type])} <strong>${children.length}</strong> 台 / 块</p><div class="scene-related-list">${children.slice(0,4).map(a=>`<button data-scene-select="${esc(a.id)}">${esc(a.name)} ↗</button>`).join('')}</div>${children.length>4?'<p>其余关联资产可在左侧按上级设备编号搜索。</p>':''}`:!upstream?'<p>暂无已确认的空间关联。</p>':''}
        </section>
        <section class="scene-detail-section scene-event-list"><h4>关联事件与复核 <small>${linked.length} 条 / ${pending.length} 条待核查</small></h4>
        ${linked.length?linked.map((e,i)=>`<details class="scene-event-entry" ${i===0?'open':''}><summary>${esc(e.event_id)}<span>${esc(reviewNames[e.review_status]||'状态未知')}</span></summary><article><strong>${esc(evidenceNames[e.evidence]||'事件线索')}</strong><p>${esc(e.start_local)} → ${esc(e.last_sample_local)}<br>观测 ${number(e.mean_actual_kw)} / 参考 ${number(e.mean_reference_kw)} kW<br>${esc(e.samples)} 个样本 · ${esc(e.observed_span_minutes)} 分钟 · 有效同群至少 ${esc(e.min_peer_count)} 台<br>参考电量差 ${number(e.reference_shortfall_kwh)} kWh（非可挽回损失）</p>${e.note?`<blockquote>${esc(e.note)}</blockquote>`:''}<button data-scene-event="${esc(e.event_id)}">查看完整证据 ↗</button><button data-scene-agent="${esc(e.event_id)}">询问智能体 ↗</button></article></details>`).join(''):'<p>当前没有挂接业务事件，不能据此判断设备正常。告警、工单与人工复核进度以接入后的业务记录为准。</p>'}</section>`;
}
