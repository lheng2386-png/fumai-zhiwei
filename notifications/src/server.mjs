import { createReadStream, existsSync, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { extname, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import { openDatabase } from './db.mjs';
import { createProviders } from './providers.mjs';
import { NotificationService, now, parseJson } from './service.mjs';
import { NotificationWorker } from './worker.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const db = openDatabase();
const service = new NotificationService(db);
const worker = new NotificationWorker(db, createProviders());
const port = Number(process.env.FUMAI_NOTIFICATION_PORT || 8787);
const publicOrigin = process.env.FUMAI_PUBLIC_ORIGIN || `http://127.0.0.1:${port}`;
const staticRoot = resolve(process.env.FUMAI_STATIC_ROOT || resolve(here, '../../frontend/dist'));
let drainRunning = false;

function sendJson(res, status, value) {
  const body = Buffer.from(JSON.stringify(value));
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': body.length,
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff'
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

function requireAdmin(req) {
  if (req.headers['x-fumai-role'] !== 'admin') throw Object.assign(new Error('需要管理员权限'), { status: 403 });
  const actor = String(req.headers['x-fumai-actor'] || '').trim();
  if (!actor) throw Object.assign(new Error('需要 X-Fumai-Actor 审计标识'), { status: 400 });
  return actor;
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
        progress: ({ processed }) => db.prepare(`UPDATE async_task SET progress=?,updated_at=? WHERE id=?`)
          .run(Math.min(95, 5 + Math.round(processed / pending * 90)), now(), id)
      });
      db.prepare(`UPDATE async_task SET status='succeeded',progress=100,result=?,updated_at=? WHERE id=?`)
        .run(JSON.stringify(result), now(), id);
    } catch (error) {
      db.prepare(`UPDATE async_task SET status='failed',error=?,updated_at=? WHERE id=?`)
        .run(error.message, now(), id);
    } finally {
      drainRunning = false;
    }
  });
  return db.prepare('SELECT * FROM async_task WHERE id=?').get(id);
}

function getTask(id) {
  const task = db.prepare('SELECT * FROM async_task WHERE id=?').get(id);
  return task ? { ...task, result: parseJson(task.result) } : null;
}

async function streamTask(req, res, id) {
  const first = getTask(id);
  if (!first) return sendJson(res, 404, { error: '任务不存在' });
  res.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-store',
    'Connection': 'keep-alive',
    'X-Accel-Buffering': 'no'
  });
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
    ['/styles.css', 'styles.css'], ['/favicon.svg', 'favicon.svg'],
    ['/notifications.html', 'notifications.html'], ['/notifications.js', 'notifications.js'],
    ['/notification-admin.css', 'notification-admin.css']
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
    if (req.headers.origin && req.headers.origin !== publicOrigin) throw Object.assign(new Error('来源不匹配'), { status: 403 });

    if (req.method === 'GET' && path === '/api/health') return sendJson(res, 200, {
      status: 'ok', notification_mode: process.env.FUMAI_NOTIFICATION_MODE || 'mock',
      real_daily_forecast: false, database: 'sqlite'
    });
    if (req.method === 'GET' && path === '/api/personnel') return sendJson(res, 200, { personnel: service.listPersonnel() });
    if (req.method === 'POST' && path === '/api/personnel') {
      requireAdmin(req);
      return sendJson(res, 200, service.upsertPersonnel(await readJson(req)));
    }
    if (req.method === 'GET' && path === '/api/equipment') return sendJson(res, 200, { equipment: service.listEquipment() });
    if (req.method === 'POST' && path === '/api/equipment') {
      const actor = requireAdmin(req);
      return sendJson(res, 200, service.upsertEquipment(await readJson(req), actor));
    }
    if (req.method === 'POST' && path === '/api/observations') return sendJson(res, 201, service.recordObservation(await readJson(req)));
    if (req.method === 'POST' && path === '/api/model-results') return sendJson(res, 201, service.recordModelResult(await readJson(req)));
    if (req.method === 'POST' && path === '/api/human-confirmations') {
      requireAdmin(req);
      return sendJson(res, 201, service.confirmFault(await readJson(req)));
    }
    if (req.method === 'POST' && path === '/api/daily-generation') return sendJson(res, 200, service.upsertDailyGeneration(await readJson(req)));
    if (req.method === 'GET' && path === '/api/notifications/events') return sendJson(res, 200, {
      events: service.listEvents({ limit: url.searchParams.get('limit'), offset: url.searchParams.get('offset') })
    });
    const eventMatch = path.match(/^\/api\/notifications\/events\/([^/]+)$/);
    if (req.method === 'GET' && eventMatch) {
      const event = service.getEvent(eventMatch[1]);
      return event ? sendJson(res, 200, event) : sendJson(res, 404, { error: '事件不存在' });
    }
    const retryMatch = path.match(/^\/api\/notifications\/deliveries\/([^/]+)\/retry$/);
    if (req.method === 'POST' && retryMatch) return sendJson(res, 200, service.retryDelivery(retryMatch[1], requireAdmin(req)));
    if (req.method === 'POST' && path === '/api/notification-jobs') {
      requireAdmin(req);
      return sendJson(res, 202, createDrainTask());
    }
    const taskEventMatch = path.match(/^\/api\/notification-jobs\/([^/]+)\/events$/);
    if (req.method === 'GET' && taskEventMatch) return streamTask(req, res, taskEventMatch[1]);
    const taskMatch = path.match(/^\/api\/notification-jobs\/([^/]+)$/);
    if (req.method === 'GET' && taskMatch) {
      const task = getTask(taskMatch[1]);
      return task ? sendJson(res, 200, task) : sendJson(res, 404, { error: '任务不存在' });
    }
    if (req.method === 'GET' && serveStatic(path, res)) return;
    return sendJson(res, 404, { error: '接口不存在', request_id: requestId });
  } catch (error) {
    const status = error.status || (error instanceof SyntaxError ? 400 : 400);
    sendJson(res, status, { error: error.message || '请求失败', request_id: requestId });
  }
});

server.listen(port, '127.0.0.1', () => {
  console.log(`Fumai notification service: ${publicOrigin} (mode=${process.env.FUMAI_NOTIFICATION_MODE || 'mock'})`);
});

for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => server.close(() => {
  db.close();
  process.exit(0);
}));

