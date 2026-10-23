-- Link a website account to its own notification recipient without claiming another employee's row.
CREATE TABLE IF NOT EXISTS app_user_contact (
    user_id TEXT PRIMARY KEY REFERENCES app_user(id),
    personnel_id TEXT NOT NULL UNIQUE REFERENCES personnel(id),
    updated_at TEXT NOT NULL
);
