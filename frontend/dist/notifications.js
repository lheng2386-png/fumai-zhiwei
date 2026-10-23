'use strict';
(() => {
  const $ = selector => document.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
  const message = (text, ok = false) => { $('#message').textContent = text; $('#message').style.color = ok ? '#23704f' : '#a93b35'; };
  const actor = () => $('#actor').value.trim();
  const headers = () => ({ 'Content-Type': 'application/json', 'X-Fumai-Role': 'admin', 'X-Fumai-Actor': actor() });
  const api = async (url, options = {}) => {
    const response = await fetch(url, { ...options, headers: { ...(options.body ? headers() : {}), ...(options.headers || {}) } });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `请求失败 (${response.status})`);
    return body;
  };
  const statusNames = { pending: '待发送', processing: '发送中', retry_wait: '等待重试', verify_pending: '待核验', simulated_sent: '模拟成功', sent: '已发送', failed: '失败', needs_attention: '待处理' };
  const eventNames = { model_anomaly: '疑似异常', confirmed_fault: '确认故障', daily_generation_ready: '日发电通知' };
  const mask = value => {
    if (!value) return '未填写';
    const at = value.indexOf('@');
    if (at > 0) return `${value.slice(0, 2)}***${value.slice(at)}`;
    return value.length > 7 ? `${value.slice(0, 3)}****${value.slice(-4)}` : '***';
  };
  let people = [];
  let equipment = [];
  let selectedEvent = null;

  async function loadPeople() {
    people = (await api('/api/personnel')).personnel;
    $('#person-count').textContent = people.length;
    $('#person-list').innerHTML = people.length ? people.map(person => `<div class="row"><div><strong>${esc(person.name)}</strong><small>${esc(person.id)} · ${esc(mask(person.phone))} · ${esc(mask(person.email))}</small></div><span class="tag ${!person.enabled ? 'off' : (!person.phone || !person.email ? 'warn' : '')}">${!person.enabled ? '停用' : (!person.phone || !person.email ? '信息不全' : '启用')}</span></div>`).join('') : '<p class="empty">暂无人员</p>';
    $('#owner-select').innerHTML = '<option value="">未分配</option>' + people.filter(p => p.enabled).map(p => `<option value="${esc(p.id)}">${esc(p.name)}（${esc(p.id)}）</option>`).join('');
  }

  async function loadEquipment() {
    equipment = (await api('/api/equipment')).equipment;
    $('#equipment-count').textContent = equipment.length;
    $('#equipment-list').innerHTML = equipment.length ? equipment.map(item => `<div class="row"><div><strong>${esc(item.name)}</strong><small>${esc(item.plant_id)} / ${esc(item.id)}</small></div><span class="tag ${item.owner_id ? '' : 'warn'}">${esc(item.owner_name || '未分配')}</span></div>`).join('') : '<p class="empty">暂无设备</p>';
  }

  async function loadEvents() {
    const events = (await api('/api/notifications/events?limit=100')).events;
    $('#event-list').innerHTML = events.length ? events.map(event => {
      const facts = event.fact_snapshot || {};
      const title = facts.equipment_name || facts.plant_id || event.source_id;
      return `<button class="event-button ${event.id === selectedEvent ? 'active' : ''}" data-event="${esc(event.id)}"><strong>${esc(eventNames[event.type] || event.type)} · ${esc(title)}</strong><small>${esc(event.created_at)} · ${event.delivered || 0}/${event.delivery_count || 0} 成功 · ${event.attention || 0} 待处理</small></button>`;
    }).join('') : '<p class="empty">暂无通知事件</p>';
    document.querySelectorAll('[data-event]').forEach(button => button.addEventListener('click', () => showEvent(button.dataset.event)));
    if (selectedEvent && events.some(e => e.id === selectedEvent)) await showEvent(selectedEvent, false);
  }

  async function showEvent(id, reloadList = true) {
    selectedEvent = id;
    const event = await api(`/api/notifications/events/${encodeURIComponent(id)}`);
    const facts = event.fact_snapshot || {};
    const fields = event.type === 'daily_generation_ready' ? [
      ['电站', facts.plant_id], ['当地日期', facts.local_date], ['实际电量', `${facts.actual_kwh} kWh`],
      ['预测电量', `${facts.forecast_kwh} kWh（模拟）`], ['修订版', facts.revision], ['时区', facts.business_timezone]
    ] : [['设备', facts.equipment_name || facts.equipment_id], ['事实状态', facts.fact_status], ['观测时间', facts.observed_at], ['事件键', facts.incident_key], ['类别', facts.suspected_class || '—'], ['模拟发送', event.simulated ? '是' : '否']];
    const deliveries = event.deliveries.map(item => `<div class="delivery"><div><strong>${esc(item.recipient_name)}</strong><div class="attempts">${esc(mask(item.address_snapshot))} · 尝试 ${item.attempt_count} 次${item.attempts.length ? ` · ${esc(item.attempts.at(-1).error_code || item.attempts.at(-1).outcome)}` : ''}</div></div><strong>${item.channel === 'sms' ? '短信' : '邮件'}</strong><span class="status ${esc(item.status)}">${esc(statusNames[item.status] || item.status)}</span>${item.status === 'failed' ? `<button class="quiet" data-retry="${esc(item.id)}">人工重试</button>` : ''}</div>`).join('');
    const problems = event.problems.map(problem => `<div class="problem"><strong>${esc(problem.code)}</strong>：${esc(problem.detail)}</div>`).join('');
    $('#event-detail').innerHTML = `<div class="section-head"><div><p class="eyebrow">${esc(eventNames[event.type] || event.type)}</p><h2>${esc(event.dedupe_key)}</h2></div></div><div class="facts">${fields.map(([key, value]) => `<div class="fact"><small>${esc(key)}</small><strong>${esc(value ?? '—')}</strong></div>`).join('')}</div><h3>收件人与渠道</h3>${deliveries || '<p class="empty">没有可发送的收件人</p>'}${problems}`;
    document.querySelectorAll('[data-retry]').forEach(button => button.addEventListener('click', async () => {
      try { await api(`/api/notifications/deliveries/${encodeURIComponent(button.dataset.retry)}/retry`, { method: 'POST', body: '{}' }); message('已记录人工重试操作。', true); await showEvent(id); } catch (error) { message(error.message); }
    }));
    if (reloadList) await loadEvents();
  }

  $('#person-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (!actor()) return message('请先填写操作人。');
    const form = new FormData(event.currentTarget);
    const input = Object.fromEntries(form);
    input.enabled = form.has('enabled');
    try {
      const result = await api('/api/personnel', { method: 'POST', body: JSON.stringify(input) });
      message(result.warnings?.length ? `人员已保存，但需补充：${result.warnings.join('、')}` : '人员已保存。', !result.warnings?.length);
      await loadPeople();
    } catch (error) { message(error.message); }
  });

  $('#equipment-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (!actor()) return message('请先填写操作人。');
    const input = Object.fromEntries(new FormData(event.currentTarget));
    try { await api('/api/equipment', { method: 'POST', body: JSON.stringify(input) }); message('设备负责人已保存并留存变更历史。', true); await loadEquipment(); }
    catch (error) { message(error.message); }
  });

  $('#run-worker').addEventListener('click', async event => {
    if (!actor()) return message('请先填写操作人。');
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
      stream.addEventListener('progress', update);
      stream.addEventListener('done', update);
      stream.onerror = () => { stream.close(); event.currentTarget.disabled = false; message('进度流断开，可刷新查看最终状态。'); };
    } catch (error) { event.currentTarget.disabled = false; message(error.message); }
  });

  $('#refresh').addEventListener('click', () => loadEvents().catch(error => message(error.message)));
  Promise.all([loadPeople(), loadEquipment(), loadEvents()]).catch(error => message(error.message));
})();

