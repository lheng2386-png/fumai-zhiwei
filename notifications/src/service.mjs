import { randomUUID } from 'node:crypto';
import { transaction } from './db.mjs';

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const PHONE = /^\+?[0-9](?:[0-9 -]{5,18}[0-9])$/;
const QUALITY = new Set(['ready', 'uncertain', 'blocked', 'missing']);

export const now = () => new Date().toISOString();

function required(value, name) {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`${name}不能为空`);
  return value.trim();
}

function json(value) {
  return JSON.stringify(value ?? {}, null, 0);
}

export function parseJson(value) {
  if (value == null) return null;
  try { return JSON.parse(value); } catch { return value; }
}

export class NotificationService {
  constructor(db, { cooldownMinutes = 30 } = {}) {
    this.db = db;
    this.cooldownMinutes = cooldownMinutes;
  }

  upsertPersonnel(input) {
    const id = required(input.id, '员工 ID');
    const name = required(input.name, '姓名');
    const phone = input.phone == null || input.phone.trim() === '' ? null : input.phone.trim();
    const email = input.email == null || input.email.trim() === '' ? null : input.email.trim().toLowerCase();
    if (phone && !PHONE.test(phone)) throw new Error('手机号格式无效');
    if (email && !EMAIL.test(email)) throw new Error('邮箱格式无效');
    const updatedAt = now();
    this.db.prepare(`
      INSERT INTO personnel(id, name, phone, email, enabled, updated_at)
      VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET name=excluded.name, phone=excluded.phone,
        email=excluded.email, enabled=excluded.enabled, updated_at=excluded.updated_at
    `).run(id, name, phone, email, input.enabled === false ? 0 : 1, updatedAt);
    const warnings = [];
    if (!phone) warnings.push('MISSING_PHONE');
    if (!email) warnings.push('MISSING_EMAIL');
    return { ...this.db.prepare('SELECT * FROM personnel WHERE id=?').get(id), warnings };
  }

  listPersonnel() {
    return this.db.prepare('SELECT * FROM personnel ORDER BY enabled DESC, name').all();
  }

  upsertEquipment(input, actor = 'api') {
    const id = required(input.id, '设备 ID');
    const plantId = required(input.plant_id, '电站 ID');
    const name = required(input.name, '设备名称');
    const ownerId = input.owner_id == null || input.owner_id === '' ? null : input.owner_id;
    if (ownerId && !this.db.prepare('SELECT 1 FROM personnel WHERE id=?').get(ownerId)) throw new Error('负责人不存在');
    const timestamp = now();
    transaction(this.db, () => {
      const previous = this.db.prepare('SELECT owner_id FROM equipment WHERE id=?').get(id);
      this.db.prepare(`
        INSERT INTO equipment(id, plant_id, name, owner_id, owner_updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET plant_id=excluded.plant_id, name=excluded.name,
          owner_id=excluded.owner_id, owner_updated_at=excluded.owner_updated_at
      `).run(id, plantId, name, ownerId, timestamp);
      if (previous && previous.owner_id !== ownerId) {
        this.db.prepare(`UPDATE equipment_owner_history SET changed_by=?
          WHERE equipment_id=? AND changed_at=?`).run(actor, id, timestamp);
      }
    });
    return this.db.prepare(`SELECT e.*, p.name owner_name FROM equipment e
      LEFT JOIN personnel p ON p.id=e.owner_id WHERE e.id=?`).get(id);
  }

  listEquipment() {
    return this.db.prepare(`SELECT e.*, p.name owner_name, p.enabled owner_enabled
      FROM equipment e LEFT JOIN personnel p ON p.id=e.owner_id ORDER BY e.plant_id, e.name`).all();
  }

  recordObservation(input) {
    const quality = input.data_quality || 'ready';
    if (!QUALITY.has(quality)) throw new Error('数据质量状态无效');
    const id = input.id || randomUUID();
    this.db.prepare(`INSERT INTO equipment_observation(
      id, equipment_id, observed_at, raw_reference, raw_json, data_quality, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?)`)
      .run(id, required(input.equipment_id, '设备 ID'), required(input.observed_at, '观测时间'),
        input.raw_reference || null, input.raw_json == null ? null : json(input.raw_json), quality, now());
    return this.db.prepare('SELECT * FROM equipment_observation WHERE id=?').get(id);
  }

  resolveIncidentKey(input) {
    if (input.incident_key) return required(input.incident_key, '异常事件键');
    const cutoff = new Date(new Date(input.observed_at).getTime() - this.cooldownMinutes * 60_000).toISOString();
    const previous = this.db.prepare(`SELECT incident_key FROM model_result
      WHERE equipment_id=? AND decision='anomaly' AND suspected_class IS ? AND observed_at>=?
      ORDER BY observed_at DESC LIMIT 1`).get(input.equipment_id, input.suspected_class || null, cutoff);
    return previous?.incident_key || `incident-${input.equipment_id}-${randomUUID()}`;
  }

  recordModelResult(input) {
    if (!QUALITY.has(input.quality_status)) throw new Error('模型结果质量状态无效');
    if (!['normal', 'anomaly', 'uncertain', 'blocked'].includes(input.decision)) throw new Error('模型 decision 无效');
    const observation = this.db.prepare('SELECT * FROM equipment_observation WHERE id=?').get(input.observation_id);
    if (!observation || observation.equipment_id !== input.equipment_id) throw new Error('观测记录不存在或设备不匹配');
    const id = input.id || randomUUID();
    const incidentKey = this.resolveIncidentKey({ ...input, observed_at: input.observed_at || observation.observed_at });
    this.db.prepare(`INSERT INTO model_result(
      id, observation_id, equipment_id, observed_at, task_name, request_id, model_version,
      threshold_version, raw_result, suspected_class, score, decision, quality_status,
      incident_key, state_level, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
      .run(id, observation.id, input.equipment_id, input.observed_at || observation.observed_at,
        required(input.task_name, '任务名'), required(input.request_id, 'request_id'),
        required(input.model_version, '模型版本'), required(input.threshold_version, '阈值版本'),
        json(input.raw_result), input.suspected_class || null, input.score ?? null,
        input.decision, input.quality_status, incidentKey, input.state_level || 1, now());
    return { ...this.db.prepare('SELECT * FROM model_result WHERE id=?').get(id), raw_result: input.raw_result };
  }

  confirmFault(input) {
    const model = this.db.prepare('SELECT * FROM model_result WHERE id=?').get(input.model_result_id);
    if (!model) throw new Error('模型结果不存在');
    const id = input.id || randomUUID();
    this.db.prepare(`INSERT INTO human_confirmation(id, model_result_id, status, note, actor, created_at)
      VALUES (?, ?, ?, ?, ?, ?)`)
      .run(id, model.id, required(input.status, '确认状态'), required(input.note, '确认说明'), required(input.actor, '操作人'), now());
    const eventCreated = input.status === 'confirmed_fault' && model.quality_status === 'ready';
    return { id, event_created: eventCreated, blocked_reason: eventCreated ? null :
      (input.status === 'confirmed_fault' ? `QUALITY_${model.quality_status.toUpperCase()}` : 'NOT_CONFIRMED') };
  }

  upsertDailyGeneration(input) {
    const revision = Number(input.revision ?? 1);
    if (!Number.isInteger(revision) || revision < 1) throw new Error('修订版本必须是正整数');
    if (input.forecast_kwh != null && input.is_test_forecast === false) {
      throw new Error('真实日发电量预测服务尚未接入；当前只允许明确标注的测试预测值');
    }
    const plantId = required(input.plant_id, '电站 ID');
    const localDate = required(input.local_date, '当地日期');
    const timezone = required(input.business_timezone, '业务时区');
    const existing = this.db.prepare(`SELECT id FROM daily_generation
      WHERE plant_id=? AND local_date=? AND revision=?`).get(plantId, localDate, revision);
    const id = existing?.id || input.id || randomUUID();
    const timestamp = now();
    this.db.prepare(`
      INSERT INTO daily_generation(id, plant_id, local_date, actual_kwh, forecast_kwh,
        forecast_generated_at, forecast_model_version, business_timezone, revision,
        is_test_forecast, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
      ON CONFLICT(plant_id, local_date, revision) DO UPDATE SET
        actual_kwh=COALESCE(excluded.actual_kwh, daily_generation.actual_kwh),
        forecast_kwh=COALESCE(excluded.forecast_kwh, daily_generation.forecast_kwh),
        forecast_generated_at=COALESCE(excluded.forecast_generated_at, daily_generation.forecast_generated_at),
        forecast_model_version=COALESCE(excluded.forecast_model_version, daily_generation.forecast_model_version),
        business_timezone=excluded.business_timezone, is_test_forecast=1, updated_at=excluded.updated_at
    `).run(id, plantId, localDate, input.actual_kwh ?? null, input.forecast_kwh ?? null,
      input.forecast_generated_at || null, input.forecast_model_version || null,
      timezone, revision, timestamp);
    return this.db.prepare(`SELECT * FROM daily_generation
      WHERE plant_id=? AND local_date=? AND revision=?`).get(plantId, localDate, revision);
  }

  listEvents({ limit = 100, offset = 0 } = {}) {
    return this.db.prepare(`SELECT e.*,
      SUM(CASE WHEN d.status IN ('sent','simulated_sent') THEN 1 ELSE 0 END) delivered,
      SUM(CASE WHEN d.status IN ('failed','needs_attention','verify_pending') THEN 1 ELSE 0 END) attention,
      COUNT(d.id) delivery_count
      FROM notification_event e LEFT JOIN notification_delivery d ON d.event_id=e.id
      GROUP BY e.id ORDER BY e.created_at DESC LIMIT ? OFFSET ?`).all(Math.min(Number(limit) || 100, 500), Number(offset) || 0)
      .map(row => ({ ...row, fact_snapshot: parseJson(row.fact_snapshot) }));
  }

  getEvent(id) {
    const event = this.db.prepare('SELECT * FROM notification_event WHERE id=?').get(id);
    if (!event) return null;
    const deliveries = this.db.prepare(`SELECT d.*, p.name recipient_name FROM notification_delivery d
      JOIN personnel p ON p.id=d.recipient_id WHERE d.event_id=? ORDER BY p.name, d.channel`).all(id)
      .map(delivery => ({ ...delivery, attempts: this.db.prepare(
        'SELECT * FROM notification_attempt WHERE delivery_id=? ORDER BY attempt_number').all(delivery.id) }));
    const problems = this.db.prepare('SELECT * FROM notification_problem WHERE event_id=? ORDER BY created_at').all(id);
    return { ...event, fact_snapshot: parseJson(event.fact_snapshot), deliveries, problems };
  }

  retryDelivery(id, actor) {
    const delivery = this.db.prepare('SELECT * FROM notification_delivery WHERE id=?').get(id);
    if (!delivery) throw new Error('发送任务不存在');
    if (delivery.status === 'verify_pending') throw new Error('服务商接收状态不明，必须先核验发送状态，不能直接重试');
    if (delivery.status !== 'failed') throw new Error('当前状态不可人工重试');
    const timestamp = now();
    transaction(this.db, () => {
      this.db.prepare(`UPDATE notification_delivery SET status='pending', next_retry_at=NULL,
        error_code=NULL, lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE id=?`).run(timestamp, id);
      this.db.prepare(`INSERT INTO operator_audit(id, actor, action, target_id, detail, created_at)
        VALUES (?, ?, 'retry_delivery', ?, ?, ?)`).run(randomUUID(), required(actor, '操作人'), id,
        json({ previous_status: delivery.status, attempt_count: delivery.attempt_count }), timestamp);
    });
    return this.db.prepare('SELECT * FROM notification_delivery WHERE id=?').get(id);
  }
}

