/* Evidence workbench. Model keys never enter the browser. */
(() => {
    'use strict';
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const glyph = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M12 3v3m-6 2h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2Zm-4 4h2m16 0h2M8 20v2m8-2v2"/><path d="M8 12h.01M16 12h.01M8 16h8" stroke-linecap="round"/></svg>';
    const uuid = () => '10000000-1000-4000-8000-100000000000'.replace(/[018]/g, c => (Number(c) ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> Number(c) / 4).toString(16));
    let session;
    try { session = sessionStorage.getItem('fumai-agent-session') || uuid(); sessionStorage.setItem('fumai-agent-session', session); } catch { session = uuid(); }
    const state = { ready:false, connected:false, busy:false, bootstrap:null, messages:[], selected:'', error:'', token:'', draft:null, tab:'chat', question:'', history:[], connectionError:'', scope:'auto' };
    const kinds = {device_specific_candidate:'单机偏离同群',shared_or_model_candidate:'同群共性变化',insufficient_peers:'同群证据不足'};
    const taskStorageKey='fumai-agent-active-task';
    const stages={mcp_connect:'正在连接 MCP 工具',retrieving_knowledge:'正在检索知识库',tool_search_knowledge:'正在检索文档原文',tool_get_knowledge_chunk:'正在核对引用段落',creating:'正在提交分析任务',queued:'任务已排队，等待分析',running:'正在分析',reading_evidence:'正在查询异常证据',evidence:'正在查询异常证据',model_analysis:'模型正在整理核查建议',model:'模型正在整理核查建议',tool_call:'正在查询工具证据',tools:'正在查询工具证据',validating:'正在校验分析与原文证据',saving:'正在保存分析记录',model_round_1:'模型正在进行第 1 轮分析',model_round_2:'模型正在进行第 2 轮分析',model_round_3:'模型正在进行第 3 轮分析',tool_list_events:'正在查询异常事件列表',tool_get_event:'正在读取事件详细证据',tool_get_runbook:'正在查询核查规程',completed:'分析已完成',failed:'分析失败',interrupted:'任务已中断'};
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
    login.className='agent-login-dialog'; login.innerHTML='<form id="agent-login"><span class="eyebrow">SECURE CONNECTION</span><h2>连接实测工作空间</h2><p>输入管理员分配的个人访问令牌，仅在当前页面内存中使用。</p><label>访问令牌<input name="token" type="password" autocomplete="off" required minlength="32"></label><p class="form-error" id="agent-login-error"></p><div class="agent-form-actions"><button type="button" class="secondary-button" data-agent-action="close-login">取消</button><button class="primary-button">连接</button></div></form>';
    const launcher = document.createElement('button');
    launcher.className='agent-launcher'; launcher.id='agent-launcher'; launcher.setAttribute('aria-label','打开伏脉运维助手'); launcher.setAttribute('aria-haspopup','dialog'); launcher.innerHTML=glyph+'<span>运维助手</span><i></i>';
    document.body.append(drawer,draftDialog,login,launcher);
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
        try { state.bootstrap=await api('bootstrap'); state.connected=true; state.history=state.bootstrap.history; state.connectionError=''; }
        catch(error) { state.connected=false; state.connectionError=error.message; }
        state.ready=true; paint();
        if(state.connected && state.task && !monitoring) resumeTask();
    }
    const knowledgeDialog=document.createElement('dialog');
    knowledgeDialog.className='agent-knowledge-dialog';knowledgeDialog.setAttribute('aria-label','知识库与 MCP 工具');
    document.body.append(knowledgeDialog);
    let kbDocuments=[],kbTimer=null,kbTick=null,kbStarted=0,kbBusy=false;
    const kbStatus={processing:'正在建库',ready:'可检索',failed:'建库失败'};
    function sourceCards(chunks) {
        return (chunks||[]).map(c=>`<article class="agent-kb-source"><strong>${esc(c.title)}</strong><small>${esc(c.filename)} · ${esc(c.location)} · 段落 ${esc(c.ordinal)}</small><blockquote>${esc(c.text)}</blockquote><small>引用编号：${esc(c.citation_id||c.chunk_id)}${c.digest?' · 版本 '+esc(c.digest.slice(0,12)):''}</small></article>`).join('');
    }
    function knowledgeSources(r) {
        if(!r.knowledge_sources?.length) return r.knowledge_notice?`<p class="agent-boundary">${esc(r.knowledge_notice)}</p>`:'';
        const cited=new Set(r.model_analysis?.evidence_ids||[]);
        return `<details class="agent-evidence" open><summary>知识库原文出处 <b>${r.knowledge_sources.length}</b></summary>${r.knowledge_sources.map(c=>sourceCards([{...c,title:(cited.has(c.citation_id)?'［模型已引用］':'［检索参考］')+c.title}])).join('')}</details>`;
    }
    async function refreshKnowledge() {
        const data=await api('knowledge/documents');kbDocuments=data.documents;
        const holder=knowledgeDialog.querySelector('[data-kb-documents]');
        if(holder) holder.innerHTML=kbDocuments.length?kbDocuments.map(d=>`<article class="agent-kb-document"><div><strong>${esc(d.title)}</strong><small>${esc(d.filename)} · ${d.chunk_count} 段 · ${esc(kbStatus[d.status]||d.status)} · <span data-kb-seconds="${esc(d.id)}">${d.elapsed_seconds}</span> 秒</small>${d.error?`<p class="agent-error">${esc(d.error)}</p>`:''}</div><div>${d.status==='ready'?`<button class="agent-link" data-kb-preview="${esc(d.id)}">查看原文</button>`:''}${d.status==='failed'?`<button class="agent-link" data-kb-retry="${esc(d.id)}">重试建库</button>`:''}${d.status!=='processing'?`<button class="agent-link" data-kb-delete="${esc(d.id)}">删除</button>`:''}</div></article>`).join(''):'<p>还没有资料。上传适用的设备手册、故障码说明或检修记录后，即可检索并提问。</p>';
    }
    function kbMessage(text) {const node=knowledgeDialog.querySelector('[data-kb-message]');if(node)node.textContent=text;}
    async function showKnowledge(tab='knowledge') {
        if(!state.connected){state.error='请先连接智能体服务后再使用知识库。';paint();return;}
        knowledgeDialog.innerHTML=`<header><div><span class="eyebrow">KNOWLEDGE & TOOLS</span><h2>${tab==='tools'?'MCP 工具连接':'运维知识库'}</h2></div><button class="icon-button" data-agent-action="close-knowledge" aria-label="关闭知识库">✕</button></header><p data-kb-message role="status">正在读取…</p>${tab==='tools'?'<div data-kb-tools></div>':`<p>上传 → 后台提取与分段 → 建立检索索引 → 提问并核对原文出处。支持文字版 PDF、DOCX、TXT、MD，每份不超过 6 MB。</p><form id="agent-kb-upload"><label>资料名称<input name="title" maxlength="160" placeholder="可选，如：某型号逆变器运维手册" /></label><label>选择资料<input type="file" name="file" accept=".pdf,.docx,.txt,.md" required /></label><button class="primary-button" type="submit">上传并建库</button><small data-kb-upload-time></small></form><h3>已上传资料</h3><div data-kb-documents></div><h3>试一下检索</h3><form id="agent-kb-search"><label>输入型号、故障码或现象<input name="query" maxlength="2000" required placeholder="例如：绝缘电阻低需要核查哪些信息？" /></label><button class="primary-button" type="submit">检索原文</button></form><div data-kb-search-results></div>`}`;
        if(!knowledgeDialog.open)knowledgeDialog.showModal();
        clearInterval(kbTimer);clearInterval(kbTick);kbTimer=kbTick=null;
        try {
            if(tab==='tools') {
                const data=await api('tools');
                knowledgeDialog.querySelector('[data-kb-tools]').innerHTML=`<p>连接状态：${esc(data.status==='connected'?'握手与工具发现成功':data.status)} · 协议 ${esc(data.protocol)}</p><p>连接地址：<code>${esc(location.origin+data.endpoint)}</code></p><p>当前五个工具均为只读，分析结果中可查看实际调用记录。外部 MCP 客户端使用该地址及相同身份连接；实测模式使用管理员分配的 Bearer 令牌。</p>${data.tools.map(t=>`<article class="agent-kb-document"><div><strong>${esc(t.name)}</strong><p>${esc(t.description)}</p></div><span>只读</span></article>`).join('')}`;
                kbMessage('已通过 MCP initialize、tools/list 验证连接。');
            } else {
                await refreshKnowledge();kbMessage('资料仅在本工作空间可见。当前采用中文与英文关键词 BM25 检索；扫描件需先做 OCR。');
                kbTimer=setInterval(()=>{if(knowledgeDialog.open && kbDocuments.some(d=>d.status==='processing'))refreshKnowledge().catch(e=>kbMessage(e.message));},2000);
                kbTick=setInterval(()=>{
                    for(const d of kbDocuments.filter(d=>d.status==='processing')){const node=knowledgeDialog.querySelector(`[data-kb-seconds="${d.id}"]`);if(node)node.textContent=Math.max(0,Math.floor(Date.now()/1000-d.started));}
                    if(kbBusy){const node=knowledgeDialog.querySelector('[data-kb-upload-time]');if(node)node.textContent=`处理中 · 已用时 ${Math.floor((Date.now()-kbStarted)/1000)} 秒`;}
                },1000);
            }
        } catch(error){kbMessage(error.message);}
    }
    knowledgeDialog.addEventListener('close',()=>{clearInterval(kbTimer);clearInterval(kbTick);});
    function welcome(compact=false) {
        return `<div class="agent-welcome"><div class="agent-orbit">${glyph}<span></span></div><span class="eyebrow">FUMAI INTELLIGENCE</span><h2>让证据，先于判断。</h2><p>把异常线索变成清晰的核查路径。<br>从你关心的设备或事件开始。</p><div class="agent-prompts">${['梳理待复核事件，给出核查顺序','结合知识库资料分析 inv_07 的异常证据','检索知识库中关于绝缘电阻低的核查要求'].map((p,i)=>`<button data-agent-prompt="${esc(p)}"><span>0${i+1}</span>${esc(p)}<b>↗</b></button>`).join('')}</div>${compact?'':'<div class="agent-capabilities"><span>可追溯证据</span><span>分步核查</span><span>人工确认</span></div>'}</div>`;
    }
    function messageHtml(message) {
        if(message.role==='user') return `<article class="agent-message user"><span class="agent-message-avatar">我</span><div><small>你的问题</small><p>${esc(message.text)}</p></div></article>`;
        const r=message.result;
        return `<article class="agent-message assistant"><span class="agent-message-avatar">${glyph}</span><div class="agent-answer"><div class="agent-answer-meta"><strong>伏脉运维助手</strong><span>${r.model_status==='connected'?'模型辅助分析':'规则分析'} · ${r.metadata.source==='measured'?'实测证据':'构造样例'}${Number.isFinite(message.elapsedSeconds)?' · 用时 '+message.elapsedSeconds+' 秒':''}</span></div><p class="agent-answer-summary">${esc(r.summary)}</p><div class="agent-boundary">${esc(r.boundary)}</div>${r.model_analysis?`<section class="agent-model-text"><h4>模型辅助解释 <small>需人工复核</small></h4><p>${esc(r.model_analysis.summary)}</p>${list(r.model_analysis.hypotheses,'推测，待验证')}${list(r.model_analysis.checks,'模型建议')}${list(r.model_analysis.unknowns,'模型提示的未知项')}</section>`:''}${knowledgeSources(r)}${list(r.hypotheses,'证据指向')}${list(r.checks,'建议核查路径',true)}<details class="agent-evidence" open><summary>引用证据 <b>${r.events.length}</b></summary>${r.events.map(e=>`<div class="agent-evidence-card"><div><strong>${esc(e.inverter)}</strong><span>${esc(kinds[e.evidence])}</span></div><small>${esc(e.event_id)} · ${esc(e.start_local)}</small><div class="agent-fact-grid"><span>实测均值<b>${esc(e.mean_actual_kw)} <small>kW</small></b></span><span>参考均值<b>${esc(e.mean_reference_kw)} <small>kW</small></b></span><span>有效其他同群<b>${esc(e.min_peer_count)} <small>台</small></b></span></div><p>${esc(e.samples)} 个采样点 · 跨度 ${esc(e.observed_span_minutes)} 分钟 · 参考电量差 ${esc(e.reference_shortfall_kwh)} kWh</p><button class="agent-link" data-agent-draft="${esc(e.event_id)}" data-agent-run="${esc(r.run_id||'')}" ${!r.run_id?'disabled':''}>生成核查工单草稿 ↗</button></div>`).join('') || '<p>当前范围无登记事件，不能据此判断设备正常。</p>'}</details>${list(r.unknowns,'仍需补充的信息')}<details class="agent-audit"><summary>查看工具记录与数据版本</summary><p>数据版本：${esc(r.metadata.dataset_version)}<br>筛查版本：${esc(r.metadata.screening_version)}<br>时区：${esc(r.metadata.timezone)}<br>快照观测时间：${esc(r.metadata.observed_at)}<br>审计编号：${esc(r.run_id||'本地预览，未保存服务端审计')}</p>${r.trace.map(t=>`<span>${esc(t.tool)}${t.transport?" / "+esc(t.transport):""} · ${esc(t.status)} · ${esc(t.result_count)} 条</span>`).join('')}</details></div></article>`;
    }
    function list(items,title,ordered=false) { return items?.length?`<section class="agent-answer-section"><h4>${esc(title)}</h4><${ordered?'ol':'ul'}>${items.map(x=>`<li>${esc(x)}</li>`).join('')}</${ordered?'ol':'ul'}></section>`:''; }
    function chat(compact=false) {
        const banner=!state.ready?'正在检查服务连接…':!state.connected?'服务未连接 · 可使用本地样例预览':`${mode()} · ${engine()}`;
        return `<section class="agent-chat"><div class="agent-chat-top"><div><i class="${state.connected?'connected':''}"></i><strong>运维分析会话</strong></div><button class="agent-link" data-agent-action="knowledge">知识库</button><span>${state.connected?'服务在线':'本地预览'}</span><button class="agent-link" data-agent-action="clear" ${state.task||state.busy?'disabled':''}>新会话 ＋</button></div><div class="agent-mode-banner">${esc(banner)}${!state.connected?'<button class="agent-link" data-agent-action="reconnect">连接服务</button>':''}</div><div class="agent-transcript" role="log" aria-label="运维助手对话" aria-busy="${state.busy}">${state.messages.length?state.messages.map(messageHtml).join(''):welcome(compact)}${taskProgress()}</div><div class="agent-input-area">${state.error?`<p class="agent-error" role="alert">${esc(state.error)}</p>`:''}<form class="agent-compose"><label class="sr-only" for="${compact?'drawer':'workbench'}-question">输入运维问题</label><textarea id="${compact?'drawer':'workbench'}-question" name="question" rows="2" maxlength="2000" placeholder="问一条异常，或描述你想核查的问题…" ${state.task||state.busy?'disabled':''}>${esc(state.question)}</textarea><div class="agent-compose-bottom"><span>${state.selected?esc(state.selected):'本站证据 · 只读分析'}</span><button type="submit" class="agent-send" ${state.task||state.busy?'disabled':''} aria-label="发送问题">${state.task?(state.task.paused?'等待恢复':'分析中…'):'发送 ↑'}</button></div></form><small class="agent-input-note">${state.connected?'分析记录保存到服务端。工单需人工确认，尚未连接外部派单系统。':'本地预览仅使用构造样例，不调用模型，不保存服务端审计。'}</small></div></section>`;
    }
    function sidebar() {
        return `<aside class="agent-context"><span class="eyebrow">WORK CONTEXT</span><h3>从现场线索开始</h3><p>选择一个事件，把提问聚焦到具体证据。</p><div class="agent-kb-entry"><button class="primary-button" data-agent-action="knowledge">知识库 · 上传与检索</button><button class="agent-link" data-agent-action="tools">MCP 工具与连接</button></div><label for="agent-analysis-scope">分析范围</label><select id="agent-analysis-scope">${[['auto','综合分析：事件＋知识库'],['knowledge','只查知识库'],['events','只分析事件']].map(([value,label])=>`<option value="${value}" ${state.scope===value?'selected':''}>${label}</option>`).join('')}</select><label for="agent-context-event">当前关注事件</label><select id="agent-context-event"><option value="">全部登记事件</option>${events().map(e=>`<option value="${esc(e.event_id)}" ${state.selected===e.event_id?'selected':''}>${esc(e.inverter)} / ${esc(e.event_id)}</option>`).join('')}</select><div class="agent-side-section"><h4>本次工作边界</h4><div class="agent-scope-row"><span>数据来源</span><b>${mode()}</b></div><div class="agent-scope-row"><span>模型服务</span><b>${state.bootstrap?.model_configured?'已配置，待验证':'未接入'}</b></div><div class="agent-scope-row"><span>设备控制</span><b>无权限</b></div></div><div class="agent-side-section"><h4>最近分析</h4>${state.history.length?state.history.slice(0,5).map(h=>`<button class="agent-history" data-agent-history="${esc(h.run_id)}"><span>${esc(h.question)}</span><small>${esc(new Date(h.created_at).toLocaleString('zh-CN'))}</small></button>`).join(''):'<p class="agent-muted">完成一次分析后，<br>在这里回看证据与记录。</p>'}</div><div class="agent-side-foot">${glyph}<strong>每个建议，都有来处。</strong><p>候选证据 → 核查建议<br>→ 人工复核 → 留档追溯</p></div></aside>`;
    }
    function render() {
        return `<div class="agent-page-heading"><div><span class="eyebrow">INTELLIGENT OPERATIONS / 智能运维</span><h1>运维智能体<span>ASSISTANT</span></h1><p>读懂异常背后的证据，把下一步交给清晰的核查路径。</p></div><span class="agent-version">证据驱动 <i></i> V0.3</span></div><div id="agent-workbench" class="agent-workbench">${sidebar()}${chat()}</div>`;
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
    function validResult(result) {
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
            if(!state.history.some(h=>h.run_id===result.run_id)) state.history.unshift({run_id:result.run_id,question,created_at:result.created_at});
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
                    consumeTask(await api('analysis-tasks',{message:task.question,event_id:task.event_id||null,...(task.scope?{scope:task.scope}:{}),request_id:task.request_id}));
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
        state.task={request_id:uuid(),question,scope:state.scope,event_id:state.scope==='knowledge'?null:state.selected||null,stage:'creating',elapsedBase:0,elapsedAt:Date.now(),transport:'正在提交，任务编号会自动保存'};
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
    document.addEventListener('change',event=>{if(event.target.id==='agent-context-event') state.selected=event.target.value;if(event.target.id==='agent-analysis-scope')state.scope=event.target.value;});
    document.addEventListener('click',async event=>{
        const prompt=event.target.closest('[data-agent-prompt]'); if(prompt) {send(prompt.dataset.agentPrompt);return;}
        const contextual=event.target.closest('[data-agent-context]'); if(contextual) {document.querySelector('#event-dialog')?.close();open(contextual.dataset.agentContext);return;}
        const draft=event.target.closest('[data-agent-draft]'); if(draft) {makeDraft(draft.dataset.agentRun,draft.dataset.agentDraft);return;}
        const history=event.target.closest('[data-agent-history]'); if(history && state.task) return; if(history) {try {const result=await api('run',{run_id:history.dataset.agentHistory});state.messages=[{role:'assistant',result}];state.error='';paint();}catch(error){state.error=error.message;paint();}return;}
        const action=event.target.closest('[data-agent-action]')?.dataset.agentAction;
        if(action==='knowledge')showKnowledge();
        if(action==='tools')showKnowledge('tools');
        if(action==='close-knowledge')knowledgeDialog.close();
        const preview=event.target.closest('[data-kb-preview]'),retry=event.target.closest('[data-kb-retry]'),remove=event.target.closest('[data-kb-delete]');
        if(preview)try{const data=await api('knowledge/documents/'+preview.dataset.kbPreview+'/preview');knowledgeDialog.querySelector('[data-kb-search-results]').innerHTML=`<h3>${esc(data.document.title)}</h3>${sourceCards(data.chunks.map(c=>({...c,title:data.document.title,filename:data.document.filename})))}${data.truncated?'<p>仅展示前 80 段，其他内容仍参与检索。</p>':''}`;}catch(error){kbMessage(error.message);}
        if(retry)try{await api('knowledge/documents/'+retry.dataset.kbRetry+'/retry',{});await refreshKnowledge();}catch(error){kbMessage(error.message);}
        if(remove&&confirm('删除这份资料后，后续检索将不再使用它。已有分析的引用快照仍保留。确定删除？'))try{await api('knowledge/documents/'+remove.dataset.kbDelete+'/delete',{});await refreshKnowledge();}catch(error){kbMessage(error.message);}
        if(action==='close') drawer.close();
        if(action==='resume-task') resumeTask();
        if(action==='clear'&&!state.busy&&!state.task) {state.messages=[];state.error='';state.question='';paint();}
        if(action==='close-draft') draftDialog.close();
        if(action==='close-login') login.close();
        if(action==='reconnect') {
            await connect();
            if(!state.connected) {if(location.protocol==='https:'||location.hostname==='127.0.0.1') login.showModal();else {state.error=state.connectionError+' 实测令牌只能通过 HTTPS 传输。';paint();}}
        }
    });
    document.addEventListener('submit',async event=>{
        if(event.target.matches('.agent-compose')) {event.preventDefault();send(new FormData(event.target).get('question'));}
        if(event.target.id==='agent-kb-upload') {
            event.preventDefault();if(kbBusy)return;
            const fields=new FormData(event.target),file=fields.get('file'),button=event.target.querySelector('button');
            if(!file||!file.size||file.size>6*1024*1024){kbMessage('请选择有内容且不超过 6 MB 的文件。');return;}
            kbBusy=true;kbStarted=Date.now();button.disabled=true;kbMessage('正在上传，上传完成后由服务器后台建库。');
            try {
                const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('无法读取文件，请重新选择。'));reader.readAsDataURL(file);});
                const doc=await api('knowledge/documents',{filename:file.name,title:fields.get('title')||file.name,content_base64:encoded});
                kbMessage(doc.status==='ready'?'相同资料已建库，可直接检索。':doc.status==='failed'?doc.error:'上传完成，正在后台建库。可以关闭窗口，重新打开可继续查看进度。');await refreshKnowledge();event.target.reset();
            }catch(error){kbMessage(error.name==='AbortError'?'上传响应未收到。请刷新资料列表；如资料不存在，可重新上传，同一文件会自动去重。':error.message);}
            finally{kbBusy=false;button.disabled=false;const node=knowledgeDialog.querySelector('[data-kb-upload-time]');if(node)node.textContent=`上传耗时 ${Math.floor((Date.now()-kbStarted)/1000)} 秒`;}
        }
        if(event.target.id==='agent-kb-search') {
            event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;
            try{const result=await api('knowledge/search',{query:new FormData(event.target).get('query'),limit:5});knowledgeDialog.querySelector('[data-kb-search-results]').innerHTML=`<p>${esc(result.notice)}</p>${sourceCards(result.chunks)}`;}catch(error){kbMessage(error.message);}finally{button.disabled=false;}
        }
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
