import { randomUUID } from 'node:crypto';
import { transaction } from './db.mjs';
import { ProviderError } from './providers.mjs';
import { now } from './service.mjs';

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

export class NotificationWorker {
  constructor(db, providers, options = {}) {
    this.db = db;
    this.providers = providers;
    this.workerId = options.workerId || process.env.FUMAI_WORKER_ID || `worker-${randomUUID()}`;
    this.concurrency = Math.max(1, Math.min(Number(options.concurrency || process.env.FUMAI_WORKER_CONCURRENCY || 4), 16));
    this.leaseSeconds = Number(options.leaseSeconds || process.env.FUMAI_WORKER_LEASE_SECONDS || 60);
    this.maxAttempts = Number(options.maxAttempts || process.env.FUMAI_WORKER_MAX_ATTEMPTS || 5);
    this.baseRetryMs = Number(options.baseRetryMs || 1000);
    this.emailStartAt=options.emailStartAt || process.env.FUMAI_EMAIL_START_AT || null;
    this.registeredEmailOnly=options.registeredEmailOnly ?? process.env.FUMAI_EMAIL_REGISTERED_ONLY==='true';
    this.providerTimeoutMs = Number(options.providerTimeoutMs || process.env.FUMAI_PROVIDER_TIMEOUT_MS || 10_000);
  }

  recoverExpiredLeases() {
    const timestamp = now();
    return this.db.prepare(`UPDATE notification_delivery
      SET status='retry_wait', next_retry_at=?, lease_owner=NULL, lease_expires_at=NULL,
          error_code='LEASE_EXPIRED', updated_at=?
      WHERE status='processing' AND lease_expires_at < ?`).run(timestamp, timestamp, timestamp).changes;
  }

  claimOne() {
    return transaction(this.db, () => {
      const timestamp = now();
      const row = this.db.prepare(`SELECT d.*, e.type event_type, e.fact_snapshot, e.simulated
        FROM notification_delivery d JOIN notification_event e ON e.id=d.event_id
        WHERE (d.status='pending' OR (d.status='retry_wait' AND d.next_retry_at<=?))
        ORDER BY d.created_at, d.id LIMIT 1`).get(timestamp);
      if (!row) return null;
      const leaseExpires = new Date(Date.now() + this.leaseSeconds * 1000).toISOString();
      const updated = this.db.prepare(`UPDATE notification_delivery
        SET status='processing', lease_owner=?, lease_expires_at=?, updated_at=?
        WHERE id=? AND status IN ('pending','retry_wait')`).run(this.workerId, leaseExpires, timestamp, row.id);
      return updated.changes === 1 ? { ...row, lease_owner: this.workerId, lease_expires_at: leaseExpires } : null;
    });
  }

  complete(delivery, result) {
    const timestamp = now();
    const attempt = delivery.attempt_count + 1;
    transaction(this.db, () => {
      this.db.prepare(`INSERT INTO notification_attempt(
        id, delivery_id, attempt_number, outcome, provider_message_id, detail, created_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?)`)
        .run(randomUUID(), delivery.id, attempt, result.simulated ? 'simulated_sent' : 'sent',
          result.providerMessageId || null, JSON.stringify({ accepted: result.accepted }), timestamp);
      this.db.prepare(`UPDATE notification_delivery SET status=?, attempt_count=?,
        provider_message_id=?, error_code=NULL, sent_at=?, next_retry_at=NULL,
        lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE id=? AND lease_owner=?`)
        .run(result.simulated ? 'simulated_sent' : 'sent', attempt, result.providerMessageId || null,
          timestamp, timestamp, delivery.id, this.workerId);
    });
  }

  fail(delivery, error) {
    const timestamp = now();
    const attempt = delivery.attempt_count + 1;
    const providerError = error instanceof ProviderError ? error :
      new ProviderError('UNEXPECTED_PROVIDER_ERROR', error?.message || '发送失败', { transient: true });
    let status = 'failed';
    let nextRetry = null;
    if (providerError.unknownAcceptance) status = 'verify_pending';
    else if (providerError.transient && attempt < this.maxAttempts) {
      status = 'retry_wait';
      const delay = Math.min(this.baseRetryMs * (2 ** (attempt - 1)), 15 * 60_000);
      nextRetry = new Date(Date.now() + delay).toISOString();
    }
    transaction(this.db, () => {
      this.db.prepare(`INSERT INTO notification_attempt(
        id, delivery_id, attempt_number, outcome, error_code, detail, created_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?)`)
        .run(randomUUID(), delivery.id, attempt, status, providerError.code,
          JSON.stringify({ message: providerError.message, transient: providerError.transient,
            unknown_acceptance: providerError.unknownAcceptance }), timestamp);
      this.db.prepare(`UPDATE notification_delivery SET status=?, attempt_count=?, next_retry_at=?,
        error_code=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=?
        WHERE id=? AND lease_owner=?`)
        .run(status, attempt, nextRetry, providerError.code, timestamp, delivery.id, this.workerId);
    });
  }

  async processOne(delivery) {
    try {
      const provider = this.providers[delivery.channel];
      if(delivery.channel==='email' && provider?.name==='smtp') {
        if(this.emailStartAt && delivery.created_at<this.emailStartAt)throw new ProviderError('SMTP_BEFORE_ACTIVATION','该通知早于真实邮件启用时间，属于历史任务，未发送；不会自动补发历史演示数据');
        const person=this.db.prepare('SELECT enabled FROM personnel WHERE id=?').get(delivery.recipient_id);
        if(!person?.enabled)throw new ProviderError('RECIPIENT_DISABLED','收件员工已停用，未发送');
        if(this.registeredEmailOnly && !this.db.prepare(`SELECT 1 FROM app_user_contact c JOIN app_user u ON u.id=c.user_id WHERE c.personnel_id=? AND u.enabled=1`).get(delivery.recipient_id))
          throw new ProviderError('RECIPIENT_NOT_REGISTERED','当前只向已注册且启用的员工发信；该人员尚未关联注册账号，未发送');
      }
      if (!provider) throw new ProviderError('CHANNEL_NOT_CONFIGURED', '发送渠道未配置');
      const result = await Promise.race([
        provider.send(delivery),
        sleep(this.providerTimeoutMs).then(() => { throw new ProviderError('PROVIDER_TIMEOUT', '服务商请求超时，接收状态待核验', { unknownAcceptance: true }); })
      ]);
      this.complete(delivery, result);
      return { id: delivery.id, status: result.simulated ? 'simulated_sent' : 'sent' };
    } catch (error) {
      this.fail(delivery, error);
      return { id: delivery.id, status: error.unknownAcceptance ? 'verify_pending' : 'failed_or_retry' };
    }
  }

  async runBatch() {
    this.recoverExpiredLeases();
    const claimed = [];
    while (claimed.length < this.concurrency) {
      const delivery = this.claimOne();
      if (!delivery) break;
      claimed.push(delivery);
    }
    return Promise.all(claimed.map(delivery => this.processOne(delivery)));
  }

  async runUntilIdle({ maxDeliveries = 1000, progress } = {}) {
    let processed = 0;
    while (processed < maxDeliveries) {
      const results = await this.runBatch();
      if (!results.length) break;
      processed += results.length;
      progress?.({ processed, results });
    }
    return { processed, remaining: this.db.prepare(`SELECT count(*) count FROM notification_delivery
      WHERE status IN ('pending','retry_wait','processing')`).get().count };
  }
}
