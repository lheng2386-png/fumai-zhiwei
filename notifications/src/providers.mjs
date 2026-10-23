import { randomUUID } from 'node:crypto';
import { execFile as nodeExecFile } from 'node:child_process';
import { parseJson } from './service.mjs';
import { sendSmtpMail } from './smtp.mjs';

export class ProviderError extends Error {
  constructor(code, message, { transient = false, unknownAcceptance = false } = {}) {
    super(message);
    this.code = code;
    this.transient = transient;
    this.unknownAcceptance = unknownAcceptance;
  }
}

export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);
}

export function buildMessage(delivery) {
  const facts = parseJson(delivery.fact_snapshot) || {};
  const prefix = delivery.event_type === 'daily_generation_ready' ? '日发电通知' :
    (delivery.event_type === 'confirmed_fault' ? '确认故障通知' : '疑似异常通知');
  const subject = `[伏脉智维] ${delivery.simulated ? '演练 / ' : ''}${prefix}`;
  let text;
  if (delivery.event_type === 'daily_generation_ready') {
    const testLabel = facts.is_test_forecast ? '（模拟预测值）' : '';
    text = `${prefix}：电站 ${facts.plant_id}，日期 ${facts.local_date}，实际 ${facts.actual_kwh} kWh，预测 ${facts.forecast_kwh} kWh${testLabel}，修订版 ${facts.revision}。`;
  } else {
    text = `${prefix}：设备 ${facts.equipment_name || facts.equipment_id}，时间 ${facts.observed_at}，状态 ${facts.fact_status}，事件 ${facts.incident_key}。`;
  }
  return {
    subject,
    text,
    html: `<p>${escapeHtml(text)}</p><p>请登录伏脉智维网站的通知中心查看详情。</p>`
  };
}

export class MockProvider {
  constructor({ fail = null } = {}) {
    this.name = 'mock';
    this.fail = fail;
    this.sent = [];
  }

  async send(delivery) {
    if (this.fail) {
      const failure = await this.fail(delivery);
      if (failure) throw failure;
    }
    const message = buildMessage(delivery);
    const result = { accepted: true, simulated: true, providerMessageId: `mock-${randomUUID()}` };
    this.sent.push({ delivery, message, result });
    return result;
  }

  async lookup() {
    return { status: 'unknown' };
  }
}

export class UnconfiguredRealProvider {
  constructor(channel) { this.channel = channel; this.name = 'unconfigured'; }
  async send() {
    throw new ProviderError('PROVIDER_NOT_CONFIGURED', `${this.channel} 真实服务商尚未配置`, { transient: false });
  }
  async lookup() { return { status: 'unknown' }; }
}

export class SmtpEmailProvider {
  constructor(config = {}, transport = sendSmtpMail) {
    this.name = 'smtp';
    this.config = config;
    this.transport = transport;
  }

  async send(delivery) {
    if (!this.config.host || !this.config.from) {
      throw new ProviderError('SMTP_NOT_CONFIGURED', 'SMTP 主机或发件人尚未配置');
    }
    const allowed=this.config.testRecipients;
    if(Array.isArray(allowed) && allowed.length && !allowed.includes(String(delivery.address_snapshot||'').toLowerCase())) {
      throw new ProviderError('SMTP_TEST_RECIPIENT_BLOCKED','当前为邮件测试模式，仅允许发往配置的测试邮箱；该员工的通知尚未发送');
    }
    try {
      return await this.transport(this.config, { to: delivery.address_snapshot, ...buildMessage(delivery) });
    } catch (error) {
      if (error instanceof ProviderError) throw error;
      throw new ProviderError(error.code || 'SMTP_SEND_FAILED', '邮件服务商发送失败', {
        transient: error.transient ?? !/^SMTP_5/.test(error.code || ''),
        unknownAcceptance: Boolean(error.unknownAcceptance)
      });
    }
  }

  async lookup() { return { status: 'unsupported' }; }
}

function executeFile(execFile, command, args, options) {
  return new Promise((resolve, reject) => {
    execFile(command, args, options, (error, stdout, stderr) => {
      if (error) {
        error.stdout = stdout;
        error.stderr = stderr;
        reject(error);
      } else resolve({ stdout, stderr });
    });
  });
}

export class GammuSmsProvider {
  constructor(config = {}, execFile = nodeExecFile) {
    this.name = 'gammu';
    this.config = config;
    this.execFile = execFile;
  }

  async send(delivery) {
    const command = this.config.command || 'gammu-smsd-inject';
    const args = [];
    if (this.config.configFile) args.push('-c', this.config.configFile);
    args.push('TEXT', delivery.address_snapshot, '-text', buildMessage(delivery).text);
    try {
      const { stdout } = await executeFile(this.execFile, command, args, {
        timeout: Number(this.config.timeoutMs || 10_000), windowsHide: true,
        maxBuffer: 64 * 1024, encoding: 'utf8'
      });
      const matched = String(stdout || '').match(/(?:ID|reference)\D+(\d+)/i);
      return { accepted: true, simulated: false, providerMessageId: matched?.[1] || `gammu-${randomUUID()}` };
    } catch (error) {
      const timedOut = error.killed || error.code === 'ETIMEDOUT';
      throw new ProviderError(timedOut ? 'GAMMU_TIMEOUT' : 'GAMMU_SEND_FAILED',
        timedOut ? '短信猫命令超时，发送状态待核验' : '短信猫发送失败', {
          transient: !timedOut,
          unknownAcceptance: timedOut
        });
    }
  }

  async lookup() { return { status: 'unsupported' }; }
}

function bool(value, fallback) {
  if (value == null || value === '') return fallback;
  return !['0', 'false', 'no', 'off'].includes(String(value).toLowerCase());
}

export function createProviders(mode = process.env.FUMAI_NOTIFICATION_MODE || 'mock') {
  const emailMode = process.env.FUMAI_EMAIL_PROVIDER || (mode === 'real' ? 'smtp' : 'mock');
  const smsMode = process.env.FUMAI_SMS_PROVIDER || (mode === 'real' ? 'gammu' : 'mock');
  const email = emailMode === 'mock' ? new MockProvider() : (emailMode === 'smtp' ? new SmtpEmailProvider({
    host: process.env.FUMAI_SMTP_HOST,
    testRecipients:(process.env.FUMAI_EMAIL_TEST_RECIPIENTS||'').split(',').map(s=>s.trim().toLowerCase()).filter(Boolean),
    port: Number(process.env.FUMAI_SMTP_PORT || 465),
    secure: bool(process.env.FUMAI_SMTP_SECURE, true),
    user: process.env.FUMAI_SMTP_USER,
    password: process.env.FUMAI_SMTP_PASSWORD,
    from: process.env.FUMAI_SMTP_FROM,
    helo: process.env.FUMAI_SMTP_HELO,
    timeoutMs: Number(process.env.FUMAI_PROVIDER_TIMEOUT_MS || 10_000)
  }) : new UnconfiguredRealProvider('email'));
  const sms = smsMode === 'mock' ? new MockProvider() : (smsMode === 'gammu' ? new GammuSmsProvider({
    command: process.env.FUMAI_GAMMU_COMMAND || 'gammu-smsd-inject',
    configFile: process.env.FUMAI_GAMMU_CONFIG,
    timeoutMs: Number(process.env.FUMAI_PROVIDER_TIMEOUT_MS || 10_000)
  }) : new UnconfiguredRealProvider('sms'));
  return { sms, email };
}
