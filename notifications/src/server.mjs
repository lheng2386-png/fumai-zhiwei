import { createReadStream, existsSync, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { extname, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID, createHash, timingSafeEqual } from 'node:crypto';
import { openDatabase } from './db.mjs';
import { AuthService } from './auth.mjs';
import { createProviders } from './providers.mjs';
import { NotificationService, now, parseJson } from './service.mjs';
import { NotificationWorker } from './worker.mjs';
import { MonitoringService } from './monitoring.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const db = openDatabase();
const service = new NotificationService(db);
const monitoring = new MonitoringService(db, service);
const auth = new AuthService(db);
const bootstrapUser = auth.ensureBootstrapFromEnv();
const providers = createProviders();
const worker = new NotificationWorker(db, providers);
const port = Number(process.env.FUMAI_NOTIFICATION_PORT || 8787);
const publicOrigin = process.env.FUMAI_PUBLIC_ORIGIN || `http://127.0.0.1:${port}`;
const expectedOrigin = new URL(publicOrigin).origin;
const staticRoot = resolve(process.env.FUMAI_STATIC_ROOT || resolve(here, '../../frontend/dist'));
const loginAttempts = new Map();
const registrationAttempts = new Map();
let drainRunning = false;

if (bootstrapUser) console.log(`Created bootstrap administrator: ${bootstrapUser.username}`);
if (!auth.userCount()) console.warn('No application user exists. Create the first administrator before opening the site.');

function sendJson(res, status, value, extraHeaders = {}) {
  const body = Buffer.from(JSON.stringify(value));
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8', 'Content-Length': body.length,
    'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', ...extraHeaders
  });
  res.end(body);
}

async function readJson(req) {
  if ((req.headers['content-type'] || '').split(';')[0] !== 'application/json') throw Object.assign(new Error('只接受 JSON'), { status: 415 });
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 64 * 1024) throw Object.assign(new Error('请求体不能超过 64 KiB'), { status: 413 });
    chunks.push(chunk);
  }
  const value = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('JSON 必须为对象');
  return value;
}

const roleLevel = { viewer: 1, operator: 2, admin: 3 };

function requireUser(req, minimumRole = 'viewer') {
  const user = auth.resolveRequest(req);
  if (!user) throw Object.assign(new Error('请先登录'), { status: 401 });
  if (roleLevel[user.role] < roleLevel[minimumRole]) throw Object.assign(new Error('当前账号权限不足'), { status: 403 });
  return user;
}

function requireModelSender(req) {
  const key = process.env.FUMAI_MODEL_INGEST_TOKEN;
  const supplied = req.headers.authorization;
  if (supplied) {
    if (!key || key.length < 32 || !supplied.startsWith('Bearer ') ||
      !timingSafeEqual(createHash('sha256').update(supplied.slice(7)).digest(), createHash('sha256').update(key).digest()))
      throw Object.assign(new Error('模型接入凭证无效，请检查服务器上的接入配置'), {status: 401});
    return {username: 'model-service'};
  }
  return requireUser(req, 'operator');
}

function checkLoginLimit(req, username) {
  const key = `${req.socket.remoteAddress || 'unknown'}:${String(username || '').trim().toLowerCase()}`;
  const cutoff = Date.now() - 15 * 60_000;
  const attempts = (loginAttempts.get(key) || []).filter(time => time > cutoff);
  loginAttempts.set(key, attempts);
  if (attempts.length >= 5) throw Object.assign(new Error('登录失败次数过多，请 15 分钟后再试'), { status: 429 });
  return { key, attempts };
}

function checkRegistrationLimit(req) {
  const key = req.socket.remoteAddress || 'unknown';
  const cutoff = Date.now() - 60 * 60_000;
  const attempts = (registrationAttempts.get(key) || []).filter(time => time > cutoff);
  if (attempts.length >= 5) throw Object.assign(new Error('该地址创建账号次数过多，请稍后再试'), { status: 429 });
  attempts.push(Date.now());
  registrationAttempts.set(key, attempts);
}

function audit(actor, action, targetId, detail) {
  db.prepare(`INSERT INTO operator_audit(id, actor, action, target_id, detail, created_at)
    VALUES (?, ?, ?, ?, ?, ?)`).run(randomUUID(), actor, action, targetId, JSON.stringify(detail || {}), now());
}

function createDrainTask() {
  if (drainRunning) throw Object.assign(new Error('已有发送处理任务正在运行'), { status: 409 });
  const id = randomUUID();
  const timestamp = now();
  db.prepare(`INSERT INTO async_task(id,type,status,progress,created_at,updated_at)
    VALUES (?,'notification_drain','queued',0,?,?)`).run(id, timestamp, timestamp);
  drainRunning = true;
  setImmediate(async () => {
    try {
      db.prepare("UPDATE async_task SET status='running',progress=5,updated_at=? WHERE id=?").run(now(), id);
      const pending = db.prepare(`SELECT count(*) count FROM notification_delivery
        WHERE status IN ('pending','retry_wait','processing')`).get().count || 1;
      const result = await worker.runUntilIdle({
        progress: ({ processed }) => db.prepare('UPDATE async_task SET progress=?,updated_at=? WHERE id=?')
          .run(Math.min(95, 5 + Math.round(processed / pending * 90)), now(), id)
      });
      db.prepare("UPDATE async_task SET status='succeeded',progress=100,result=?,updated_at=? WHERE id=?")
        .run(JSON.stringify(result), now(), id);
    } catch (error) {
      db.prepare("UPDATE async_task SET status='failed',error=?,updated_at=? WHERE id=?")
        .run(error.message, now(), id);
    } finally { drainRunning = false; }
  });
  return db.prepare('SELECT * FROM async_task WHERE id=?').get(id);
}

function getTask(id) {
  const task = db.prepare('SELECT * FROM async_task WHERE id=?').get(id);
  return task ? { ...task, result: parseJson(task.result) } : null;
}

async function streamTask(req, res, id) {
  requireUser(req, 'admin');
  const first = getTask(id);
  if (!first) return sendJson(res, 404, { error: '任务不存在' });
  res.writeHead(200, { 'Content-Type': 'text/event-stream; charset=utf-8', 'Cache-Control': 'no-store', 'Connection': 'keep-alive', 'X-Accel-Buffering': 'no' });
  const deadline = Date.now() + 60_000;
  while (!res.destroyed && Date.now() < deadline) {
    const task = getTask(id);
    const terminal = ['succeeded', 'failed'].includes(task.status);
    res.write(`event: ${terminal ? 'done' : 'progress'}\ndata: ${JSON.stringify(task)}\n\n`);
    if (terminal) break;
    await new Promise(resolveTimer => setTimeout(resolveTimer, 500));
  }
  res.end();
}

function serveStatic(pathname, res) {
  const allowed = new Map([
    ['/', 'index.html'], ['/index.html', 'index.html'], ['/app.js', 'app.js'], ['/data.js', 'data.js'],
    ['/styles.css', 'styles.css'], ['/favicon.svg', 'favicon.svg'], ['/agent.css', 'agent.css'],
    ['/agent.js', 'agent.js'], ['/intent.js', 'intent.js'],
    ['/monitoring.js', 'monitoring.js'], ['/monitoring.css', 'monitoring.css'],
    ['/notifications.html', 'notifications.html'], ['/notifications.js', 'notifications.js'],
    ['/notification-admin.css', 'notification-admin.css'], ['/login.html', 'login.html'],
    ['/login.js', 'login.js'], ['/login.css', 'login.css'], ['/auth-guard.js', 'auth-guard.js']
  ]);
  const name = allowed.get(pathname);
  if (!name) return false;
  const file = resolve(staticRoot, name);
  if (!file.startsWith(staticRoot) || !existsSync(file) || !statSync(file).isFile()) return false;
  const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' }[extname(file)] || 'application/octet-stream';
  res.writeHead(200, { 'Content-Type': `${mime}; charset=utf-8`, 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
  createReadStream(file).pipe(res);
  return true;
}

const server = createServer(async (req, res) => {
  const requestId = randomUUID();
  try {
    const url = new URL(req.url, publicOrigin);
    const path = url.pathname;
    if (req.headers.origin && new URL(req.headers.origin).origin !== expectedOrigin) throw Object.assign(new Error('来源不匹配'), { status: 403 });

    if (req.method === 'GET' && path === '/api/health') return sendJson(res, 200, {
      status: 'ok', notification_mode: process.env.FUMAI_NOTIFICATION_MODE || 'mock',
      providers: { sms: providers.sms.name, email: providers.email.name },
      authentication_ready: auth.userCount() > 0, real_daily_forecast: false, database: 'sqlite',
      model_result_ingestion: 'competition-inference-v1', equipment_monitor_logs: true
    });
    if (req.method === 'POST' && path === '/api/auth/login') {
      const input = await readJson(req);
      const rate = checkLoginLimit(req, input.username);
      const user = auth.authenticate(input.username, input.password);
      if (!user) {
        rate.attempts.push(Date.now());
        loginAttempts.set(rate.key, rate.attempts);
        throw Object.assign(new Error('用户名或密码错误'), { status: 401 });
      }
      loginAttempts.delete(rate.key);
      const session = auth.createSession(user.id);
      return sendJson(res, 200, { user, expires_at: session.expires_at }, { 'Set-Cookie': auth.sessionCookie(session.token) });
    }
    if (req.method === 'POST' && path === '/api/auth/register') {
      if (process.env.FUMAI_SELF_REGISTRATION === 'false') throw Object.assign(new Error('当前未开放自行创建账号'), { status: 403 });
      checkRegistrationLimit(req);
      const user = auth.registerUser(await readJson(req));
      const session = auth.createSession(user.id);
      audit(user.username, 'self_register', user.id, { role: user.role });
      return sendJson(res, 201, { user, expires_at: session.expires_at }, { 'Set-Cookie': auth.sessionCookie(session.token) });
    }
    if (req.method === 'POST' && path === '/api/auth/logout') {
      auth.revokeRequest(req);
      return sendJson(res, 200, { logged_out: true }, { 'Set-Cookie': auth.clearCookie() });
    }
    if (req.method === 'GET' && path === '/api/auth/me') return sendJson(res, 200, { user: {...requireUser(req),notification_contact:auth.contact(requireUser(req).id)} });

    if (path==='/api/auth/notification-email') {
      const user=requireUser(req);
      if(req.method==='GET') return sendJson(res,200,{contact:auth.contact(user.id)});
      if(req.method==='POST') {
        const contact=auth.saveContact(user.id,await readJson(req));
        audit(user.username,'save_own_notification_email',user.id,{personnel_id:contact.personnel_id});
        return sendJson(res,200,{contact,message:'通知邮箱已保存。新通知将使用此地址，历史发送记录保持原地址。'});
      }
    }
    if (req.method === 'GET' && path === '/api/users') {
      const current = requireUser(req, 'admin');
      return sendJson(res, 200, { users: auth.listUsers(), current_user: current });
    }
    if (req.method === 'POST' && path === '/api/users') {
      const actor = requireUser(req, 'admin');
      const saved = auth.saveUser(await readJson(req));
      audit(actor.username, 'save_user', saved.id, { username: saved.username, role: saved.role, enabled: saved.enabled });
      return sendJson(res, 200, saved);
    }
    if (req.method === 'GET' && path === '/api/personnel') { requireUser(req); return sendJson(res, 200, { personnel: service.listPersonnel() }); }
    if (req.method === 'POST' && path === '/api/personnel') { requireUser(req, 'admin'); return sendJson(res, 200, service.upsertPersonnel(await readJson(req))); }
    if (req.method === 'GET' && path === '/api/equipment') { requireUser(req); return sendJson(res, 200, { equipment: service.listEquipment() }); }
    if (req.method === 'POST' && path === '/api/equipment') {
      const actor = requireUser(req, 'admin');
      return sendJson(res, 200, service.upsertEquipment(await readJson(req), actor.username));
    }
    if (req.method === 'POST' && path === '/api/observations') { requireUser(req, 'operator'); return sendJson(res, 201, service.recordObservation(await readJson(req))); }
    if (req.method === 'POST' && path === '/api/model-results') { requireUser(req, 'operator'); return sendJson(res, 201, service.recordModelResult(await readJson(req))); }
    if (req.method === 'POST' && path === '/api/model-ingestions') {
      const sender = requireModelSender(req);
      const result = monitoring.receive(await readJson(req), sender.username);
      return sendJson(res, result.replayed ? 200 : 201, result);
    }
    const receiptMatch = path.match(/^\/api\/model-ingestions\/([^/]+)$/);
    if (req.method === 'GET' && receiptMatch) {
      requireModelSender(req);
      const receipt = monitoring.receipt(decodeURIComponent(receiptMatch[1]));
      return receipt ? sendJson(res, 200, receipt) : sendJson(res, 404, {error: '接收记录不存在'});
    }
    if (path === '/api/model-asset-mappings') {
      if (req.method === 'GET') {requireUser(req); return sendJson(res, 200, {mappings: monitoring.mappings()});}
      if (req.method === 'POST') {const actor = requireUser(req, 'admin'); return sendJson(res, 200, monitoring.saveMapping(await readJson(req), actor.username));}
    }
    if (req.method === 'GET' && path === '/api/monitoring/equipment') {
      requireUser(req); return sendJson(res, 200, {equipment: monitoring.devices()});
    }
    if (req.method === 'GET' && path === '/api/monitoring/logs') {
      requireUser(req); return sendJson(res, 200, monitoring.logs(Object.fromEntries(url.searchParams)));
    }
    const monitorMatch = path.match(/^\/api\/monitoring\/logs\/([^/]+)$/);
    if (req.method === 'GET' && monitorMatch) {
      requireUser(req);
      const detail = monitoring.detail(monitorMatch[1]);
      return detail ? sendJson(res, 200, detail) : sendJson(res, 404, {error: '设备监控日志不存在'});
    }
    if (req.method === 'POST' && path === '/api/human-confirmations') {
      const actor = requireUser(req, 'admin');
      return sendJson(res, 201, service.confirmFault({ ...(await readJson(req)), actor: actor.username }));
    }
    if (req.method === 'POST' && path === '/api/daily-generation') { requireUser(req, 'operator'); return sendJson(res, 200, service.upsertDailyGeneration(await readJson(req))); }
    if (req.method === 'GET' && path === '/api/notifications/events') {
      requireUser(req);
      return sendJson(res, 200, { events: service.listEvents({ limit: url.searchParams.get('limit'), offset: url.searchParams.get('offset'), category: url.searchParams.get('category') || 'all' }) });
    }
    const eventMatch = path.match(/^\/api\/notifications\/events\/([^/]+)$/);
    if (req.method === 'GET' && eventMatch) {
      requireUser(req);
      const event = service.getEvent(eventMatch[1]);
      return event ? sendJson(res, 200, event) : sendJson(res, 404, { error: '事件不存在' });
    }
    const retryMatch = path.match(/^\/api\/notifications\/deliveries\/([^/]+)\/retry$/);
    if (req.method === 'POST' && retryMatch) {
      const actor = requireUser(req, 'admin');
      return sendJson(res, 200, service.retryDelivery(retryMatch[1], actor.username));
    }
    if (req.method === 'POST' && path === '/api/notification-jobs') { requireUser(req, 'admin'); return sendJson(res, 202, createDrainTask()); }
    const taskEventMatch = path.match(/^\/api\/notification-jobs\/([^/]+)\/events$/);
    if (req.method === 'GET' && taskEventMatch) return streamTask(req, res, taskEventMatch[1]);
    const taskMatch = path.match(/^\/api\/notification-jobs\/([^/]+)$/);
    if (req.method === 'GET' && taskMatch) {
      requireUser(req, 'admin');
      const task = getTask(taskMatch[1]);
      return task ? sendJson(res, 200, task) : sendJson(res, 404, { error: '任务不存在' });
    }
    if (req.method === 'GET' && serveStatic(path, res)) return;
    return sendJson(res, 404, { error: '接口不存在', request_id: requestId });
  } catch (error) {
    const databaseFailure = String(error.code || '').startsWith('SQLITE') || String(error.code || '').startsWith('ERR_SQLITE');
    const constraintFailure = databaseFailure && (Number(error.errcode) & 255) === 19;
    const status = constraintFailure ? 400 : databaseFailure ? 503 : error.status || 400;
    sendJson(res, status, { error: constraintFailure ? '记录编号重复或关联记录不存在，请检查请求编号和设备信息' : databaseFailure ? '数据暂未保存，请稍后使用相同请求编号重试' : error.message || '请求失败',
      ...(error.action ? {action: error.action, code: error.code} : {}), request_id: requestId });
  }
});

server.listen(port, '127.0.0.1', () => console.log(`Fumai notification service: ${publicOrigin} (sms=${providers.sms.name}, email=${providers.email.name})`));

for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => server.close(() => { db.close(); process.exit(0); }));
