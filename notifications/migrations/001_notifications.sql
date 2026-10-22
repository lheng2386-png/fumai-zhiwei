PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS personnel (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 100),
    phone TEXT,
    email TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS equipment (
    id TEXT PRIMARY KEY,
    plant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    owner_id TEXT REFERENCES personnel(id) ON UPDATE CASCADE ON DELETE SET NULL,
    owner_updated_at TEXT NOT NULL,
    UNIQUE (plant_id, name)
);

CREATE TABLE IF NOT EXISTS equipment_owner_history (
    id TEXT PRIMARY KEY,
    equipment_id TEXT NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
    owner_id TEXT REFERENCES personnel(id) ON DELETE SET NULL,
    changed_at TEXT NOT NULL,
    changed_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS equipment_observation (
    id TEXT PRIMARY KEY,
    equipment_id TEXT NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
    observed_at TEXT NOT NULL,
    raw_reference TEXT,
    raw_json TEXT,
    data_quality TEXT NOT NULL CHECK (data_quality IN ('ready', 'uncertain', 'blocked', 'missing')),
    created_at TEXT NOT NULL,
    UNIQUE (equipment_id, observed_at)
);

CREATE TABLE IF NOT EXISTS model_result (
    id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES equipment_observation(id) ON DELETE RESTRICT,
    equipment_id TEXT NOT NULL REFERENCES equipment(id) ON DELETE RESTRICT,
    observed_at TEXT NOT NULL,
    task_name TEXT NOT NULL,
    request_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    threshold_version TEXT NOT NULL,
    raw_result TEXT NOT NULL,
    suspected_class TEXT,
    score REAL,
    decision TEXT NOT NULL CHECK (decision IN ('normal', 'anomaly', 'uncertain', 'blocked')),
    quality_status TEXT NOT NULL CHECK (quality_status IN ('ready', 'uncertain', 'blocked', 'missing')),
    incident_key TEXT NOT NULL,
    state_level INTEGER NOT NULL DEFAULT 1 CHECK (state_level >= 1),
    created_at TEXT NOT NULL,
    UNIQUE (request_id)
);

CREATE TABLE IF NOT EXISTS human_confirmation (
    id TEXT PRIMARY KEY,
    model_result_id TEXT NOT NULL REFERENCES model_result(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('confirmed_fault', 'rejected', 'needs_investigation')),
    note TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_generation (
    id TEXT PRIMARY KEY,
    plant_id TEXT NOT NULL,
    local_date TEXT NOT NULL,
    actual_kwh REAL,
    forecast_kwh REAL,
    forecast_generated_at TEXT,
    forecast_model_version TEXT,
    business_timezone TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    is_test_forecast INTEGER NOT NULL DEFAULT 1 CHECK (is_test_forecast IN (0, 1)),
    updated_at TEXT NOT NULL,
    UNIQUE (plant_id, local_date, revision)
);

CREATE TABLE IF NOT EXISTS notification_event (
    id TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL CHECK (type IN ('model_anomaly', 'confirmed_fault', 'daily_generation_ready')),
    equipment_id TEXT REFERENCES equipment(id) ON DELETE RESTRICT,
    plant_id TEXT,
    local_date TEXT,
    source_table TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    fact_snapshot TEXT NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 1 CHECK (simulated IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_delivery (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES notification_event(id) ON DELETE CASCADE,
    recipient_id TEXT NOT NULL REFERENCES personnel(id) ON DELETE RESTRICT,
    channel TEXT NOT NULL CHECK (channel IN ('sms', 'email')),
    address_snapshot TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending', 'processing', 'retry_wait', 'verify_pending', 'simulated_sent', 'sent', 'failed', 'needs_attention')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_retry_at TEXT,
    provider_message_id TEXT,
    error_code TEXT,
    sent_at TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (event_id, recipient_id, channel)
);

CREATE TABLE IF NOT EXISTS notification_attempt (
    id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL REFERENCES notification_delivery(id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    provider_message_id TEXT,
    error_code TEXT,
    detail TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (delivery_id, attempt_number)
);

CREATE TABLE IF NOT EXISTS notification_problem (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES notification_event(id) ON DELETE CASCADE,
    recipient_id TEXT REFERENCES personnel(id) ON DELETE SET NULL,
    channel TEXT CHECK (channel IS NULL OR channel IN ('sms', 'email')),
    code TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (event_id, recipient_id, channel, code)
);

CREATE TABLE IF NOT EXISTS operator_audit (
    id TEXT PRIMARY KEY,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target_id TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS async_task (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    progress INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    result TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_delivery_claim
ON notification_delivery(status, next_retry_at, lease_expires_at, created_at);
CREATE INDEX IF NOT EXISTS idx_event_created ON notification_event(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_model_equipment_time ON model_result(equipment_id, observed_at DESC);

CREATE TRIGGER IF NOT EXISTS trg_equipment_owner_insert
AFTER INSERT ON equipment
WHEN NEW.owner_id IS NOT NULL
BEGIN
    INSERT INTO equipment_owner_history(id, equipment_id, owner_id, changed_at, changed_by)
    VALUES (lower(hex(randomblob(16))), NEW.id, NEW.owner_id, NEW.owner_updated_at, 'system:create');
END;

CREATE TRIGGER IF NOT EXISTS trg_equipment_owner_update
AFTER UPDATE OF owner_id ON equipment
WHEN NEW.owner_id IS NOT OLD.owner_id
BEGIN
    INSERT INTO equipment_owner_history(id, equipment_id, owner_id, changed_at, changed_by)
    VALUES (lower(hex(randomblob(16))), NEW.id, NEW.owner_id, NEW.owner_updated_at, 'system:update');
END;

CREATE TRIGGER IF NOT EXISTS trg_model_anomaly_event
AFTER INSERT ON model_result
WHEN NEW.decision = 'anomaly' AND NEW.quality_status = 'ready'
BEGIN
    INSERT OR IGNORE INTO notification_event(
        id, dedupe_key, type, equipment_id, plant_id, source_table, source_id,
        source_version, fact_snapshot, simulated, created_at
    )
    SELECT lower(hex(randomblob(16))),
           'equipment:' || NEW.equipment_id || ':incident:' || NEW.incident_key || ':level:' || NEW.state_level,
           'model_anomaly', NEW.equipment_id, e.plant_id, 'model_result', NEW.id,
           NEW.model_version || '/' || NEW.threshold_version,
           json_object(
               'fact_status', '疑似异常', 'equipment_id', NEW.equipment_id,
               'equipment_name', e.name, 'observed_at', NEW.observed_at,
               'suspected_class', NEW.suspected_class, 'score', NEW.score,
               'decision', NEW.decision, 'quality_status', NEW.quality_status,
               'incident_key', NEW.incident_key, 'state_level', NEW.state_level
           ), 1, NEW.created_at
    FROM equipment e WHERE e.id = NEW.equipment_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_confirmed_fault_event
AFTER INSERT ON human_confirmation
WHEN NEW.status = 'confirmed_fault'
BEGIN
    INSERT OR IGNORE INTO notification_event(
        id, dedupe_key, type, equipment_id, plant_id, source_table, source_id,
        source_version, fact_snapshot, simulated, created_at
    )
    SELECT lower(hex(randomblob(16))),
           'equipment:' || mr.equipment_id || ':incident:' || mr.incident_key || ':confirmed',
           'confirmed_fault', mr.equipment_id, e.plant_id, 'human_confirmation', NEW.id,
           mr.model_version || '/' || mr.threshold_version,
           json_object(
               'fact_status', '人工确认故障', 'equipment_id', mr.equipment_id,
               'equipment_name', e.name, 'observed_at', mr.observed_at,
               'suspected_class', mr.suspected_class, 'incident_key', mr.incident_key,
               'actor', NEW.actor, 'note', NEW.note
           ), 1, NEW.created_at
    FROM model_result mr JOIN equipment e ON e.id = mr.equipment_id
    WHERE mr.id = NEW.model_result_id AND mr.quality_status = 'ready';
END;

CREATE TRIGGER IF NOT EXISTS trg_daily_generation_insert
AFTER INSERT ON daily_generation
WHEN NEW.actual_kwh IS NOT NULL AND NEW.forecast_kwh IS NOT NULL
BEGIN
    INSERT INTO notification_event(
        id, dedupe_key, type, plant_id, local_date, source_table, source_id,
        source_version, fact_snapshot, simulated, created_at
    ) SELECT
        lower(hex(randomblob(16))),
        'daily:' || NEW.plant_id || ':' || NEW.local_date || ':revision:' || NEW.revision,
        'daily_generation_ready', NEW.plant_id, NEW.local_date, 'daily_generation', NEW.id,
        CAST(NEW.revision AS TEXT),
        json_object(
            'fact_status', '日发电量双值就绪', 'plant_id', NEW.plant_id,
            'local_date', NEW.local_date, 'actual_kwh', NEW.actual_kwh,
            'forecast_kwh', NEW.forecast_kwh, 'revision', NEW.revision,
            'business_timezone', NEW.business_timezone,
            'forecast_model_version', NEW.forecast_model_version,
            'is_test_forecast', NEW.is_test_forecast
        ), 1, NEW.updated_at
    WHERE NOT EXISTS (
        SELECT 1 FROM notification_event
        WHERE dedupe_key = 'daily:' || NEW.plant_id || ':' || NEW.local_date || ':revision:' || NEW.revision
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_daily_generation_update
AFTER UPDATE OF actual_kwh, forecast_kwh, revision ON daily_generation
WHEN NEW.actual_kwh IS NOT NULL AND NEW.forecast_kwh IS NOT NULL
BEGIN
    INSERT INTO notification_event(
        id, dedupe_key, type, plant_id, local_date, source_table, source_id,
        source_version, fact_snapshot, simulated, created_at
    ) SELECT
        lower(hex(randomblob(16))),
        'daily:' || NEW.plant_id || ':' || NEW.local_date || ':revision:' || NEW.revision,
        'daily_generation_ready', NEW.plant_id, NEW.local_date, 'daily_generation', NEW.id,
        CAST(NEW.revision AS TEXT),
        json_object(
            'fact_status', '日发电量双值就绪', 'plant_id', NEW.plant_id,
            'local_date', NEW.local_date, 'actual_kwh', NEW.actual_kwh,
            'forecast_kwh', NEW.forecast_kwh, 'revision', NEW.revision,
            'business_timezone', NEW.business_timezone,
            'forecast_model_version', NEW.forecast_model_version,
            'is_test_forecast', NEW.is_test_forecast
        ), 1, NEW.updated_at
    WHERE NOT EXISTS (
        SELECT 1 FROM notification_event
        WHERE dedupe_key = 'daily:' || NEW.plant_id || ':' || NEW.local_date || ':revision:' || NEW.revision
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_equipment_event_deliveries
AFTER INSERT ON notification_event
WHEN NEW.type IN ('model_anomaly', 'confirmed_fault')
BEGIN
    INSERT OR IGNORE INTO notification_delivery(
        id, event_id, recipient_id, channel, address_snapshot, status,
        error_code, created_at, updated_at
    )
    SELECT lower(hex(randomblob(16))), NEW.id, p.id, 'sms', p.phone,
           CASE WHEN p.phone IS NULL OR length(trim(p.phone)) < 7 THEN 'needs_attention' ELSE 'pending' END,
           CASE WHEN p.phone IS NULL OR length(trim(p.phone)) < 7 THEN 'INVALID_OR_MISSING_PHONE' END,
           NEW.created_at, NEW.created_at
    FROM equipment e JOIN personnel p ON p.id = e.owner_id
    WHERE e.id = NEW.equipment_id AND p.enabled = 1;

    INSERT OR IGNORE INTO notification_delivery(
        id, event_id, recipient_id, channel, address_snapshot, status,
        error_code, created_at, updated_at
    )
    SELECT lower(hex(randomblob(16))), NEW.id, p.id, 'email', p.email,
           CASE WHEN p.email IS NULL OR instr(p.email, '@') < 2 THEN 'needs_attention' ELSE 'pending' END,
           CASE WHEN p.email IS NULL OR instr(p.email, '@') < 2 THEN 'INVALID_OR_MISSING_EMAIL' END,
           NEW.created_at, NEW.created_at
    FROM equipment e JOIN personnel p ON p.id = e.owner_id
    WHERE e.id = NEW.equipment_id AND p.enabled = 1;

    INSERT OR IGNORE INTO notification_problem(id, event_id, code, detail, created_at)
    SELECT lower(hex(randomblob(16))), NEW.id, 'NO_ENABLED_OWNER', '设备未配置启用负责人', NEW.created_at
    WHERE NOT EXISTS (
        SELECT 1 FROM equipment e JOIN personnel p ON p.id = e.owner_id
        WHERE e.id = NEW.equipment_id AND p.enabled = 1
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_daily_event_deliveries
AFTER INSERT ON notification_event
WHEN NEW.type = 'daily_generation_ready'
BEGIN
    INSERT OR IGNORE INTO notification_delivery(
        id, event_id, recipient_id, channel, address_snapshot, status,
        error_code, created_at, updated_at
    )
    SELECT lower(hex(randomblob(16))), NEW.id, p.id, 'sms', p.phone,
           CASE WHEN p.phone IS NULL OR length(trim(p.phone)) < 7 THEN 'needs_attention' ELSE 'pending' END,
           CASE WHEN p.phone IS NULL OR length(trim(p.phone)) < 7 THEN 'INVALID_OR_MISSING_PHONE' END,
           NEW.created_at, NEW.created_at
    FROM personnel p WHERE p.enabled = 1;

    INSERT OR IGNORE INTO notification_delivery(
        id, event_id, recipient_id, channel, address_snapshot, status,
        error_code, created_at, updated_at
    )
    SELECT lower(hex(randomblob(16))), NEW.id, p.id, 'email', p.email,
           CASE WHEN p.email IS NULL OR instr(p.email, '@') < 2 THEN 'needs_attention' ELSE 'pending' END,
           CASE WHEN p.email IS NULL OR instr(p.email, '@') < 2 THEN 'INVALID_OR_MISSING_EMAIL' END,
           NEW.created_at, NEW.created_at
    FROM personnel p WHERE p.enabled = 1;
END;

CREATE TRIGGER IF NOT EXISTS trg_delivery_problem
AFTER INSERT ON notification_delivery
WHEN NEW.status = 'needs_attention'
BEGIN
    INSERT OR IGNORE INTO notification_problem(
        id, event_id, recipient_id, channel, code, detail, created_at
    ) VALUES (
        lower(hex(randomblob(16))), NEW.event_id, NEW.recipient_id, NEW.channel,
        COALESCE(NEW.error_code, 'CONTACT_REQUIRED'),
        '收件地址缺失或格式非法，请在人员管理中修正；本事件保留原地址快照。', NEW.created_at
    );
END;

