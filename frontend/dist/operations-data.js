'use strict';
(() => {
  const state = window.FumaiOperations = { loading: true, ready: false, error: '', status: null,
    equipment: [], warnings: [], snapshot: [], reports: [], diagnosis: [] };
  const get = async path => {
    const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store' });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || `接口 ${response.status}`);
    return response.json();
  };
  (window.FumaiAuth || Promise.resolve()).then(async () => {
    try {
      const [status, equipment, warnings, snapshot, reports, diagnosis] = await Promise.all([
        get('/api/operations/status'), get('/api/operations/equipment?limit=2000'),
        get('/api/operations/warnings?limit=500'), get('/api/operations/snapshot'),
        get('/api/operations/reports/generation'), get('/api/operations/diagnosis?limit=500')
      ]);
      Object.assign(state, { status, equipment: equipment.records || [], warnings: warnings.warnings || [],
        snapshot: snapshot.values || [], reports: reports.reports || [], diagnosis: diagnosis.results || [], ready: true });
    } catch (error) { state.error = error.message || '业务数据读取失败'; }
    finally {
      state.loading = false;
      window.dispatchEvent(new CustomEvent('fumai-operations-ready', { detail: state }));
    }
  });
})();
