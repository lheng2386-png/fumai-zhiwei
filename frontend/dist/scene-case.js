// Local, constructed engineering walkthrough. Never writes a business review.
const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
export const CASES=[
    {id:'single',eventId:'DEMO-2107-001',title:'单机功率偏离',evidence:'device_specific_candidate',answer:'single'},
    {id:'quality',eventId:'DEMO-2107-006',title:'同群证据不足',evidence:'insufficient_peers',answer:'insufficient'},
];
export function createCase(id,events,now=Date.now()){
    const definition=CASES.find(item=>item.id===id);
    const event=events.find(item=>item.event_id===definition?.eventId);
    if(!definition||!event||event.evidence!==definition.evidence||![event.mean_actual_kw,event.mean_reference_kw,event.threshold_kw,event.samples,event.min_peer_count,event.peer_flag_samples,event.observed_span_minutes].every(Number.isFinite))throw Error('该演练样例不完整，暂不能开始。');
    return {definition,event:{...event},stage:0,startedAt:now,completedAt:null,picked:'',evidenceSeen:false,attempts:[],note:'',feedback:''};
}
export function advanceCase(state,action,value,now=Date.now()){
    if(!state||state.stage===3)return false;
    let expected;
    if(action==='locate'&&state.stage===0){expected=state.event.inverter;value=state.picked;}
    else if(action==='evidence'&&state.stage===1){
        if(!state.evidenceSeen){state.feedback='先展开本条事件的证据，核对数值和同群条件。';return false;}
        expected=state.definition.answer;
    }else if(action==='decision'&&state.stage===2){
        if(state.note.trim().length<8){state.feedback='请用至少 8 个字写下已知现象和下一步需补的材料。';return false;}
        expected='needs_investigation';
    }else return false;
    const correct=value===expected;
    state.attempts.push({step:action,value:value||'',correct,at:new Date(now).toISOString()});
    if(!correct){
        state.feedback=action==='locate'?'设备尚未对应本条事件。请在场景或索引中选择 '+expected+'，再确认。':action==='evidence'?(expected==='single'?'样例显示持续偏低且存在同群偏离，只支持单机候选；尚无通信丢包或故障原因证据。':'当前只有 '+state.event.min_peer_count+' 台有效其他同群。比较依据不足，应先补齐可比观测，不能据此确定单机故障。'):'没有现场告警、检修或工单证据，暂存为待进一步核查。功率偏低不能直接确认故障或正常运行。';
        return false;
    }
    state.stage++;state.feedback='';
    if(state.stage===3)state.completedAt=now;
    return true;
}
export function caseReport(state,now=Date.now()){
    if(state.stage!==3)throw Error('请先完成案例，再导出复盘。');
    return {schema_version:'fumai-case-rehearsal-v1',data_kind:'synthetic_demo',record_scope:'local_rehearsal_only',case_id:state.definition.id,event:{...state.event},started_at:new Date(state.startedAt).toISOString(),completed_at:new Date(state.completedAt).toISOString(),exported_at:new Date(now).toISOString(),elapsed_seconds:Math.max(0,(state.completedAt-state.startedAt)/1000),attempts:state.attempts.map(item=>({...item})),review:{status:'needs_investigation',note:state.note.trim(),submitted_to_backend:false},limitations:['构造事件与示意布局，不是现场确认故障。','耗时仅为本次体验，未证明运维效率提升。','未触发设备控制、通知或真实工单。']};
}
export function renderCase(state,available=true){
    const e=escapeHtml;
    if(!state)return `<div class="scene-case-intro"><div><small>ENGINEERING WALKTHROUGH / 工程案例演练</small><h2>从一条异常线索，走到有依据的核查决定。</h2><p>约 3–5 分钟 · 构造样例 · 定位、证据与人工判断</p></div><div class="scene-case-start">${CASES.map(c=>`<button data-case-start="${c.id}" ${available?'':'disabled'}>${e(c.title)} →</button>`).join('')}</div>${available?'':'<p>已切换为导入场景，内置演练已暂停。刷新恢复示意场景后可开始。</p>'}</div>`;
    const event=state.event,stage=state.stage;
    const progress=['空间定位','核对证据','核查决定','体验复盘'];
    const heading=`<div class="scene-case-heading"><div><small>案例演练 · ${e(state.definition.title)} · 构造样例</small><strong>${e(event.event_id)} / ${e(event.inverter)}</strong></div><ol>${progress.map((title,index)=>`<li ${index===stage?'aria-current="step"':''} class="${index<stage?'done':''}">${index<stage?'✓':index+1} ${title}</li>`).join('')}</ol><button data-case-exit>退出演练</button></div>`;
    let content='';
    if(stage===0)content=`<div><h3>01 / 找到需要核查的设备</h3><p>${e(event.start_local)}，${e(event.inverter)} 出现功率偏低线索。请点击场景中的红色标记，或在设备索引中搜索并选择该设备。</p><p class="scene-case-pick">本次选择：<b>${e(state.picked||'尚未选择')}</b></p></div><div class="scene-case-actions"><p>目标：把事件编号、设备编号与空间位置对应起来。</p><button data-case-action="locate">确认已定位 →</button></div>`;
    if(stage===1){
        const difference=(event.mean_reference_kw-event.mean_actual_kw).toFixed(1);
        content=`<div><h3>02 / 先看证据，再判断线索类型</h3><p>事件时段 ${e(event.start_local.slice(11,16))}–${e(event.last_sample_local.slice(11,16))} · 场站本地时间 PST8PDT。这里使用本条事件观测，与设备快照分开。</p><button data-case-show-evidence aria-expanded="${state.evidenceSeen}">${state.evidenceSeen?'已展开事件证据':'展开事件证据 ↓'}</button>${state.evidenceSeen?`<dl class="scene-case-facts"><div><dt>实测 / 同时刻参考</dt><dd>${e(event.mean_actual_kw)} / ${e(event.mean_reference_kw)} kW</dd></div><div><dt>均值差 / 样例残差阈值</dt><dd>${difference} / ${e(event.threshold_kw)} kW</dd></div><div><dt>连续有效采样 / 跨度</dt><dd>${e(event.samples)} 点 / ${e(event.observed_span_minutes)} 分钟</dd></div><div><dt>有效其他同群 / 偏离点</dt><dd>${e(event.min_peer_count)} 台 / ${e(event.peer_flag_samples)} 点</dd></div></dl><p class="scene-case-footnote">构造证据；参考使用同时刻天气观测，不是未来发电预测。</p>`:''}</div><div class="scene-case-options"><p>本条证据支持哪项判断？</p><button data-case-action="evidence" data-case-value="single">单机相对同群偏离，保留为候选</button><button data-case-action="evidence" data-case-value="insufficient">同群观测不足，先补数据再归因</button><button data-case-action="evidence" data-case-value="communication">已确定通信故障，应直接报修</button></div>`;
    }
    if(stage===2)content=`<div><h3>03 / 记录下一步核查决定</h3><p>${state.definition.answer==='single'?'已知：样例功率持续偏低，并存在单机偏离线索。':'已知：样例功率偏低，但有效同群观测不足。'}未知：现场告警、检修安排和实际故障原因。</p><label for="scene-case-note">已知现象与待补材料</label><textarea id="scene-case-note" maxlength="1000" placeholder="例如：先核对同群数据、设备告警与检修记录，再安排现场复核。">${e(state.note)}</textarea><button data-case-agent>打开智能体追问依据 ↗</button></div><div class="scene-case-options"><p>选择与当前证据相符的结论，生成本地演练复盘。</p><button data-case-action="decision" data-case-value="needs_investigation">待进一步核查</button><button data-case-action="decision" data-case-value="confirmed_fault">确认设备故障</button><button data-case-action="decision" data-case-value="expected_operation">确认正常运行</button><small>演练不写业务记录；离开页面即清除，请完成后下载复盘。智能体需联网，不自动发起分析。</small></div>`;
    if(stage===3)content=`<div><h3>04 / 本次案例已完成</h3><p>你已将 ${e(event.inverter)} 对应到空间位置，核对 ${e(event.event_id)} 的功率和同群条件，并保留为“待进一步核查”。</p><blockquote>${e(state.note)}</blockquote><p>后续应补齐现场记录，由有资质的运维人员复核、处理并复测。当前没有完成真实维修或恢复验证。</p></div><div class="scene-case-actions"><button data-case-export>下载本次演练复盘 ↗</button><button data-case-start="${state.definition.id}">重新体验</button><small>JSON 包含样例证据、操作记录与耗时；仅下载到本机。</small></div>`;
    return heading+`<div class="scene-case-body">${content}</div><p class="scene-case-feedback" role="status" ${state.feedback?'':'hidden'}>${e(state.feedback)}</p>`;
}
