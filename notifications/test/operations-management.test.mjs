import test from 'node:test';
import assert from 'node:assert/strict';
import { openDatabase } from '../src/db.mjs';
import { OperationsService } from '../src/operations.mjs';

test('运维主数据、巡检、资料和故障记录可写入查询并保持关联', () => {
  const db = openDatabase(':memory:');
  const service = new OperationsService(db);
  service.upsertSubstation({ id: 'station-1', code: 'S1', name: '一号站' });
  service.upsertSubArray({ id: 'array-1', substation_id: 'station-1', code: 'A1', name: '一号子阵' });
  service.upsertTransformer({ id: 'transformer-1', sub_array_id: 'array-1', code: 'T1', name: '一号箱变' });
  service.upsertInverter({ id: 'inverter-1', transformer_id: 'transformer-1', code: 'I1', name: '一号逆变器' });
  service.upsertPanelSeries({ id: 'series-1', inverter_id: 'inverter-1', code: 'P1', name: '一号组串', panel_count: 24 });
  service.upsertEquipment({ id: 'equipment-1', substation_id: 'station-1', equipment_type: 'inverter', code: 'EQ1', name: '设备一' });
  service.upsertMaterial({ id: 'material-1', equipment_id: 'equipment-1', material_type: 'manual', title: '说明书', file_url: '/files/manual.pdf' }, 'operator');
  service.upsertInspection({ id: 'inspection-1', equipment_id: 'equipment-1', inspection_type: 'daily', status: 'issue_found', result: { temperature: 72 } });
  service.upsertFault({ id: 'fault-1', equipment_id: 'equipment-1', fault_code: 'F01', fault_name: '温度异常', status: 'processing' }, 'operator');
  assert.equal(service.list('panel-series').length, 1);
  assert.equal(service.list('materials', { equipmentId: 'equipment-1' })[0].uploaded_by, 'operator');
  assert.equal(service.list('inspections')[0].result.temperature, 72);
  assert.equal(service.list('faults', { status: 'processing' })[0].confirmed_by, 'operator');
  assert.throws(() => service.delete('equipment', 'equipment-1'), /引用/);
  db.close();
});

test('滚动存储先归档过期遥测再清理热表', () => {
  const db = openDatabase(':memory:');
  const service = new OperationsService(db);
  service.upsertSubstation({ id: 'station-1', code: 'S1', name: '一号站' });
  service.upsertEquipment({ id: 'equipment-1', substation_id: 'station-1', equipment_type: 'inverter', code: 'EQ1', name: '设备一' });
  service.upsertEndpoint({ id: 'endpoint-1', substation_id: 'station-1', name: '104 网关', host: '127.0.0.1', enabled: true });
  service.upsertPoint({ id: 'point-1', endpoint_id: 'endpoint-1', equipment_id: 'equipment-1', ioa: 1, type_id: 13, point_kind: 'telemetry', name: '有功功率', unit: 'kW' });
  service.ingestIec104({ endpoint_id: 'endpoint-1', points: [{ ioa: 1, value: 0, observed_at: '2020-01-01T00:00:00Z' }] });
  const result = service.purgeExpired();
  assert.equal(result.tables.telemetry_info.archived, 1);
  assert.equal(result.tables.telemetry_info.deleted, 1);
  assert.equal(db.prepare('SELECT count(*) count FROM telemetry_info_archive').get().count, 1);
  assert.equal(db.prepare('SELECT count(*) count FROM telemetry_info').get().count, 0);
  db.close();
});
