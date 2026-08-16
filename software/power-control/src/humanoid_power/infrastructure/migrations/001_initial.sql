CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at_utc TEXT NOT NULL,
    password_changed_at_utc TEXT NOT NULL,
    failed_login_count INTEGER NOT NULL DEFAULT 0,
    locked_until_utc TEXT NULL
);

CREATE TABLE IF NOT EXISTS system_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    active_profile TEXT NOT NULL CHECK (active_profile IN ('MONITOR', 'DIRECT', 'TIMED')),
    timezone TEXT NOT NULL,
    show_schedules_tab INTEGER NOT NULL DEFAULT 1 CHECK (show_schedules_tab IN (0, 1)),
    show_activity_tab INTEGER NOT NULL DEFAULT 1 CHECK (show_activity_tab IN (0, 1)),
    technical_details_default INTEGER NOT NULL DEFAULT 0
        CHECK (technical_details_default IN (0, 1)),
    config_version INTEGER NOT NULL DEFAULT 1 CHECK (config_version >= 1),
    updated_at_utc TEXT NOT NULL,
    updated_by INTEGER NULL REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS outlet_settings (
    outlet_id INTEGER PRIMARY KEY CHECK (outlet_id BETWEEN 1 AND 4),
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 48),
    description TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 120),
    dashboard_visible INTEGER NOT NULL DEFAULT 1 CHECK (dashboard_visible IN (0, 1)),
    control_enabled INTEGER NOT NULL DEFAULT 1 CHECK (control_enabled IN (0, 1)),
    criticality TEXT NOT NULL DEFAULT 'NORMAL' CHECK (criticality IN ('NORMAL', 'CRITICAL')),
    confirm_on INTEGER NOT NULL DEFAULT 0 CHECK (confirm_on IN (0, 1)),
    confirm_off INTEGER NOT NULL DEFAULT 0 CHECK (confirm_off IN (0, 1)),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    updated_at_utc TEXT NOT NULL,
    updated_by INTEGER NULL REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS hardware_operations (
    operation_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    outlet_id INTEGER NOT NULL CHECK (outlet_id BETWEEN 1 AND 4),
    source TEXT NOT NULL,
    requested_by INTEGER NULL REFERENCES users(id),
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_at_utc TEXT NOT NULL,
    started_at_utc TEXT NULL,
    completed_at_utc TEXT NULL,
    state_before TEXT NULL,
    state_after TEXT NULL,
    result_code TEXT NULL,
    technical_detail TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_hardware_operations_outlet_status
    ON hardware_operations(outlet_id, status);
CREATE INDEX IF NOT EXISTS idx_hardware_operations_requested
    ON hardware_operations(requested_at_utc DESC);

CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at_utc TEXT NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    actor TEXT NOT NULL,
    outlet_id INTEGER NULL CHECK (outlet_id BETWEEN 1 AND 4),
    message_code TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_events_occurred ON events(occurred_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_events_outlet ON events(outlet_id, occurred_at_utc DESC);
