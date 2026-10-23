'use strict';
(() => {
  const tasks = {timeseries:'时序异常识别',classification:'图片分类',detection:'热点检测'};
  const decisions = {normal:'未发现对应异常',anomaly:'疑似异常',uncertain:'无法确定',blocked:'输入不符合要求',confirmed_fault:'人工确认故障',rejected:'人工排除',needs_investigation:'待进一步调查'};
  const kinds = {site_observation:'真实采集',public_research:'研究数据',synthetic_demo:'演示数据',legacy_unknown:'历史来源未标记'};
  const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const time = value => value ? new Date(value).toLocaleString('zh-CN',{hour12:false}) : '尚未接收';
  let stop = () => {};
  async function api(path, options={}) {
    const response = await fetch(path,{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(10000),...options});
    const value = await response.json();
    if (!response.ok) throw new Error([value.error,value.action].filter(Boolean).join('；') || '请求失败，请稍后重试');
    return value;
  }
  const post = (path,value) => api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
  function sample(device) {
    const id = globalThis.crypto?.randomUUID?.() || `sample-${Date.now()}`;
    return {request_id:id,schema_version:'competition-inference-v1',evidence:[{
      evidence_id:`demo-${id}`,plant_id:device?.plant_id || '请填写电站编号',asset_id:device?.id || '请填写系统设备编号',
      observation_id:`capture-${id}`,observed_at:new Date().toISOString(),task:'detection',model_version:'DEMO-NOT-A-REAL-MODEL',
      threshold_version:'DEMO-v1',data_kind:'synthetic_demo',quality:'ready',quality_reason:'演示输入，不代表真实识别',
      decision:'anomaly',score:0.93,model_output:{detections:[{bbox_xyxy:[10,20,80,100],score:0.93,class:'hotspot'}]}
    }]};
  }
  function html() {
    return `<section id="monitoring-panel" class="panel monitor-panel" aria-labelledby="monitoring-title">
      <div class="panel-heading"><div><h2 id="monitoring-title">设备监控日志</h2><p>查看后端实际接收的识别结果与人工复核记录。</p></div><button class="primary-button" type="button" id="monitor-refresh">刷新记录</button></div>
      <p class="monitor-note">模型服务尚待部署。本页已支持接收三类模型的结果；研究与演示数据会单独标记。下方原有设备卡片仍为样例。</p>
      <div id="monitor-summary" class="monitor-summary"></div>
      <div class="monitor-filters">
        <label>设备<select id="monitor-device"><option value="">全部设备</option></select></label>
        <label>模型<select id="monitor-task"><option value="">全部模型</option>${Object.entries(tasks).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></label>
        <label>数据来源<select id="monitor-kind"><option value="">全部来源</option>${Object.entries(kinds).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></label>
        <label>判定<select id="monitor-decision"><option value="">全部判定</option>${Object.entries(decisions).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></label>
      </div>
      <p id="monitor-message" class="monitor-message" role="status">正在读取设备与日志…</p>
      <div id="monitor-log-list"></div><div class="monitor-pages"><button id="monitor-prev" type="button" class="secondary-button">上一页</button><span id="monitor-page-label"></span><button id="monitor-next" type="button" class="secondary-button">下一页</button></div>
      <details class="monitor-config" id="monitor-import" hidden><summary>提交模型结果 JSON</summary><p>仅用于接口联调。不会运行模型。结果将保存到数据库，符合条件的异常会进入通知流程。</p><button type="button" id="monitor-sample" class="secondary-button">填入演示格式</button><label for="monitor-json">模型返回的完整 JSON</label><textarea id="monitor-json" rows="10" spellcheck="false" placeholder='{"schema_version":"competition-inference-v1","request_id":"...","evidence":[...]}'></textarea><button type="button" class="primary-button" id="monitor-submit">接收结果并写入日志</button><div id="monitor-receipt" role="status"></div></details>
      <details class="monitor-config" id="monitor-mapping" hidden><summary>设备编号对应关系</summary><p>模型设备编号与系统设备编号不一致时，在这里关联。系统设备请先到<a href="/notifications.html">通知中心</a>创建。</p><form id="monitor-map-form"><label>电站编号<input name="plant_id" required maxlength="160"></label><label>模型设备编号<input name="asset_id" required maxlength="160"></label><label>系统设备<select name="equipment_id" id="monitor-map-device" required></select></label><button class="primary-button" type="submit">保存对应关系</button></form><div id="monitor-map-list"></div></details>
      <dialog id="monitor-detail" class="monitor-dialog"><div class="monitor-dialog-head"><h2>设备识别详情</h2><button type="button" id="monitor-close" class="secondary-button">关闭</button></div><div id="monitor-detail-content"></div></dialog>
    </section>`;
  }
  async function mount(root) {
    stop();
    let active=true, refreshing=false,offset=0,lastUpdated=null,openedId=null;
    const timers=[],listeners=[];
    const intro=root.querySelector('.page-intro');
    if(intro)intro.insertAdjacentHTML('afterend',html());else root.insertAdjacentHTML('afterbegin',html());
    const panel=root.querySelector('#monitoring-panel');
    const q=id=>panel.querySelector('#'+id);
    let devices=[],user=null;
    const on=(target,event,fn)=>{target.addEventListener(event,fn);listeners.push(()=>target.removeEventListener(event,fn));};
    stop=()=>{active=false;timers.forEach(clearInterval);listeners.forEach(fn=>fn());q('monitor-detail')?.close();};
    const fail=error=>{if(active){lastUpdated=null;q('monitor-message').textContent=error.message;}};
    function clock() {if(active && lastUpdated)q('monitor-message').textContent=`已更新 · ${Math.floor((Date.now()-lastUpdated)/1000)} 秒前 · 每 5 秒自动刷新`;}
    function filters() {return new URLSearchParams({equipment_id:q('monitor-device').value,task:q('monitor-task').value,data_kind:q('monitor-kind').value,decision:q('monitor-decision').value,limit:'25',offset:String(offset)});}
    function renderDevices() {
      const value=q('monitor-device').value;
      const options=devices.map(d=>`<option value="${esc(d.id)}">${esc(d.name)} · ${esc(d.id)}</option>`).join('');
      q('monitor-device').innerHTML='<option value="">全部设备</option>'+options;q('monitor-device').value=value;
      const mappingValue=q('monitor-map-device').value;
      q('monitor-map-device').innerHTML='<option value="">请选择系统设备</option>'+options;q('monitor-map-device').value=mappingValue;
      q('monitor-summary').innerHTML=devices.length ? devices.map(d=>`<article><strong>${esc(d.name)}</strong><small>${esc(d.plant_id)} / ${esc(d.id)}</small>${d.model_states.length ? d.model_states.map(s=>`<p><span class="monitor-tag">${esc(kinds[s.data_kind])}</span> ${esc(tasks[s.task_name]||s.task_name)}：<b>${esc(decisions[s.decision]||s.decision)}</b><small>${esc(time(s.observed_at))}</small></p>`).join('') : '<p>尚未接收模型结果</p>'}</article>`).join('') : '<p class="monitor-empty">还没有系统设备。请先在通知中心创建设备，再关联模型设备编号。</p>';
    }
    async function refresh() {
      if(!active||refreshing||document.hidden)return;
      refreshing=true;q('monitor-refresh').disabled=true;
      try {
        const [catalog,logs]=await Promise.all([api('/api/monitoring/equipment'),api('/api/monitoring/logs?'+filters())]);
        if(!active)return;
        devices=catalog.equipment;renderDevices();
        q('monitor-log-list').innerHTML=logs.logs.length ? `<div class="table-scroll"><table class="event-table"><thead><tr><th>设备 / 采集时间</th><th>模型 / 来源</th><th>识别结果</th><th>记录说明</th><th>操作</th></tr></thead><tbody>${logs.logs.map(l=>`<tr><td>${esc(l.equipment_name)}<small>${esc(time(l.observed_at))}</small></td><td>${esc(tasks[l.task_name]||l.task_name)}<small>${esc(kinds[l.data_kind])}</small></td><td><span class="monitor-tag ${l.decision==='anomaly'?'alert':''}">${esc(decisions[l.decision]||l.decision)}</span><small>${l.kind==='human_confirmation'?'人工复核':'模型分数：'+(l.score==null?'未提供':Number(l.score).toFixed(3))}</small></td><td>${esc(l.summary)}</td><td><button type="button" class="table-link" data-monitor-log="${esc(l.id)}">查看证据</button></td></tr>`).join('')}</tbody></table></div>` : '<p class="monitor-empty">当前筛选条件下没有监控日志。模型未接入前，不会自动产生真实识别记录。</p>';
        q('monitor-prev').disabled=offset===0;q('monitor-next').disabled=offset+25>=logs.total;
        q('monitor-page-label').textContent=`共 ${logs.total} 条 · 第 ${Math.floor(offset/25)+1} 页`;
        lastUpdated=Date.now();clock();
      }catch(error){fail(error);}finally{refreshing=false;if(active)q('monitor-refresh').disabled=false;}
    }
    async function mappingList() {
      const data=await api('/api/model-asset-mappings');if(!active)return;
      q('monitor-map-list').innerHTML=data.mappings.map(m=>`<p>${esc(m.plant_id)} / ${esc(m.asset_id)} → ${esc(m.equipment_id)}</p>`).join('')||'<p>尚未设置；编号完全一致且电站一致时，可以直接关联。</p>';
    }
    async function detail(id) {
      const dialog=q('monitor-detail');openedId=id;q('monitor-detail-content').textContent='正在读取原始证据…';if(!dialog.open)dialog.showModal();
      try {
        const item=await api('/api/monitoring/logs/'+encodeURIComponent(id));if(!active||openedId!==id)return;
        q('monitor-detail-content').innerHTML=`<h3>${esc(item.equipment_name)} · ${esc(decisions[item.decision]||item.decision)}</h3><p>${esc(item.summary)}</p><dl><dt>采集时间</dt><dd>${esc(time(item.observed_at))}</dd><dt>写入时间</dt><dd>${esc(time(item.created_at))}</dd><dt>数据来源</dt><dd>${esc(kinds[item.data_kind])}</dd><dt>模型 / 阈值版本</dt><dd>${esc(item.model_version)} / ${esc(item.threshold_version)}</dd><dt>证据编号</dt><dd>${esc(item.evidence_id||'历史接口未提供')}</dd><dt>采集记录</dt><dd>${esc(item.raw_reference)}</dd></dl><details><summary>查看模型原始 JSON（含分类或检测框）</summary><pre>${esc(JSON.stringify(item.raw_result,null,2))}</pre></details><h3>人工核查记录</h3>${item.human_history.map(h=>`<p><b>${esc(decisions[h.status])}</b> · ${esc(h.actor)} · ${esc(time(h.created_at))}<br>${esc(h.note)}</p>`).join('')||'<p>尚无人工核查记录。</p>'}<h3>关联通知</h3>${item.notification_events.map(e=>`<p>${e.type==='confirmed_fault'?'人工确认故障':'模型疑似异常'} · ${e.simulated?'演练':'真实采集'} · <a href="/notifications.html">到通知中心查看发送状态</a></p>`).join('')||'<p>没有新通知。正常、不确定或质量不合格的结果不触发异常通知；同一异常也会合并通知。</p>'}${user?.role==='admin'?'<form id="monitor-confirm-form"><label>人工核查结论<select name="status"><option value="needs_investigation">待进一步调查</option><option value="confirmed_fault">人工确认故障</option><option value="rejected">人工排除</option></select></label><label>现场依据或处理说明<textarea name="note" required maxlength="4000" rows="3"></textarea></label><button class="primary-button" type="submit">追加人工核查记录</button><p id="monitor-confirm-message" role="status"></p></form>':''}`;
        const form=q('monitor-confirm-form');
        if(form)form.onsubmit=async event=>{event.preventDefault();const button=form.querySelector('button');button.disabled=true;try{const values=Object.fromEntries(new FormData(form));await post('/api/human-confirmations',{...values,model_result_id:item.model_result_id});await detail(id);await refresh();}catch(error){q('monitor-confirm-message').textContent=error.message;}finally{button.disabled=false;}};
      } catch(error){if(active&&openedId===id)q('monitor-detail-content').textContent=error.message;}
    }
    on(q('monitor-refresh'),'click',refresh);
    for(const id of ['monitor-device','monitor-task','monitor-kind','monitor-decision'])on(q(id),'change',()=>{offset=0;refresh();});
    on(q('monitor-prev'),'click',()=>{offset=Math.max(0,offset-25);refresh();});
    on(q('monitor-next'),'click',()=>{offset+=25;refresh();});
    on(panel,'click',event=>{const button=event.target.closest('[data-monitor-log]');if(button)detail(button.dataset.monitorLog);});
    on(q('monitor-close'),'click',()=>{openedId=null;q('monitor-detail').close();});
    on(q('monitor-sample'),'click',()=>{q('monitor-json').value=JSON.stringify(sample(devices.find(d=>d.id===q('monitor-device').value)||devices[0]),null,2);});
    on(q('monitor-submit'),'click',async()=>{
      const button=q('monitor-submit');button.disabled=true;
      try {
        let value;try{value=JSON.parse(q('monitor-json').value);}catch{throw new Error('JSON 格式不正确。请复制模型返回的完整内容；不要漏掉逗号或括号。');}
        const result=await post('/api/model-ingestions',value);if(!active)return;
        q('monitor-receipt').innerHTML=`<p>已接收 ${result.accepted} 条，重复 ${result.duplicates} 条，需要修改 ${result.rejected} 条。${result.replayed?'本次为重复请求，未重复写入。':''}</p>${result.items.map(i=>i.status==='rejected'?`<p class="monitor-error">第 ${i.index+1} 条：${esc(i.reason)}<br>修改方法：${esc(i.action)}</p>`:(i.warnings||[]).map(w=>`<p>${esc(w)}</p>`).join('')).join('')}`;await refresh();
      }catch(error){if(active)q('monitor-receipt').textContent=error.message;}finally{if(active)button.disabled=false;}
    });
    on(q('monitor-map-form'),'submit',async event=>{
      event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;
      try{await post('/api/model-asset-mappings',Object.fromEntries(new FormData(event.target)));await mappingList();q('monitor-message').textContent='设备编号对应关系已保存。之前未接收的条目请使用新请求编号重新提交。';}catch(error){fail(error);}finally{if(active)button.disabled=false;}
    });
    try{
      user=await (window.FumaiAuth || api('/api/auth/me').then(v=>v.user));if(!active)return;
      q('monitor-import').hidden=!['admin','operator'].includes(user.role);q('monitor-mapping').hidden=user.role!=='admin';
      await refresh();if(user.role==='admin')await mappingList();
      if(!active)return;timers.push(setInterval(refresh,5000),setInterval(clock,1000));
    }catch(error){fail(error);}
  }
  window.FumaiMonitoring={mount,unmount:()=>stop()};
})();
