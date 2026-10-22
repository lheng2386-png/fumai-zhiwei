import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { openDatabase } from '../src/db.mjs';
import { MockProvider, ProviderError } from '../src/providers.mjs';
import { NotificationService } from '../src/service.mjs';
import { NotificationWorker } from '../src/worker.mjs';

function setup() {
  const db = openDatabase(':memory:');
  const service = new NotificationService(db, { cooldownMinutes: 30 });
  service.upsertPersonnel({ id: 'p1', name: '甲负责人', phone: '+8613800000001', email: 'p1@example.com' });
  service.upsertPersonnel({ id: 'p2', name: '乙负责人', phone: '+8613800000002', email: 'p2@example.com' });
  service.upsertEquipment({ id: 'A', plant_id: 'plant-1', name: '设备 A', owner_id: 'p1' }, 'test');
  service.upsertEquipment({ id: 'B', plant_id: 'plant-1', name: '设备 B', owner_id: 'p2' }, 'test');
  return { db, service };
}

function anomaly(service, equipmentId, suffix, overrides = {}) {
  const observedAt = overrides.observed_at || `2026-10-22T0${suffix}:00:00.000Z`;
  const observation = service.recordObservation({
    id: `obs-${equipmentId}-${suffix}`, equipment_id: equipmentId, observed_at: observedAt,
    data_quality: overrides.observation_quality || 'ready', raw_json: { kw: 1 }
  });
  return service.recordModelResult({
    observation_id: observation.id, equipment_id: equipmentId, observed_at: observedAt,
    task_name: 'runtime-anomaly', request_id: `request-${equipmentId}-${suffix}-${randomUUID()}`,
    model_version: 'runtime-v1', threshold_version: 'threshold-v1', raw_result: { demo: true },
    suspected_class: 'dc-drop', score: 0.91, decision: overrides.decision || 'anomaly',
    quality_status: overrides.quality_status || 'ready', incident_key: overrides.incident_key,
    state_level: overrides.state_level || 1
  });
}

test('一人两台设备时只通知异常设备的启用负责人', () => {
  const { db, service } = setup();
  anomaly(service, 'A', 1, { incident_key: 'inc-a' });
  const event = service.listEvents()[0];
  assert.equal(event.type, 'model_anomaly');
  const detail = service.getEvent(event.id);
  assert.deepEqual(detail.deliveries.map(row => [row.recipient_id, row.channel]), [['p1', 'email'], ['p1', 'sms']]);
  db.close();
});

test('同一持续事件同等级重复写入不会重复事件或 delivery', async () => {
  const { db, service } = setup();
  anomaly(service, 'A', 1, { incident_key: 'same-incident' });
  anomaly(service, 'A', 2, { incident_key: 'same-incident' });
  assert.equal(service.listEvents().length, 1);
  assert.equal(db.prepare('SELECT count(*) count FROM notification_delivery').get().count, 2);
  const providers = { sms: new MockProvider(), email: new MockProvider() };
  const worker = new NotificationWorker(db, providers, { concurrency: 4 });
  await worker.runUntilIdle();
  await worker.runUntilIdle();
  assert.equal(providers.sms.sent.length, 1);
  assert.equal(providers.email.sent.length, 1);
  assert.equal(db.prepare("SELECT count(*) count FROM notification_delivery WHERE status='simulated_sent'").get().count, 2);
  db.close();
});

test('短信失败邮件成功时仅短信进入重试', async () => {
  const { db, service } = setup();
  anomaly(service, 'A', 1, { incident_key: 'channel-failure' });
  let failSms = true;
  const sms = new MockProvider({ fail: () => failSms ? new ProviderError('SMS_TEMP', '临时失败', { transient: true }) : null });
  const email = new MockProvider();
  const worker = new NotificationWorker(db, { sms, email }, { concurrency: 4, baseRetryMs: 1, maxAttempts: 3 });
  await worker.runBatch();
  const first = db.prepare('SELECT channel,status FROM notification_delivery ORDER BY channel').all()
    .map(row => ({ ...row }));
  assert.deepEqual(first, [{ channel: 'email', status: 'simulated_sent' }, { channel: 'sms', status: 'retry_wait' }]);
  failSms = false;
  db.prepare("UPDATE notification_delivery SET next_retry_at='2000-01-01T00:00:00.000Z' WHERE channel='sms'").run();
  await worker.runUntilIdle();
  assert.equal(sms.sent.length, 1);
  assert.equal(email.sent.length, 1);
  db.close();
});

test('每日发电双值任意先后、0 kWh 与修订版均正确去重', () => {
  const { db, service } = setup();
  service.upsertDailyGeneration({ plant_id: 'plant-1', local_date: '2026-10-20', actual_kwh: 0,
    business_timezone: 'Asia/Shanghai', revision: 1 });
  assert.equal(service.listEvents().length, 0);
  service.upsertDailyGeneration({ plant_id: 'plant-1', local_date: '2026-10-20', forecast_kwh: 12,
    forecast_model_version: 'TEST_ONLY', business_timezone: 'Asia/Shanghai', revision: 1 });
  assert.equal(service.listEvents().length, 1);
  service.upsertDailyGeneration({ plant_id: 'plant-1', local_date: '2026-10-20', forecast_kwh: 12,
    business_timezone: 'Asia/Shanghai', revision: 1 });
  assert.equal(service.listEvents().length, 1);
  service.upsertDailyGeneration({ plant_id: 'plant-1', local_date: '2026-10-21', forecast_kwh: 20,
    forecast_model_version: 'TEST_ONLY', business_timezone: 'Asia/Shanghai', revision: 1 });
  service.upsertDailyGeneration({ plant_id: 'plant-1', local_date: '2026-10-21', actual_kwh: 18,
    business_timezone: 'Asia/Shanghai', revision: 1 });
  assert.equal(service.listEvents().length, 2);
  service.upsertDailyGeneration({ plant_id: 'plant-1', local_date: '2026-10-21', actual_kwh: 19, forecast_kwh: 20,
    forecast_model_version: 'TEST_ONLY', business_timezone: 'Asia/Shanghai', revision: 2 });
  assert.equal(service.listEvents().length, 3);
  assert.equal(db.prepare('SELECT count(*) count FROM notification_delivery').get().count, 12);
  assert.ok(service.listEvents().every(event => event.fact_snapshot.is_test_forecast === 1));
  db.close();
});

test('事务回滚不留通知，过期租约可恢复', () => {
  const { db, service } = setup();
  const observation = service.recordObservation({ id: 'rollback-obs', equipment_id: 'A',
    observed_at: '2026-10-22T10:00:00.000Z', data_quality: 'ready' });
  db.exec('BEGIN IMMEDIATE');
  db.prepare(`INSERT INTO model_result(id,observation_id,equipment_id,observed_at,task_name,request_id,
    model_version,threshold_version,raw_result,suspected_class,score,decision,quality_status,incident_key,state_level,created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run('rollback-result', observation.id, 'A', observation.observed_at,
    'runtime-anomaly', 'rollback-request', 'v1', 't1', '{}', 'dc-drop', 0.9, 'anomaly', 'ready', 'rollback', 1, observation.created_at);
  db.exec('ROLLBACK');
  assert.equal(service.listEvents().length, 0);
  anomaly(service, 'A', 1, { incident_key: 'lease-test' });
  const worker = new NotificationWorker(db, { sms: new MockProvider(), email: new MockProvider() }, { workerId: 'w1' });
  const claimed = worker.claimOne();
  assert.ok(claimed);
  db.prepare("UPDATE notification_delivery SET lease_expires_at='2000-01-01T00:00:00.000Z' WHERE id=?").run(claimed.id);
  assert.equal(worker.recoverExpiredLeases(), 1);
  assert.equal(db.prepare('SELECT status FROM notification_delivery WHERE id=?').get(claimed.id).status, 'retry_wait');
  db.close();
});

test('停用员工不群发，缺联系方式与无负责人可定位，blocked/uncertain 不发确认故障', () => {
  const { db, service } = setup();
  service.upsertPersonnel({ id: 'disabled', name: '停用人员', phone: '+8613800000003', email: 'off@example.com', enabled: false });
  service.upsertPersonnel({ id: 'missing', name: '缺联系方式', enabled: true });
  service.upsertEquipment({ id: 'C', plant_id: 'plant-1', name: '设备 C', owner_id: 'missing' }, 'test');
  service.upsertEquipment({ id: 'D', plant_id: 'plant-1', name: '设备 D', owner_id: null }, 'test');
  anomaly(service, 'C', 1, { incident_key: 'missing-contact' });
  let event = service.listEvents()[0];
  assert.deepEqual(service.getEvent(event.id).deliveries.map(d => d.status), ['needs_attention', 'needs_attention']);
  anomaly(service, 'D', 2, { incident_key: 'no-owner' });
  event = service.listEvents()[0];
  assert.equal(service.getEvent(event.id).problems[0].code, 'NO_ENABLED_OWNER');
  const blocked = anomaly(service, 'A', 3, { incident_key: 'blocked', decision: 'blocked', quality_status: 'blocked' });
  const before = service.listEvents().length;
  const confirmation = service.confirmFault({ model_result_id: blocked.id, status: 'confirmed_fault', note: '人工查看但数据质量阻断', actor: 'tester' });
  assert.equal(confirmation.event_created, false);
  assert.equal(service.listEvents().length, before);
  service.upsertDailyGeneration({ plant_id: 'plant-1', local_date: '2026-10-22', actual_kwh: 1, forecast_kwh: 2,
    business_timezone: 'Asia/Shanghai', forecast_model_version: 'TEST_ONLY' });
  const daily = service.listEvents()[0];
  assert.equal(service.getEvent(daily.id).deliveries.some(d => d.recipient_id === 'disabled'), false);
  db.close();
});

test('服务商超时未知状态进入待核验，不能直接人工重试', async () => {
  const { db, service } = setup();
  anomaly(service, 'A', 1, { incident_key: 'unknown-provider-state' });
  const unknown = new MockProvider({ fail: () => new ProviderError('TIMEOUT_UNKNOWN', '服务商可能已接收', { unknownAcceptance: true }) });
  const worker = new NotificationWorker(db, { sms: unknown, email: new MockProvider() }, { concurrency: 4 });
  await worker.runBatch();
  const sms = db.prepare("SELECT * FROM notification_delivery WHERE channel='sms'").get();
  assert.equal(sms.status, 'verify_pending');
  assert.throws(() => service.retryDelivery(sms.id, 'tester'), /必须先核验/);
  db.close();
});

