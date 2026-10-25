PRAGMA foreign_keys = ON;

-- 冷数据保留区。业务查询默认只读热表，定时维护先归档再删除，避免直接丢失历史采集数据。
CREATE TABLE IF NOT EXISTS telemetry_info_archive (
    id TEXT PRIMARY KEY, point_id TEXT NOT NULL, equipment_id TEXT NOT NULL, observed_at TEXT NOT NULL,
    value REAL, unit TEXT, quality TEXT NOT NULL, cause INTEGER, source_sequence INTEGER,
    raw_json TEXT NOT NULL, ingested_at TEXT NOT NULL, archived_at TEXT NOT NULL,
    UNIQUE(point_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_telemetry_archive_equipment_time
    ON telemetry_info_archive(equipment_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS telesignalling_info_archive (
    id TEXT PRIMARY KEY, point_id TEXT NOT NULL, equipment_id TEXT NOT NULL, observed_at TEXT NOT NULL,
    state INTEGER, value_text TEXT, quality TEXT NOT NULL, cause INTEGER, source_sequence INTEGER,
    raw_json TEXT NOT NULL, ingested_at TEXT NOT NULL, archived_at TEXT NOT NULL,
    UNIQUE(point_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_signal_archive_equipment_time
    ON telesignalling_info_archive(equipment_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS meteorograph_data_info_archive (
    id TEXT PRIMARY KEY, substation_id TEXT NOT NULL, equipment_id TEXT, observed_at TEXT NOT NULL,
    irradiance_wm2 REAL, ambient_temperature_c REAL, module_temperature_c REAL,
    wind_speed_ms REAL, wind_direction_deg REAL, humidity_percent REAL, pressure_hpa REAL,
    quality TEXT NOT NULL, raw_json TEXT NOT NULL, archived_at TEXT NOT NULL,
    UNIQUE(substation_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_meteorograph_archive_station_time
    ON meteorograph_data_info_archive(substation_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS operations_maintenance_run (
    id TEXT PRIMARY KEY, task_type TEXT NOT NULL, status TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}', started_at TEXT NOT NULL, completed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_operations_maintenance_time
    ON operations_maintenance_run(completed_at DESC);

UPDATE data_retention_policy SET archive_enabled=1,
    updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
WHERE table_name IN ('telemetry_info','telesignalling_info','meteorograph_data_info');
