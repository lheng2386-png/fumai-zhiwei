import { randomUUID } from 'node:crypto';
import { parseJson } from './service.mjs';

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
  const subject = `[伏脉智维] ${prefix}`;
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
    html: `<p>${escapeHtml(text)}</p><p><a href="/notifications.html">返回伏脉智维通知中心</a></p>`
  };
}

export class MockProvider {
  constructor({ fail = null } = {}) {
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
  constructor(channel) { this.channel = channel; }
  async send() {
    throw new ProviderError('PROVIDER_NOT_CONFIGURED', `${this.channel} 真实服务商尚未配置`, { transient: false });
  }
  async lookup() { return { status: 'unknown' }; }
}

export function createProviders(mode = process.env.FUMAI_NOTIFICATION_MODE || 'mock') {
  if (mode === 'mock') return { sms: new MockProvider(), email: new MockProvider() };
  return { sms: new UnconfiguredRealProvider('sms'), email: new UnconfiguredRealProvider('email') };
}

