/* Evidence workbench. Model keys never enter the browser. */
(() => {
    'use strict';
    // Follow the visible viewport when the mobile keyboard changes its height.
    // Pinch zoom keeps the existing layout instead of shrinking the workbench.
    if (window.visualViewport) {
        const viewport = window.visualViewport;
        const fitViewport = () => {
            if (viewport.scale !== 1) return;
            document.documentElement.style.setProperty('--agent-viewport-height', `${Math.round(viewport.height)}px`);
            const typing = document.activeElement?.matches('.agent-compose textarea');
            document.documentElement.classList.toggle('agent-keyboard-open', !!typing && viewport.height < window.innerHeight - 120);
        };
        viewport.addEventListener('resize', fitViewport);
        document.addEventListener('focusin', fitViewport);
        document.addEventListener('focusout', () => requestAnimationFrame(fitViewport));
        fitViewport();
    }
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const glyph = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M12 3v3m-6 2h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2Zm-4 4h2m16 0h2M8 20v2m8-2v2"/><path d="M8 12h.01M16 12h.01M8 16h8" stroke-linecap="round"/></svg>';
    const uuid = () => '10000000-1000-4000-8000-100000000000'.replace(/[018]/g, c => (Number(c) ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> Number(c) / 4).toString(16));
    let session,client=null,hadClient=false,freshSession=false,accessToken='';
    try {client=localStorage.getItem('fumai-agent-client');hadClient=!!client;client=client||uuid();localStorage.setItem('fumai-agent-client',client);} catch { /* Without persistent storage, keep the existing session-scoped visitor. */ }
    try {
        const navigation=window.performance?.getEntriesByType?.('navigation')?.[0]?.type;
        freshSession=hadClient&&navigation==='navigate';
        session=freshSession?uuid():sessionStorage.getItem('fumai-agent-session')||uuid();
        sessionStorage.setItem('fumai-agent-session',session);
        accessToken=sessionStorage.getItem('fumai-agent-access-token')||'';
    } catch {session=uuid();}
    const state = { ready:false, connected:false, busy:false, bootstrap:null, messages:[], selected:'', error:'', token:accessToken, draft:null, tab:'chat', question:'', history:[], connectionError:'',responseMode:'auto' };
    const kinds = {device_specific_candidate:'单机偏离同群',shared_or_model_candidate:'同群共性变化',insufficient_peers:'同群证据不足'};
    const taskStorageKey='fumai-agent-active-task';
    if(freshSession) {try {sessionStorage.removeItem(taskStorageKey);} catch { /* In-memory conversation still starts fresh. */ }}
    const stages={answering:'正在回答问题',creating:'正在提交分析任务',queued:'任务已排队，等待分析',running:'正在分析',reading_evidence:'正在查询异常证据',evidence:'正在查询异常证据',model_analysis:'模型正在整理核查建议',model:'模型正在整理核查建议',tool_call:'正在查询工具证据',tools:'正在查询工具证据',validating:'正在校验分析与原文证据',saving:'正在保存分析记录',model_round_1:'模型正在进行第 1 轮分析',model_round_2:'模型正在进行第 2 轮分析',model_round_3:'模型正在进行第 3 轮分析',tool_list_events:'正在查询异常事件列表',tool_get_event:'正在读取事件详细证据',tool_get_runbook:'正在查询核查规程',completed:'分析已完成',failed:'分析失败',interrupted:'任务已中断'};
    let clockTimer=null,monitoring=false;
    function loadTask() {
        try {
            const saved=JSON.parse(sessionStorage.getItem(taskStorageKey)||'null');
            if(saved && /^[0-9a-f-]{36}$/i.test(saved.request_id) && typeof saved.question==='string' && saved.question.length<=2000 && (!saved.task_id || typeof saved.task_id==='string' && saved.task_id.length<=128)) {
                return {...saved,elapsedBase:Math.max(0,Number(saved.elapsed_seconds)||0),elapsedAt:Number(saved.saved_at)||Date.now(),transport:'恢复上次分析任务',paused:true};
            }
        } catch { /* Storage may be unavailable in private browsing. */ }
        return null;
    }
    state.task=loadTask();
    if(state.task) state.messages.push({role:'user',text:state.task.question});
    function elapsedSeconds(task=state.task) { return task?Math.max(0,Math.floor(task.elapsedBase+(Date.now()-task.elapsedAt)/1000)):0; }
    function persistTask() {
        try {
            if(!state.task) sessionStorage.removeItem(taskStorageKey);
            else sessionStorage.setItem(taskStorageKey,JSON.stringify({...state.task,elapsed_seconds:elapsedSeconds(),saved_at:Date.now()}));
        } catch { /* The running request still works when browser storage is disabled. */ }
    }
    function tickClock() { for(const node of document.querySelectorAll('[data-agent-elapsed]')) node.textContent=`已用时 ${elapsedSeconds()} 秒`; }
    function startClock() { if(!clockTimer) clockTimer=setInterval(tickClock,1000); tickClock(); }
    function stopClock() { if(clockTimer) clearInterval(clockTimer); clockTimer=null; }
    function taskProgress() {
        if(!state.task) return '';
        return `<div class="agent-pending${state.task.paused?' paused':''}" role="status"><span class="agent-pending-dot"></span><div><strong data-agent-stage>${esc(stages[state.task.stage]||'正在整理核查路径')}</strong><small data-agent-transport>${esc(state.task.transport||'任务在服务器处理，离开页面后可恢复')}</small></div><b data-agent-elapsed>已用时 ${elapsedSeconds()} 秒</b>${state.task.paused?'<button class="agent-link" data-agent-action="resume-task">恢复任务</button>':''}</div>`;
    }
    function updateProgress(transport) {
        if(!state.task) return;
        if(transport) state.task.transport=transport;
        for(const node of document.querySelectorAll('[data-agent-stage]')) node.textContent=stages[state.task.stage]||'正在整理核查路径';
        for(const node of document.querySelectorAll('[data-agent-transport]')) node.textContent=state.task.transport||'';
        tickClock();
    }
    function requestError(message,status=0,retryable=true) { return Object.assign(new Error(message),{status,retryable}); }
    function requestHeaders(body=false) {
        const headers={'X-Fumai-Session':session};
        if(client) headers['X-Fumai-Client']=client;
        if(state.token) headers.Authorization='Bearer '+state.token;
        if(body) headers['Content-Type']='application/json';
        return headers;
    }
    async function checkedResponse(response) {
        const type=response.headers.get('content-type')||'';
        if(!response.ok) {
            const data=type.includes('application/json')?await response.json().catch(()=>({})):{};
            const message=response.status===401?'实测工作空间需要访问令牌，请点击“连接服务”后恢复任务。':data.error||'服务暂时不可用，正在等待恢复。';
            throw requestError(message,response.status,response.status>=500||response.status===408||response.status===429);
        }
        return type;
    }
    const drawer = document.createElement('dialog');
    drawer.id = 'agent-dialog'; drawer.className = 'agent-dialog'; drawer.setAttribute('aria-label','伏脉运维助手');
    const draftDialog = document.createElement('dialog');
    draftDialog.id='agent-draft-dialog'; draftDialog.className='agent-draft-dialog'; draftDialog.setAttribute('aria-label','确认核查工单草稿');
    const login = document.createElement('dialog');
    login.className='agent-login-dialog'; login.innerHTML='<form id="agent-login"><span class="eyebrow">PERSONAL WORKSPACE</span><h2>连接个人工作空间</h2><p>使用管理员分配的个人访问令牌；同一账号可查看自己的跨浏览器历史。令牌仅保存在当前标签页会话存储，退出账号后清除。访客模式只共享当前浏览器的历史。</p><label>个人访问令牌<input name="token" type="password" autocomplete="off" required minlength="32"></label><p class="form-error" id="agent-login-error"></p><div class="agent-form-actions"><button type="button" class="secondary-button" data-agent-action="close-login">取消</button><button class="primary-button">连接</button></div></form>';
    const launcher = document.createElement('button');
    launcher.className='agent-launcher'; launcher.id='agent-launcher'; launcher.setAttribute('aria-label','打开伏脉运维助手'); launcher.setAttribute('aria-haspopup','dialog'); launcher.innerHTML=glyph+'<span>运维助手</span><i></i>';
    document.body.append(drawer,draftDialog,login,launcher);
    const historyDialog=document.createElement('dialog');
    historyDialog.id='agent-history-dialog';historyDialog.className='agent-draft-dialog agent-session-dialog';historyDialog.setAttribute('aria-label','我的历史会话');document.body.append(historyDialog);
    const demoEvents = () => window.FumaiDemo?.events || [];
    const events = () => state.bootstrap?.events || demoEvents();
    const mode = () => state.bootstrap?.mode === 'live' ? '实测快照' : '样例演练';
    const engine = () => state.bootstrap?.model_configured ? '模型已配置 · 待请求验证' : '规则分析 · 模型未接入';
    async function api(path,body) {
        const requestController=new AbortController();
        const timeout=setTimeout(()=>requestController.abort(),15000);
        try {
            const response=await fetch('/agent-api/'+path,{method:body?'POST':'GET',headers:requestHeaders(Boolean(body)),body:body?JSON.stringify(body):undefined,signal:requestController.signal,cache:'no-store'});
            const type=await checkedResponse(response);
            if(!type.includes('application/json')) throw requestError('当前站点未连接智能体服务。',0,false);
            return await response.json();
        } finally { clearTimeout(timeout); }
    }
    async function connect() {
        try { state.bootstrap=await api('bootstrap'); state.connected=true; state.history=state.bootstrap.history; state.connectionError='';
            if(!state.messages.length&&!state.task&&Array.isArray(state.bootstrap.turns)) state.messages=turnMessages(state.bootstrap.turns);
        }
        catch(error) { state.connected=false; state.connectionError=error.message; }
        state.ready=true; paint();
        if(state.connected && state.task && !monitoring) resumeTask();
    }
    function welcome(compact=false) {
        return `<div class="agent-welcome"><div class="agent-orbit">${glyph}<span></span></div><span class="eyebrow">FUMAI INTELLIGENCE</span><h2>让证据，先于判断。</h2><p>把异常线索变成清晰的核查路径。<br>从你关心的设备或事件开始。</p><div class="agent-prompts">${['梳理待复核事件，给出核查顺序','分析 inv_07 的异常证据','同群证据不足时应该先查什么？'].map((p,i)=>`<button data-agent-prompt="${esc(p)}"><span>0${i+1}</span>${esc(p)}<b>↗</b></button>`).join('')}</div>${compact?'':'<div class="agent-capabilities"><span>可追溯证据</span><span>分步核查</span><span>人工确认</span></div>'}</div>`;
    }
    function messageHtml(message) {
        if(message.role==='user') return `<article class="agent-message user"><span class="agent-message-avatar">我</span><div><small>你的问题</small><p>${esc(message.text)}</p></div></article>`;
        const r=message.result;
        if(r.response_type==='direct') return FumaiIntent.renderDirect(r,esc,glyph);
        if(r.response_type==='answer'||r.presentation==='question_answer') return answerHtml(r);
        const reportSummary=r.answers?.length>1?r.summary:r.model_analysis?.summary||r.summary;
        const questions=r.answers?.length>1?r.answers.map((a,i)=>`<section class="agent-question-answer"><h4>${i+1}. ${esc(a.question)}</h4><p>${esc(a.answer)}</p></section>`).join(''):'';
        return `<article class="agent-message assistant"><span class="agent-message-avatar">${glyph}</span><div class="agent-answer"><div class="agent-answer-meta"><strong>伏脉运维助手</strong><span>完整诊断${Number.isFinite(message.elapsedSeconds)?' · 用时 '+message.elapsedSeconds+' 秒':''} · ${r.model_status==='connected'?'模型辅助':'规则整理'} · ${r.metadata.source==='measured'?'实测证据':'构造样例'}</span></div>${r.introduction&&!questions?`<p>${esc(r.introduction)}</p>`:''}<p class="agent-answer-summary">${esc(reportSummary)}</p>${questions}<div class="agent-boundary">${esc(r.boundary)}</div><details class="agent-evidence" open><summary>观测事实与引用证据 <b>${r.events.length}</b></summary>${r.events.map(e=>`<div class="agent-evidence-card"><div><strong>${esc(e.inverter)}</strong><span>${esc(kinds[e.evidence])}</span></div><small>${esc(e.event_id)} · ${esc(e.start_local)}</small><div class="agent-fact-grid"><span>实测均值<b>${esc(e.mean_actual_kw)} <small>kW</small></b></span><span>参考均值<b>${esc(e.mean_reference_kw)} <small>kW</small></b></span><span>有效其他同群<b>${esc(e.min_peer_count)} <small>台</small></b></span></div><p>${esc(e.samples)} 个采样点 · 跨度 ${esc(e.observed_span_minutes)} 分钟 · 参考电量差 ${esc(e.reference_shortfall_kwh)} kWh</p><button class="agent-link" data-agent-draft="${esc(e.event_id)}" data-agent-run="${esc(r.run_id||'')}" ${!r.run_id?'disabled':''}>生成核查工单草稿 ↗</button></div>`).join('')||'<p>当前范围没有登记事件，不能据此认定设备正常。</p>'}</details>${list(r.hypotheses,'待核查可能性 · 无原因排序依据')}${list(r.checks,'建议核查路径',true)}${list(r.unknowns,'未知信息与证据限制')}<details class="agent-audit"><summary>查看工具记录与数据版本</summary><p>数据版本：${esc(r.metadata.dataset_version)}<br>筛查版本：${esc(r.metadata.screening_version)}<br>时区：${esc(r.metadata.timezone)}<br>审计编号：${esc(r.run_id||'本地预览')}</p>${r.trace.map(t=>`<span>${esc(t.tool)} · ${esc(t.status)} · ${esc(t.result_count)} 条</span>`).join('')}</details></div></article>`;
    }
    function chatHeader() {
        return `<div class="agent-chat-top"><div><i class="${state.connected?'connected':''}"></i><strong>运维分析会话</strong></div><span>${state.connected?'服务在线':'本地预览'}</span><div class="agent-chat-controls"><select data-agent-mode aria-label="回答方式"><option value="auto" ${state.responseMode==='auto'?'selected':''}>自动选择</option><option value="chat" ${state.responseMode==='chat'?'selected':''}>直接问答</option><option value="report" ${state.responseMode==='report'?'selected':''}>完整诊断</option></select><button class="agent-link" data-agent-action="history" ${state.task||state.busy?'disabled':''}>历史</button><button class="agent-link" data-agent-action="clear" ${state.task||state.busy?'disabled':''}>新会话 ＋</button></div></div>`;
    }
    function list(items,title,ordered=false) { return items?.length?`<section class="agent-answer-section"><h4>${esc(title)}</h4><${ordered?'ol':'ul'}>${items.map(x=>`<li>${esc(x)}</li>`).join('')}</${ordered?'ol':'ul'}></section>`:''; }
    function answerHtml(r) {
        const labels={history:'历史查询',followup:'上下文追问',correction:'纠错反馈',knowledge:'知识问答',business:'业务查询',evidence_query:'观测查询',compound:'逐项回答',maintenance:'证据问答'};
        const answers=r.answers||[{question:'',answer:r.summary,status:'answered'}];
        return `<article class="agent-message assistant"><span class="agent-message-avatar">${glyph}</span><div class="agent-answer agent-conversation-answer"><div class="agent-answer-meta"><strong>伏脉运维助手</strong><span>${esc(labels[r.intent]||'直接问答')}${r.model_status==='connected'?' · 模型辅助':''}</span></div>${answers.map((a,i)=>`<section class="agent-question-answer">${answers.length>1?`<h4>${i+1}. ${esc(a.question)}</h4>`:''}${a.status&&a.status!=='answered'?`<span class="agent-answer-status">${a.status==='not_connected'?'数据未接入':'证据待补充'}</span>`:''}<p>${esc(a.answer)}</p></section>`).join('')}${r.history_records?.length?`<div class="agent-history-results">${r.history_records.map(h=>`<button class="agent-history" data-agent-history="${esc(h.run_id)}"><span>${esc(h.question)}</span><small>${esc(new Date(h.created_at).toLocaleString('zh-CN'))} · ${esc((h.devices||[]).join('、'))} · 打开会话 ↗</small></button>`).join('')}</div>`:''}${r.limitations?.length?`<p class="agent-reply-limit">${esc(r.limitations[0])}</p>`:''}${r.facts?.length?`<details class="agent-answer-facts"><summary>查看引用的观测事实 · ${r.facts.length}</summary>${r.facts.map(f=>`<p>${esc(f.text)}<small>${esc(f.event_id)}</small></p>`).join('')}</details>`:''}${r.source_run_ids?.length?`<div class="agent-context-reference">基于本会话已保存的分析${r.source_run_ids.map(id=>`<button class="agent-link" data-agent-history="${esc(id)}">回看引用分析 ↗</button>`).join('')}</div>`:''}</div></article>`;
    }
    function turnMessages(turns) {return turns.filter(validResult).flatMap(r=>[{role:'user',text:r.question||'历史问题'},{role:'assistant',result:r}]);}
    async function loadConversation(id) {
        if(state.busy||state.task)return;
        const data=await api('conversations',{conversation_id:id});
        session=data.conversation_id;try{sessionStorage.setItem('fumai-agent-session',session);}catch{}
        state.messages=turnMessages(data.turns);state.selected='';state.question='';state.error='';
        if(state.bootstrap){state.bootstrap.conversation_id=session;state.bootstrap.conversations=data.conversations;}
        paint();
    }
    async function newConversation() {
        session=uuid();try{sessionStorage.setItem('fumai-agent-session',session);}catch{}
        state.messages=[];state.selected='';state.error='';state.question='';state.draft=null;
        paint();if(state.connected)await connect();
    }
    function chat(compact=false) {
        const banner=!state.ready?'正在检查服务连接…':!state.connected?'服务未连接 · 可使用本地样例预览':`${mode()} · ${engine()}`;
        return `<section class="agent-chat">${chatHeader()}<div class="agent-mode-banner">${esc(banner)}${!state.connected?'<button class="agent-link" data-agent-action="reconnect">连接服务</button>':''}</div><div class="agent-transcript" role="log" aria-label="运维助手对话" aria-busy="${state.busy}">${state.messages.length?state.messages.map(messageHtml).join(''):welcome(compact)}${taskProgress()}</div><div class="agent-input-area">${state.error?`<p class="agent-error" role="alert">${esc(state.error)}</p>`:''}<form class="agent-compose"><label class="sr-only" for="${compact?'drawer':'workbench'}-question">输入运维问题</label><textarea id="${compact?'drawer':'workbench'}-question" name="question" rows="2" maxlength="2000" placeholder="问一条异常，或描述你想核查的问题…" ${state.task||state.busy?'disabled':''}>${esc(state.question)}</textarea><div class="agent-compose-bottom"><span>${state.selected?esc(state.selected):'本站证据 · 只读分析'}</span><button type="submit" class="agent-send" ${state.task||state.busy?'disabled':''} aria-label="发送问题">${state.task?(state.task.paused?'等待恢复':'分析中…'):'发送 ↑'}</button></div></form><small class="agent-input-note">${state.connected?'分析记录保存到服务端。工单需人工确认，尚未连接外部派单系统。':'本地预览仅使用构造样例，不调用模型，不保存服务端审计。'}</small></div></section>`;
    }
    function sidebar() {
        return `<aside class="agent-context"><span class="eyebrow">WORK CONTEXT</span><h3>从现场线索开始</h3><p>选择一个事件，把提问聚焦到具体证据。</p><label for="agent-context-event">当前关注事件</label><select id="agent-context-event"><option value="">全部登记事件</option>${events().map(e=>`<option value="${esc(e.event_id)}" ${state.selected===e.event_id?'selected':''}>${esc(e.inverter)} / ${esc(e.event_id)}</option>`).join('')}</select><div class="agent-side-section"><h4>本次工作边界</h4><div class="agent-scope-row"><span>数据来源</span><b>${mode()}</b></div><div class="agent-scope-row"><span>模型服务</span><b>${state.bootstrap?.model_configured?'已配置，待验证':'未接入'}</b></div><div class="agent-scope-row"><span>设备控制</span><b>无权限</b></div><div class="agent-scope-row"><span>历史归属</span><b>${state.bootstrap?.identity?.kind==='account'?'当前个人账号':'当前浏览器访客'}</b></div></div><div class="agent-side-section"><h4>最近分析</h4>${state.history.length?state.history.slice(0,5).map(h=>`<button class="agent-history" data-agent-history="${esc(h.run_id)}"><span>${esc(h.question)}</span><small>${esc(new Date(h.created_at).toLocaleString('zh-CN'))}</small></button>`).join(''):'<p class="agent-muted">完成一次分析后，<br>在这里回看证据与记录。</p>'}</div><div class="agent-side-foot">${glyph}<strong>每个建议，都有来处。</strong><p>候选证据 → 核查建议<br>→ 人工复核 → 留档追溯</p></div></aside>`;
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
        const intent=FumaiIntent.classify(question,!!state.selected);
        if(intent!=='maintenance') return FumaiIntent.offlineAnswer(intent);
        const data=window.FumaiDemo;
        let rows=state.selected?data.events.filter(e=>e.event_id===state.selected):data.events.filter(e=>question.includes(e.inverter)||question.includes(e.event_id));
        if(!rows.length) rows=data.events.slice(0,3);
        return {summary:`已整理 ${rows.length} 条构造样例证据。此结果用于流程预览。`,hypotheses:[...new Set(rows.map(e=>kinds[e.evidence]+'，需要结合运行背景复核。'))],checks:['核对限发、检修和通信记录。','检查同群设备可比性、缺失观测与时间对齐。','由有资质人员按现场规程补充核查材料。'],unknowns:['当前未连接后端和模型；没有真实电站数据。','参考电量差不能解释为可挽回损失。'],events:rows,metadata:{source:'constructed_demo',dataset_version:'demo-2023-07-18-v1',screening_version:'demo-fixtures-v1',timezone:'America/Los_Angeles',observed_at:'2023-07-19T01:00:00Z'},trace:[{tool:'browser_demo_lookup',status:'ok',result_count:rows.length}],boundary:'候选证据需人工复核；当前为本地样例预览。',engine:'rules'};
    }
    function validResult(result) {
        if(result?.response_type==='direct') return FumaiIntent.validDirect(result);
        if(result?.response_type==='answer') return typeof result.run_id==='string'&&typeof result.summary==='string'&&Array.isArray(result.answers)&&result.answers.length>0&&result.answers.every(a=>a&&typeof a.question==='string'&&typeof a.answer==='string'&&typeof a.status==='string')&&Array.isArray(result.events)&&Array.isArray(result.evidence_ids)&&Array.isArray(result.trace);
        const texts=value=>Array.isArray(value)&&value.every(x=>typeof x==='string');
        return result && typeof result==='object' && typeof result.run_id==='string' && typeof result.summary==='string' && typeof result.boundary==='string' &&
            texts(result.hypotheses)&&texts(result.checks)&&texts(result.unknowns) && Array.isArray(result.events)&&result.events.every(e=>e&&typeof e==='object'&&typeof e.event_id==='string') &&
            result.metadata&&typeof result.metadata==='object' && Array.isArray(result.trace)&&result.trace.every(t=>t&&typeof t==='object') &&
            (!result.model_analysis || typeof result.model_analysis.summary==='string'&&texts(result.model_analysis.hypotheses)&&texts(result.model_analysis.checks)&&texts(result.model_analysis.unknowns));
    }
    function finishTask(data) {
        const question=state.task.question;
        if(data.status==='completed'&&!validResult(data.result)) throw requestError('分析结果格式不完整，尚未展示。请恢复任务重新查询。',0,false);
        if(data.status==='completed') {
            const result=data.result;
            state.messages.push({role:'assistant',result,elapsedSeconds:elapsedSeconds()});
            if(result.response_type==='analysis'&&!state.history.some(h=>h.run_id===result.run_id)) state.history.unshift({run_id:result.run_id,question,created_at:result.created_at});
            state.question='';state.error='';
        } else { state.error=data.error||'本次分析失败，请稍后重新提交。';state.question=question; }
        state.task=null;state.busy=false;persistTask();stopClock();paint();
    }
    function consumeTask(data) {
        if(!state.task) return;
        if(!data||typeof data.task_id!=='string'||!['queued','running','completed','failed'].includes(data.status)||state.task.task_id&&state.task.task_id!==data.task_id) throw requestError('任务状态格式异常，正在重新查询。');
        state.task.task_id=data.task_id;
        state.task.status=data.status;
        state.task.stage=typeof data.stage==='string'?data.stage:data.status;
        state.task.elapsedBase=Math.max(elapsedSeconds(),Number(data.elapsed_seconds)||0);
        state.task.elapsedAt=Date.now();
        if(data.created_at) state.task.created_at=data.created_at;
        persistTask();
        if(data.status==='completed'||data.status==='failed') finishTask(data);
        else updateProgress();
    }
    // Parse chunks incrementally: UTF-8 characters and SSE lines can span network reads.
    function sseParser(accept) {
        let buffer='',event='',data=[];
        return chunk=>{
            buffer+=chunk;
            if(buffer.length>1048576) throw requestError('任务通知过大，正在重新查询。');
            let end;
            while((end=buffer.indexOf('\n'))>=0) {
                let line=buffer.slice(0,end);buffer=buffer.slice(end+1);
                if(line.endsWith('\r')) line=line.slice(0,-1);
                if(!line) { if(data.length && ['progress','done'].includes(event)) accept(JSON.parse(data.join('\n')));event='';data=[]; }
                else if(line.startsWith('event:')) event=line.slice(6).trim();
                else if(line.startsWith('data:')) data.push(line.slice(5).replace(/^ /,''));
            }
        };
    }
    async function streamTask(taskId) {
        const streamController=new AbortController();
        const timeout=setTimeout(()=>streamController.abort(),60000);
        let reader;
        try {
            const response=await fetch('/agent-api/analysis-tasks/'+encodeURIComponent(taskId)+'/events',{headers:{...requestHeaders(),Accept:'text/event-stream'},signal:streamController.signal,cache:'no-store'});
            const type=await checkedResponse(response);
            if(!type.includes('text/event-stream')||!response.body) throw requestError('进度连接不可用，正在查询任务状态。');
            updateProgress('实时接收分析进度，结果校验完成后展示');
            reader=response.body.getReader();
            const decoder=new TextDecoder(),parse=sseParser(consumeTask);
            while(state.task) {
                const {value,done}=await reader.read();
                if(done) {parse(decoder.decode());break;}
                parse(decoder.decode(value,{stream:true}));
            }
        } finally { clearTimeout(timeout);streamController.abort();if(reader) await reader.cancel().catch(()=>{}); }
    }
    const waitForPoll=()=>new Promise(resolve=>setTimeout(resolve,2000));
    async function resumeTask() {
        if(monitoring||!state.task) return;
        monitoring=true;state.busy=true;state.task.paused=false;state.error='';paint();startClock();
        try {
            while(state.task&&!state.task.task_id) {
                try {
                    const task=state.task;
                    consumeTask(await api('analysis-tasks',{message:task.question,event_id:task.event_id||null,request_id:task.request_id,...(task.response_mode?{response_mode:task.response_mode}:{})}));
                } catch(error) {
                    if(error.retryable===false) throw error;
                    updateProgress('提交响应暂未收到，正在用同一任务标识重试');await waitForPoll();
                }
            }
            if(state.task) {
                try { await streamTask(state.task.task_id); }
                catch(error) { if(error.retryable===false) throw error; }
            }
            while(state.task) {
                updateProgress('正在查询任务状态，服务器继续处理');await waitForPoll();
                try { consumeTask(await api('analysis-tasks/'+encodeURIComponent(state.task.task_id))); }
                catch(error) {
                    if(error.retryable===false) throw error;
                    updateProgress('连接暂时中断，正在自动重连；请勿重复提交');
                }
            }
        } catch(error) {
            state.busy=false;state.error=error.message;
            if(state.task && [400,404,409,422].includes(error.status)) {state.question=state.task.question;state.task=null;persistTask();stopClock();}
            if(state.task) {state.task.paused=true;state.task.transport='任务记录已保留，可点击恢复继续查询';persistTask();}
            if(error.status===401||error.status===403) state.connected=false;
            paint();
        } finally { monitoring=false;if(!state.task) {state.busy=false;stopClock();} }
    }
    async function send(question) {
        if(state.busy||state.task||typeof question!=='string'||!question.trim()) return;
        state.error='';state.messages.push({role:'user',text:question});state.question='';
        if(!state.connected) {state.messages.push({role:'assistant',result:localAnalysis(question)});paint();return;}
        state.task={request_id:uuid(),question,event_id:state.selected||null,response_mode:state.responseMode,stage:'creating',elapsedBase:0,elapsedAt:Date.now(),transport:'正在提交，任务编号会自动保存'};
        persistTask(); // Persist before POST, so a lost response or reload reuses the same request ID.
        await resumeTask();
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
    document.addEventListener('change',event=>{if(event.target.id==='agent-context-event') state.selected=event.target.value;if(event.target.matches('[data-agent-mode]'))state.responseMode=event.target.value;if(event.target.id==='agent-conversation-picker'&&event.target.value)loadConversation(event.target.value).catch(error=>{state.error=error.message;paint();});});
    document.addEventListener('click',async event=>{
        const prompt=event.target.closest('[data-agent-prompt]'); if(prompt) {send(prompt.dataset.agentPrompt);return;}
        const contextual=event.target.closest('[data-agent-context]'); if(contextual) {document.querySelector('#event-dialog')?.close();open(contextual.dataset.agentContext);return;}
        const draft=event.target.closest('[data-agent-draft]'); if(draft) {makeDraft(draft.dataset.agentRun,draft.dataset.agentDraft);return;}
        const history=event.target.closest('[data-agent-history]'); if(history && (state.task||state.busy)) return; if(history) {try {const result=await api('run',{run_id:history.dataset.agentHistory});if(result.conversation_id)await loadConversation(result.conversation_id);else{state.messages=[{role:'assistant',result}];state.error='';paint();}}catch(error){state.error=error.message;paint();}return;}
        const action=event.target.closest('[data-agent-action]')?.dataset.agentAction;
        if(action==='close') drawer.close();
        if(action==='resume-task') resumeTask();
        if(action==='clear'&&!state.busy&&!state.task) await newConversation();
        if(action==='history'&&!state.busy&&!state.task) {
            try {
                const data=await api('conversations');const account=state.bootstrap?.identity?.kind==='account';
                historyDialog.innerHTML=`<div class="dialog-heading"><span class="eyebrow">MY CONVERSATIONS</span><button class="icon-button" data-agent-action="close-history" aria-label="关闭历史会话">✕</button></div><h2>我的历史会话</h2><p>${account?'当前账号的个人历史；其他用户的私人对话不会显示。':'当前浏览器的访客历史，可跨标签查看；跨浏览器个人历史需要连接同一账号。'}</p><button class="agent-link" data-agent-action="account">${state.token?'退出账号':'连接个人账号'}</button><div class="agent-session-list">${data.conversations.length?data.conversations.map(c=>`<button class="agent-history" data-agent-conversation="${esc(c.id)}"><span>${esc(c.title||'新会话')}</span><small>${esc(new Date(c.updated_at||c.created_at).toLocaleString('zh-CN'))} · ${esc(c.turns)} 轮对话</small></button>`).join(''):'<p>尚无已保存的会话。新会话不会继承旧会话的上下文。</p>'}</div>`;
                historyDialog.showModal();
            }catch(error){state.error=error.message;paint();}
        }
        const conversation=event.target.closest('[data-agent-conversation]');
        if(conversation&&!state.busy&&!state.task){try{await loadConversation(conversation.dataset.agentConversation);historyDialog.close();}catch(error){state.error=error.message;paint();}return;}
        if(action==='close-history')historyDialog.close();
        if(action==='account'&&!state.busy&&!state.task) {
            historyDialog.close();
            if(state.token){state.token='';try{sessionStorage.removeItem('fumai-agent-access-token');}catch{}await newConversation();}
            else if(location.protocol==='https:'||location.hostname==='127.0.0.1')login.showModal();
            else{state.error='个人账号历史需要 HTTPS 和管理员配置的个人访问令牌。当前为浏览器访客，历史仅在同一浏览器内共享。';paint();}
        }
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
        if(event.target.id==='agent-login') {event.preventDefault();const previous={session,token:state.token,messages:state.messages};state.token=new FormData(event.target).get('token');session=uuid();state.messages=[];try{sessionStorage.setItem('fumai-agent-session',session);}catch{}await connect();if(state.connected){try{sessionStorage.setItem('fumai-agent-access-token',state.token);}catch{}event.target.reset();login.close();}else{const error=state.connectionError;session=previous.session;state.token=previous.token;state.messages=previous.messages;try{sessionStorage.setItem('fumai-agent-session',session);}catch{}await connect();event.target.querySelector('#agent-login-error').textContent=error;}}
    });
    document.addEventListener('keydown',event=>{if(event.target.matches('.agent-compose textarea')&&event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();send(event.target.value);}});
    connect();
})();
