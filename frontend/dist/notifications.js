'use strict';
(() => {
  const $ = selector => document.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
  const message = (text, ok = false) => { $('#message').textContent = text; $('#message').style.color = ok ? '#23704f' : '#a93b35'; };
  const api = async (url, options = {}) => {
    const response = await fetch(url, {
      credentials: 'same-origin', ...options,
      headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) }
    });
    if (response.status === 401) { location.replace(`/login.html?return=${encodeURIComponent(location.pathname)}`); throw new Error('请先登录'); }
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `请求失败 (${response.status})`);
    return body;
  };
  const statusNames = { pending: '待发送', processing: '发送中', retry_wait: '等待重试', verify_pending: '待核验', simulated_sent: '模拟成功', sent: '已发送', failed: '失败', needs_attention: '待处理' };
  const eventNames = { model_anomaly: '疑似异常', confirmed_fault: '确认故障', daily_generation_ready: '日发电通知' };
  const roleNames = { admin: '管理员', operator: '操作员', viewer: '查看者' };
  const providerNames = { mock: '模拟 / DRY-RUN', smtp: 'SMTP 真实邮件', gammu: 'Gammu 短信猫', unconfigured: '未配置' };
  const mask = value => {
    if (!value) return '未填写';
    const at = value.indexOf('@');
    if (at > 0) return `${value.slice(0, 2)}***${value.slice(at)}`;
    return value.length > 7 ? `${value.slice(0, 3)}****${value.slice(-4)}` : '***';
  };
  let people = [];
  let equipment = [];
  let selectedEvent = null;
  let selectedCategory = 'fault';
  let currentUser = null;

  async function loadHealth() {
    const health = await api('/api/health');
    const email = health.providers?.email || 'mock';
    const sms = health.providers?.sms || 'mock';
    $('#email-provider').textContent = providerNames[email] || email;
    $('#sms-provider').textContent = providerNames[sms] || sms;
    const real = email !== 'mock' || sms !== 'mock';
    $('#provider-mode').innerHTML = `<i></i>${real ? '真实渠道已接入' : 'MOCK / DRY-RUN'}`;
    $('#provider-mode').classList.toggle('real', real);
  }

  async function loadUsers() {
    if (currentUser.role !== 'admin') return;
    const users = (await api('/api/users')).users;
    $('#user-count').textContent = users.length;
    $('#user-list').innerHTML = users.length ? users.map(user => `<div class="row"><div><strong>${esc(user.display_name)}</strong><small>${esc(user.username)} · ${esc(roleNames[user.role])}${user.last_login_at ? ` · 最近登录 ${esc(user.last_login_at)}` : ''}</small></div><span class="tag ${user.enabled ? '' : 'off'}">${user.enabled ? '启用' : '停用'}</span></div>`).join('') : '<p class="empty">暂无网站账号</p>';
  }

  async function loadPeople() {
    people = (await api('/api/personnel')).personnel;
    if ($('#person-count')) $('#person-count').textContent = people.length;
    if ($('#person-list')) $('#person-list').innerHTML = people.length ? people.map(person => `<div class="row"><div><strong>${esc(person.name)}</strong><small>${esc(person.id)} · ${esc(mask(person.phone))} · ${esc(mask(person.email))}</small></div><span class="tag ${!person.enabled ? 'off' : (!person.phone || !person.email ? 'warn' : '')}">${!person.enabled ? '停用' : (!person.email ? '未填通知邮箱' : !person.phone ? '邮件可用 · 未填手机号' : '启用')}</span></div>`).join('') : '<p class="empty">暂无人员</p>';
    if ($('#owner-select')) $('#owner-select').innerHTML = '<option value="">未分配</option>' + people.filter(p => p.enabled).map(p => `<option value="${esc(p.id)}">${esc(p.name)}（${esc(p.id)}）</option>`).join('');
  }

  async function loadEquipment() {
    equipment = (await api('/api/equipment')).equipment;
    if ($('#equipment-count')) $('#equipment-count').textContent = equipment.length;
    if ($('#equipment-list')) $('#equipment-list').innerHTML = equipment.length ? equipment.map(item => `<div class="row"><div><strong>${esc(item.name)}</strong><small>${esc(item.plant_id)} / ${esc(item.id)}</small></div><span class="tag ${item.owner_id ? '' : 'warn'}">${esc(item.owner_name || '未分配')}</span></div>`).join('') : '<p class="empty">暂无设备</p>';
  }

  async function loadEvents() {
    const [filtered, all] = await Promise.all([
      api(`/api/notifications/events?limit=100&category=${selectedCategory}`),
      api('/api/notifications/events?limit=500&category=all')
    ]);
    const events = filtered.events;
    $('#fault-count').textContent = all.events.filter(event => event.category === 'fault').length;
    $('#daily-count').textContent = all.events.filter(event => event.category === 'daily').length;
    $('#event-list').innerHTML = events.length ? events.map(event => {
      const facts = event.fact_snapshot || {};
      const title = facts.equipment_name || facts.plant_id || event.source_id;
      return `<button class="event-button ${event.id === selectedEvent ? 'active' : ''}" data-event="${esc(event.id)}"><strong>${esc(eventNames[event.type] || event.type)} · ${esc(title)}</strong><small>${esc(event.created_at)} · ${event.delivered || 0}/${event.delivery_count || 0} 成功 · ${event.attention || 0} 待处理</small></button>`;
    }).join('') : `<p class="empty">暂无${selectedCategory === 'fault' ? '故障' : '日发电'}通知</p>`;
    document.querySelectorAll('[data-event]').forEach(button => button.addEventListener('click', () => showEvent(button.dataset.event)));
    if (selectedEvent && events.some(event => event.id === selectedEvent)) await showEvent(selectedEvent, false);
    else { selectedEvent = null; $('#event-detail').innerHTML = '<p class="empty">选择事件查看每位收件人的短信、邮件状态和尝试历史。</p>'; }
  }

  async function showEvent(id, reloadList = true) {
    selectedEvent = id;
    const event = await api(`/api/notifications/events/${encodeURIComponent(id)}`);
    const facts = event.fact_snapshot || {};
    const fields = event.type === 'daily_generation_ready' ? [
      ['电站', facts.plant_id], ['当地日期', facts.local_date], ['实际电量', `${facts.actual_kwh} kWh`],
      ['预测电量', `${facts.forecast_kwh} kWh${facts.is_test_forecast ? '（模拟）' : ''}`], ['修订版', facts.revision], ['时区', facts.business_timezone]
    ] : [['设备', facts.equipment_name || facts.equipment_id], ['事实状态', facts.fact_status], ['观测时间', facts.observed_at], ['事件键', facts.incident_key], ['类别', facts.suspected_class || '—'], ['通知分类', '故障通知']];
    const deliveries = event.deliveries.map(item => `<div class="delivery"><div><strong>${esc(item.recipient_name)}</strong><div class="attempts">${esc(mask(item.address_snapshot))} · 尝试 ${item.attempt_count} 次${item.attempts.length ? ` · ${esc(item.attempts.at(-1).error_code || item.attempts.at(-1).outcome)}` : ''}</div></div><strong>${item.channel === 'sms' ? '短信' : '邮件'}</strong><span class="status ${esc(item.status)}">${esc(statusNames[item.status] || item.status)}</span>${currentUser.role === 'admin' && item.status === 'failed' ? `<button class="quiet" data-retry="${esc(item.id)}">人工重试</button>` : ''}</div>`).join('');
    const problems = event.problems.map(problem => `<div class="problem"><strong>${esc(problem.code)}</strong>：${esc(problem.detail)}</div>`).join('');
    $('#event-detail').innerHTML = `<div class="section-head"><div><p class="eyebrow">${esc(eventNames[event.type] || event.type)}</p><h2>${esc(event.dedupe_key)}</h2></div></div><div class="facts">${fields.map(([key, value]) => `<div class="fact"><small>${esc(key)}</small><strong>${esc(value ?? '—')}</strong></div>`).join('')}</div><h3>收件人与渠道</h3>${deliveries || '<p class="empty">没有可发送的收件人</p>'}${problems}`;
    document.querySelectorAll('[data-retry]').forEach(button => button.addEventListener('click', async () => {
      try { await api(`/api/notifications/deliveries/${encodeURIComponent(button.dataset.retry)}/retry`, { method: 'POST', body: '{}' }); message('已记录人工重试操作。', true); await showEvent(id); } catch (error) { message(error.message); }
    }));
    if (reloadList) await loadEvents();
  }

  function bindForms() {
    $('#my-email-form')?.addEventListener('submit',async event=>{event.preventDefault();const button=event.currentTarget.querySelector('button');button.disabled=true;try{const result=await api('/api/auth/notification-email',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(event.currentTarget)))});message(result.message,true);await loadPeople();}catch(error){message(error.message);}finally{button.disabled=false;}});
    $('#user-form')?.addEventListener('submit', async event => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const input = Object.fromEntries(form);
      input.enabled = form.has('enabled');
      if(input.email==='')delete input.email;
      try { await api('/api/users', { method: 'POST', body: JSON.stringify(input) }); message('网站账号已保存。', true); event.currentTarget.reset(); event.currentTarget.elements.enabled.checked = true; await loadUsers(); }
      catch (error) { message(error.message); }
    });
    $('#person-form')?.addEventListener('submit', async event => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const input = Object.fromEntries(form);
      input.enabled = form.has('enabled');
      try { const result = await api('/api/personnel', { method: 'POST', body: JSON.stringify(input) }); message(result.warnings?.length ? `人员已保存，但需补充：${result.warnings.join('、')}` : '人员已保存。', !result.warnings?.length); await loadPeople(); }
      catch (error) { message(error.message); }
    });
    $('#equipment-form')?.addEventListener('submit', async event => {
      event.preventDefault();
      try { await api('/api/equipment', { method: 'POST', body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget))) }); message('设备负责人已保存并留存变更历史。', true); await loadEquipment(); }
      catch (error) { message(error.message); }
    });
  }

  $('#run-worker')?.addEventListener('click', async event => {
    event.currentTarget.disabled = true;
    const progress = $('#job-progress');
    progress.hidden = false;
    try {
      const task = await api('/api/notification-jobs', { method: 'POST', body: '{}' });
      const stream = new EventSource(`/api/notification-jobs/${encodeURIComponent(task.id)}/events`);
      const update = payload => {
        const current = JSON.parse(payload.data);
        progress.querySelector('span').style.width = `${current.progress}%`;
        progress.querySelector('strong').textContent = `异步处理：${current.progress}% · ${current.status}`;
        if (['succeeded', 'failed'].includes(current.status)) { stream.close(); event.currentTarget.disabled = false; message(current.status === 'succeeded' ? `处理完成：${current.result?.processed || 0} 条。` : current.error, current.status === 'succeeded'); loadEvents(); }
      };
      stream.addEventListener('progress', update); stream.addEventListener('done', update);
      stream.onerror = () => { stream.close(); event.currentTarget.disabled = false; message('进度流断开，可刷新查看最终状态。'); };
    } catch (error) { event.currentTarget.disabled = false; message(error.message); }
  });

  document.querySelectorAll('[data-category]').forEach(button => button.addEventListener('click', () => {
    selectedCategory = button.dataset.category;
    selectedEvent = null;
    document.querySelectorAll('[data-category]').forEach(item => item.classList.toggle('active', item === button));
    loadEvents().catch(error => message(error.message));
  }));
  $('#refresh').addEventListener('click', () => loadEvents().catch(error => message(error.message)));

  window.FumaiAuth.then(async user => {
    currentUser = user;
    $('#current-user').textContent = `${user.display_name} · ${roleNames[user.role]}`;
    if (user.role !== 'admin') document.querySelectorAll('[data-admin-only]').forEach(element => element.hidden = true);
    bindForms();
    const ownContact=await api('/api/auth/notification-email');$('#my-email').value=ownContact.contact.email||'';
    await Promise.all([loadHealth(), loadPeople(), loadEquipment(), loadEvents(), loadUsers()]);
  }).catch(error => message(error.message));
})();
