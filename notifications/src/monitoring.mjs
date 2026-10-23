import { createHash, randomUUID } from 'node:crypto';
import { transaction } from './db.mjs';
import { now, parseJson } from './service.mjs';

const TASKS = new Set(['timeseries', 'classification', 'detection']);
const KINDS = new Set(['site_observation', 'public_research', 'synthetic_demo']);
const DECISIONS = new Set(['normal', 'anomaly', 'uncertain', 'blocked']);
const QUALITIES = new Set(['ready', 'uncertain', 'blocked', 'missing']);
const CLASS_NAMES = {'hotspot':'组件热点','timeseries_anomaly':'运行时序异常','bottom dirt':'底部积尘',
  'break':'组件破损','debris cover':'异物覆盖','healthy panel':'健康组件','hot cell':'电池片热点',
  'junction box heat':'接线盒发热','shadow':'遮挡','short circuit panel':'组件短路',
  'string short circuit':'组串短路','substring open circuit':'子串开路'};
function fault(code, message, action, status = 400) {
  return Object.assign(new Error(message), { code, action, status });
}
function text(value, field, max = 160) {
  if (typeof value !== 'string' || !value.trim() || value.length > max || /[\u0000-\u001f]/.test(value))
    throw fault('INVALID_FIELD', `${field}未填写或格式不正确`, `请提供不超过 ${max} 字的${field}，不要包含换行或控制字符。`);
  return value.trim();
}
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  return value;
}
function digest(value) { return createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex'); }
function timestamp(value) {
  const source = text(value, '采集时间', 50);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(source) || !Number.isFinite(Date.parse(source)))
    throw fault('INVALID_TIME', '采集时间缺少时区或无法识别', '请使用带时区的时间，例如 2026-10-23T10:00:00+08:00。');
  const date = new Date(source);
  const [year, month, day, hour, minute, second] = source.slice(0, 19).split(/[-T:]/).map(Number);
  if (month < 1 || month > 12 || day < 1 || day > new Date(Date.UTC(year, month, 0)).getUTCDate() || hour > 23 || minute > 59 || second > 59)
    throw fault('INVALID_TIME', '采集日期或时间不存在', '请检查年月日和时分秒，使用实际采集时间。');
  if (date.getTime() > Date.now() + 5 * 60_000) throw fault('FUTURE_TIME', '采集时间晚于当前时间', '请检查采集设备的时钟和时区；不要填写预计执行时间。');
  return date.toISOString();
}
function bounded(value, fallback, maximum) {
  if (value == null || value === '') return fallback;
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < 0) throw fault('INVALID_PAGE', '分页参数不正确', '请使用非负整数。');
  return Math.min(number, maximum);
}

export class MonitoringService {
  constructor(db, notifications) { this.db = db; this.notifications = notifications; }

  saveMapping(input, actor) {
    const plant = text(input.plant_id, '电站编号');
    const asset = text(input.asset_id, '模型设备编号');
    const equipment = this.db.prepare('SELECT * FROM equipment WHERE id=?').get(text(input.equipment_id, '系统设备编号'));
    if (!equipment || equipment.plant_id !== plant) throw fault('DEVICE_MISMATCH', '系统设备不存在，或不属于该电站', '请先在通知中心创建设备，并选择同一电站下的设备。');
    return transaction(this.db, () => {
      this.db.prepare(`INSERT INTO model_asset_mapping(plant_id,asset_id,equipment_id,updated_at,updated_by)
        VALUES(?,?,?,?,?) ON CONFLICT(plant_id,asset_id) DO UPDATE SET equipment_id=excluded.equipment_id,
        updated_at=excluded.updated_at,updated_by=excluded.updated_by`).run(plant, asset, equipment.id, now(), actor);
      this.db.prepare(`INSERT INTO operator_audit(id,actor,action,target_id,detail,created_at) VALUES(?,?,'map_model_asset',?,?,?)`)
        .run(randomUUID(), actor, equipment.id, JSON.stringify({plant_id: plant, asset_id: asset}), now());
      return this.db.prepare('SELECT * FROM model_asset_mapping WHERE plant_id=? AND asset_id=?').get(plant, asset);
    });
  }
  mappings() { return this.db.prepare('SELECT * FROM model_asset_mapping ORDER BY plant_id,asset_id').all(); }

  receive(input, actor) {
    const requestId = text(input.request_id, '请求编号', 128);
    if (input.schema_version !== 'competition-inference-v1') throw fault('SCHEMA_VERSION', '模型结果格式版本不支持', '请使用 competition-inference-v1 格式；参考接口文档中的完整示例。');
    if (!Array.isArray(input.evidence) || !input.evidence.length || input.evidence.length > 100)
      throw fault('EVIDENCE_COUNT', '每次需要提供 1 至 100 条识别结果', '请把结果放入 evidence 数组；超过 100 条时分批提交，并使用不同请求编号。');
    const payloadHash = digest(input);
    return transaction(this.db, () => {
      const previous = this.db.prepare('SELECT * FROM model_import_receipt WHERE request_id=?').get(requestId);
      if (previous) {
        if (previous.payload_sha256 !== payloadHash) throw fault('REQUEST_CONFLICT', '这个请求编号已经接收过其他内容', '重试请保持原内容；修改结果后请生成新的 request_id。', 409);
        return {...JSON.parse(previous.outcome_json), replayed: true};
      }
      const items = input.evidence.map((item, index) => {
        this.db.exec('SAVEPOINT import_item');
        try {
          const saved = this.receiveOne(item, actor);
          this.db.exec('RELEASE import_item');
          return {index, ...saved};
        } catch (error) {
          this.db.exec('ROLLBACK TO import_item; RELEASE import_item');
          // Infrastructure failures must roll back the receipt so a retry can succeed.
          if (!error.code || !error.action) throw error;
          return {index, evidence_id: typeof item?.evidence_id === 'string' ? item.evidence_id : null,
            status: 'rejected', code: error.code, reason: error.message, action: error.action};
        }
      });
      const accepted = items.filter(item => item.status === 'accepted').length;
      const duplicates = items.filter(item => item.status === 'duplicate').length;
      const rejected = items.length - accepted - duplicates;
      const result = {request_id: requestId, status: rejected ? (accepted + duplicates ? 'partial' : 'rejected') : 'completed',
        accepted, duplicates, rejected, items, replayed: false, received_at: now()};
      this.db.prepare('INSERT INTO model_import_receipt(request_id,payload_sha256,outcome_json,actor,created_at) VALUES(?,?,?,?,?)')
        .run(requestId, payloadHash, JSON.stringify(result), actor, result.received_at);
      return result;
    });
  }

  receiveOne(item, actor) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) throw fault('INVALID_EVIDENCE', '这条识别结果不是对象', '请按接口示例提供单条 evidence 对象。');
    const evidenceId = text(item.evidence_id, '证据编号', 128);
    const itemHash = digest(item);
    const old = this.db.prepare('SELECT * FROM model_evidence_receipt WHERE evidence_id=?').get(evidenceId);
    if (old) {
      if (old.payload_sha256 !== itemHash) throw fault('EVIDENCE_CONFLICT', '这个证据编号已有不同结果', '请保留原记录；重新识别或更正结果时使用新的 evidence_id。', 409);
      return {evidence_id: evidenceId, status: 'duplicate', model_result_id: old.model_result_id};
    }
    const plant = text(item.plant_id, '电站编号');
    const asset = text(item.asset_id, '设备编号');
    const sourceObservation = text(item.observation_id, '采集记录编号');
    const observedAt = timestamp(item.observed_at);
    if (!TASKS.has(item.task)) throw fault('UNKNOWN_TASK', '无法识别模型任务类型', 'task 只能是 timeseries、classification 或 detection。');
    if (!KINDS.has(item.data_kind)) throw fault('DATA_KIND_REQUIRED', '没有说明结果来自真实采集还是测试数据', 'data_kind 请填写 site_observation（真实采集）、public_research（研究数据）或 synthetic_demo（演示数据）。');
    if (!QUALITIES.has(item.quality) || !DECISIONS.has(item.decision)) throw fault('INVALID_DECISION', '判定状态或数据质量状态不正确', 'decision 使用 normal/anomaly/uncertain/blocked；quality 使用 ready/uncertain/blocked/missing。');
    if (item.score != null && (typeof item.score !== 'number' || !Number.isFinite(item.score) || item.score < 0 || item.score > 1))
      throw fault('INVALID_SCORE', '模型分数必须在 0 到 1 之间', '请提供数字，不能传百分号、字符串或负数；没有有效分数时不填写 score。');
    const modelVersion = text(item.model_version, '模型版本');
    const thresholdVersion = text(item.threshold_version, '阈值版本');
    const qualityReason = item.quality_reason == null ? '' : text(item.quality_reason, '数据质量原因', 1000);
    const mapping = this.db.prepare('SELECT equipment_id FROM model_asset_mapping WHERE plant_id=? AND asset_id=?').get(plant, asset);
    const device = this.db.prepare('SELECT * FROM equipment WHERE id=?').get(mapping?.equipment_id || asset);
    if (!device || device.plant_id !== plant) throw fault('DEVICE_NOT_LINKED', `设备“${asset}”尚未关联到电站“${plant}”下的系统设备`, '请先在通知中心创建设备；如果模型编号与系统编号不同，在设备监测中设置设备编号对应关系后，用新 request_id 重试。');
    const quality = item.quality;
    // A low-quality anomaly never creates an alert, but is retained in the log.
    const decision = quality === 'ready' ? item.decision : 'uncertain';
    const raw = {...item, source_request_actor: actor};
    let observation = this.db.prepare('SELECT * FROM equipment_observation WHERE equipment_id=? AND observed_at=?').get(device.id, observedAt);
    if (!observation) observation = this.notifications.recordObservation({equipment_id: device.id, observed_at: observedAt,
      data_quality: quality, raw_reference: sourceObservation, raw_json: {input_sha256: item.input_sha256 || null}});
    const suspected = item.task === 'classification' && typeof item.model_output?.predicted_class === 'string'
      ? text(item.model_output.predicted_class, '预测类别') : item.task === 'detection' ? 'hotspot' : 'timeseries_anomaly';
    const bucket = Math.floor(Date.parse(observedAt) / (30 * 60_000));
    const result = this.notifications.recordModelResult({observation_id: observation.id, equipment_id: device.id,
      observed_at: observedAt, task_name: item.task, request_id: 'evidence:' + digest(evidenceId),
      model_version: modelVersion, threshold_version: thresholdVersion, raw_result: raw,
      suspected_class: suspected, score: item.score ?? null, decision, quality_status: quality,
      incident_key: `${item.data_kind}:${item.task}:${digest([device.id, suspected]).slice(0, 16)}:${bucket}`});
    this.db.prepare('INSERT INTO model_evidence_receipt(evidence_id,payload_sha256,model_result_id,data_kind,created_at) VALUES(?,?,?,?,?)')
      .run(evidenceId, itemHash, result.id, item.data_kind, now());
    const summary = quality !== 'ready'
      ? `输入数据不满足识别要求：${qualityReason || '模型未提供具体原因，请检查原始文件或采集完整性'}；已保留结果，未触发异常通知`
      : decision === 'anomaly' ? `模型发现疑似异常（${CLASS_NAMES[suspected] || suspected}），需要人工核查`
      : decision === 'normal' ? '本次模型检测未发现对应异常' : '本次识别无法确定，需要人工核查';
    this.db.prepare("UPDATE equipment_monitor_log SET data_kind=?,evidence_id=?,summary=? WHERE source_table='model_result' AND source_id=?")
      .run(item.data_kind, evidenceId, summary, result.id);
    // Outbox rows are not visible to the worker before this transaction commits.
    this.db.prepare("UPDATE notification_event SET simulated=? WHERE source_table='model_result' AND source_id=?")
      .run(item.data_kind === 'site_observation' ? 0 : 1, result.id);
    const event = this.db.prepare("SELECT id FROM notification_event WHERE source_table='model_result' AND source_id=?").get(result.id);
    return {status: 'accepted', evidence_id: evidenceId, equipment_id: device.id, model_result_id: result.id,
      notification_event_id: event?.id || null, decision, quality_status: quality,
      warnings: quality !== 'ready' ? [summary] : [], data_kind: item.data_kind};
  }

  receipt(id) {
    const result = this.db.prepare('SELECT outcome_json FROM model_import_receipt WHERE request_id=?').get(id);
    return result ? JSON.parse(result.outcome_json) : null;
  }

  devices() {
    const allStates = this.db.prepare(`SELECT * FROM (SELECT mr.id,mr.equipment_id,mr.task_name,mr.decision,mr.quality_status,mr.observed_at,
      COALESCE(r.data_kind,'legacy_unknown') data_kind,
      row_number() OVER(PARTITION BY mr.equipment_id,mr.task_name,COALESCE(r.data_kind,'legacy_unknown')
        ORDER BY mr.observed_at DESC,mr.created_at DESC,mr.rowid DESC) rank
      FROM model_result mr LEFT JOIN model_evidence_receipt r ON r.model_result_id=mr.id)
      WHERE rank=1 ORDER BY task_name,data_kind`).all();
    const statesByDevice = new Map();
    for (const {equipment_id, rank, ...state} of allStates) {
      if (!statesByDevice.has(equipment_id)) statesByDevice.set(equipment_id, []);
      statesByDevice.get(equipment_id).push(state);
    }
    const logTimes = new Map(this.db.prepare('SELECT equipment_id,max(observed_at) time FROM equipment_monitor_log GROUP BY equipment_id').all().map(row => [row.equipment_id,row.time]));
    return this.notifications.listEquipment().map(device => {
      return {...device, model_states: statesByDevice.get(device.id) || [], latest_log_at: logTimes.get(device.id) || null};
    });
  }

  logs({equipment_id, task, decision, data_kind, limit, offset} = {}) {
    const clauses = [], values = [];
    for (const [column, value, allowed] of [['equipment_id',equipment_id,null], ['task_name',task,TASKS],
      ['decision',decision,new Set([...DECISIONS,'confirmed_fault','rejected','needs_investigation'])],
      ['data_kind',data_kind,new Set([...KINDS,'legacy_unknown'])]]) {
      if (!value) continue;
      if (allowed && !allowed.has(value)) throw fault('INVALID_FILTER', '筛选条件不支持', '请使用页面提供的筛选项。');
      clauses.push(`l.${column}=?`); values.push(value);
    }
    const where = clauses.length ? 'WHERE ' + clauses.join(' AND ') : '';
    const count = this.db.prepare(`SELECT count(*) total FROM equipment_monitor_log l ${where}`).get(...values).total;
    const rows = this.db.prepare(`SELECT l.*,e.name equipment_name,e.plant_id,mr.score,mr.suspected_class,mr.model_version,mr.threshold_version
      FROM equipment_monitor_log l JOIN equipment e ON e.id=l.equipment_id JOIN model_result mr ON mr.id=l.model_result_id
      ${where} ORDER BY l.observed_at DESC,l.created_at DESC,l.rowid DESC LIMIT ? OFFSET ?`)
      .all(...values, bounded(limit, 25, 100), bounded(offset, 0, 1000000));
    return {total: count, logs: rows, server_time: now()};
  }
  detail(id) {
    const row = this.db.prepare(`SELECT l.*,e.name equipment_name,e.plant_id,mr.raw_result,mr.model_version,
      mr.threshold_version,mr.score,mr.suspected_class,o.raw_reference
      FROM equipment_monitor_log l JOIN equipment e ON e.id=l.equipment_id JOIN model_result mr ON mr.id=l.model_result_id
      JOIN equipment_observation o ON o.id=mr.observation_id WHERE l.id=?`).get(id);
    const raw = row ? parseJson(row.raw_result) : null;
    return row ? {...row, raw_result: raw, raw_reference: raw?.observation_id || row.raw_reference,
      human_history: this.db.prepare('SELECT status,note,actor,created_at FROM human_confirmation WHERE model_result_id=? ORDER BY created_at').all(row.model_result_id),
      notification_events: this.db.prepare("SELECT id,type,simulated,created_at FROM notification_event WHERE source_id=? OR source_id IN (SELECT id FROM human_confirmation WHERE model_result_id=?) ORDER BY created_at").all(row.model_result_id, row.model_result_id)} : null;
  }
}
