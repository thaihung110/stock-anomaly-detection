-- Phase 3.1: Initial schema for user-defined custom alerts
-- NEVER UPDATE OR DELETE rows in user_alert_events

CREATE TYPE alert_operator AS ENUM ('>', '<', '>=', '<=', 'CROSSES_UP', 'CROSSES_DOWN');

CREATE TYPE alert_field AS ENUM (
    'price',
    'daily_return',
    'day_volume',
    'volume_zscore',
    'volume_ratio_20d',
    'price_zscore',
    'rsi_14',       -- batch daily (end-of-previous-day), not real-time intraday
    'bb_position'   -- batch daily (end-of-previous-day), not real-time intraday
);

CREATE TYPE alert_status AS ENUM ('ACTIVE', 'PAUSED', 'TRIGGERED');

CREATE TYPE alert_frequency AS ENUM ('ONCE', 'EVERY_TIME');

CREATE TABLE users (
    user_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_id BIGINT UNIQUE NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE user_alert_rules (
    rule_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(user_id),
    symbols      TEXT[] NOT NULL,
    field        alert_field NOT NULL,
    operator     alert_operator NOT NULL,
    threshold    FLOAT NOT NULL,
    frequency    alert_frequency NOT NULL DEFAULT 'EVERY_TIME',
    cooldown_min INT NOT NULL DEFAULT 60,
    status       alert_status NOT NULL DEFAULT 'ACTIVE',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Immutable event log — INSERT only, never UPDATE or DELETE
CREATE TABLE user_alert_events (
    event_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_id            UUID NOT NULL REFERENCES user_alert_rules(rule_id),
    user_id            UUID NOT NULL REFERENCES users(user_id),
    symbol             TEXT NOT NULL,
    triggered_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    field_snapshot     alert_field NOT NULL,
    operator_snapshot  alert_operator NOT NULL,
    threshold_snapshot FLOAT NOT NULL,
    triggered_value    FLOAT NOT NULL
);

CREATE TABLE sync_watermarks (
    job_name     TEXT PRIMARY KEY,
    last_sync_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_uar_user_id   ON user_alert_rules(user_id);
CREATE INDEX idx_uar_status    ON user_alert_rules(status);
CREATE INDEX idx_uae_rule_id   ON user_alert_events(rule_id);
CREATE INDEX idx_uae_triggered ON user_alert_events(triggered_at);
