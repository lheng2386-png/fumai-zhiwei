import { randomUUID } from 'node:crypto';
import { transaction } from './db.mjs';

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const ASSET_TYPES = new Set(['substation','sub_array','transformer','inverter','panel_series','meteorograph','switch','meter','other']);
const INSPECTION_STATUSES = new Set(['planned','in_progress','passed','issue_found','closed']);
const FAULT_STATUSES = new Set(['suspected','confirmed','processing','resolved','rejected']);
const BUSINESS_TABLES = Object.freeze({
  substations: 'substation_info', 'sub-arrays': 'sub_array_info', transformers: 'transformer_info',
  inverters: 'inverter_info', 'panel-series': 'panel_series_info', equipment: 'equipment_info',
  'iec104-endpoints': 'iec104_endpoint', 'iec104-points': 'iec104_point', meteorograph: 'meteorograph_data_info',
  materials: 'equipment_materials_info', inspections: 'equipment_inspection_info', faults: 'fault_history_info'
});

function timestamp(value = new Date().toISOString()) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) throw new Error(`无效时间：${value}`);
  return date.toISOString();
}
function required(value, name) {
  const result = String(value ?? '').trim();
  if (!result) throw new Error(`${name}不能为空`);
  return result;
}
function optional(value) { return value == null || value === '' ? null : String(value).trim(); }
function numberOrNull(value, name) {
  if (value == null || value === '') return null;
  const result = Number(value);
  if (!Number.isFinite(result)) throw new Error(`${name}必须是数字`);
  return result;
}
function integer(value, name) {
  const result = Number(value);
  if (!Number.isInteger(result)) throw new Error(`${name}必须是整数`);
  return result;
}
function json(value) { return JSON.stringify(value ?? {}); }
function parse(value, fallback = {}) { try { return JSON.parse(value); } catch { return fallback; } }

export class OperationsService {
  constructor(db) { this.db = db; }

  list(resource, { substationId = null, equipmentId = null, status = null, limit = 500 } = {}) {
    const table = BUSINESS_TABLES[resource];
    if (!table) throw new Error('不支持的业务资源');
    const columns = new Set(this.db.prepare(`PRAGMA table_info(${table})`).all().map(row => row.name));
    const clauses = [];
    const params = [];
    if (substationId && columns.has('substation_id')) { clauses.push('substation_id=?'); params.push(substationId); }
    if (equipmentId && columns.has('equipment_id')) { clauses.push('equipment_id=?'); params.push(equipmentId); }
    if (status && columns.has('status')) { clauses.push('status=?'); params.push(status); }
    const order = columns.has('observed_at') ? 'observed_at DESC' : columns.has('updated_at') ? 'updated_at DESC' :
      columns.has('created_at') ? 'created_at DESC' : 'id';
    params.push(Math.min(Math.max(Number(limit) || 500, 1), 2000));
    return this.db.prepare(`SELECT * FROM ${table}${clauses.length ? ` WHERE ${clauses.join(' AND ')}` : ''} ORDER BY ${order} LIMIT ?`)
      .all(...params).map(row => {
        const result = { ...row };
        for (const field of ['metadata_json','raw_json','result_json']) {
          if (field in result) { result[field.replace('_json', '')] = parse(result[field]); delete result[field]; }
        }
        return result;
      });
  }

  delete(resource, id) {
    const table = BUSINESS_TABLES[resource];
    if (!table) throw new Error('不支持的业务资源');
    const key = required(id, '记录 ID');
    const row = this.db.prepare(`SELECT * FROM ${table} WHERE id=?`).get(key);
    if (!row) throw new Error('记录不存在');
    try { this.db.prepare(`DELETE FROM ${table} WHERE id=?`).run(key); }
    catch (error) {
      if (String(error.message).includes('FOREIGN KEY')) throw new Error('该记录仍被其他业务数据引用，不能删除');
      throw error;
    }
    return { deleted: true, resource, id: key };
  }

  status() {
    const count = table => this.db.prepare(`SELECT count(*) count FROM ${table}`).get().count;
    const connected = this.db.prepare("SELECT count(*) count FROM iec104_endpoint WHERE connection_status='connected'").get().count;
    return {
      source_status: count('iec104_endpoint') ? (connected ? 'connected' : 'configured_offline') : 'unconfigured',
      protocol: 'IEC 60870-5-104', database: 'sqlite-wal', cache: 'latest_point_value',
      counts: {
        substations: count('substation_info'), equipment: count('equipment_info'), points: count('iec104_point'),
        telemetry: count('telemetry_info'), signals: count('telesignalling_info'), active_warnings:
          this.db.prepare("SELECT count(*) count FROM warning_info WHERE status='active'").get().count,
        model_runs: count('model_inference_run')
      },
      model_families: this.db.prepare(`SELECT model_family, max(completed_at) last_completed_at,
        sum(CASE WHEN status='succeeded' THEN 1 ELSE 0 END) succeeded_runs
        FROM model_inference_run GROUP BY model_family ORDER BY model_family`).all(),
      limitations: {
        field_gateway: count('iec104_endpoint') ? null : '尚未配置现场 104 地址和点表',
        models: count('model_inference_run') ? null : '尚未收到三套模型的真实输出',
        read_replica: '当前为单机 SQLite WAL；切换 PostgreSQL/MySQL 后才能配置物理读副本'
      }
    };
  }

  upsertSubstation(input) {
    const id = optional(input.id) || randomUUID();
    this.db.prepare(`INSERT INTO substation_info(id,code,name,timezone,latitude,longitude,installed_capacity_kw,enabled,updated_at)
      VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET code=excluded.code,name=excluded.name,
      timezone=excluded.timezone,latitude=excluded.latitude,longitude=excluded.longitude,
      installed_capacity_kw=excluded.installed_capacity_kw,enabled=excluded.enabled,updated_at=excluded.updated_at`)
      .run(id, required(input.code, '场站编码'), required(input.name, '场站名称'), optional(input.timezone) || 'Asia/Shanghai',
        numberOrNull(input.latitude, '纬度'), numberOrNull(input.longitude, '经度'),
        numberOrNull(input.installed_capacity_kw, '装机容量'), input.enabled === false ? 0 : 1, timestamp());
    return this.db.prepare('SELECT * FROM substation_info WHERE id=?').get(id);
  }

  upsertSubArray(input) {
    const id = optional(input.id) || randomUUID();
    this.db.prepare(`INSERT INTO sub_array_info(id,substation_id,code,name,installed_capacity_kw,updated_at)
      VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET substation_id=excluded.substation_id,code=excluded.code,
      name=excluded.name,installed_capacity_kw=excluded.installed_capacity_kw,updated_at=excluded.updated_at`)
      .run(id, required(input.substation_id, '场站 ID'), required(input.code, '子阵编码'), required(input.name, '子阵名称'),
        numberOrNull(input.installed_capacity_kw, '装机容量'), timestamp());
    return this.db.prepare('SELECT * FROM sub_array_info WHERE id=?').get(id);
  }

  upsertTransformer(input) {
    const id = optional(input.id) || randomUUID();
    this.db.prepare(`INSERT INTO transformer_info(id,sub_array_id,code,name,rated_capacity_kva,voltage_level,updated_at)
      VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET sub_array_id=excluded.sub_array_id,code=excluded.code,
      name=excluded.name,rated_capacity_kva=excluded.rated_capacity_kva,voltage_level=excluded.voltage_level,updated_at=excluded.updated_at`)
      .run(id, optional(input.sub_array_id), required(input.code, '箱变编码'), required(input.name, '箱变名称'),
        numberOrNull(input.rated_capacity_kva, '额定容量'), optional(input.voltage_level), timestamp());
    return this.db.prepare('SELECT * FROM transformer_info WHERE id=?').get(id);
  }

  upsertInverter(input) {
    const id = optional(input.id) || randomUUID();
    this.db.prepare(`INSERT INTO inverter_info(id,transformer_id,code,name,rated_power_kw,manufacturer,model,updated_at)
      VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET transformer_id=excluded.transformer_id,code=excluded.code,
      name=excluded.name,rated_power_kw=excluded.rated_power_kw,manufacturer=excluded.manufacturer,
      model=excluded.model,updated_at=excluded.updated_at`)
      .run(id, optional(input.transformer_id), required(input.code, '逆变器编码'), required(input.name, '逆变器名称'),
        numberOrNull(input.rated_power_kw, '额定功率'), optional(input.manufacturer), optional(input.model), timestamp());
    return this.db.prepare('SELECT * FROM inverter_info WHERE id=?').get(id);
  }

  upsertPanelSeries(input) {
    const id = optional(input.id) || randomUUID();
    this.db.prepare(`INSERT INTO panel_series_info(id,inverter_id,code,name,panel_count,rated_power_kw,updated_at)
      VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET inverter_id=excluded.inverter_id,code=excluded.code,
      name=excluded.name,panel_count=excluded.panel_count,rated_power_kw=excluded.rated_power_kw,updated_at=excluded.updated_at`)
      .run(id, required(input.inverter_id, '逆变器 ID'), required(input.code, '组串编码'), required(input.name, '组串名称'),
        input.panel_count == null ? null : integer(input.panel_count, '组件数量'), numberOrNull(input.rated_power_kw, '额定功率'), timestamp());
    return this.db.prepare('SELECT * FROM panel_series_info WHERE id=?').get(id);
  }

  upsertEquipment(input) {
    const type = required(input.equipment_type, '设备类型');
    if (!ASSET_TYPES.has(type)) throw new Error('不支持的设备类型');
    const id = optional(input.id) || randomUUID();
    const now = timestamp();
    this.db.prepare(`INSERT INTO equipment_info(id,substation_id,parent_id,equipment_type,business_ref_id,code,name,
      manufacturer,model,serial_number,status,longitude,latitude,metadata_json,enabled,created_at,updated_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
      substation_id=excluded.substation_id,parent_id=excluded.parent_id,equipment_type=excluded.equipment_type,
      business_ref_id=excluded.business_ref_id,code=excluded.code,name=excluded.name,manufacturer=excluded.manufacturer,
      model=excluded.model,serial_number=excluded.serial_number,status=excluded.status,longitude=excluded.longitude,
      latitude=excluded.latitude,metadata_json=excluded.metadata_json,enabled=excluded.enabled,updated_at=excluded.updated_at`)
      .run(id, required(input.substation_id, '场站 ID'), optional(input.parent_id), type, optional(input.business_ref_id),
        required(input.code, '设备编码'), required(input.name, '设备名称'), optional(input.manufacturer), optional(input.model),
        optional(input.serial_number), optional(input.status) || 'unknown', numberOrNull(input.longitude, '经度'),
        numberOrNull(input.latitude, '纬度'), json(input.metadata), input.enabled === false ? 0 : 1, now, now);
    return this.db.prepare('SELECT * FROM equipment_info WHERE id=?').get(id);
  }

  upsertEndpoint(input) {
    const id = optional(input.id) || randomUUID();
    const port = input.port == null ? 2404 : integer(input.port, '端口');
    this.db.prepare(`INSERT INTO iec104_endpoint(id,substation_id,name,host,port,common_address,enabled,connection_status,updated_at)
      VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET substation_id=excluded.substation_id,name=excluded.name,
      host=excluded.host,port=excluded.port,common_address=excluded.common_address,enabled=excluded.enabled,updated_at=excluded.updated_at`)
      .run(id, required(input.substation_id, '场站 ID'), required(input.name, '采集端名称'), required(input.host, '主机地址'),
        port, input.common_address == null ? null : integer(input.common_address, '公共地址'), input.enabled ? 1 : 0,
        input.enabled ? 'disconnected' : 'unconfigured', timestamp());
    return this.db.prepare('SELECT * FROM iec104_endpoint WHERE id=?').get(id);
  }

  upsertPoint(input) {
    const id = optional(input.id) || randomUUID();
    const kind = required(input.point_kind, '点类型');
    if (!['telemetry', 'telesignalling'].includes(kind)) throw new Error('点类型只能是 telemetry 或 telesignalling');
    this.db.prepare(`INSERT INTO iec104_point(id,endpoint_id,equipment_id,ioa,common_address,type_id,point_kind,name,unit,
      scale,offset,alarm_value,warning_code,warning_title,warning_severity,enabled,updated_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET endpoint_id=excluded.endpoint_id,
      equipment_id=excluded.equipment_id,ioa=excluded.ioa,common_address=excluded.common_address,type_id=excluded.type_id,
      point_kind=excluded.point_kind,name=excluded.name,unit=excluded.unit,scale=excluded.scale,offset=excluded.offset,
      alarm_value=excluded.alarm_value,warning_code=excluded.warning_code,warning_title=excluded.warning_title,
      warning_severity=excluded.warning_severity,enabled=excluded.enabled,updated_at=excluded.updated_at`)
      .run(id, required(input.endpoint_id, '采集端 ID'), required(input.equipment_id, '设备 ID'), integer(input.ioa, 'IOA'),
        input.common_address == null ? null : integer(input.common_address, '公共地址'), integer(input.type_id, '类型标识'), kind,
        required(input.name, '点名称'), optional(input.unit), numberOrNull(input.scale, '比例') ?? 1,
        numberOrNull(input.offset, '偏移') ?? 0, optional(input.alarm_value), optional(input.warning_code),
        optional(input.warning_title), optional(input.warning_severity), input.enabled === false ? 0 : 1, timestamp());
    return this.db.prepare('SELECT * FROM iec104_point WHERE id=?').get(id);
  }

  ingestIec104(input) {
    const endpointId = required(input.endpoint_id, '采集端 ID');
    const endpoint = this.db.prepare('SELECT * FROM iec104_endpoint WHERE id=? AND enabled=1').get(endpointId);
    if (!endpoint) throw new Error('采集端不存在或未启用');
    if (!Array.isArray(input.points) || !input.points.length) throw new Error('points 至少包含一个点');
    const asdu = input.asdu || {};
    const result = { accepted: 0, duplicate: 0, unmapped: [], warnings_created: 0 };
    const ingestedAt = timestamp();
    transaction(this.db, () => {
      for (const sample of input.points) {
        const ioa = integer(sample.ioa, 'IOA');
        const point = this.db.prepare('SELECT * FROM iec104_point WHERE endpoint_id=? AND ioa=? AND enabled=1').get(endpointId, ioa);
        if (!point) { result.unmapped.push(ioa); continue; }
        const observedAt = timestamp(sample.observed_at || input.observed_at || ingestedAt);
        const quality = optional(sample.quality) || 'good';
        const raw = json({ endpoint_id: endpointId, asdu, sample });
        let inserted;
        let numericValue = null;
        let textValue = null;
        if (point.point_kind === 'telemetry') {
          numericValue = numberOrNull(sample.value, point.name);
          if (numericValue != null) numericValue = numericValue * point.scale + point.offset;
          inserted = this.db.prepare(`INSERT OR IGNORE INTO telemetry_info
            (id,point_id,equipment_id,observed_at,value,unit,quality,cause,source_sequence,raw_json,ingested_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)`).run(randomUUID(), point.id, point.equipment_id, observedAt, numericValue,
              point.unit, quality, asdu.cause ?? null, asdu.sequence ?? null, raw, ingestedAt).changes;
        } else {
          numericValue = sample.value == null ? null : Number(Boolean(Number(sample.value)));
          textValue = optional(sample.value_text) || String(sample.value ?? '');
          inserted = this.db.prepare(`INSERT OR IGNORE INTO telesignalling_info
            (id,point_id,equipment_id,observed_at,state,value_text,quality,cause,source_sequence,raw_json,ingested_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)`).run(randomUUID(), point.id, point.equipment_id, observedAt, numericValue,
              textValue, quality, asdu.cause ?? null, asdu.sequence ?? null, raw, ingestedAt).changes;
        }
        if (!inserted) { result.duplicate += 1; continue; }
        result.accepted += 1;
        this.db.prepare(`INSERT INTO latest_point_value(point_id,equipment_id,point_kind,observed_at,numeric_value,text_value,unit,quality,updated_at)
          VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(point_id) DO UPDATE SET equipment_id=excluded.equipment_id,
          point_kind=excluded.point_kind,observed_at=excluded.observed_at,numeric_value=excluded.numeric_value,
          text_value=excluded.text_value,unit=excluded.unit,quality=excluded.quality,updated_at=excluded.updated_at
          WHERE excluded.observed_at >= latest_point_value.observed_at`)
          .run(point.id, point.equipment_id, point.point_kind, observedAt, numericValue, textValue, point.unit, quality, ingestedAt);
        if (point.alarm_value != null && String(sample.value) === point.alarm_value) {
          const key = `${point.id}:${observedAt}:${point.alarm_value}`;
          result.warnings_created += this.db.prepare(`INSERT OR IGNORE INTO warning_info
            (id,dedupe_key,substation_id,equipment_id,source_type,source_id,warning_code,title,content,severity,status,
             occurred_at,quality,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,'active',?,?,?,?)`)
            .run(randomUUID(), key, endpoint.substation_id, point.equipment_id, 'iec104', point.id,
              point.warning_code || `IEC104-${point.ioa}`, point.warning_title || `${point.name}状态告警`,
              `${point.name} 上送值 ${sample.value}`, point.warning_severity || 'major', observedAt, quality, ingestedAt, ingestedAt).changes;
        }
      }
      this.db.prepare(`UPDATE iec104_endpoint SET connection_status='connected',last_connected_at=?,last_error=NULL,updated_at=? WHERE id=?`)
        .run(ingestedAt, ingestedAt, endpointId);
    });
    return { ...result, endpoint_id: endpointId, ingested_at: ingestedAt };
  }

  deviceTree(substationId = null) {
    const rows = this.db.prepare(`SELECT e.*, s.name substation_name,
      (SELECT count(*) FROM warning_info w WHERE w.equipment_id=e.id AND w.status='active') active_warning_count,
      (SELECT max(observed_at) FROM latest_point_value l WHERE l.equipment_id=e.id) latest_observed_at
      FROM equipment_info e JOIN substation_info s ON s.id=e.substation_id
      WHERE e.enabled=1 AND (? IS NULL OR e.substation_id=?) ORDER BY e.substation_id,e.parent_id,e.code`).all(substationId, substationId)
      .map(row => ({ ...row, metadata: parse(row.metadata_json), metadata_json: undefined, children: [] }));
    const byId = new Map(rows.map(row => [row.id, row]));
    const roots = [];
    for (const row of rows) (row.parent_id && byId.has(row.parent_id) ? byId.get(row.parent_id).children : roots).push(row);
    return { source_status: rows.length ? this.status().source_status : 'empty', roots, flat: rows };
  }

  snapshot(substationId = null) {
    const rows = this.db.prepare(`SELECT l.*,p.name point_name,p.ioa,p.type_id,e.code equipment_code,e.name equipment_name,e.status equipment_status
      FROM latest_point_value l JOIN iec104_point p ON p.id=l.point_id JOIN equipment_info e ON e.id=l.equipment_id
      WHERE (? IS NULL OR e.substation_id=?) ORDER BY e.code,p.ioa`).all(substationId, substationId);
    return { source_status: rows.length ? this.status().source_status : 'empty', generated_at: timestamp(), values: rows };
  }

  warnings({ status = null, limit = 100 } = {}) {
    const safeLimit = Math.min(Math.max(Number(limit) || 100, 1), 500);
    return this.db.prepare(`SELECT w.*,e.code equipment_code,e.name equipment_name,s.name substation_name
      FROM warning_info w LEFT JOIN equipment_info e ON e.id=w.equipment_id JOIN substation_info s ON s.id=w.substation_id
      WHERE (? IS NULL OR w.status=?) ORDER BY w.occurred_at DESC LIMIT ?`).all(status, status, safeLimit);
  }

  acknowledgeWarning(id, actor) {
    const at = timestamp();
    const changed = this.db.prepare(`UPDATE warning_info SET status='acknowledged',acknowledged_by=?,acknowledged_at=?,updated_at=?
      WHERE id=? AND status='active'`).run(required(actor, '确认人'), at, at, id).changes;
    if (!changed) throw new Error('告警不存在或已处理');
    return this.db.prepare('SELECT * FROM warning_info WHERE id=?').get(id);
  }

  clearWarning(id, actor) {
    const at = timestamp();
    const changed = this.db.prepare(`UPDATE warning_info SET status='cleared',acknowledged_by=coalesce(acknowledged_by,?),
      acknowledged_at=coalesce(acknowledged_at,?),cleared_at=?,updated_at=? WHERE id=? AND status!='cleared'`)
      .run(required(actor, '操作人'), at, at, at, id).changes;
    if (!changed) throw new Error('告警不存在或已清除');
    return this.db.prepare('SELECT * FROM warning_info WHERE id=?').get(id);
  }

  recordMeteorograph(input) {
    const id = optional(input.id) || randomUUID();
    const observedAt = timestamp(input.observed_at);
    this.db.prepare(`INSERT INTO meteorograph_data_info(id,substation_id,equipment_id,observed_at,irradiance_wm2,
      ambient_temperature_c,module_temperature_c,wind_speed_ms,wind_direction_deg,humidity_percent,pressure_hpa,quality,raw_json)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(substation_id,observed_at) DO UPDATE SET
      equipment_id=excluded.equipment_id,irradiance_wm2=excluded.irradiance_wm2,
      ambient_temperature_c=excluded.ambient_temperature_c,module_temperature_c=excluded.module_temperature_c,
      wind_speed_ms=excluded.wind_speed_ms,wind_direction_deg=excluded.wind_direction_deg,
      humidity_percent=excluded.humidity_percent,pressure_hpa=excluded.pressure_hpa,quality=excluded.quality,raw_json=excluded.raw_json`)
      .run(id, required(input.substation_id, '场站 ID'), optional(input.equipment_id), observedAt,
        numberOrNull(input.irradiance_wm2, '辐照度'), numberOrNull(input.ambient_temperature_c, '环境温度'),
        numberOrNull(input.module_temperature_c, '组件温度'), numberOrNull(input.wind_speed_ms, '风速'),
        numberOrNull(input.wind_direction_deg, '风向'), numberOrNull(input.humidity_percent, '湿度'),
        numberOrNull(input.pressure_hpa, '气压'), optional(input.quality) || 'good', json(input.raw));
    return this.db.prepare('SELECT * FROM meteorograph_data_info WHERE substation_id=? AND observed_at=?')
      .get(input.substation_id, observedAt);
  }

  upsertMaterial(input, actor = null) {
    const id = optional(input.id) || randomUUID();
    const now = timestamp();
    this.db.prepare(`INSERT INTO equipment_materials_info(id,equipment_id,material_type,title,file_url,version,checksum,uploaded_by,created_at,updated_at)
      VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET equipment_id=excluded.equipment_id,
      material_type=excluded.material_type,title=excluded.title,file_url=excluded.file_url,version=excluded.version,
      checksum=excluded.checksum,uploaded_by=excluded.uploaded_by,updated_at=excluded.updated_at`)
      .run(id, required(input.equipment_id, '设备 ID'), required(input.material_type, '资料类型'), required(input.title, '资料标题'),
        required(input.file_url, '文件地址'), optional(input.version), optional(input.checksum), optional(input.uploaded_by) || actor, now, now);
    return this.db.prepare('SELECT * FROM equipment_materials_info WHERE id=?').get(id);
  }

  upsertInspection(input) {
    const id = optional(input.id) || randomUUID();
    const status = optional(input.status) || 'planned';
    if (!INSPECTION_STATUSES.has(status)) throw new Error('不支持的巡检状态');
    const now = timestamp();
    this.db.prepare(`INSERT INTO equipment_inspection_info(id,equipment_id,inspector_id,inspection_type,status,scheduled_at,
      started_at,completed_at,result_json,note,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(id) DO UPDATE SET equipment_id=excluded.equipment_id,inspector_id=excluded.inspector_id,
      inspection_type=excluded.inspection_type,status=excluded.status,scheduled_at=excluded.scheduled_at,
      started_at=excluded.started_at,completed_at=excluded.completed_at,result_json=excluded.result_json,
      note=excluded.note,updated_at=excluded.updated_at`)
      .run(id, required(input.equipment_id, '设备 ID'), optional(input.inspector_id), required(input.inspection_type, '巡检类型'),
        status, input.scheduled_at ? timestamp(input.scheduled_at) : null, input.started_at ? timestamp(input.started_at) : null,
        input.completed_at ? timestamp(input.completed_at) : null, json(input.result), optional(input.note), now, now);
    return this.db.prepare('SELECT * FROM equipment_inspection_info WHERE id=?').get(id);
  }

  upsertFault(input, actor = null) {
    const id = optional(input.id) || randomUUID();
    const status = optional(input.status) || 'suspected';
    if (!FAULT_STATUSES.has(status)) throw new Error('不支持的故障状态');
    const now = timestamp();
    const existing = this.db.prepare('SELECT * FROM fault_history_info WHERE id=?').get(id);
    const discoveredAt = input.discovered_at ? timestamp(input.discovered_at) : existing?.discovered_at || now;
    const confirmedAt = status === 'confirmed' || status === 'processing' || status === 'resolved'
      ? (input.confirmed_at ? timestamp(input.confirmed_at) : existing?.confirmed_at || now) : null;
    const resolvedAt = status === 'resolved' ? (input.resolved_at ? timestamp(input.resolved_at) : existing?.resolved_at || now) : null;
    this.db.prepare(`INSERT INTO fault_history_info(id,warning_id,equipment_id,fault_code,fault_name,suspected_cause,resolution,
      status,discovered_at,confirmed_at,resolved_at,confirmed_by,model_result_id,created_at,updated_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET warning_id=excluded.warning_id,
      equipment_id=excluded.equipment_id,fault_code=excluded.fault_code,fault_name=excluded.fault_name,
      suspected_cause=excluded.suspected_cause,resolution=excluded.resolution,status=excluded.status,
      discovered_at=excluded.discovered_at,confirmed_at=excluded.confirmed_at,resolved_at=excluded.resolved_at,
      confirmed_by=excluded.confirmed_by,model_result_id=excluded.model_result_id,updated_at=excluded.updated_at`)
      .run(id, optional(input.warning_id), required(input.equipment_id, '设备 ID'), required(input.fault_code, '故障编码'),
        required(input.fault_name, '故障名称'), optional(input.suspected_cause), optional(input.resolution), status,
        discoveredAt, confirmedAt, resolvedAt, optional(input.confirmed_by) || (confirmedAt ? actor : null),
        optional(input.model_result_id), existing?.created_at || now, now);
    return this.db.prepare('SELECT * FROM fault_history_info WHERE id=?').get(id);
  }

  upsertGeneration(input) {
    const id = optional(input.id) || randomUUID();
    const date = required(input.local_date, '业务日期');
    if (!ISO_DATE.test(date)) throw new Error('业务日期格式必须为 YYYY-MM-DD');
    const now = timestamp();
    const revision = input.revision == null ? 1 : integer(input.revision, '修订版本');
    this.db.prepare(`INSERT INTO power_generation_report_info(id,substation_id,local_date,timezone,actual_kwh,forecast_kwh,
      grid_kwh,installed_capacity_kw,revision,is_simulated,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(substation_id,local_date,revision) DO UPDATE SET timezone=excluded.timezone,actual_kwh=excluded.actual_kwh,
      forecast_kwh=excluded.forecast_kwh,grid_kwh=excluded.grid_kwh,installed_capacity_kw=excluded.installed_capacity_kw,
      is_simulated=excluded.is_simulated,source=excluded.source,updated_at=excluded.updated_at`)
      .run(id, required(input.substation_id, '场站 ID'), date, optional(input.timezone) || 'Asia/Shanghai',
        numberOrNull(input.actual_kwh, '实际发电量'), numberOrNull(input.forecast_kwh, '预测发电量'),
        numberOrNull(input.grid_kwh, '上网电量'), numberOrNull(input.installed_capacity_kw, '装机容量'), revision,
        input.is_simulated ? 1 : 0, required(input.source, '数据来源'), now, now);
    return this.db.prepare('SELECT * FROM power_generation_report_info WHERE substation_id=? AND local_date=? AND revision=?')
      .get(input.substation_id, date, revision);
  }

  generation({ substationId = null, from = null, to = null } = {}) {
    return this.db.prepare(`SELECT r.*,s.code substation_code,s.name substation_name FROM power_generation_report_info r
      JOIN substation_info s ON s.id=r.substation_id WHERE (? IS NULL OR r.substation_id=?)
      AND (? IS NULL OR r.local_date>=?) AND (? IS NULL OR r.local_date<=?) ORDER BY r.local_date DESC,r.revision DESC`)
      .all(substationId, substationId, from, from, to, to);
  }

  calendar(input = {}) {
    const rows = this.generation(input);
    const values = rows.map(row => row.actual_kwh).filter(value => value != null);
    const max = values.length ? Math.max(...values) : 0;
    return { max_kwh: max, days: rows.map(row => ({ ...row, intensity: max ? row.actual_kwh / max : 0 })) };
  }

  recordModelOutput(input) {
    const run = input.run || input;
    const runId = optional(run.id) || randomUUID();
    const requestId = required(run.request_id, '模型 request_id');
    const existing = this.db.prepare('SELECT id FROM model_inference_run WHERE request_id=?').get(requestId);
    if (existing) return { duplicate: true, run_id: existing.id };
    const results = Array.isArray(input.results) ? input.results : [];
    const createdAt = timestamp();
    transaction(this.db, () => {
      this.db.prepare(`INSERT INTO model_inference_run(id,request_id,model_family,model_name,model_version,threshold_version,
        substation_id,equipment_id,input_started_at,input_ended_at,status,data_quality,source_reference,error,created_at,completed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(runId, requestId, required(run.model_family, '模型族'),
          required(run.model_name, '模型名称'), required(run.model_version, '模型版本'), optional(run.threshold_version),
          optional(run.substation_id), optional(run.equipment_id), run.input_started_at ? timestamp(run.input_started_at) : null,
          run.input_ended_at ? timestamp(run.input_ended_at) : null, optional(run.status) || 'succeeded',
          required(run.data_quality, '数据质量'), optional(run.source_reference), optional(run.error), createdAt,
          run.completed_at ? timestamp(run.completed_at) : createdAt);
      for (const item of results) this.db.prepare(`INSERT INTO model_inference_result(id,run_id,equipment_id,observed_at,
        result_type,suspected_class,score,decision,metrics_json,raw_result_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)`)
        .run(optional(item.id) || randomUUID(), runId, optional(item.equipment_id) || optional(run.equipment_id),
          timestamp(item.observed_at || run.input_ended_at || createdAt), required(item.result_type, '结果类型'),
          optional(item.suspected_class), numberOrNull(item.score, '模型分数'), required(item.decision, '模型决策'),
          json(item.metrics), json(item.raw_result ?? item), createdAt);
    });
    return { duplicate: false, run_id: runId, result_count: results.length };
  }

  diagnosis({ equipmentId = null, modelFamily = null, limit = 200 } = {}) {
    return this.db.prepare(`SELECT r.*,q.request_id,q.model_family,q.model_name,q.model_version,q.data_quality,e.code equipment_code,e.name equipment_name
      FROM model_inference_result r JOIN model_inference_run q ON q.id=r.run_id
      LEFT JOIN equipment_info e ON e.id=r.equipment_id WHERE (? IS NULL OR r.equipment_id=?)
      AND (? IS NULL OR q.model_family=?) ORDER BY r.observed_at DESC LIMIT ?`)
      .all(equipmentId, equipmentId, modelFamily, modelFamily, Math.min(Math.max(Number(limit) || 200, 1), 1000))
      .map(row => ({ ...row, metrics: parse(row.metrics_json), raw_result: parse(row.raw_result_json), metrics_json: undefined, raw_result_json: undefined }));
  }

  topology(substationId = null) {
    const tree = this.deviceTree(substationId);
    return { ...tree, live_values: this.snapshot(substationId).values };
  }

  agentBootstrap() {
    const warnings = this.warnings({ limit: 100 });
    const model = this.diagnosis({ limit: 100 });
    const events = [
      ...warnings.map(row => ({ id: row.id, kind: 'warning', equipment_id: row.equipment_id, equipment_name: row.equipment_name,
        title: row.title, severity: row.severity, status: row.status, occurred_at: row.occurred_at, content: row.content,
        source: 'business_database' })),
      ...model.map(row => ({ id: row.id, kind: 'model_result', equipment_id: row.equipment_id, equipment_name: row.equipment_name,
        title: `${row.model_name}：${row.suspected_class || row.decision}`, severity: row.decision === 'anomaly' ? 'major' : 'info',
        status: row.decision, occurred_at: row.observed_at, score: row.score, model_family: row.model_family,
        model_version: row.model_version, source: 'business_database' }))
    ].sort((a, b) => b.occurred_at.localeCompare(a.occurred_at));
    const state = this.status();
    return { mode: events.length ? 'live' : 'empty', source: 'business_database', source_status: state.source_status,
      events, history: [], operations_status: state };
  }

  purgeExpired() {
    const layouts = {
      telemetry_info: ['id','point_id','equipment_id','observed_at','value','unit','quality','cause','source_sequence','raw_json','ingested_at'],
      telesignalling_info: ['id','point_id','equipment_id','observed_at','state','value_text','quality','cause','source_sequence','raw_json','ingested_at'],
      meteorograph_data_info: ['id','substation_id','equipment_id','observed_at','irradiance_wm2','ambient_temperature_c',
        'module_temperature_c','wind_speed_ms','wind_direction_deg','humidity_percent','pressure_hpa','quality','raw_json']
    };
    const result = {};
    const runId = randomUUID();
    const startedAt = timestamp();
    transaction(this.db, () => {
      for (const policy of this.db.prepare('SELECT * FROM data_retention_policy').all()) {
        const columns = layouts[policy.table_name];
        if (!columns) continue;
        const modifier = `-${policy.hot_days} days`;
        const cutoff = this.db.prepare("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now',?) value").get(modifier).value;
        let archived = 0;
        if (policy.archive_enabled) {
          archived = this.db.prepare(`INSERT OR IGNORE INTO ${policy.table_name}_archive(${columns.join(',')},archived_at)
            SELECT ${columns.join(',')},? FROM ${policy.table_name} WHERE observed_at < ?`).run(startedAt, cutoff).changes;
        }
        const deleted = this.db.prepare(`DELETE FROM ${policy.table_name} WHERE observed_at < ?`).run(cutoff).changes;
        result[policy.table_name] = { cutoff, archived, deleted };
      }
      const completedAt = timestamp();
      this.db.prepare(`INSERT INTO operations_maintenance_run(id,task_type,status,result_json,started_at,completed_at)
        VALUES (?,'rolling_archive','succeeded',?,?,?)`).run(runId, json(result), startedAt, completedAt);
    });
    return { run_id: runId, tables: result };
  }
}
