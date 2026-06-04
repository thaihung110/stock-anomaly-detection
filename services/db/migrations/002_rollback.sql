-- =====================================================================
-- 002 ROLLBACK — undo multi-user routing migration
-- =====================================================================
-- ⚠️ DESTRUCTIVE: Sẽ xoá vĩnh viễn watchlist + preferences + chat_id của
-- mọi user. Chỉ chạy khi cần revert Phase 0.
--
-- Thứ tự DROP: trigger → function → table → index → column → type
-- (ngược lại với thứ tự CREATE trong 002_multi_user_routing.sql).
-- =====================================================================

BEGIN;

-- Triggers
DROP TRIGGER IF EXISTS trg_prefs_touch_updated ON user_preferences;
DROP TRIGGER IF EXISTS trg_users_touch_updated ON users;
DROP TRIGGER IF EXISTS trg_users_create_prefs  ON users;

-- Functions
DROP FUNCTION IF EXISTS touch_preferences_updated_at();
DROP FUNCTION IF EXISTS touch_users_updated_at();
DROP FUNCTION IF EXISTS create_default_preferences();

-- Tables (CASCADE để bỏ FK constraints)
DROP TABLE IF EXISTS user_preferences CASCADE;
DROP TABLE IF EXISTS user_watchlist   CASCADE;

-- Index trên user_alert_events
DROP INDEX IF EXISTS idx_uae_user_triggered;

-- Index trên users
DROP INDEX IF EXISTS idx_users_chat_id;

-- Columns trên users (theo thứ tự ngược với ADD)
ALTER TABLE users
    DROP COLUMN IF EXISTS last_seen_at,
    DROP COLUMN IF EXISTS updated_at,
    DROP COLUMN IF EXISTS chat_id;

-- ENUM type — drop sau cùng vì user_preferences phụ thuộc
DROP TYPE IF EXISTS system_alert_mode;

COMMIT;

-- =====================================================================
-- Verify rollback:
--   \d users                       -- không còn chat_id/updated_at/last_seen_at
--   \dt user_watchlist             -- "Did not find any relation"
--   \dt user_preferences           -- "Did not find any relation"
--   SELECT typname FROM pg_type WHERE typname = 'system_alert_mode';  -- empty
-- =====================================================================
