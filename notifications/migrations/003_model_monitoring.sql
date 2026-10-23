CREATE TABLE IF NOT EXISTS model_asset_mapping (
    plant_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    equipment_id TEXT NOT NULL REFERENCES equipment(id) ON DELETE RESTRICT,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    PRIMARY KEY (plant_id, asset_id)
);
CREATE TABLE IF NOT EXISTS model_import_receipt (
    request_id TEXT PRIMARY KEY,
    payload_sha256 TEXT NOT NULL,
    outcome_json TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_evidence_receipt (
    evidence_id TEXT PRIMARY KEY,
    payload_sha256 TEXT NOT NULL,
    model_result_id TEXT NOT NULL UNIQUE REFERENCES model_result(id) ON DELETE RESTRICT,
    data_kind TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS equipment_monitor_log (
    id TEXT PRIMARY KEY,
    equipment_id TEXT NOT NULL REFERENCES equipment(id) ON DELETE RESTRICT,
    model_result_id TEXT NOT NULL REFERENCES model_result(id) ON DELETE RESTRICT,
    source_table TEXT NOT NULL,
    source_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('model_result','human_confirmation')),
    decision TEXT NOT NULL,
    quality_status TEXT NOT NULL,
    task_name TEXT NOT NULL,
    summary TEXT NOT NULL,
    actor TEXT,
    data_kind TEXT NOT NULL DEFAULT 'legacy_unknown',
    evidence_id TEXT,
    UNIQUE(source_table, source_id)
);
CREATE INDEX IF NOT EXISTS idx_monitor_device_time ON equipment_monitor_log(equipment_id, observed_at DESC, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_monitor_model ON equipment_monitor_log(model_result_id);
CREATE INDEX IF NOT EXISTS idx_model_task_time ON model_result(equipment_id,task_name,observed_at DESC,created_at DESC);
CREATE TRIGGER IF NOT EXISTS trg_model_monitor_log
AFTER INSERT ON model_result
BEGIN
    INSERT INTO equipment_monitor_log(id,equipment_id,model_result_id,source_table,source_id,
      observed_at,created_at,kind,decision,quality_status,task_name,summary)
    VALUES(lower(hex(randomblob(16))),NEW.equipment_id,NEW.id,'model_result',NEW.id,
      NEW.observed_at,NEW.created_at,'model_result',NEW.decision,NEW.quality_status,NEW.task_name,
      CASE WHEN NEW.quality_status!='ready' THEN '数据不足或不符合要求，需要检查原始输入'
        WHEN NEW.decision='anomaly' THEN '模型发现疑似异常，等待人工核查'
        WHEN NEW.decision='normal' THEN '本次模型检测未发现对应异常'
        ELSE '本次识别无法确定，需要人工核查' END);
END;
CREATE TRIGGER IF NOT EXISTS trg_human_monitor_log
AFTER INSERT ON human_confirmation
BEGIN
    INSERT INTO equipment_monitor_log(id,equipment_id,model_result_id,source_table,source_id,
      observed_at,created_at,kind,decision,quality_status,task_name,summary,actor,data_kind,evidence_id)
    SELECT lower(hex(randomblob(16))),mr.equipment_id,mr.id,'human_confirmation',NEW.id,
      NEW.created_at,NEW.created_at,'human_confirmation',NEW.status,mr.quality_status,mr.task_name,NEW.note,NEW.actor,
      COALESCE(r.data_kind,'legacy_unknown'),r.evidence_id
    FROM model_result mr LEFT JOIN model_evidence_receipt r ON r.model_result_id=mr.id WHERE mr.id=NEW.model_result_id;
END;
-- Backfill the original results only; never recreate historical notifications.
INSERT OR IGNORE INTO equipment_monitor_log(id,equipment_id,model_result_id,source_table,source_id,
  observed_at,created_at,kind,decision,quality_status,task_name,summary)
SELECT lower(hex(randomblob(16))),equipment_id,id,'model_result',id,observed_at,created_at,
  'model_result',decision,quality_status,task_name,'历史模型记录，数据来源未标记' FROM model_result;
INSERT OR IGNORE INTO equipment_monitor_log(id,equipment_id,model_result_id,source_table,source_id,
  observed_at,created_at,kind,decision,quality_status,task_name,summary,actor)
SELECT lower(hex(randomblob(16))),mr.equipment_id,mr.id,'human_confirmation',h.id,h.created_at,h.created_at,
  'human_confirmation',h.status,mr.quality_status,mr.task_name,h.note,h.actor
FROM human_confirmation h JOIN model_result mr ON mr.id=h.model_result_id;
