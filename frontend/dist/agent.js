/* Evidence workbench. Model keys never enter the browser. */
(() => {
    'use strict';
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const glyph = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M12 3v3m-6 2h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2Zm-4 4h2m16 0h2M8 20v2m8-2v2"/><path d="M8 12h.01M16 12h.01M8 16h8" stroke-linecap="round"/></svg>';
    const uuid = () => '10000000-1000-4000-8000-100000000000'.replace(/[018]/g, c => (Number(c) ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> Number(c) / 4).toString(16));
    let session;
    try { session = sessionStorage.getItem('fumai-agent-session') || uuid(); sessionStorage.setItem('fumai-agent-session', session); } catch { session = uuid(); }
    const state = { ready:false, connected:false, busy:false, bootstrap:null, messages:[], selected:'', error:'', token:'', draft:null, tab:'chat', question:'', history:[], connectionError:'' };
    const kinds = {device_specific_candidate:'单机偏离同群',shared_or_model_candidate:'同群共性变化',insufficient_peers:'同群证据不足'};
    let controller;
    const drawer = document.createElement('dialog');
    drawer.id = 'agent-dialog'; drawer.className = 'agent-dialog'; drawer.setAttribute('aria-label','伏脉运维助手');
    const draftDialog = document.createElement('dialog');
    draftDialog.id='agent-draft-dialog'; draftDialog.className='agent-draft-dialog'; draftDialog.setAttribute('aria-label','确认核查工单草稿');
    const login = document.createElement('dialog');
    login.className='agent-login-dialog'; login.innerHTML='<form id="agent-login"><span class="eyebrow">SECURE CONNECTION</span><h2>连接实测工作空间</h2><p>输入管理员分配的个人访问令牌，仅在当前页面内存中使用。</p><label>访问令牌<input name="token" type="password" autocomplete="off" required minlength="32"></label><p class="form-error" id="agent-login-error"></p><div class="agent-form-actions"><button type="button" class="secondary-button" data-agent-action="close-login">取消</button><button class="primary-button">连接</button></div></form>';
    const launcher = document.createElement('button');
    launcher.className='agent-launcher'; launcher.id='agent-launcher'; launcher.setAttribute('aria-label','打开伏脉运维助手'); launcher.setAttribute('aria-haspopup','dialog'); launcher.innerHTML=glyph+'<span>运维助手</span><i></i>';
    document.body.append(drawer,draftDialog,login,launcher);
    const demoEvents = () => window.FumaiDemo?.events || [];
    const events = () => state.bootstrap?.events || demoEvents();
    const mode = () => state.bootstrap?.mode === 'live' ? '实测快照' : '样例演练';
    const engine = () => state.bootstrap?.model_configured ? '模型已配置 · 待请求验证' : '规则分析 · 模型未接入';
    async function api(path, body) {
        const signal = controller?.signal;
        const headers = {'X-Fumai-Session':session};
        if(state.token) headers.Authorization='Bearer '+state.token;
        if(body) headers['Content-Type']='application/json';
        const response = await fetch('/agent-api/'+path,{method:body?'POST':'GET',headers,body:body?JSON.stringify(body):undefined,signal,cache:'no-store'});
        const type=response.headers.get('content-type') || '';
        if(!type.includes('application/json')) throw new Error('当前站点未连接智能体服务。');
        const data=await response.json();
        if(!response.ok) { if(response.status===401) throw new Error('实测工作空间需要访问令牌，请点击“连接服务”。'); throw new Error(data.error || '请求失败，请重试。'); }
        return data;
    }
    async function connect() {
        try { state.bootstrap=await api('bootstrap'); state.connected=true; state.history=state.bootstrap.history; state.connectionError=''; }
        catch(error) { state.connected=false; state.connectionError=error.message; }
        state.ready=true; paint();
    }
    function welcome(compact=false) {
        return `<div class="agent-welcome"><div class="agent-orbit">${glyph}<span></span></div><span class="eyebrow">FUMAI INTELLIGENCE</span><h2>让证据，先于判断。</h2><p>把异常线索变成清晰的核查路径。<br>从你关心的设备或事件开始。</p><div class="agent-prompts">${['梳理待复核事件，给出核查顺序','分析 inv_07 的异常证据','同群证据不足时应该先查什么？'].map((p,i)=>`<button data-agent-prompt="${esc(p)}"><span>0${i+1}</span>${esc(p)}<b>↗</b></button>`).join('')}</div>${compact?'':'<div class="agent-capabilities"><span>可追溯证据</span><span>分步核查</span><span>人工确认</span></div>'}</div>`;
    }
    function messageHtml(message) {
        if(message.role==='user') return `<article class="agent-message user"><span class="agent-message-avatar">我</span><div><small>你的问题</small><p>${esc(message.text)}</p></div></article>`;
        const r=message.result;
        return `<article class="agent-message assistant"><span class="agent-message-avatar">${glyph}</span><div class="agent-answer"><div class="agent-answer-meta"><strong>伏脉运维助手</strong><span>${r.engine==='responses'?'模型辅助分析':'规则分析'} · ${r.metadata.source==='measured'?'实测证据':'构造样例'}</span></div><p class="agent-answer-summary">${esc(r.summary)}</p><div class="agent-boundary">${esc(r.boundary)}</div>${r.model_analysis?`<section class="agent-model-text"><h4>模型辅助解释 <small>需人工复核</small></h4><p>${esc(r.model_analysis.summary)}</p>${list(r.model_analysis.hypotheses,'推测，待验证')}${list(r.model_analysis.checks,'模型建议')}${list(r.model_analysis.unknowns,'模型提示的未知项')}</section>`:''}${list(r.hypotheses,'证据指向')}${list(r.checks,'建议核查路径',true)}<details class="agent-evidence" open><summary>引用证据 <b>${r.events.length}</b></summary>${r.events.map(e=>`<div class="agent-evidence-card"><div><strong>${esc(e.inverter)}</strong><span>${esc(kinds[e.evidence])}</span></div><small>${esc(e.event_id)} · ${esc(e.start_local)}</small><div class="agent-fact-grid"><span>实测均值<b>${esc(e.mean_actual_kw)} <small>kW</small></b></span><span>参考均值<b>${esc(e.mean_reference_kw)} <small>kW</small></b></span><span>有效其他同群<b>${esc(e.min_peer_count)} <small>台</small></b></span></div><p>${esc(e.samples)} 个采样点 · 跨度 ${esc(e.observed_span_minutes)} 分钟 · 参考电量差 ${esc(e.reference_shortfall_kwh)} kWh</p><button class="agent-link" data-agent-draft="${esc(e.event_id)}" data-agent-run="${esc(r.run_id||'')}" ${!r.run_id?'disabled':''}>生成核查工单草稿 ↗</button></div>`).join('') || '<p>当前范围无登记事件，不能据此判断设备正常。</p>'}</details>${list(r.unknowns,'仍需补充的信息')}<details class="agent-audit"><summary>查看工具记录与数据版本</summary><p>数据版本：${esc(r.metadata.dataset_version)}<br>筛查版本：${esc(r.metadata.screening_version)}<br>时区：${esc(r.metadata.timezone)}<br>快照观测时间：${esc(r.metadata.observed_at)}<br>审计编号：${esc(r.run_id||'本地预览，未保存服务端审计')}</p>${r.trace.map(t=>`<span>${esc(t.tool)} · ${esc(t.status)} · ${esc(t.result_count)} 条</span>`).join('')}</details></div></article>`;
    }
    function list(items,title,ordered=false) { return items?.length?`<section class="agent-answer-section"><h4>${esc(title)}</h4><${ordered?'ol':'ul'}>${items.map(x=>`<li>${esc(x)}</li>`).join('')}</${ordered?'ol':'ul'}></section>`:''; }
    function chat(compact=false) {
        const banner=!state.ready?'正在检查服务连接…':!state.connected?'服务未连接 · 可使用本地样例预览':`${mode()} · ${engine()}`;
        return `<section class="agent-chat"><div class="agent-chat-top"><div><i class="${state.connected?'connected':''}"></i><strong>运维分析会话</strong></div><span>${state.connected?'服务在线':'本地预览'}</span><button class="agent-link" data-agent-action="clear" ${state.busy?'disabled':''}>新会话 ＋</button></div><div class="agent-mode-banner">${esc(banner)}${!state.connected?'<button class="agent-link" data-agent-action="reconnect">连接服务</button>':''}</div><div class="agent-transcript" role="log" aria-label="运维助手对话" aria-busy="${state.busy}">${state.messages.length?state.messages.map(messageHtml).join(''):welcome(compact)}${state.busy?'<div class="agent-pending" role="status"><span></span>正在查询证据并整理核查路径…</div>':''}</div><div class="agent-input-area">${state.error?`<p class="agent-error" role="alert">${esc(state.error)}</p>`:''}<form class="agent-compose"><label class="sr-only" for="${compact?'drawer':'workbench'}-question">输入运维问题</label><textarea id="${compact?'drawer':'workbench'}-question" name="question" rows="2" maxlength="2000" placeholder="问一条异常，或描述你想核查的问题…" ${state.busy?'disabled':''}>${esc(state.question)}</textarea><div class="agent-compose-bottom"><span>${state.selected?esc(state.selected):'本站证据 · 只读分析'}</span><button type="submit" class="agent-send" ${state.busy?'disabled':''} aria-label="发送问题">${state.busy?'分析中…':'发送 ↑'}</button></div></form><small class="agent-input-note">${state.connected?'分析记录保存到服务端。工单需人工确认，尚未连接外部派单系统。':'本地预览仅使用构造样例，不调用模型，不保存服务端审计。'}</small></div></section>`;
    }
    function sidebar() {
        return `<aside class="agent-context"><span class="eyebrow">WORK CONTEXT</span><h3>从现场线索开始</h3><p>选择一个事件，把提问聚焦到具体证据。</p><label for="agent-context-event">当前关注事件</label><select id="agent-context-event"><option value="">全部登记事件</option>${events().map(e=>`<option value="${esc(e.event_id)}" ${state.selected===e.event_id?'selected':''}>${esc(e.inverter)} / ${esc(e.event_id)}</option>`).join('')}</select><div class="agent-side-section"><h4>本次工作边界</h4><div class="agent-scope-row"><span>数据来源</span><b>${mode()}</b></div><div class="agent-scope-row"><span>模型服务</span><b>${state.bootstrap?.model_configured?'已配置，待验证':'未接入'}</b></div><div class="agent-scope-row"><span>设备控制</span><b>无权限</b></div></div><div class="agent-side-section"><h4>最近分析</h4>${state.history.length?state.history.slice(0,5).map(h=>`<button class="agent-history" data-agent-history="${esc(h.run_id)}"><span>${esc(h.question)}</span><small>${esc(new Date(h.created_at).toLocaleString('zh-CN'))}</small></button>`).join(''):'<p class="agent-muted">完成一次分析后，<br>在这里回看证据与记录。</p>'}</div><div class="agent-side-foot">${glyph}<strong>每个建议，都有来处。</strong><p>候选证据 → 核查建议<br>→ 人工复核 → 留档追溯</p></div></aside>`;
    }
    function render() {
        return `<div class="agent-page-heading"><div><span class="eyebrow">INTELLIGENT OPERATIONS / 智能运维</span><h1>运维智能体<span>ASSISTANT</span></h1><p>读懂异常背后的证据，把下一步交给清晰的核查路径。</p></div><span class="agent-version">证据驱动 <i></i> V0.2</span></div><div id="agent-workbench" class="agent-workbench">${sidebar()}${chat()}</div>`;
    }
    function paint() {
        const workbench=document.querySelector('#agent-workbench');
        if(workbench) workbench.innerHTML=sidebar()+chat();
        if(drawer.open) drawer.innerHTML=`<div class="agent-drawer-heading"><div>${glyph}<strong>伏脉运维助手</strong><span>${esc(mode())}</span></div><button class="icon-button" data-agent-action="close" aria-label="关闭运维助手">✕</button></div>${chat(true)}`;
        for(const root of [workbench,drawer.open?drawer:null].filter(Boolean)) { const transcript=root.querySelector('.agent-transcript'); if(transcript && state.messages.length) transcript.scrollTop=transcript.scrollHeight; }
    }
    function localAnalysis(question) {
        const data=window.FumaiDemo;
        let rows=state.selected?data.events.filter(e=>e.event_id===state.selected):data.events.filter(e=>question.includes(e.inverter)||question.includes(e.event_id));
        if(!rows.length) rows=data.events.slice(0,3);
        return {summary:`已整理 ${rows.length} 条构造样例证据。此结果用于流程预览。`,hypotheses:[...new Set(rows.map(e=>kinds[e.evidence]+'，需要结合运行背景复核。'))],checks:['核对限发、检修和通信记录。','检查同群设备可比性、缺失观测与时间对齐。','由有资质人员按现场规程补充核查材料。'],unknowns:['当前未连接后端和模型；没有真实电站数据。','参考电量差不能解释为可挽回损失。'],events:rows,metadata:{source:'constructed_demo',dataset_version:'demo-2023-07-18-v1',screening_version:'demo-fixtures-v1',timezone:'America/Los_Angeles',observed_at:'2023-07-19T01:00:00Z'},trace:[{tool:'browser_demo_lookup',status:'ok',result_count:rows.length}],boundary:'候选证据需人工复核；当前为本地样例预览。',engine:'rules'};
    }
    async function send(question) {
        if(state.busy || !question.trim()) return;
        state.question=question; state.busy=true; state.error=''; state.messages.push({role:'user',text:question}); paint();
        controller=new AbortController(); const timeout=setTimeout(()=>controller.abort(),65000);
        try {
            const result=state.connected?await api('analyze',{message:question,event_id:state.selected||null}):localAnalysis(question);
            state.messages.push({role:'assistant',result}); state.question='';
            if(result.run_id) state.history.unshift({run_id:result.run_id,question,created_at:result.created_at});
        } catch(error) { state.error=error.name==='AbortError'?'等待超时。服务端可能仍在处理，请先查看最近分析，避免重复提交。':error.message; }
        finally { clearTimeout(timeout); controller=null; state.busy=false; paint(); }
    }
    async function makeDraft(runId,eventId) {
        try {
            state.draft=await api('drafts',{run_id:runId,event_id:eventId});
            const d=state.draft;
            draftDialog.innerHTML=`<form id="agent-confirm"><span class="eyebrow">HUMAN REVIEW REQUIRED</span><h2>确认核查工单草稿</h2><span class="agent-draft-badge">${d.content.metadata.source==='measured'?'实测证据':'样例演练'} · ${esc(d.status)}</span><h3>${esc(d.content.title)}</h3><p>事件：${esc(d.event_id)}<br>数据版本：${esc(d.content.metadata.dataset_version)}</p>${list(d.content.checks,'建议核查事项',true)}<div class="agent-boundary">${esc(d.content.notice)}</div><label>确认说明<textarea name="note" required minlength="2" maxlength="1000" placeholder="记录核对人、核对内容及后续安排…"></textarea></label><label class="agent-confirm-check"><input type="checkbox" name="confirmed" required>我已查看证据与数据来源，确认将此核查草稿留档。</label><p class="form-error" id="agent-confirm-error" role="alert"></p><div class="agent-form-actions"><button type="button" class="secondary-button" data-agent-action="close-draft">取消</button><button class="primary-button" ${d.status==='confirmed_draft'?'disabled':''}>确认留档</button></div></form>`;
            if(!draftDialog.open) draftDialog.showModal();
        } catch(error) {state.error=error.message;paint();}
    }
    function open(eventId='') {
        state.selected=eventId;
        if(eventId && state.bootstrap?.mode==='live') {state.selected='';state.error='业务页面这条线索来自样例数据。请在智能体工作台选择实测事件。';}
        if(!drawer.open) drawer.showModal(); paint(); drawer.querySelector('textarea')?.focus();
    }
    window.FumaiAgent={render,mount:paint,open};
    launcher.addEventListener('click',()=>open());
    drawer.addEventListener('close',()=>launcher.focus());
    document.addEventListener('input',event=>{if(event.target.matches('.agent-compose textarea')) state.question=event.target.value;});
    document.addEventListener('change',event=>{if(event.target.id==='agent-context-event') state.selected=event.target.value;});
    document.addEventListener('click',async event=>{
        const prompt=event.target.closest('[data-agent-prompt]'); if(prompt) {send(prompt.dataset.agentPrompt);return;}
        const contextual=event.target.closest('[data-agent-context]'); if(contextual) {document.querySelector('#event-dialog')?.close();open(contextual.dataset.agentContext);return;}
        const draft=event.target.closest('[data-agent-draft]'); if(draft) {makeDraft(draft.dataset.agentRun,draft.dataset.agentDraft);return;}
        const history=event.target.closest('[data-agent-history]'); if(history) {try {const result=await api('run',{run_id:history.dataset.agentHistory});state.messages=[{role:'assistant',result}];state.error='';paint();}catch(error){state.error=error.message;paint();}return;}
        const action=event.target.closest('[data-agent-action]')?.dataset.agentAction;
        if(action==='close') drawer.close();
        if(action==='clear'&&!state.busy) {state.messages=[];state.error='';state.question='';paint();}
        if(action==='close-draft') draftDialog.close();
        if(action==='close-login') login.close();
        if(action==='reconnect') {
            await connect();
            if(!state.connected) {if(location.protocol==='https:'||location.hostname==='127.0.0.1') login.showModal();else {state.error=state.connectionError+' 实测令牌只能通过 HTTPS 传输。';paint();}}
        }
    });
    document.addEventListener('submit',async event=>{
        if(event.target.matches('.agent-compose')) {event.preventDefault();send(new FormData(event.target).get('question'));}
        if(event.target.id==='agent-confirm') {
            event.preventDefault(); const button=event.target.querySelector('button.primary-button'); button.disabled=true;
            try {const fields=new FormData(event.target);const r=await api('confirm',{draft_id:state.draft.id,note:fields.get('note'),confirmed:fields.get('confirmed')==='on'});event.target.innerHTML=`<span class="eyebrow">SAVED FOR REVIEW</span><h2>草稿已确认留档</h2><p>${esc(r.message)}</p><p>编号：${esc(r.draft_id)}</p><button type="button" class="primary-button" data-agent-action="close-draft">完成</button>`;}
            catch(error){event.target.querySelector('#agent-confirm-error').textContent=error.message;button.disabled=false;}
        }
        if(event.target.id==='agent-login') {event.preventDefault();state.token=new FormData(event.target).get('token');await connect();if(state.connected){event.target.reset();login.close();}else{state.token='';event.target.querySelector('#agent-login-error').textContent=state.connectionError;}}
    });
    document.addEventListener('keydown',event=>{if(event.target.matches('.agent-compose textarea')&&event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();send(event.target.value);}});
    connect();
})();
