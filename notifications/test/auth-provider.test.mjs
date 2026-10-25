import test from 'node:test';
import assert from 'node:assert/strict';
import { openDatabase } from '../src/db.mjs';
import { AuthService } from '../src/auth.mjs';
import { GammuSmsProvider, SmtpEmailProvider } from '../src/providers.mjs';
import { NotificationService } from '../src/service.mjs';

test('多用户密码哈希、角色和 HttpOnly 会话可用', () => {
  const db = openDatabase(':memory:');
  const auth = new AuthService(db, { sessionHours: 1, secureCookie: false });
  const admin = auth.saveUser({ username: 'admin.yzx', display_name: '管理员', password: 'StrongPass-2026', role: 'admin' });
  const viewer = auth.saveUser({ username: 'viewer_01', display_name: '查看者', password: 'ViewerPass-2026', role: 'viewer' });
  assert.equal(auth.authenticate('ADMIN.YZX', 'StrongPass-2026').id, admin.id);
  assert.equal(auth.authenticate('admin.yzx', 'wrong-password'), null);
  assert.notEqual(db.prepare('SELECT password_hash FROM app_user WHERE id=?').get(admin.id).password_hash, 'StrongPass-2026');
  const session = auth.createSession(viewer.id);
  const cookie = auth.sessionCookie(session.token);
  assert.match(cookie, /HttpOnly/);
  assert.match(cookie, /SameSite=Lax/);
  const request = { headers: { cookie }, socket: {} };
  assert.equal(auth.resolveRequest(request).username, 'viewer_01');
  auth.revokeRequest(request);
  assert.equal(auth.resolveRequest(request), null);
  assert.throws(() => auth.saveUser({ username: 'admin.yzx', display_name: '管理员', role: 'viewer', enabled: true }), /最后一个/);
  db.close();
});

test('故障通知和日发电通知可按类别独立查询', () => {
  const db = openDatabase(':memory:');
  const service = new NotificationService(db);
  service.upsertPersonnel({ id: 'p1', name: '负责人', phone: '+8613800000001', email: 'p1@example.com' });
  service.upsertEquipment({ id: 'A', plant_id: 'plant-1', name: '设备 A', owner_id: 'p1' }, 'test');
  const observation = service.recordObservation({ equipment_id: 'A', observed_at: '2026-10-23T01:00:00.000Z', data_quality: 'ready' });
  service.recordModelResult({ observation_id: observation.id, equipment_id: 'A', task_name: 'runtime', request_id: 'r1',
    model_version: 'v1', threshold_version: 't1', raw_result: {}, suspected_class: 'drop', score: .9,
    decision: 'anomaly', quality_status: 'ready', incident_key: 'incident-1' });
  service.upsertDailyGeneration({ plant_id: 'plant-1', local_date: '2026-10-23', actual_kwh: 0, forecast_kwh: 10,
    business_timezone: 'Asia/Shanghai', forecast_model_version: 'TEST_ONLY' });
  assert.deepEqual(service.listEvents({ category: 'fault' }).map(event => event.category), ['fault']);
  assert.deepEqual(service.listEvents({ category: 'daily' }).map(event => event.category), ['daily']);
  assert.equal(service.listEvents({ category: 'all' }).length, 2);
  db.close();
});

test('SMTP 邮件适配器使用真实结果结构且模板转义 HTML', async () => {
  let captured;
  const provider = new SmtpEmailProvider({ host: 'smtp.example.com', port: 465, from: 'Fumai <noreply@example.com>' }, async (config, message) => {
    captured = { config, message };
    return { accepted: true, simulated: false, providerMessageId: 'smtp-1' };
  });
  const result = await provider.send({ address_snapshot: 'user@example.com', event_type: 'model_anomaly',
    fact_snapshot: JSON.stringify({ equipment_name: '<设备 A>', observed_at: '2026-10-23T01:00:00Z', fact_status: '疑似异常', incident_key: 'i1' }) });
  assert.equal(result.providerMessageId, 'smtp-1');
  assert.equal(captured.message.to, 'user@example.com');
  assert.match(captured.message.html, /&lt;设备 A&gt;/);
  assert.doesNotMatch(captured.message.html, /<设备 A>/);
});

test('Gammu 短信猫适配器通过参数数组调用且返回消息编号', async () => {
  let call;
  const fakeExec = (command, args, options, callback) => {
    call = { command, args, options };
    callback(null, 'Written message with ID 42', '');
  };
  const provider = new GammuSmsProvider({ command: 'gammu-smsd-inject', configFile: '/etc/gammu-smsdrc' }, fakeExec);
  const result = await provider.send({ address_snapshot: '+8613800000001', event_type: 'daily_generation_ready',
    fact_snapshot: JSON.stringify({ plant_id: 'p1', local_date: '2026-10-23', actual_kwh: 0, forecast_kwh: 1, revision: 1, is_test_forecast: 1 }) });
  assert.equal(result.simulated, false);
  assert.equal(result.providerMessageId, '42');
  assert.equal(call.command, 'gammu-smsd-inject');
  assert.deepEqual(call.args.slice(0, 4), ['-c', '/etc/gammu-smsdrc', 'TEXT', '+8613800000001']);
});
