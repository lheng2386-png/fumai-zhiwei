'use strict';
(() => {
    const data = window.FumaiDemo;
    const main = document.querySelector('#main');
    const drawer = document.querySelector('#event-dialog');
    const statusNames = { unreviewed: '待复核', needs_investigation: '待进一步调查', expected_operation: '预期运行', data_issue: '数据问题', confirmed_fault: '确认故障' };
    const evidenceNames = { device_specific_candidate: '单机偏离同群', shared_or_model_candidate: '同群共性变化', insufficient_peers: '同群证据不足' };
    const pageNames = { overview: '电站概览', events: '异常事件', devices: '设备监测', reviews: '复核记录', models: '数据与模型', agent: '运维智能体', scene:'三维电站' };
    const paths = {
        grid: '<rect x="3" y="3" width="7" height="7" rx="1.4"/><rect x="14" y="3" width="7" height="7" rx="1.4"/><rect x="3" y="14" width="7" height="7" rx="1.4"/><rect x="14" y="14" width="7" height="7" rx="1.4"/>',
        activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
        panel: '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M3 10h18M9 4v13m6-13v13M8 21h8m-4-4v4"/>',
        clipboard: '<rect x="5" y="5" width="14" height="16" rx="2"/><rect x="9" y="3" width="6" height="4" rx="1"/><path d="m9 14 2 2 4-5"/>',
        layers: '<path d="m12 3 10 6-10 6L2 9l10-6Zm-9 11 9 5 9-5M3 18l9 5 9-5"/>',
        leaf: '<path d="M20 3C9 2 3 7 4 14c1 6 9 8 13 2 2-3 1-7 3-13ZM4 21 15 9"/>',
        'chevron-down': '<path d="m7 10 5 5 5-5"/>',
        search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
        help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 0 1 5 .5c0 1.5-2.5 2-2.5 3.5m0 3h.01"/>',
        menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
        sidebar: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16m7-11-3 3 3 3"/>',
        calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 11h18m-11 4h3"/>',
        download: '<path d="M12 3v12m-4-4 4 4 4-4M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/>',
        'arrow-up-right': '<path d="M6 18 18 6M6 6h12v12"/>',
        arrow: '<path d="M4 12h16m-5-5 5 5-5 5"/>',
        bolt: '<path d="m14 2-9 12h6l-1 8 9-12h-6l1-8Z"/>',
        sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
        alert: '<path d="M10.3 4 2.5 18A2 2 0 0 0 4.2 21h15.6a2 2 0 0 0 1.7-3L13.7 4a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4m0 4h.01"/>',
        check: '<path d="m5 12 4 4L19 6"/>',
        pin: '<path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 0 1 14 0Z"/><circle cx="12" cy="10" r="2"/>',
        x: '<path d="m6 6 12 12M6 18 18 6"/>',
        clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
        filter: '<path d="M3 5h18l-7 8v6l-4 2v-8L3 5Z"/>',
        database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
        brain: '<path d="M12 4a4 4 0 0 0-7 3 4 4 0 0 0-1 7 4 4 0 0 0 5 6l3-2V4Zm0 0a4 4 0 0 1 7 3 4 4 0 0 1 1 7 4 4 0 0 1-5 6l-3-2M8 8l4 2 4-2M7 15l5-2 5 2"/>',
        file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Zm0 0v6h6M8 13h8m-8 4h5"/>',
    };
    const icon = name => `<span data-icon="${name}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.grid}</svg></span>`;
    const hydrateIcons = root => root.querySelectorAll('[data-icon]:empty').forEach(el => { el.innerHTML = icon(el.dataset.icon).replace(/^.*?<svg/, '<svg').replace(/<\/span>$/, ''); });
    const esc = text => String(text ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
    const number = (n, digits = 0) => Number(n).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
    const storageKey = 'fumai-demo-reviews-v1';
    let storageAvailable = true;
    let history = [];
    try {
        const saved = JSON.parse(localStorage.getItem(storageKey) || '[]');
        if (Array.isArray(saved)) history = saved.filter(r => data.events.some(e => e.event_id === r.event_id) && statusNames[r.status] && typeof r.note === 'string' && !isNaN(Date.parse(r.created_at))).slice(-2000);
    } catch { storageAvailable = false; }
    const state = { page: 'overview', query: '', status: 'all', evidence: 'all', device: 'all', range: 'day', deviceFilter: 'all' };
    const charts = new Map();
    const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
    const mobileLayout = matchMedia('(max-width: 600px)');
    const sidebarPreference = 'fumai-sidebar-collapsed-v1';
    let displayedPage = null;
    let routeSequence = 0;
    let pageAnimation = null;
    let sidebarMotionTimer;
    let sidebarCollapsed = false;
    try { sidebarCollapsed = localStorage.getItem(sidebarPreference) === 'true'; } catch { /* Keep the in-memory preference when storage is unavailable. */ }
    let toastTimer;
    function toast(message) {
        const el = document.querySelector('#toast');
        el.textContent = message;
        el.classList.add('show');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => el.classList.remove('show'), 4200);
    }
    function getEvents() {
        return data.events.map(event => {
            const review = history.findLast(r => r.event_id === event.event_id);
            return review ? { ...event, review_status: review.status, note: review.note, reviewed_at: review.created_at } : { ...event };
        });
    }
    function filteredEvents() {
        const query = state.query.trim().toLowerCase();
        return getEvents().filter(e => (state.status === 'all' || e.review_status === state.status)
            && (state.evidence === 'all' || e.evidence === state.evidence)
            && (state.device === 'all' || e.inverter === state.device)
            && (!query || [e.event_id, e.inverter, evidenceNames[e.evidence], e.note].some(x => x.toLowerCase().includes(query))));
    }
    const badge = status => `<span class="status-pill ${esc(status)}">${esc(statusNames[status] || status)}</span>`;
    const evidence = kind => `<span class="evidence-label ${kind === 'shared_or_model_candidate' ? 'shared' : kind === 'insufficient_peers' ? 'insufficient' : ''}"><i></i>${esc(evidenceNames[kind])}</span>`;
    function intro(title, eyebrow, description, action = '') {
        return `<div class="page-intro"><div><span class="eyebrow">${eyebrow}</span><h1>${title}</h1><p>${description}</p></div><div class="intro-controls">${action}</div></div>`;
    }
    function emptyState(title, body, reset = false) {
        return `<div class="empty-state">${icon('search')}<h3>${title}</h3><p>${body}</p>${reset ? '<button class="secondary-button" data-action="reset-filters">清除筛选条件</button>' : ''}</div>`;
    }
    function stats() {
        const events = getEvents();
        const waiting = events.filter(e => e.review_status === 'unreviewed').length;
        const total = data.energy('actual');
        const deficit = (1 - total / data.energy('expected')) * 100;
        return `<div class="stat-grid">
            <article class="stat"><div class="stat-label">回放日发电量<span class="stat-icon">${icon('bolt')}</span></div><div class="stat-value">${number(total, 1)}<span>kWh</span></div><div class="stat-foot"><b>↘ ${number(deficit, 1)}%</b> 低于同日参考电量</div></article>
            <article class="stat"><div class="stat-label">接入设备<span class="stat-icon">${icon('panel')}</span></div><div class="stat-value">24<span>台逆变器</span></div><div class="stat-foot"><b>24 / 24</b> 设备样例可查看</div></article>
            <article class="stat warning"><div class="stat-label">待复核事件<span class="stat-icon">${icon('clipboard')}</span></div><div class="stat-value">${waiting}<span>条</span></div><div class="stat-foot"><b class="amber">人工复核</b> 从证据开始排查</div></article>
            <article class="stat"><div class="stat-label">单机偏离候选<span class="stat-icon">${icon('activity')}</span></div><div class="stat-value">${events.filter(e => e.evidence === 'device_specific_candidate').length}<span>条</span></div><div class="stat-foot"><b>本机 × 同群</b> 不等同于确认故障</div></article></div>`;
    }
    function overviewSeries() {
        if (state.range === 'day') return data.power;
        const days = state.range === 'week' ? 7 : 30;
        return Array.from({ length: days }, (_, i) => {
            const expected = 4700 + Math.sin(i * 1.2) * 580 + Math.cos(i * .7) * 240;
            const date = new Date(Date.UTC(2023, 6, 19 - days + i));
            return { time: `${String(date.getUTCMonth() + 1).padStart(2, '0')}/${String(date.getUTCDate()).padStart(2, '0')}`, expected, actual: expected * (.9 + .043 * Math.sin(i * 1.9)) };
        });
    }
    function powerChart(id, series, options = {}) {
        charts.set(id, series);
        const detail = !!options.detail;
        const w = 660, h = detail ? 225 : 242, left = 45, right = 641, top = 25, bottom = h - 31;
        const max = options.max || (state.range === 'day' ? 800 : 6000);
        const x = i => left + i / Math.max(1, series.length - 1) * (right - left);
        const y = value => bottom - value / max * (bottom - top);
        const line = key => series.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(2)},${y(p[key]).toFixed(2)}`).join(' ');
        const actual = line('actual');
        const area = `${actual} L${right},${bottom} L${left},${bottom}Z`;
        const ticks = Array.from({ length: 5 }, (_, i) => {
            const v = max / 4 * i, py = y(v);
            return `<line x1="${left}" x2="${right}" y1="${py}" y2="${py}" stroke="#edf1e9" stroke-dasharray="3 4"/><text x="${left - 12}" y="${py + 3}" text-anchor="end" fill="#a3ad9b" font-size="9">${number(v)}</text>`;
        }).join('');
        const labels = Array.from(new Set([0, Math.round((series.length - 1) / 4), Math.round((series.length - 1) / 2), Math.round((series.length - 1) * 3 / 4), series.length - 1])).map(i => `<text x="${x(i)}" y="${bottom + 20}" text-anchor="middle" fill="#9aa692" font-size="9">${esc(series[i].time)}</text>`).join('');
        const zone = options.zone || (!detail && state.range === 'day' ? [54, 68] : null);
        const band = zone ? `<rect x="${x(zone[0])}" y="${top}" width="${x(zone[1]) - x(zone[0])}" height="${bottom - top}" fill="#f3e5c2" opacity=".4"/><line x1="${x(zone[0])}" x2="${x(zone[0])}" y1="${top}" y2="${bottom}" stroke="#d9bb77" stroke-dasharray="3 4"/>` : '';
        return `<div class="chart-wrap" data-chart="${id}" data-unit="${options.unit || 'kW'}"><svg class="chart-svg" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(options.label || '样例实测与参考功率曲线，横轴为场站时间，纵轴为千瓦')}"><defs><linearGradient id="fill-${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="#92ba87" stop-opacity=".30"/><stop offset="100%" stop-color="#92ba87" stop-opacity=".02"/></linearGradient></defs><text x="${left}" y="13" fill="#9ca894" font-size="9">${options.unit || 'kW'}</text>${ticks}${band}<path d="${area}" fill="url(#fill-${id})"/><path d="${line('expected')}" fill="none" stroke="#a8b8a0" stroke-width="1.8" stroke-dasharray="5 5"/><path d="${actual}" fill="none" stroke="#47875b" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>${labels}</svg><div class="chart-tooltip" hidden></div></div>`;
    }
    function eventRows(events, compact = false) {
        return events.map(e => `<tr><td><span class="device-code">${icon('panel')}${esc(e.inverter)}</span><span class="event-time">${esc(e.start_local.slice(11, 16))} – ${esc(e.last_sample_local.slice(11, 16))}</span></td>${compact ? '' : `<td><span class="event-time" style="margin:0">${esc(e.event_id)}</span></td>`}<td>${evidence(e.evidence)}</td><td>${number(e.reference_shortfall_kwh, 2)} <span style="color:#9ca58f;font-size:9px">kWh</span></td><td>${badge(e.review_status)}</td><td><button class="table-link" data-event="${esc(e.event_id)}" aria-label="查看 ${esc(e.event_id)} 的证据">查看证据 ↗</button></td></tr>`).join('');
    }
    function eventTable(events, compact = false) {
        if (!events.length) return emptyState('没有匹配的事件', '试试其他设备、复核状态或关键词。', true);
        return `<div class="table-scroll"><table class="event-table"><thead><tr><th>设备 / 发生时间</th>${compact ? '' : '<th>事件编号</th>'}<th>候选证据</th><th>参考电量差</th><th>复核状态</th><th>操作</th></tr></thead><tbody>${eventRows(events, compact)}</tbody></table></div>`;
    }
    function solarIllustration() {
        let panels = '';
        for (let row = 0; row < 3; row++) for (let col = 0; col < 4; col++) {
            const x = 32 + col * 42 + row * 13, y = 75 + row * 25 - col * 12;
            panels += `<g><path d="M${x},${y + 14}v12m31-24v12" stroke="#8aa080" stroke-width="2"/><path d="m${x - 3} ${y + 4} 34-11 17 18-35 12Z" fill="#244f46" stroke="#f0f7e8" stroke-width="1.2"/><path d="m${x + 7} ${y + 1} 17 18m-6-21 17 18m-32-6 34-11" fill="none" stroke="#7caa98" stroke-width=".6"/></g>`;
        }
        return `<svg class="solar-illustration" viewBox="0 0 260 170" fill="none" aria-hidden="true"><ellipse cx="140" cy="135" rx="107" ry="23" fill="#b1c6a2" opacity=".20"/><circle cx="208" cy="26" r="12" fill="#d6b96c" opacity=".7"/><path d="M27 37h32m-16-6h27M167 17h11m7 0h22" stroke="#c0ceae" stroke-width="2" stroke-linecap="round"/><path d="M23 126c34-7 28 15 77 15s43-10 84-8 52-1 60-5" stroke="#c0d0b2" stroke-width="1.2"/>${panels}<path d="m28 127-7 2v-9m211-25 5-2v-7" stroke="#8aab7d" stroke-width="2"/></svg>`;
    }
    function overview() {
        return `${intro('每一度电，都有迹可循。', 'STATION OVERVIEW / 电站概览', '掌握场站表现，让值得关注的变化先被看见。', `<span class="date-chip">${icon('calendar')}2023 年 07 月 18 日</span><button class="primary-button" data-action="export-events">${icon('download')}导出事件</button>`)}
            ${stats()}<div class="dashboard-top"><section class="panel"><div class="panel-heading"><div><h2>发电功率趋势 <span class="subtle" id="chart-period-label">POWER TREND</span></h2><p>实测与同一时刻的天气参考对比 · 样例曲线</p></div><div class="segmented" aria-label="趋势时间范围"><button data-range="day" class="${state.range === 'day' ? 'active' : ''}" aria-pressed="${state.range === 'day'}">日</button><button data-range="week" class="${state.range === 'week' ? 'active' : ''}" aria-pressed="${state.range === 'week'}">周</button><button data-range="month" class="${state.range === 'month' ? 'active' : ''}" aria-pressed="${state.range === 'month'}">月</button></div></div><div class="chart-legend" style="margin-top:19px"><span class="legend-item"><i></i>样例实测</span><span class="legend-item reference"><i></i>模型参考</span><span class="legend-item candidate"><i></i>候选区间</span></div>${powerChart('overview', overviewSeries(), { unit: state.range === 'day' ? 'kW' : 'kWh', label: state.range === 'day' ? '样例日功率曲线，5 分钟间隔，单位千瓦' : '构造的样例日发电量，单位千瓦时' })}<div class="chart-bottom"><span><b>10:40–11:05</b> · inv_07 出现单机偏离线索</span><button class="table-link" data-event="DEMO-2107-001">查看证据 ${icon('arrow-up-right')}</button></div></section>
            <section class="station-card"><span class="station-label">SELECTED STATION</span><div class="station-heading"><h2>PVDAQ 2107</h2><button class="icon-button" data-action="open-models" aria-label="查看场站数据说明">${icon('arrow-up-right')}</button></div><div class="station-location">${icon('pin')}光伏场站 · 历史回放样例</div><span class="station-status"><i></i>样例场站</span>${solarIllustration()}<div class="station-specs"><div><small>设备数量</small><strong>24 <span>台</span></strong></div><div><small>数据粒度</small><strong>5 <span>分钟</span></strong></div><div><small>回放范围</small><strong>1 <span>日</span></strong></div></div></section></div>
            <div class="dashboard-bottom"><section class="panel"><div class="panel-heading panel-table-heading"><div><h2>值得先看的事件 <span class="subtle">REVIEW QUEUE</span></h2><p>优先查看偏离线索，结合现场记录完成人工判断。</p></div><button class="table-link" data-action="view-all">全部事件 ${icon('arrow')}</button></div>${eventTable(getEvents().filter(e => e.review_status === 'unreviewed').slice(0, 4), true)}</section><section class="panel fleet-panel"><div class="panel-heading"><h2>设备一览</h2><span class="subtle">24 台</span></div><div class="device-matrix">${data.devices.map((d, i) => `<button class="device-cell ${d.signal}" data-device="${d.id}" title="${esc(d.name)} · ${d.signal === 'attention' ? '存在候选线索' : '查看设备'}" aria-label="查看 ${esc(d.id)} 的候选事件">${String(i + 1).padStart(2, '0')}</button>`).join('')}</div><div class="fleet-legend"><span><i></i>参考范围示例</span><span><i class="alert"></i>候选关注</span></div><div class="fleet-footer"><span>颜色仅表示样例监测线索</span><button class="table-link" data-action="open-devices">查看设备 →</button></div></section></div>`;
    }
    function eventsPage() {
        return `${intro('把异常，变成可追溯的线索。', 'EVENT EXPLORER / 异常事件', '候选筛查 ≠ 故障结论。结合曲线、同群和现场记录再判断。', `<button class="primary-button" data-action="export-events">${icon('download')}导出筛选结果</button>`)}<div class="page-notice">${icon('help')}当前共 ${data.events.length} 条构造样例；时间沿用场站本地墙上时钟 PST8PDT，未转换为北京时间。</div><section class="panel"><div class="filter-bar"><label class="search-field">${icon('search')}<input id="event-query" aria-label="搜索事件、设备或复核说明" value="${esc(state.query)}" placeholder="搜索事件、设备或复核说明"></label><select id="status-filter" aria-label="复核状态"><option value="all">全部复核状态</option>${Object.entries(statusNames).map(([value, label]) => `<option value="${value}" ${state.status === value ? 'selected' : ''}>${label}</option>`).join('')}</select><select id="evidence-filter" aria-label="候选证据"><option value="all">全部证据类型</option>${Object.entries(evidenceNames).map(([value, label]) => `<option value="${value}" ${state.evidence === value ? 'selected' : ''}>${label}</option>`).join('')}</select><select id="device-filter" aria-label="设备"><option value="all">全部设备</option>${data.devices.map(d => `<option value="${d.id}" ${state.device === d.id ? 'selected' : ''}>${d.id}</option>`).join('')}</select></div><div id="events-results">${eventTable(filteredEvents())}</div><div class="filters-footer"><span id="event-result-count">共 ${filteredEvents().length} 条匹配事件</span><button class="table-link" data-action="reset-filters">清除筛选</button></div></section>`;
    }
    function devicesPage() {
        const devices = data.devices.filter(d => state.deviceFilter === 'all' || d.signal === 'attention');
        return `${intro('每台设备，都有自己的参照。', 'DEVICE MONITOR / 设备监测', '对比本机参考与同群表现，定位值得进一步查看的设备。')}<div class="page-notice">${icon('clock')}设备功率为统一的演示快照，与事件列表中的历史时段分别展示。点击设备查看它的样例候选事件。</div><section class="panel" style="margin-bottom:18px"><div class="filter-bar"><span style="font-size:11px;color:#728668">PVDAQ 2107 · 24 台设备</span><div class="segmented" style="margin-left:auto"><button data-device-filter="all" class="${state.deviceFilter === 'all' ? 'active' : ''}" aria-pressed="${state.deviceFilter === 'all'}">全部设备</button><button data-device-filter="attention" class="${state.deviceFilter === 'attention' ? 'active' : ''}" aria-pressed="${state.deviceFilter === 'attention'}">候选关注 · 3</button></div></div></section><div class="device-grid">${devices.map(d => `<button class="device-card" data-device="${d.id}" aria-label="查看 ${d.id} 的候选事件"><div class="device-card-top"><strong>${icon('panel')}${d.id}</strong><span class="status-pill ${d.signal === 'attention' ? 'candidate-alert' : 'expected_operation'}">${d.signal === 'attention' ? '候选关注' : '参考范围'}</span></div><p>${d.name} · 2107 暂定同群</p><div class="device-measures"><div><small>样例实测</small><strong>${number(d.actual_kw, 1)}<span>kW</span></strong></div><div><small>样例参考</small><strong>${number(d.expected_kw, 1)}<span>kW</span></strong></div></div><div class="device-card-footer"><span>查看历史候选与证据</span>${icon('arrow-up-right')}</div></button>`).join('')}</div>`;
    }
    function localTime(iso) {
        return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(iso));
    }
    function reviewsPage() {
        const reviewed = getEvents().filter(e => e.review_status !== 'unreviewed');
        return `${intro('让每一次判断，留下依据。', 'HUMAN REVIEW / 复核记录', '保留人工判断和复核说明，让后续排查能够接着往下走。', `<button class="primary-button" data-action="export-reviews">${icon('download')}导出记录</button>`)}<div class="page-notice warning">${icon('help')}当前为模拟复核。新增记录仅保存在此浏览器，未提交到项目后端；预置记录同样是样例。</div><section class="panel"><div class="panel-heading panel-table-heading"><div><h2>最新复核状态</h2><p>${reviewed.length} 条已复核样例 · 本机新增 ${history.length} 次操作历史</p></div><button class="table-link" data-action="review-waiting">查看待复核 →</button></div>${reviewed.length ? `<div class="table-scroll"><table class="event-table"><thead><tr><th>事件 / 设备</th><th>复核结论</th><th>依据与说明</th><th>记录来源</th><th>操作</th></tr></thead><tbody>${reviewed.map(e => `<tr><td><span class="device-code">${esc(e.inverter)}</span><span class="event-time">${esc(e.event_id)}</span></td><td>${badge(e.review_status)}</td><td><div class="review-note">${esc(e.note)}</div></td><td><span class="review-time">${e.reviewed_at ? `本机新增<br>${esc(localTime(e.reviewed_at))} 北京时间` : '预置样例'}</span></td><td><button class="table-link" data-event="${e.event_id}">查看 / 修改 ↗</button></td></tr>`).join('')}</tbody></table></div>` : emptyState('还没有复核记录', '从异常事件中选择一条，查看证据并保存你的模拟判断。')}</section>`;
    }
    function modelsPage() {
        const repo = 'https://github.com/lheng2386-png/fumai-zhiwei';
        return `${intro('每一个结果，都应说明来处。', 'DATA & MODELS / 数据与模型', '理解数据、模型和能力边界，才能读懂一条异常线索。')}<div class="page-notice">${icon('database')}本原型全部采用构造样例，没有加载仓库训练权重，也没有调用实时推理服务。下列方法说明来自项目文档。</div><div class="model-grid"><article class="model-card"><div class="model-icon">${icon('sun')}</div><h2>功率参考与同群筛查</h2><p>基于同一时刻的天气给出参考功率，再结合残差和其他同群设备，筛选连续低发电线索。</p><dl><dt>主数据</dt><dd>PVDAQ 2107</dd><dt>连续条件</dt><dd>至少 3 个 5 分钟采样点</dd><dt>同群要求</dt><dd>至少 5 台其他有效设备</dd><dt>输出性质</dt><dd>待核查的候选事件</dd></dl></article><article class="model-card"><div class="model-icon">${icon('brain')}</div><h2>DC 侧故障分类</h2><p>使用独立公开双组串数据开展五分类，包括时序编码器、掩码重建预训练和同协议对照。</p><dl><dt>主数据</dt><dd>Costa / Lazzaretti</dd><dt>模型框架</dt><dd>PyTorch 时序卷积</dd><dt>任务范围</dt><dd>公开数据中的 DC 侧状态</dd><dt>适用边界</dt><dd>跨电站效果仍需验证</dd></dl></article><article class="model-card"><div class="model-icon">${icon('layers')}</div><h2>多变量与活动文本</h2><p>以 Isolation Forest 观察电气组合的新颖性，以 TF-IDF + KMeans 整理相似活动文本。</p><dl><dt>主数据</dt><dd>OpenCEM 分片</dd><dt>划分原则</dt><dd>按时间训练、校准、留出</dd><dt>文本含义</dt><dd>活动上下文</dd><dt>适用边界</dt><dd>活动分组不等于故障标签</dd></dl></article></div><section class="panel source-panel"><span class="eyebrow">EVIDENCE FIRST</span><h2 style="margin-top:10px">候选线索与确认故障之间，还需要人工证据。</h2><p>这个前端负责呈现和复核。真实数据接入时，应保留输入校验值、模型版本、特征规范、时间语义和逐点证据。未取得真实故障标签时，不能把候选数量换算成识别准确率。</p><p>完整平台、真实工单闭环、大模型智能体与云边端服务仍是后续研发内容；本原型不把它们显示为已部署能力。</p><div class="source-links"><a class="text-link" href="${repo}/blob/main/README.md" target="_blank" rel="noopener noreferrer">项目说明 ${icon('arrow-up-right')}</a><a class="text-link" href="${repo}/blob/main/DEVELOPMENT.md" target="_blank" rel="noopener noreferrer">研发与验证范围 ${icon('arrow-up-right')}</a><a class="text-link" href="${repo}/blob/main/CONTRIBUTING.md" target="_blank" rel="noopener noreferrer">协作规范 ${icon('arrow-up-right')}</a></div></section>`;
    }
    function detailSeries(event) {
        const start = event.start_local.slice(11, 16);
        return Array.from({ length: event.samples + 12 }, (_, i) => {
            const expected = event.mean_reference_kw;
            return { time: data.addMinutes(start, (i - 6) * 5), expected, actual: i >= 6 && i < 6 + event.samples ? event.mean_actual_kw : +(expected * (.95 + .015 * Math.sin(i))).toFixed(2) };
        });
    }
    function openEvent(id) {
        const event = getEvents().find(e => e.event_id === id);
        if (!event) return;
        const timeline = history.filter(r => r.event_id === id).slice().reverse();
        const series = detailSeries(event);
        const reason = event.evidence === 'device_specific_candidate' ? '连续残差超限，并存在本机相对同群偏低的样例证据。建议结合限发、检修、通信和现场检查，区分设备问题与运行变化。' : event.evidence === 'insufficient_peers' ? '连续残差超限，但有效同群数量不足。先核对缺失观测与设备可比性，再作进一步归因。' : '连续残差超限，但未形成充分的单机偏离证据。保留共性运行变化或模型偏差的可能，需要核对全站状态。';
        drawer.innerHTML = `<div class="drawer-header"><div class="drawer-header-top"><span class="eyebrow">EVENT EVIDENCE / 样例事件证据</span><button class="icon-button" data-close="event-dialog" aria-label="关闭事件详情">${icon('x')}</button></div><h2 id="detail-title">${esc(event.inverter)} <span style="font-weight:400;color:#a6b29b">/</span> 事件详情</h2><p>${esc(event.event_id)}<br>${esc(event.start_local.slice(0, 16))} — ${esc(event.last_sample_local.slice(11, 16))} · 场站本地时间 PST8PDT</p></div><div class="drawer-body"><div style="display:flex;gap:12px;align-items:center">${evidence(event.evidence)}${badge(event.review_status)}</div><div class="drawer-summary"><div><small>样例实测均值</small><strong>${number(event.mean_actual_kw, 1)}<span>kW</span></strong></div><div><small>样例参考均值</small><strong>${number(event.mean_reference_kw, 1)}<span>kW</span></strong></div><div><small>参考电量差</small><strong>${number(event.reference_shortfall_kwh, 2)}<span>kWh</span></strong></div></div><section class="drawer-section"><h3>功率与候选区间</h3><div class="chart-legend"><span class="legend-item"><i></i>样例实测</span><span class="legend-item reference"><i></i>参考功率</span><span class="legend-item candidate"><i></i>候选区间</span></div>${powerChart('detail', series, { detail: true, max: 40, zone: [6, 6 + event.samples - 1], label: `${event.inverter} 样例事件功率曲线，前后各 30 分钟` })}<p>曲线为构造证据示例。参考电量差用于描述观测差距，不能解释为维修后可挽回电量。</p></section><section class="drawer-section"><h3>这条线索为什么值得查看？</h3><p>${reason}</p><div class="evidence-facts"><div>连续有效采样<strong>${event.samples} 个点 / 跨度 ${event.observed_span_minutes} 分钟</strong></div><div>最少有效其他同群<strong>${event.min_peer_count} 台</strong></div><div>样例残差阈值<strong>${number(event.threshold_kw, 1)} kW</strong></div><div>同群偏离采样<strong>${event.peer_flag_samples} 个点</strong></div></div></section><section class="drawer-section"><h3>候选区间逐点证据 · 样例</h3><div class="table-scroll"><table class="event-table"><thead><tr><th>本地时间</th><th>实测 kW</th><th>参考 kW</th><th>残差 kW</th></tr></thead><tbody>${series.slice(6, 6 + event.samples).map(p => `<tr><td>${p.time}</td><td>${number(p.actual, 1)}</td><td>${number(p.expected, 1)}</td><td>${number(p.expected - p.actual, 1)}</td></tr>`).join('')}</tbody></table></div></section><section class="drawer-section"><h3>保存人工复核草稿</h3><p>仅用于本次演示，保存在当前浏览器。选择确认故障时，请填写可核查的依据。</p><form class="review-form" id="review-form" data-event-id="${esc(id)}"><label for="review-status">复核结论</label><select id="review-status">${Object.entries(statusNames).filter(([k]) => k !== 'unreviewed').map(([value, label]) => `<option value="${value}" ${(event.review_status === value || event.review_status === 'unreviewed' && value === 'needs_investigation') ? 'selected' : ''}>${label}</option>`).join('')}</select><label for="review-note">依据与说明 <span style="color:#b68c51">*</span></label><textarea id="review-note" maxlength="1000" placeholder="记录已核对的现象、材料或需要进一步确认的内容…">${esc(event.note)}</textarea><p class="form-error" id="review-error" aria-live="polite"></p><div class="review-actions"><small>不会自动形成训练标签，也不会向后端提交。</small><button class="primary-button" type="submit">${icon('check')}保存复核草稿</button></div></form></section>${timeline.length ? `<section class="drawer-section"><h3>本机复核历史 · ${timeline.length} 次</h3><ul class="review-history">${timeline.map(r => `<li><strong>${esc(statusNames[r.status])}</strong><time>${esc(localTime(r.created_at))} 北京时间</time><p>${esc(r.note)}</p></li>`).join('')}</ul></section>` : ''}</div>`;
        drawer.querySelector('.drawer-body').insertAdjacentHTML('afterbegin', `<button class="secondary-button" data-agent-context="${esc(id)}" style="margin-bottom:20px">${icon('brain')}让运维助手分析这条线索</button>`);
        bindCharts(drawer);
        if (!drawer.open) drawer.showModal();
    }
    function bindCharts(root) {
        root.querySelectorAll('[data-chart]').forEach(wrap => {
            const svg = wrap.querySelector('svg');
            const tooltip = wrap.querySelector('.chart-tooltip');
            svg.addEventListener('pointermove', event => {
                const bounds = svg.getBoundingClientRect();
                const series = charts.get(wrap.dataset.chart);
                if (!series?.length || !bounds.width) return;
                const position = ((event.clientX - bounds.left) / bounds.width * 660 - 45) / (641 - 45);
                const index = Math.max(0, Math.min(series.length - 1, Math.round(position * (series.length - 1))));
                const point = series[index];
                tooltip.innerHTML = `<strong>${esc(point.time)}</strong><span>样例实测</span> ${number(point.actual, 1)} ${esc(wrap.dataset.unit)}<br><span>模型参考</span> ${number(point.expected, 1)} ${esc(wrap.dataset.unit)}`;
                tooltip.hidden = false;
                tooltip.style.left = `${Math.max(0, Math.min(event.clientX - wrap.getBoundingClientRect().left + 12, wrap.clientWidth - tooltip.offsetWidth - 5))}px`;
                tooltip.style.top = '22px';
            });
            svg.addEventListener('pointerleave', () => { tooltip.hidden = true; });
        });
    }
    function render() {
        window.FumaiScene?.dispose();
        charts.clear();
        const views = { overview, events: eventsPage, devices: devicesPage, reviews: reviewsPage, models: modelsPage, agent: () => window.FumaiAgent.render(), scene:()=>window.FumaiScene.render() };
        main.innerHTML = views[state.page]();
        main.dataset.view = state.page;
        document.querySelector('#breadcrumb-title').textContent = pageNames[state.page];
        document.title = `${pageNames[state.page]} · 伏脉智维`;
        updateNavigation(state.page);
        const waiting = getEvents().filter(e => e.review_status === 'unreviewed').length;
        const bubble = document.querySelector('#nav-count');
        bubble.textContent = waiting;
        bubble.hidden = waiting === 0;
        document.querySelector('#nav-count-label').textContent = waiting ? `${waiting} 条异常候选待复核` : '当前没有待复核异常候选';
        bindCharts(main);
        if(state.page==='scene') void window.FumaiScene.mount(getEvents);
    }
    function updateNavigation(page) {
        document.querySelectorAll('[data-nav]').forEach(a => {
            a.classList.toggle('active', a.dataset.nav === page);
            if (a.dataset.nav === page) a.setAttribute('aria-current', 'page');
            else a.removeAttribute('aria-current');
        });
    }
    function applySidebarPreference() {
        document.body.classList.toggle('sidebar-collapsed', sidebarCollapsed);
        const button = document.querySelector('#sidebar-toggle');
        const label = sidebarCollapsed ? '展开侧栏' : '收起侧栏';
        button.setAttribute('aria-expanded', String(!sidebarCollapsed));
        button.setAttribute('aria-label', label);
        button.title = label;
    }
    function setMobileNav(isOpen) {
        const sidebar = document.querySelector('#sidebar');
        const insideNav = sidebar.contains(document.activeElement);
        if (!isOpen && insideNav && mobileLayout.matches) document.querySelector('#mobile-menu').focus();
        sidebar.classList.toggle('open', isOpen);
        sidebar.inert = mobileLayout.matches && !isOpen;
        document.querySelector('#nav-scrim').classList.toggle('open', isOpen);
        const button = document.querySelector('#mobile-menu');
        button.setAttribute('aria-expanded', String(isOpen));
        button.setAttribute('aria-label', isOpen ? '关闭导航' : '打开导航');
        if (isOpen) sidebar.querySelector('[aria-current="page"]')?.focus();
    }
    function closeNav() {
        setMobileNav(false);
    }
    async function route(focus = false) {
        const sequence = ++routeSequence;
        const key = location.hash.slice(1).split('?')[0];
        const nextPage = Object.hasOwn(pageNames, key) ? key : 'overview';
        const shouldAnimate = displayedPage !== null && displayedPage !== nextPage && !reducedMotion.matches;
        const visual = getComputedStyle(main);
        const start = { opacity: visual.opacity, transform: visual.transform };
        pageAnimation?.cancel();
        updateNavigation(nextPage);
        document.querySelector('#breadcrumb-title').textContent = pageNames[nextPage];
        closeNav();
        main.setAttribute('aria-busy', 'true');
        main.inert = shouldAnimate;
        document.querySelector('.topbar').classList.toggle('is-navigating', shouldAnimate);
        if (shouldAnimate) {
            pageAnimation = main.animate([start, { opacity: 0, transform: 'translateY(-6px)' }], {
                duration: 110, easing: 'cubic-bezier(.4,0,1,1)', fill: 'forwards',
            });
            try { await pageAnimation.finished; } catch { if (sequence !== routeSequence) return; }
        }
        if (sequence !== routeSequence) return;
        pageAnimation?.cancel();
        state.page = nextPage;
        render();
        displayedPage = nextPage;
        main.inert = false;
        if (focus) {
            main.focus({ preventScroll: true });
            window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
        }
        if (shouldAnimate && !reducedMotion.matches) {
            pageAnimation = main.animate([
                { opacity: 0, transform: 'translateY(10px)' },
                { opacity: 1, transform: 'translateY(0)' },
            ], { duration: 220, easing: 'cubic-bezier(.22,1,.36,1)', fill: 'forwards' });
            try { await pageAnimation.finished; } catch { if (sequence !== routeSequence) return; }
        }
        if (sequence !== routeSequence) return;
        pageAnimation?.cancel();
        pageAnimation = null;
        main.setAttribute('aria-busy', 'false');
        document.querySelector('.topbar').classList.remove('is-navigating');
        document.querySelector('#route-announcer').textContent = `已切换到${pageNames[nextPage]}`;
    }
    function navigate(page) {
        if (location.hash === `#${page}`) route(true);
        else location.hash = page;
    }
    function resetFilters() {
        state.query = ''; state.status = 'all'; state.evidence = 'all'; state.device = 'all';
        document.querySelector('#global-query').value = '';
    }
    function refreshEventResults() {
        const events = filteredEvents();
        document.querySelector('#events-results').innerHTML = eventTable(events);
        document.querySelector('#event-result-count').textContent = `共 ${events.length} 条匹配事件`;
    }
    function downloadCsv(rows, name) {
        if (!rows.length) { toast('当前没有可导出的记录，请调整筛选条件。'); return; }
        const columns = Object.keys(rows[0]);
        const cell = value => {
            let text = String(value ?? '');
            // Neutralize spreadsheet formula injection in human-entered notes.
            if (/^[\s]*[=+\-@]/.test(text) || /^[\t\r]/.test(text)) text = `'${text}`;
            return `"${text.replace(/"/g, '""')}"`;
        };
        const csv = '\uFEFF' + [columns.map(cell).join(','), ...rows.map(row => columns.map(c => cell(row[c])).join(','))].join('\r\n');
        const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
        const a = document.createElement('a');
        a.href = url; a.download = name; document.body.append(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 2000);
        toast(`已导出 ${rows.length} 条演示记录。`);
    }
    function exportEvents() {
        const events = state.page === 'events' ? filteredEvents() : getEvents();
        downloadCsv(events.map(e => ({ data_source: 'constructed_demo', event_id: e.event_id, inverter: e.inverter,
            start_local: e.start_local, last_sample_local: e.last_sample_local, timezone: data.timezone,
            samples: e.samples, evidence: e.evidence, review_status: e.review_status,
            mean_actual_kw: e.mean_actual_kw, mean_reference_kw: e.mean_reference_kw,
            reference_shortfall_kwh: e.reference_shortfall_kwh, note: e.note })), 'fumai-demo-events.csv');
    }
    function exportReviews() {
        // Export all locally appended history plus the four seeded example states.
        const seed = data.events.filter(e => e.review_status !== 'unreviewed').map(e => ({
            data_source: 'constructed_demo', record_source: 'seeded_example', event_id: e.event_id,
            status: e.review_status, note: e.note, created_at: '', timestamp_timezone: '',
        }));
        const local = history.map(r => ({ data_source: 'constructed_demo', record_source: 'browser_local',
            event_id: r.event_id, status: r.status, note: r.note, created_at: r.created_at, timestamp_timezone: 'UTC' }));
        downloadCsv([...seed, ...local], 'fumai-demo-review-history.csv');
    }
    document.addEventListener('click', event => {
        const close = event.target.closest('[data-close]');
        if (close) { document.getElementById(close.dataset.close).close(); return; }
        const detail = event.target.closest('[data-event]');
        if (detail) { openEvent(detail.dataset.event); return; }
        const device = event.target.closest('[data-device]');
        if (device) { resetFilters(); state.device = device.dataset.device; navigate('events'); return; }
        const range = event.target.closest('[data-range]');
        if (range) { state.range = range.dataset.range; render(); main.querySelector(`[data-range="${state.range}"]`).focus({ preventScroll: true }); return; }
        const deviceFilter = event.target.closest('[data-device-filter]');
        if (deviceFilter) { state.deviceFilter = deviceFilter.dataset.deviceFilter; render(); return; }
        const action = event.target.closest('[data-action]')?.dataset.action;
        if (action === 'export-events') exportEvents();
        if (action === 'export-reviews') exportReviews();
        if (action === 'reset-filters') { resetFilters(); render(); }
        if (action === 'view-all') { resetFilters(); navigate('events'); }
        if (action === 'review-waiting') { resetFilters(); state.status = 'unreviewed'; navigate('events'); }
        if (action === 'open-devices') navigate('devices');
        if (action === 'open-models') navigate('models');
    });
    document.addEventListener('input', event => {
        if (event.target.id === 'event-query') { state.query = event.target.value; refreshEventResults(); }
    });
    document.addEventListener('change', event => {
        const fields = { 'status-filter': 'status', 'evidence-filter': 'evidence', 'device-filter': 'device' };
        const field = fields[event.target.id];
        if (field) { state[field] = event.target.value; refreshEventResults(); }
    });
    document.addEventListener('submit', event => {
        if (event.target.id === 'global-search') {
            event.preventDefault();
            const query = document.querySelector('#global-query').value;
            resetFilters(); state.query = query; document.querySelector('#global-query').value = query;
            navigate('events');
        }
        if (event.target.id === 'review-form') {
            event.preventDefault();
            const note = document.querySelector('#review-note').value.trim();
            const error = document.querySelector('#review-error');
            if (note.length < 2) { error.textContent = '请填写至少 2 个字的复核依据或待核查事项。'; document.querySelector('#review-note').focus(); return; }
            const id = event.target.dataset.eventId;
            const record = { event_id: id, status: document.querySelector('#review-status').value, note, created_at: new Date().toISOString() };
            const next = [...history, record];
            try { localStorage.setItem(storageKey, JSON.stringify(next)); }
            catch { error.textContent = '浏览器暂时无法保存记录。请检查站点存储设置后重试。'; return; }
            history = next;
            render(); openEvent(id);
            drawer.querySelector('button[type="submit"]').focus({ preventScroll: true });
            toast('已保存演示复核草稿，仅当前浏览器可见。');
        }
    });
    document.querySelector('#sidebar-toggle').addEventListener('click', () => {
        document.body.classList.add('sidebar-animating');
        clearTimeout(sidebarMotionTimer);
        sidebarCollapsed = !sidebarCollapsed;
        applySidebarPreference();
        sidebarMotionTimer = setTimeout(() => document.body.classList.remove('sidebar-animating'), 340);
        try { localStorage.setItem(sidebarPreference, String(sidebarCollapsed)); } catch { /* The toggle still works without persistent browser storage. */ }
    });
    document.querySelector('#mobile-menu').addEventListener('click', () => setMobileNav(!document.querySelector('#sidebar').classList.contains('open')));
    document.querySelector('#nav-scrim').addEventListener('click', closeNav);
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') closeNav();
        if (event.key === 'Tab' && document.querySelector('#sidebar').classList.contains('open') && matchMedia('(max-width: 600px)').matches) {
            const links = [...document.querySelectorAll('#sidebar a[href]')];
            const first = links[0], last = links.at(-1);
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
    });
    document.querySelector('#about-button').addEventListener('click', () => document.querySelector('#about-dialog').showModal());
    for (const dialog of [drawer, document.querySelector('#about-dialog')]) dialog.addEventListener('click', event => {
        const rect = dialog.getBoundingClientRect();
        if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
    });
    window.addEventListener('hashchange', () => route(true));
    mobileLayout.addEventListener('change', () => {
        clearTimeout(sidebarMotionTimer);
        document.body.classList.remove('sidebar-animating');
        closeNav();
    });
    reducedMotion.addEventListener('change', () => { if (reducedMotion.matches) pageAnimation?.finish(); });
    hydrateIcons(document);
    applySidebarPreference();
    closeNav();
    route();
    if (!storageAvailable) toast('已有复核记录暂时无法读取；新记录保存前请检查浏览器存储设置。');
})();
