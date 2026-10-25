PRAGMA foreign_keys = ON;

-- 光伏场站资产主数据。equipment_info 是统一设备台账，其余表保存各类设备特有字段。
CREATE TABLE IF NOT EXISTS substation_info (
    id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai', latitude REAL, longitude REAL,
    installed_capacity_kw REAL, enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sub_array_info (
    id TEXT PRIMARY KEY, substation_id TEXT NOT NULL REFERENCES substation_info(id),
    code TEXT NOT NULL, name TEXT NOT NULL, installed_capacity_kw REAL, updated_at TEXT NOT NULL,
    UNIQUE(substation_id, code)
);
CREATE TABLE IF NOT EXISTS transformer_info (
    id TEXT PRIMARY KEY, sub_array_id TEXT REFERENCES sub_array_info(id), code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL, rated_capacity_kva REAL, voltage_level TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inverter_info (
    id TEXT PRIMARY KEY, transformer_id TEXT REFERENCES transformer_info(id), code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL, rated_power_kw REAL, manufacturer TEXT, model TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS panel_series_info (
    id TEXT PRIMARY KEY, inverter_id TEXT NOT NULL REFERENCES inverter_info(id), code TEXT NOT NULL,
    name TEXT NOT NULL, panel_count INTEGER, rated_power_kw REAL, updated_at TEXT NOT NULL,
    UNIQUE(inverter_id, code)
);
CREATE TABLE IF NOT EXISTS equipment_info (
    id TEXT PRIMARY KEY, substation_id TEXT NOT NULL REFERENCES substation_info(id),
    parent_id TEXT REFERENCES equipment_info(id), equipment_type TEXT NOT NULL
      CHECK (equipment_type IN ('substation','sub_array','transformer','inverter','panel_series','meteorograph','switch','meter','other')),
    business_ref_id TEXT, code TEXT NOT NULL, name TEXT NOT NULL, manufacturer TEXT, model TEXT,
    serial_number TEXT, status TEXT NOT NULL DEFAULT 'unknown'
      CHECK (status IN ('online','offline','warning','fault','maintenance','unknown')),
    longitude REAL, latitude REAL, metadata_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(substation_id, code)
);

-- IEC 60870-5-104 标准化边界：连接参数与点表分离，密码不入库。
CREATE TABLE IF NOT EXISTS iec104_endpoint (
    id TEXT PRIMARY KEY, substation_id TEXT NOT NULL REFERENCES substation_info(id), name TEXT NOT NULL,
    host TEXT NOT NULL, port INTEGER NOT NULL DEFAULT 2404 CHECK (port BETWEEN 1 AND 65535),
    common_address INTEGER, enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
    connection_status TEXT NOT NULL DEFAULT 'unconfigured'
      CHECK (connection_status IN ('unconfigured','connected','disconnected','error')),
    last_connected_at TEXT, last_error TEXT, updated_at TEXT NOT NULL,
    UNIQUE(substation_id, host, port)
);
CREATE TABLE IF NOT EXISTS iec104_point (
    id TEXT PRIMARY KEY, endpoint_id TEXT NOT NULL REFERENCES iec104_endpoint(id),
    equipment_id TEXT NOT NULL REFERENCES equipment_info(id), ioa INTEGER NOT NULL,
    common_address INTEGER, type_id INTEGER NOT NULL, point_kind TEXT NOT NULL
      CHECK (point_kind IN ('telemetry','telesignalling')),
    name TEXT NOT NULL, unit TEXT, scale REAL NOT NULL DEFAULT 1, offset REAL NOT NULL DEFAULT 0,
    alarm_value TEXT, warning_code TEXT, warning_title TEXT, warning_severity TEXT
      CHECK (warning_severity IS NULL OR warning_severity IN ('info','minor','major','critical')),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)), updated_at TEXT NOT NULL,
    UNIQUE(endpoint_id, ioa)
);

CREATE TABLE IF NOT EXISTS telemetry_info (
    id TEXT PRIMARY KEY, point_id TEXT NOT NULL REFERENCES iec104_point(id),
    equipment_id TEXT NOT NULL REFERENCES equipment_info(id), observed_at TEXT NOT NULL,
    value REAL, unit TEXT, quality TEXT NOT NULL, cause INTEGER, source_sequence INTEGER,
    raw_json TEXT NOT NULL, ingested_at TEXT NOT NULL, UNIQUE(point_id, observed_at)
);
CREATE TABLE IF NOT EXISTS telesignalling_info (
    id TEXT PRIMARY KEY, point_id TEXT NOT NULL REFERENCES iec104_point(id),
    equipment_id TEXT NOT NULL REFERENCES equipment_info(id), observed_at TEXT NOT NULL,
    state INTEGER, value_text TEXT, quality TEXT NOT NULL, cause INTEGER, source_sequence INTEGER,
    raw_json TEXT NOT NULL, ingested_at TEXT NOT NULL, UNIQUE(point_id, observed_at)
);
CREATE TABLE IF NOT EXISTS latest_point_value (
    point_id TEXT PRIMARY KEY REFERENCES iec104_point(id), equipment_id TEXT NOT NULL REFERENCES equipment_info(id),
    point_kind TEXT NOT NULL, observed_at TEXT NOT NULL, numeric_value REAL, text_value TEXT,
    unit TEXT, quality TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_telemetry_equipment_time ON telemetry_info(equipment_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_signal_equipment_time ON telesignalling_info(equipment_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS meteorograph_data_info (
    id TEXT PRIMARY KEY, substation_id TEXT NOT NULL REFERENCES substation_info(id), equipment_id TEXT REFERENCES equipment_info(id),
    observed_at TEXT NOT NULL, irradiance_wm2 REAL, ambient_temperature_c REAL, module_temperature_c REAL,
    wind_speed_ms REAL, wind_direction_deg REAL, humidity_percent REAL, pressure_hpa REAL,
    quality TEXT NOT NULL, raw_json TEXT NOT NULL DEFAULT '{}', UNIQUE(substation_id, observed_at)
);
CREATE TABLE IF NOT EXISTS warning_info (
    id TEXT PRIMARY KEY, dedupe_key TEXT NOT NULL UNIQUE, substation_id TEXT NOT NULL REFERENCES substation_info(id),
    equipment_id TEXT REFERENCES equipment_info(id), source_type TEXT NOT NULL, source_id TEXT,
    warning_code TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL, severity TEXT NOT NULL
      CHECK (severity IN ('info','minor','major','critical')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','acknowledged','cleared')),
    occurred_at TEXT NOT NULL, acknowledged_by TEXT, acknowledged_at TEXT, cleared_at TEXT,
    quality TEXT NOT NULL DEFAULT 'good', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_warning_status_time ON warning_info(status, occurred_at DESC);
CREATE TABLE IF NOT EXISTS fault_history_info (
    id TEXT PRIMARY KEY, warning_id TEXT REFERENCES warning_info(id), equipment_id TEXT NOT NULL REFERENCES equipment_info(id),
    fault_code TEXT NOT NULL, fault_name TEXT NOT NULL, suspected_cause TEXT, resolution TEXT,
    status TEXT NOT NULL CHECK (status IN ('suspected','confirmed','processing','resolved','rejected')),
    discovered_at TEXT NOT NULL, confirmed_at TEXT, resolved_at TEXT, confirmed_by TEXT,
    model_result_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS equipment_materials_info (
    id TEXT PRIMARY KEY, equipment_id TEXT NOT NULL REFERENCES equipment_info(id), material_type TEXT NOT NULL,
    title TEXT NOT NULL, file_url TEXT NOT NULL, version TEXT, checksum TEXT, uploaded_by TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS equipment_inspection_info (
    id TEXT PRIMARY KEY, equipment_id TEXT NOT NULL REFERENCES equipment_info(id), inspector_id TEXT,
    inspection_type TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('planned','in_progress','passed','issue_found','closed')),
    scheduled_at TEXT, started_at TEXT, completed_at TEXT, result_json TEXT NOT NULL DEFAULT '{}',
    note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS power_generation_report_info (
    id TEXT PRIMARY KEY, substation_id TEXT NOT NULL REFERENCES substation_info(id), local_date TEXT NOT NULL,
    timezone TEXT NOT NULL, actual_kwh REAL, forecast_kwh REAL, grid_kwh REAL, installed_capacity_kw REAL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1), is_simulated INTEGER NOT NULL DEFAULT 0 CHECK (is_simulated IN (0,1)),
    source TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(substation_id, local_date, revision)
);
CREATE INDEX IF NOT EXISTS idx_generation_station_date ON power_generation_report_info(substation_id, local_date DESC);

-- 三套模型统一结果契约，模型程序通过 request_id 幂等写入。
CREATE TABLE IF NOT EXISTS model_inference_run (
    id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, model_family TEXT NOT NULL
      CHECK (model_family IN ('power_reference','dc_five_class','opencem','deep_tcn','other')),
    model_name TEXT NOT NULL, model_version TEXT NOT NULL, threshold_version TEXT,
    substation_id TEXT REFERENCES substation_info(id), equipment_id TEXT REFERENCES equipment_info(id),
    input_started_at TEXT, input_ended_at TEXT, status TEXT NOT NULL
      CHECK (status IN ('queued','running','succeeded','failed','blocked')),
    data_quality TEXT NOT NULL, source_reference TEXT, error TEXT, created_at TEXT NOT NULL, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS model_inference_result (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES model_inference_run(id) ON DELETE CASCADE,
    equipment_id TEXT REFERENCES equipment_info(id), observed_at TEXT NOT NULL, result_type TEXT NOT NULL,
    suspected_class TEXT, score REAL, decision TEXT NOT NULL
      CHECK (decision IN ('normal','anomaly','uncertain','blocked')),
    metrics_json TEXT NOT NULL DEFAULT '{}', raw_result_json TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(run_id, equipment_id, observed_at, result_type)
);
CREATE INDEX IF NOT EXISTS idx_model_result_equipment_time ON model_inference_result(equipment_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS data_retention_policy (
    table_name TEXT PRIMARY KEY, hot_days INTEGER NOT NULL CHECK (hot_days > 0),
    archive_enabled INTEGER NOT NULL DEFAULT 0 CHECK (archive_enabled IN (0,1)), updated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO data_retention_policy(table_name, hot_days, archive_enabled, updated_at) VALUES
 ('telemetry_info', 90, 0, strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 ('telesignalling_info', 365, 0, strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 ('meteorograph_data_info', 365, 0, strftime('%Y-%m-%dT%H:%M:%fZ','now'));
